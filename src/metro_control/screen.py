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
