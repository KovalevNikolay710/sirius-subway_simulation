"""Full service-day run on the baseline timetable (pure). Service day starts 03:00 MSK."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from metro_control.entries import ASSUMPTIONS_PATH
from metro_control.line import LineRef
from metro_control.od import OdParams, slot_od
from metro_control.sim import NORTH, SOUTH, Arrival, SimParams, SimState, new_state, run

DAY_END_MIN = 1440.0


def load_assumption_items(path: Path | str | None = None) -> dict[str, Any]:
    return json.loads(Path(path or ASSUMPTIONS_PATH).read_text(encoding="utf-8"))["items"]


def service_origin(d: date) -> datetime:
    """03:00 MSK of the service date = 00:00 UTC."""
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _check_aware(ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime not allowed; attach a timezone")


def to_minutes(ts: datetime, origin: datetime) -> float:
    _check_aware(ts)
    _check_aware(origin)
    return (ts - origin).total_seconds() / 60.0


def minutes_to_utc(t: float, origin: datetime) -> datetime:
    _check_aware(origin)
    return (origin + timedelta(minutes=t)).astimezone(UTC)


def baseline_trips(pairs: list[int], first_hour_msk: int = 5) -> list[tuple[str, str, float]]:
    """pairs[i] = departures/hour from EACH terminal in MSK hour first_hour_msk+i."""
    out: list[tuple[str, str, float]] = []
    for prefix, direction in (("N", NORTH), ("S", SOUTH)):
        k = 0
        for i, p in enumerate(pairs):
            start = (first_hour_msk + i - 3) * 60.0
            for j in range(int(p)):
                k += 1
                out.append((f"{prefix}{k:04d}", direction, start + j * 60.0 / p))
    return out


def day_sim_params(line: LineRef, assumptions: dict[str, Any]) -> SimParams:
    stations = tuple(s.id for s in sorted(line.stations, key=lambda s: s.order))
    n = len(stations)
    dwell = float(assumptions["sim_dwell_min"]["value"])
    turnback = float(assumptions["sim_turnback_min"]["value"])
    cycle = float(line.params["cycle_min"].value)
    run_min = (cycle - 2 * turnback) / (2 * (n - 1)) - dwell
    if run_min <= 0:
        raise ValueError(f"run_min: derived value <= 0 ({run_min:.4f}); check dwell/turnback/cycle")
    return SimParams(
        stations=stations,
        run_min=(run_min,) * (n - 1),
        dwell_min=dwell,
        turnback_min=turnback,
        capacity=float(line.params["train_capacity"].value),
    )


def arrivals_from_od(
    slots: list[datetime],
    od: np.ndarray,
    origin: datetime,
    stations: tuple[str, ...],
    step_min: float,
    slot_min: float = 15.0,
) -> list[Arrival]:
    if step_min <= 0:
        raise ValueError("step_min: must be > 0")
    ratio = slot_min / step_min
    if abs(ratio - round(ratio)) > 1e-9:
        raise ValueError("step_min: must divide slot_min")
    k = max(1, round(ratio))
    out: list[Arrival] = []
    for s, ts in enumerate(slots):
        t0 = to_minutes(ts, origin)
        for i, j in zip(*np.nonzero(od[s] > 1e-9), strict=True):
            if i == j:
                continue
            amt = float(od[s, i, j]) / k
            out.extend(Arrival(t0 + m * step_min, stations[i], stations[j], amt) for m in range(k))
    return out


def pairs_for(day_type: str, line: LineRef) -> list[int]:
    key = "planned_pairs_weekday" if day_type == "weekday" else "planned_pairs_weekend"
    return [int(x) for x in line.params[key].value]


def build_day(
    entries_day: pl.DataFrame,
    attraction: pl.DataFrame,
    line: LineRef,
    od_params: OdParams,
    assumptions: dict[str, Any],
) -> tuple[SimParams, list[Arrival], list[tuple[str, str, float]], datetime, str]:
    """Demand, baseline trips, service origin and day type of one service day."""
    types = entries_day["day_type"].unique().to_list()
    if len(types) != 1:
        raise ValueError(f"expected one day_type, got {sorted(types)}")
    first = entries_day["ts"].min()
    d = first.astimezone(UTC).date()  # service day = one UTC calendar day (03:00 MSK start)
    if entries_day["ts"].max().astimezone(UTC).date() != d:
        raise ValueError("entries_day: rows span more than one service day")
    origin = service_origin(d)
    params = day_sim_params(line, assumptions)
    slots, od = slot_od(entries_day, attraction, line, od_params)
    step = float(assumptions["sim_arrival_step_min"]["value"])
    demand = arrivals_from_od(slots, od, origin, params.stations, step)
    trips = baseline_trips(pairs_for(types[0], line))
    return params, demand, trips, origin, types[0]


def simulate_day(
    entries_day: pl.DataFrame,
    attraction: pl.DataFrame,
    line: LineRef,
    od_params: OdParams,
    assumptions: dict[str, Any],
) -> tuple[SimState, SimParams]:
    params, demand, trips, _, _ = build_day(entries_day, attraction, line, od_params, assumptions)
    state = new_state(params, [], 0.0, trips)
    return run(state, params, demand, DAY_END_MIN), params
