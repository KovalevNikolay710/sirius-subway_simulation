"""Pure view-model helpers for the dispatcher screen (no Streamlit)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import ValidationError

from metro_control.contracts import (
    ForecastPackage,
    LoadPackage,
    Recommendation,
    StationEntriesPackage,
)
from metro_control.line import load_line
from metro_control.timeutil import to_msk

BAND_COLORS = {"low": "#2e9e5b", "mid": "#f0b429", "high": "#d64545", "none": "#9aa0a6"}
BAND_LABELS_RU = {"low": "≤80%", "mid": "80–100%", "high": ">100%", "none": "нет движения"}

FILES: dict[str, tuple[str, type]] = {
    "station_entries": ("entries.json", StationEntriesPackage),
    "forecast": ("forecast.json", ForecastPackage),
    "load": ("load.json", LoadPackage),
    "recommendation": ("recommendation.json", Recommendation),
}

ACTION_TITLES_RU = {
    "add_reserve": "Добавить резервный поезд",
    "remove_train": "Снять поезд с линии",
    "shift_peak": "Сдвинуть пик",
    "limit_entry": "Ограничить вход на станцию",
    "none": "Действий не требуется",
}


@dataclass(frozen=True)
class PackageResult:
    kind: str
    ok: bool
    reason: str | None
    package: Any


def load_package(path: Path | str, model: type) -> PackageResult:
    p = Path(path)
    kind = next((k for k, (_, m) in FILES.items() if m is model), p.stem)
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return PackageResult(kind, False, f"{p.name}: unreadable (not UTF-8)", None)
    except OSError:
        return PackageResult(kind, False, f"{p.name}: file not found", None)
    try:
        data = json.loads(text)
    except ValueError as e:
        line = getattr(e, "lineno", "?")
        return PackageResult(kind, False, f"{p.name}: invalid JSON (line {line})", None)
    try:
        return PackageResult(kind, True, None, model.model_validate(data))
    except ValidationError as e:
        err = e.errors()[0]
        loc = ".".join(str(x) for x in err["loc"]) or "$"
        return PackageResult(kind, False, f"{p.name}: {loc}: {err['msg']}", None)
    except (OSError, ValueError) as e:
        return PackageResult(kind, False, f"{p.name}: {e}".splitlines()[0], None)


def load_bundle(run_dir: Path | str) -> dict[str, PackageResult]:
    d = Path(run_dir)
    return {kind: load_package(d / name, model) for kind, (name, model) in FILES.items()}


def band(r: float | None, r_off: float | None = None, r_on: float | None = None) -> str:
    if r_off is None or r_on is None:
        a = _thresholds()
        r_off = a[0] if r_off is None else r_off
        r_on = a[1] if r_on is None else r_on
    if r is None:
        return "none"
    if r <= r_off:
        return "low"
    if r <= r_on:
        return "mid"
    return "high"


def _thresholds() -> tuple[float, float]:
    from metro_control.entries import ASSUMPTIONS_PATH

    items = json.loads(Path(ASSUMPTIONS_PATH).read_text(encoding="utf-8"))["items"]
    return float(items["r_off"]["value"]), float(items["r_on"]["value"])


def segment_view(load_pkg: LoadPackage, at: datetime) -> pl.DataFrame:
    line = load_line()
    r_off, r_on = _thresholds()
    at_rows = {r.segment_id: r for r in load_pkg.payload if r.interval_start == at}
    out = []
    for s in line.segments:
        row = at_rows.get(s.id)
        r = row.r if row else None
        out.append(
            {
                "segment_id": s.id,
                "from_station": s.from_station,
                "to_station": s.to_station,
                "direction": s.direction,
                "order": s.order,
                "demand": row.demand if row else None,
                "departures": row.departures if row else None,
                "r": r,
                "band": band(r, r_off, r_on),
            }
        )
    return pl.DataFrame(
        out,
        schema={
            "segment_id": pl.String,
            "from_station": pl.String,
            "to_station": pl.String,
            "direction": pl.String,
            "order": pl.Int64,
            "demand": pl.Float64,
            "departures": pl.Int64,
            "r": pl.Float64,
            "band": pl.String,
        },
    )


def station_series(
    entries_pkg: StationEntriesPackage | None,
    forecast_pkg: ForecastPackage | None,
    station_id: str,
) -> pl.DataFrame:
    schema = {
        "interval_start": pl.Datetime("us", "UTC"),
        "kind": pl.String,
        "value": pl.Float64,
        "q10": pl.Float64,
        "q90": pl.Float64,
    }
    rows: list[dict[str, Any]] = []
    cutoff = forecast_pkg.payload.as_of if forecast_pkg else None
    if entries_pkg:
        for r in entries_pkg.payload:
            if r.station_id != station_id or (cutoff and r.interval_start >= cutoff):
                continue
            rows.append(
                {
                    "interval_start": r.interval_start,
                    "kind": "fact",
                    "value": r.entries,
                    "q10": None,
                    "q90": None,
                }
            )
    if forecast_pkg:
        for r in forecast_pkg.payload.rows:
            if r.station_id == station_id:
                rows.append(
                    {
                        "interval_start": r.interval_start,
                        "kind": "forecast",
                        "value": r.q50,
                        "q10": r.q10,
                        "q90": r.q90,
                    }
                )
    return pl.DataFrame(rows, schema=schema).sort("interval_start")


def action_card(rec_pkg: Recommendation) -> dict[str, Any]:
    line = load_line()
    p = rec_pkg.payload
    names = {s.id: s.name_ru for s in line.stations}
    seg = {s.id: f"{names[s.from_station]} → {names[s.to_station]}" for s in line.segments}
    target = names.get(p.target) or seg.get(p.target, p.target)
    start, end = to_msk(p.start), to_msk(p.end)
    return {
        "action": p.action,
        "title": ACTION_TITLES_RU[p.action],
        "target": target,
        "window": f"{start:%H:%M}–{end:%H:%M}",
        "reason": p.reason,
        "is_mock": rec_pkg.data_mode == "mock" or p.source == "mock",
    }


KIND_NAMES_RU = {
    "station_entries": "Факт входов",
    "forecast": "Прогноз",
    "load": "Загрузка перегонов",
    "recommendation": "Рекомендация",
}
SOURCE_NAMES_RU = {
    "forecast": "Прогноз человека 2",
    "recommendation": "Рекомендация человека 4",
    "explanation": "Объяснение человека 4",
}


@dataclass(frozen=True)
class StatusLine:
    level: str  # ok | info | warning | error
    text: str


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def source_statuses(run_dir: Path | str, bundle: dict[str, PackageResult]) -> list[StatusLine]:
    d = Path(run_dir)
    out: list[StatusLine] = []
    for kind, res in bundle.items():
        name = KIND_NAMES_RU.get(kind, kind)
        fname = FILES[kind][0] if kind in FILES else kind
        if not res.ok:
            missing = (res.reason or "").endswith("file not found")
            if missing and kind == "forecast":
                text = (
                    "Прогноз: нет файла forecast.json — "
                    "график прогноза и загрузка перегонов недоступны"
                )
            elif missing and kind == "recommendation":
                text = "Рекомендация: нет файла recommendation.json — карточка действия недоступна"
            elif missing:
                text = f"{name}: нет файла {fname}"
            else:
                text = f"{name}: пакет повреждён — {res.reason}"
            out.append(StatusLine("error", text))
        elif res.package.data_mode == "mock":
            out.append(StatusLine("warning", f"{name}: mock"))
        elif kind == "forecast" and res.package.payload.status == "degraded":
            out.append(StatusLine("warning", f"{name}: данные деградированы (status degraded)"))
        else:
            out.append(StatusLine("ok", f"{name}: ok ({res.package.data_mode})"))
    if not _read_text(d / "explanation.txt"):
        out.append(
            StatusLine(
                "warning",
                "Объяснение: нет текста от человека 4 — показана причина из рекомендации",
            )
        )
    sp = d / "sources.json"
    if sp.is_file():
        try:
            src = json.loads(sp.read_text(encoding="utf-8"))
            if not isinstance(src, dict):
                raise ValueError("object expected")
        except (OSError, ValueError) as e:
            out.append(
                StatusLine("warning", f"Источники: sources.json не читается — {e}".splitlines()[0])
            )
            src = {}
        for kind, name in SOURCE_NAMES_RU.items():
            s = src.get(kind)
            if not isinstance(s, dict):
                continue
            tail = "" if kind == "explanation" else "; показан mock"
            if s.get("status") == "error":
                out.append(StatusLine("warning", f"{name}: ошибка — {s.get('reason')}{tail}"))
            elif s.get("status") == "missing" and kind != "explanation":
                out.append(StatusLine("warning", f"{name}: не передан{tail}"))
    fc, rec = bundle.get("forecast"), bundle.get("recommendation")
    if fc and rec and fc.ok and rec.ok and fc.package.payload.as_of != rec.package.payload.as_of:
        a, b = to_msk(fc.package.payload.as_of), to_msk(rec.package.payload.as_of)
        out.append(
            StatusLine(
                "warning",
                f"Время не совпадает: прогноз на {a:%d.%m %H:%M}, "
                f"рекомендация на {b:%d.%m %H:%M} МСК",
            )
        )
    return out


def explanation_text(run_dir: Path | str, rec: Recommendation) -> tuple[str, str]:
    text = (
        _read_text(Path(run_dir) / "explanation.txt") if rec.payload.source == "person4" else None
    )
    if text:
        return text, "person4"
    return rec.payload.reason, "reason"


ACTION_RU = {
    "add_reserve": "Резервный поезд",
    "remove_train": "Снять поезд",
    "shift_peak": "Сдвинуть пик",
}
STATUS_RU = {
    "applied": "выполнено",
    "rejected": "отклонено",
    "pending": "ожидает",
    "source_error": "ошибка источника",
}


def fmt_int(x: float, signed: bool = False) -> str:
    """Integer with narrow no-break space thousands separator; `signed` adds + or minus."""
    n = round(x)
    body = f"{abs(n):,}".replace(",", " ")
    if n < 0:
        return "−" + body
    return ("+" + body) if signed and n > 0 else body


def action_row(ac: dict[str, Any], names: dict[str, str]) -> tuple[str, str, str]:
    """Human-readable (action, target, status) for the actions table."""
    action = str(ac.get("action", ""))
    status = str(ac.get("status", ""))
    target = str(ac.get("target", ""))
    parts = target.split("__")
    if len(parts) == 2 and all(p in names for p in parts):
        target = f"{names[parts[0]]} → {names[parts[1]]}"
    elif target in names:
        target = names[target]
    return ACTION_RU.get(action, action), target, STATUS_RU.get(status, status)


@dataclass(frozen=True)
class RecList:
    items: list[Recommendation]
    problems: list[str]


def load_recommendations(run_dir: Path | str, single: PackageResult | None = None) -> RecList:
    """All recommendations of a run: `recommendations.jsonl` (one Recommendation package per line,
    same v0.1 contract) plus `recommendation.json` if its id is not in the list. Bad lines are
    skipped and reported, never raised."""
    d = Path(run_dir)
    items: list[Recommendation] = []
    problems: list[str] = []
    text = _read_text(d / "recommendations.jsonl")
    for n, line in enumerate((text or "").splitlines(), 1):
        if not line.strip():
            continue
        try:
            items.append(Recommendation.model_validate_json(line))
        except (ValidationError, ValueError) as e:
            problems.append(f"recommendations.jsonl, строка {n}: {str(e).splitlines()[0]}")
    single = (
        single if single is not None else load_package(d / "recommendation.json", Recommendation)
    )
    ids = {r.payload.recommendation_id for r in items}
    if single.ok and single.package.payload.recommendation_id not in ids:
        items.insert(0, single.package)
    return RecList(items, problems)


def explanation_for(run_dir: Path | str, rec: Recommendation) -> tuple[str, str]:
    """Person 4 text for this recommendation (`explanations/<id>.txt`, else `explanation.txt`),
    or the recommendation's own reason."""
    if rec.payload.source == "person4":
        own = _read_text(Path(run_dir) / "explanations" / f"{rec.payload.recommendation_id}.txt")
        if own:
            return own, "person4"
    return explanation_text(run_dir, rec)


def rec_evidence(rec: Recommendation, load_pkg: LoadPackage | None) -> dict[str, Any] | None:
    """What backs a segment recommendation in the load package: r per slot, the window, the peak."""
    if load_pkg is None:
        return None
    p = rec.payload
    rows = sorted(
        (r for r in load_pkg.payload if r.segment_id == p.target), key=lambda r: r.interval_start
    )
    if not rows:
        return None
    r_off, r_on = _thresholds()
    known = [r for r in rows if r.r is not None]
    peak = max(known, key=lambda r: r.r or 0.0) if known else None
    return {
        "times": [f"{to_msk(r.interval_start):%H:%M}" for r in rows],
        "r": [r.r for r in rows],
        "in_window": [p.start <= r.interval_start < p.end for r in rows],
        "r_on": r_on,
        "r_off": r_off,
        "peak": None
        if peak is None
        else {
            "time": f"{to_msk(peak.interval_start):%H:%M}",
            "r": peak.r,
            "demand": peak.demand,
            "departures": peak.departures,
            "capacity": peak.departures * peak.capacity_per_train,
        },
    }
