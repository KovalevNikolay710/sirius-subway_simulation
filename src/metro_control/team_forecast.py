"""Person 2's hourly `serve --json` forecast -> our v0.2 15-min ForecastPackage. Pure Python."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import polars as pl

from metro_control import mock
from metro_control.adapters import AdapterError, _checksum, _read_json
from metro_control.contracts import ForecastPackage
from metro_control.entries import day_type, load_holidays
from metro_control.line import load_line
from metro_control.team_schema import validate_team_rows
from metro_control.timeutil import MSK

PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "intrahour_profile.csv"
_THEIRS = {
    "weekday": "рабочий",
    "saturday": "суббота",
    "sunday": "воскресенье",
    "holiday": "воскресенье",
}
_DT_ORDER = ("рабочий", "суббота", "воскресенье")
_FLAT = (0.25,) * 4
HOUR = timedelta(hours=1)
NO_DATA = "no_data"

Profile = dict[tuple[str, str, int], tuple[float, float, float, float]]


@dataclass
class TeamForecast:
    package: ForecastPackage
    no_data: list[str]
    context: dict | None


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def is_team_data(obj: Any) -> bool:
    if isinstance(obj, dict):
        return isinstance(obj.get("records"), list)
    return (
        isinstance(obj, list)
        and bool(obj)
        and all(
            isinstance(r, dict) and {"station_id", "ts", "horizon_min"} <= r.keys() for r in obj
        )
    )


def is_team_file(path: Path | str) -> bool:
    p = Path(path)
    if p.suffix.lower() != ".json":
        return False
    try:
        return is_team_data(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False


def theirs_day_type(our: str) -> str:
    return _THEIRS.get(our, "рабочий")


def profile_day_type(t: datetime) -> str:
    """Their day type of the MSK service date of `t`."""
    return theirs_day_type(day_type(mock._msk_service_date(t), load_holidays()))


@lru_cache(maxsize=4)
def _load_profile(path: str) -> Profile:
    df = pl.read_csv(path).sort("station_id", "day_type", "hour", "quarter")
    out: Profile = {}
    for (s, d, h), g in df.group_by(["station_id", "day_type", "hour"], maintain_order=True):
        sh = dict(zip(g["quarter"].to_list(), g["share"].to_list(), strict=True))
        v = [float(sh.get(q, 0.0)) for q in range(4)]
        tot = sum(v)
        out[(s, d, int(h))] = tuple(x / tot for x in v) if tot > 0 else _FLAT  # type: ignore[assignment]
    return out


def load_profile(path: Path | str | None = None) -> Profile:
    return _load_profile(str(path or PROFILE_PATH))


def shares(profile: Profile, station: str, dtype: str, hour: int) -> tuple[float, ...]:
    for d in (dtype, *_DT_ORDER):
        if (station, d, hour) in profile:
            return profile[(station, d, hour)]
    return _FLAT


def _err(name: str, text: str) -> AdapterError:
    return AdapterError(f"{name}: {text}")


def _parse(name: str, data: Any) -> tuple[list[dict], dict | None, list]:
    if isinstance(data, dict):
        rows, meta, expl = data.get("records"), data.get("meta") or {}, data.get("explanations")
    else:
        rows, meta, expl = data, None, None
    if not isinstance(rows, list) or not rows:
        raise _err(name, "no team rows")
    errs = validate_team_rows(rows)
    if errs:
        raise _err(name, "; ".join(errs[:3]))
    return rows, meta, expl if isinstance(expl, list) else []


def _slot_min(rows: list[dict]) -> int:
    """30 for the «stack» half-hour format, 60 for the hourly one."""
    for r in rows:
        if r["horizon_min"] in (30, 90):
            return 30
        m = datetime.fromisoformat(r["ts"]).astimezone(MSK).minute
        if m == 30:
            return 30
    return 60


def _check_minute(name: str, as_of: datetime, slot_min: int) -> None:
    m = as_of.astimezone(MSK)
    if m.second or m.microsecond:
        raise _err(name, f"as_of must be on an MSK hour (got {m:%H:%M})")
    if slot_min == 30:
        if m.minute not in (0, 30):
            raise _err(name, f"as_of must be on :00 or :30 MSK (got {m:%H:%M})")
    elif m.minute:
        raise _err(name, f"as_of must be on an MSK hour (got {m:%H:%M})")


def read_team_forecast(
    path: Path | str,
    *,
    as_of: datetime | None = None,
    history: pl.DataFrame | None = None,
    profile: Profile | None = None,
) -> TeamForecast:
    p = Path(path)
    name = p.name
    rows, meta, expl = _parse(name, _read_json(p))
    line = load_line()
    known = {s.id for s in line.stations}
    unknown = sorted({r["station_id"] for r in rows} - known)
    if unknown:
        raise _err(name, f"unknown station id: {', '.join(unknown)}")
    prof = profile if profile is not None else load_profile()

    parsed = [(r, datetime.fromisoformat(r["ts"]).astimezone(UTC)) for r in rows]
    slot_min = _slot_min(rows)
    anchor = [t for r, t in parsed if r["horizon_min"] == slot_min]
    if as_of is None:
        if not anchor:
            raise _err(name, f"no horizon_min {slot_min} rows to derive as_of")
        as_of = min(anchor)
        _check_minute(name, as_of, slot_min)
    else:
        if as_of.tzinfo is None:
            raise _err(name, "as_of must be timezone-aware")
        as_of = as_of.astimezone(UTC)
        _check_minute(name, as_of, slot_min)
        if as_of not in anchor:
            raise _err(
                name,
                f"no horizon_min {slot_min} rows for as_of {as_of.astimezone(MSK):%H:%M} MSK",
            )

    by_key: dict = {}
    for r, t in parsed:
        key = (r["station_id"], t, r["horizon_min"])
        if key in by_key:
            raise _err(
                name,
                f"duplicate record: {r['station_id']} {t.astimezone(MSK):%Y-%m-%d %H:%M} MSK "
                f"horizon_min {r['horizon_min']}",
            )
        by_key[key] = r
    out: list[dict] = []
    no_data: list[str] = []
    for st in sorted(line.stations, key=lambda s: s.order):
        if slot_min == 60:
            picks = [(as_of, 60, 60), (as_of + HOUR, 120, 60)]
        else:
            half = timedelta(minutes=30)
            picks = [(as_of + i * half, 30 * (i + 1), 30) for i in range(4)]
        found = [by_key.get((st.id, h, hz)) for h, hz, _ in picks]
        if any(f is None for f in found):
            no_data.append(st.id)
            continue
        for (slot_ts, _, width), r in zip(picks, found, strict=True):
            hr = slot_ts.astimezone(MSK)
            sh = shares(prof, st.id, profile_day_type(slot_ts), hr.hour)
            if width == 30:
                sh = sh[2:] if hr.minute >= 30 else sh[:2]
                tot = sum(sh)
                sh = tuple(x / tot for x in sh) if tot > 0 else (0.5, 0.5)
            for q in range(len(sh)):
                slot = slot_ts + timedelta(minutes=15 * q)
                out.append(
                    {
                        "station_id": st.id,
                        "ts": _iso(slot),
                        "horizon_min": int((slot - as_of).total_seconds() // 60) + 15,
                        "q10": r["q10"] * sh[q],
                        "q50": r["q50"] * sh[q],
                        "q90": r["q90"] * sh[q],
                        "baseline": r["baseline"] * sh[q],
                        "is_anomaly": bool(r["is_anomaly"]),
                        "model_version": str(r["model_version"]),
                    }
                )
    if no_data:
        out += _fill(no_data, history, as_of)
    order = {s.id: s.order for s in line.stations}
    out.sort(key=lambda r: (order[r["station_id"]], r["ts"]))

    versions = {r["model_version"] for r in out if r["model_version"] != NO_DATA}
    is_mock = any(v.startswith(("mock", "stub")) for v in versions)
    status = "mock" if is_mock else "degraded" if no_data else "ok"
    env = {
        "schema_version": "0.2",
        "kind": "forecast",
        "run_id": f"person2-{_iso(as_of)}",
        "generated_at": _iso(as_of),
        "data_mode": "mock" if is_mock else "real",
        "manifest": {"source": f"person2 serve: {name}", "checksum": _checksum(p)},
        "payload": {"as_of": _iso(as_of), "status": status, "quantiles_ready": True, "rows": out},
    }
    try:
        pkg = ForecastPackage.model_validate(env)
    except ValueError as e:
        raise _err(name, str(e).splitlines()[0]) from e
    ctx = None
    if meta is not None:
        ctx = {"meta": meta, "explanations": [_expl_utc(e) for e in expl]}
    return TeamForecast(pkg, no_data, ctx)


def _expl_utc(e: Any) -> Any:
    if isinstance(e, dict) and isinstance(e.get("ts"), str):
        try:
            return {**e, "ts": _iso(datetime.fromisoformat(e["ts"]))}
        except ValueError:
            return e
    return e


def _fill(stations: list[str], history: pl.DataFrame | None, as_of: datetime) -> list[dict]:
    """History-norm rows (assumption team_forecast_missing_station) for stations without data."""
    rows: list[dict] = []
    if history is not None:
        fc = mock.mock_forecast(history, as_of).model_dump(mode="json")["payload"]["rows"]
        rows = [{**r, "model_version": NO_DATA} for r in fc if r["station_id"] in stations]
    else:
        for s in stations:
            for k in range(8):
                rows.append(
                    {
                        "station_id": s,
                        "ts": _iso(as_of + timedelta(minutes=15 * k)),
                        "horizon_min": 15 * (k + 1),
                        "q10": 0.0,
                        "q50": 0.0,
                        "q90": 0.0,
                        "baseline": 0.0,
                        "is_anomaly": False,
                        "model_version": NO_DATA,
                    }
                )
    return rows


def read_team_context(run_dir: Path | str) -> dict | None:
    p = Path(run_dir) / "team_context.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def mock_team_serve(history: pl.DataFrame, as_of: datetime, *, model: str = "lgbm") -> dict:
    """A `serve --json` shaped demo file (18 stations, rain, trains to Moskovsky station). Mock."""
    as_of = as_of.astimezone(UTC)
    fc = mock.mock_forecast(history, as_of).model_dump(mode="json")["payload"]["rows"]
    line = load_line()
    stack = model == "stack"
    step = timedelta(minutes=30) if stack else HOUR
    n_slots = 4 if stack else 2
    nq = 2 if stack else 4
    hours = [as_of + i * step for i in range(n_slots)]
    t0 = (as_of - HOUR).astimezone(MSK)
    records: list[dict] = []
    explanations: list[dict] = []
    for st in sorted(line.stations, key=lambda s: s.order):
        if st.id == "tekhnologichesky_institut":
            continue
        mine = [r for r in fc if r["station_id"] == st.id]
        vos = st.id == "vosstaniya"
        k = 1.12 * (1.25 if vos else 1.0)
        for i, h in enumerate(hours):
            blk = mine[nq * i : nq * i + nq]
            q50 = round(sum(r["q50"] for r in blk) * k)
            ts = h.astimezone(MSK).isoformat()
            hz = (30 if stack else 60) * (i + 1)
            records.append(
                {
                    "station_id": st.id,
                    "ts": ts,
                    "horizon_min": hz,
                    "q10": round(0.85 * q50),
                    "q50": q50,
                    "q90": round(1.15 * q50),
                    "baseline": round(sum(r["baseline"] for r in blk)),
                    "is_anomaly": vos,
                    "model_version": "mock_team_v1",
                }
            )
            reasons = [{"feature": "fc_precip_tau", "text": "дождь с 15:00", "effect_pct": 12.0}]
            if vos:
                reasons.append(
                    {
                        "feature": "railway_arrivals",
                        "text": "прибытие поездов на Московский вокзал",
                        "effect_pct": 25.0,
                    }
                )
            explanations.append(
                {
                    "station_id": st.id,
                    "ts": ts,
                    "horizon_min": hz,
                    "reasons": reasons,
                    "context": {
                        "railway": {"station": "Московский вокзал", "arrivals": 3} if vos else None,
                        "events": None,
                    },
                }
            )
    return {
        "records": records,
        "explanations": explanations,
        "meta": {
            "model": "stack" if stack else "mock",
            "model_version": "mock_team_v1",
            "t0": t0.isoformat(),
            "weather": {
                "source": "mock",
                "warning": None,
                "hours": [h.astimezone(MSK).isoformat() for h in hours],
                "features": [
                    {"ts": h.astimezone(MSK).isoformat(), "fc_precip_tau": 1.2} for h in hours
                ],
            },
            "context": None,
            "warning": "mock: демо-файл в формате serve --json",
            "out_of_sample": False,
        },
    }
