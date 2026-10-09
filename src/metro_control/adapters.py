"""Import files from person 2 (forecast) and person 4 (recommendation, explanation). Pure Python."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import ValidationError

from metro_control import mock, od
from metro_control.contracts import ForecastPackage, LoadPackage, Recommendation
from metro_control.entries import day_type, load_holidays
from metro_control.line import load_line


class AdapterError(ValueError):
    """Source file is unusable; the message starts with the file name."""


@dataclass(frozen=True)
class SourceStatus:
    origin: str  # person2 | person4 | mock
    status: str  # ok | missing | error
    reason: str | None = None
    path: str | None = None


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validation_error(name: str, e: ValidationError) -> AdapterError:
    err = e.errors()[0]
    loc = ".".join(str(x) for x in err["loc"]) or "$"
    return AdapterError(f"{name}: {loc}: {err['msg']}")


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except UnicodeDecodeError as e:
        raise AdapterError(f"{p.name}: not UTF-8 text") from e
    except OSError as e:
        raise AdapterError(f"{p.name}: file not readable") from e
    except ValueError as e:
        raise AdapterError(f"{p.name}: invalid JSON (line {getattr(e, 'lineno', '?')})") from e


def _checksum(p: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError as e:
        raise AdapterError(f"{p.name}: file not readable") from e


def _num(v: Any, col: str, name: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError) as e:
        raise AdapterError(f"{name}: {col}: not a number ({v!r})") from e
    if not math.isfinite(x):
        raise AdapterError(f"{name}: {col}: must be finite (got {v!r})")
    return x


def _parse_time(v: Any, name: str) -> datetime:
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.strip())
        except ValueError as e:
            raise AdapterError(f"{name}: ts: not an ISO datetime ({v!r})") from e
    if not isinstance(v, datetime):
        raise AdapterError(f"{name}: ts: not a datetime ({v!r})")
    if v.tzinfo is None:
        raise AdapterError(f"{name}: ts: must be timezone-aware (got {v.isoformat()})")
    return v.astimezone(UTC)


def read_forecast(
    path: Path | str,
    as_of: datetime | None = None,
    model_name: str | None = None,
    history: pl.DataFrame | None = None,
) -> ForecastPackage:
    """Read a forecast file. Table input: optional team columns baseline, is_anomaly,
    model_version, horizon_min; a missing baseline falls back to q50 (placeholder until A1)."""
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        if suffix == ".json":
            from metro_control import team_forecast

            if team_forecast.is_team_file(p):
                return team_forecast.read_team_forecast(p, as_of=as_of, history=history).package
            return ForecastPackage.model_validate(_read_json(p))
        if suffix not in (".csv", ".parquet"):
            raise AdapterError(f"{p.name}: unsupported format (use .json, .csv or .parquet)")
        try:
            df = pl.read_csv(p) if suffix == ".csv" else pl.read_parquet(p)
        except (pl.exceptions.PolarsError, OSError) as e:
            raise AdapterError(f"{p.name}: cannot read table: {e}".splitlines()[0]) from e
        need = ["station_id", "ts", "q50"]
        miss = [c for c in need if c not in df.columns]
        if miss:
            raise AdapterError(f"{p.name}: missing column {', '.join(miss)}")
        has_q = "q10" in df.columns and "q90" in df.columns
        rows = []
        mv = model_name or "person2"
        given_h: list[int | None] = []
        for r in df.iter_rows(named=True):
            if r["q50"] is None:
                raise AdapterError(f"{p.name}: q50: empty value")
            t = _parse_time(r["ts"], p.name)
            q50 = _num(r["q50"], "q50", p.name)
            q10 = _num(r["q10"], "q10", p.name) if has_q and r["q10"] is not None else q50
            q90 = _num(r["q90"], "q90", p.name) if has_q and r["q90"] is not None else q50
            rows.append(
                {
                    "station_id": r["station_id"],
                    "ts": _iso(t),
                    "q10": q10,
                    "q50": q50,
                    "q90": q90,
                    "baseline": _num(r["baseline"], "baseline", p.name)
                    if r.get("baseline") is not None
                    else q50,
                    "is_anomaly": bool(r["is_anomaly"])
                    if r.get("is_anomaly") is not None
                    else False,
                    "model_version": str(r["model_version"]) if r.get("model_version") else mv,
                }
            )
            h = r.get("horizon_min")
            given_h.append(None if h is None else int(_num(h, "horizon_min", p.name)))
        if not rows:
            raise AdapterError(f"{p.name}: no rows")
        start = min(datetime.fromisoformat(r["ts"]) for r in rows)
        as_of = (as_of or start).astimezone(UTC)
        for i, r in enumerate(rows):
            h = int((datetime.fromisoformat(r["ts"]) - as_of).total_seconds() // 60) + 15
            if given_h[i] is not None and given_h[i] != h:
                raise AdapterError(
                    f"{p.name}: row {i}: horizon_min {given_h[i]} != ts - as_of + 15 min ({h})"
                )
            r["horizon_min"] = h
        env = {
            "schema_version": "0.2",
            "kind": "forecast",
            "run_id": f"person2-{_iso(as_of)}",
            "generated_at": _iso(as_of),
            "data_mode": "real",
            "manifest": {"source": f"person2: {p.name}", "checksum": _checksum(p)},
            "payload": {
                "as_of": _iso(as_of),
                "status": "ok",
                "quantiles_ready": has_q,
                "rows": rows,
            },
        }
        return ForecastPackage.model_validate(env)
    except ValidationError as e:
        raise _validation_error(p.name, e) from e


def read_recommendation(path: Path | str) -> Recommendation:
    p = Path(path)
    data = _read_json(p)
    if not isinstance(data, dict):
        raise AdapterError(f"{p.name}: JSON object expected")
    try:
        if "kind" not in data:
            payload = {"source": "person4", **data}
            as_of = payload.get("as_of")
            if not isinstance(as_of, str):
                raise AdapterError(f"{p.name}: as_of: required")
            try:
                parsed = datetime.fromisoformat(as_of)
            except ValueError as e:
                raise AdapterError(f"{p.name}: as_of: not an ISO datetime") from e
            if parsed.tzinfo is None:
                raise AdapterError(f"{p.name}: as_of: must be timezone-aware (got {as_of})")
            gen = _iso(parsed)
            data = {
                "schema_version": "0.2",
                "kind": "recommendation",
                "run_id": f"person4-{gen}",
                "generated_at": gen,
                "data_mode": "real",
                "manifest": {"source": f"person4: {p.name}", "checksum": _checksum(p)},
                "payload": payload,
            }
        return Recommendation.model_validate(data)
    except ValidationError as e:
        raise _validation_error(p.name, e) from e


def read_explanation(path: Path | str) -> str:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as e:
        raise AdapterError(f"{p.name}: not UTF-8 text") from e
    except OSError as e:
        raise AdapterError(f"{p.name}: file not readable") from e
    if not text:
        raise AdapterError(f"{p.name}: text is empty")
    return text


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _try(origin: str, path: Path | str | None, reader: Any) -> tuple[Any, SourceStatus]:
    if path is None:
        return None, SourceStatus("mock", "missing")
    try:
        return reader(Path(path)), SourceStatus(origin, "ok", None, str(path))
    except (AdapterError, ValueError, OSError) as e:
        return None, SourceStatus("mock", "error", f"{e}".splitlines()[0], str(path))


def build_team_bundle(
    out_dir: Path | str,
    entries: pl.DataFrame,
    *,
    forecast: Path | str | None = None,
    recommendation: Path | str | None = None,
    explanation: Path | str | None = None,
    forecast_as_of: datetime | None = None,
    as_of: datetime | None = None,
    surge: dict[str, float] | None = None,
) -> dict[str, SourceStatus]:
    """as_of: moment of the mock packages when neither forecast nor recommendation gives one.
    surge: demo demand factors for the mock load (None = demo default, {} = none)."""
    from metro_control import team_forecast

    as_of_default = as_of
    out = Path(out_dir)
    team: list[team_forecast.TeamForecast] = []

    def _read_fc(p: Path) -> ForecastPackage:
        if team_forecast.is_team_file(p):
            t = team_forecast.read_team_forecast(p, as_of=forecast_as_of, history=entries)
            team.append(t)
            return t.package
        return read_forecast(p, as_of=forecast_as_of)

    fc, st_fc = _try("person2", forecast, _read_fc)
    rec, st_rec = _try("person4", recommendation, read_recommendation)
    if fc is not None:
        as_of = fc.payload.as_of
        if rec is not None and rec.payload.as_of > as_of:
            rec = None
            st_rec = SourceStatus(
                "mock",
                "error",
                f"{Path(str(recommendation)).name}: as_of after the forecast as_of (future data)",
                str(recommendation),
            )
    elif rec is not None:
        a = rec.payload.as_of
        as_of = a.replace(minute=a.minute // 15 * 15, second=0, microsecond=0)
    else:
        as_of = as_of_default or mock.default_as_of(entries)
    mock.build_mock_bundle(out, entries, as_of, surge=surge)
    if fc is not None:
        history = entries.filter(pl.col("ts") < as_of)
        line = load_line()
        dtype = day_type(mock._msk_service_date(as_of), load_holidays())
        try:
            ld = mock.mock_load(fc, history, line, od.load_od_params(), dtype, surge={})
        except (ValueError, KeyError) as e:
            fc, st_fc = None, SourceStatus("mock", "error", f"load model: {e}", str(forecast))
        else:
            src = "load model on person2 forecast"
            env = mock._envelope("load", f"person2-{mock._iso(as_of)}", as_of, src)
            env["data_mode"] = "real"
            ld = LoadPackage.model_validate(
                {**env, "payload": ld.model_dump(mode="json")["payload"]}
            )
            _dump(out / "forecast.json", fc.model_dump(mode="json"))
            if team:
                t = team[0]
                if t.context is not None:
                    _dump(out / "team_context.json", {**t.context, "no_data": t.no_data})
                if t.no_data:
                    nm = {s.id: s.name_ru for s in line.stations}
                    st_fc = SourceStatus(
                        "person2",
                        "ok",
                        "нет данных: "
                        + ", ".join(nm[i] for i in t.no_data)
                        + " — подставлена норма по истории",
                        str(forecast),
                    )
            _dump(out / "load.json", ld.model_dump(mode="json"))
    if rec is not None:
        _dump(out / "recommendation.json", rec.model_dump(mode="json"))
    if st_fc.origin != "person2" or not team or team[0].context is None:
        (out / "team_context.json").unlink(missing_ok=True)
    stale = out / "explanation.txt"
    if stale.exists():
        stale.unlink()
    if explanation is not None and rec is None:
        st_exp = SourceStatus(
            "mock", "error", "no person 4 recommendation to explain", str(explanation)
        )
    else:
        exp, st_exp = _try("person4", explanation, read_explanation)
        if exp is not None:
            stale.write_text(exp + "\n", encoding="utf-8")
    statuses = {"forecast": st_fc, "recommendation": st_rec, "explanation": st_exp}
    _dump(out / "sources.json", {k: asdict(v) for k, v in statuses.items()})
    return statuses
