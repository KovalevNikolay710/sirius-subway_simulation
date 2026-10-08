"""Action executor: applies a Recommendation to a SimState (pure, inputs never mutated)."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from metro_control.contracts import Recommendation
from metro_control.dayrun import minutes_to_utc, to_minutes
from metro_control.line import LineRef
from metro_control.policy import DEPOT_NORTH, DEPOT_SOUTH, segment_direction
from metro_control.sim import (
    NORTH,
    SimParams,
    SimState,
    add_trip,
    cancel_trip,
    departure_list,
)
from metro_control.timeutil import MSK

TERMINAL = {NORTH: "veteranov", "south": "devyatkino"}
Status = Literal["applied", "rejected", "duplicate", "noop"]


@dataclass(frozen=True)
class ExecContext:
    origin: datetime
    day_type: str
    line: LineRef
    assumptions: dict[str, Any]


@dataclass(frozen=True)
class Outcome:
    recommendation_id: str
    status: Status
    reason: str
    train_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecState:
    reserves_left: dict[str, int]
    seen: dict[str, Outcome]


def new_exec_state(line: LineRef) -> ExecState:
    return ExecState(dict(line.params["reserve_depots"].value), {})


def _a(ctx: ExecContext, key: str) -> float:
    return float(ctx.assumptions[key]["value"])


def _clock_msk(t: float, ctx: ExecContext) -> float:
    m = minutes_to_utc(t, ctx.origin).astimezone(MSK)
    return m.hour * 60.0 + m.minute + m.second / 60.0


def _hm(s: str) -> float:
    h, m = s.split(":")
    return int(h) * 60.0 + int(m)


def headway_limits(t: float, ctx: ExecContext) -> tuple[float, float]:
    """(min, max) headway in seconds for the period containing sim time t."""
    key = "sunday" if ctx.day_type == "holiday" else ctx.day_type
    periods = ctx.line.params["headway_min_max"].value
    clock = _clock_msk(t, ctx)
    if clock < 180:  # after midnight, still the previous service day
        clock += 1440
    chosen = periods[0]
    for p in periods:
        a, _ = p["period"].split("-")
        if clock >= _hm(a):
            chosen = p
    lo, hi = chosen[key]
    return float(lo), float(hi)


def _reject(ex: ExecState, sim: SimState, rec: Recommendation, why: str):
    out = Outcome(rec.payload.recommendation_id, "rejected", why)
    return sim, replace(ex, seen={**ex.seen, out.recommendation_id: out}), out


def _largest_gap_mid(points: list[float], lo: float, hi: float) -> float:
    pts = [lo] + sorted(p for p in points if lo < p < hi) + [hi]
    a, b = max(zip(pts, pts[1:], strict=False), key=lambda g: g[1] - g[0])
    return (a + b) / 2


def _rate_ok(deps: list[float], x: float, ctx: ExecContext) -> bool:
    half = _a(ctx, "policy_headway_window_min")
    cap = math.floor(3600 / headway_limits(x, ctx)[0])
    return sum(1 for d in deps if x - half <= d < x + half) + 1 <= cap


def _gap_ok(deps: list[float], t: float, ctx: ExecContext) -> bool:
    """Removing the departure at t keeps the gap between its neighbours within max headway."""
    prev = [d for d in deps if d < t]
    nxt = [d for d in deps if d > t]
    if not prev or not nxt:
        return False
    return (nxt[0] - prev[-1]) * 60 <= headway_limits(t, ctx)[1]


def _execute(
    sim: SimState,
    params: SimParams,
    ex: ExecState,
    rec: Recommendation,
    ctx: ExecContext,
) -> tuple[SimState, ExecState, Outcome]:
    p = rec.payload
    rid = p.recommendation_id
    if rid in ex.seen:
        prev = ex.seen[rid]
        return (
            sim,
            ex,
            Outcome(rid, "duplicate", f"already handled ({prev.status})", prev.train_ids),
        )
    now = sim.t
    as_of = to_minutes(p.as_of, ctx.origin)
    start, end = to_minutes(p.start, ctx.origin), to_minutes(p.end, ctx.origin)
    if as_of > now:
        out = Outcome(rid, "rejected", "as_of is later than sim time (future knowledge)")
        return sim, ex, out
    if p.action == "none":
        out = Outcome(rid, "noop", "no action")
        return sim, replace(ex, seen={**ex.seen, rid: out}), out
    if end <= now:
        return _reject(ex, sim, rec, "recommendation expired")
    if p.action == "limit_entry":
        return _reject(ex, sim, rec, "limit_entry is not modelled in the simulator")
    if not any(s.id == p.target for s in ctx.line.segments):
        return _reject(ex, sim, rec, f"{p.action} needs a segment target, got {p.target}")
    direction = segment_direction(p.target)
    deps = departure_list(sim, params, direction)
    times = [t for t, _ in deps]
    pending_ids = {e[2] for e in sim.events}
    pending = [(t, i) for t, i in deps if i in pending_ids and t >= now]
    lo = max(now, start)

    if p.action == "add_reserve":
        depot = DEPOT_NORTH if direction == NORTH else DEPOT_SOUTH
        if ex.reserves_left.get(depot, 0) <= 0:
            return _reject(ex, sim, rec, f"no reserve left at depot {depot}")
        t0 = max(now + _a(ctx, "policy_reserve_delay_min"), start)
        if t0 >= end:
            return _reject(ex, sim, rec, "no feasible time: entry delay runs past the window")
        x = _largest_gap_mid(times, t0, end)
        if not _rate_ok(times, x, ctx):
            return _reject(ex, sim, rec, "min headway: too many departures around the slot")
        total = int(ctx.line.params["reserve_depots"].value[depot])
        tid = f"R-{depot}-{total - ex.reserves_left[depot] + 1}"
        new = add_trip(sim, params, tid, direction, x)
        left = {**ex.reserves_left, depot: ex.reserves_left[depot] - 1}
        reason = f"added {tid} at t={x:.1f} min from {TERMINAL[direction]}"
    elif p.action == "remove_train":
        cand = [(t, i) for t, i in pending if lo <= t < end]
        pick = next(((t, i) for t, i in cand if _gap_ok(times, t, ctx)), None)
        if pick is None:
            return _reject(ex, sim, rec, "max headway would be exceeded, or no pending trip")
        tid = pick[1]
        new, left = cancel_trip(sim, tid), ex.reserves_left
        reason = f"cancelled {tid} at t={pick[0]:.1f} min"
    else:  # shift_peak
        ahead = _a(ctx, "policy_shift_lookahead_min")
        cand = [(t, i) for t, i in pending if end <= t < end + ahead]
        if not cand:
            return _reject(ex, sim, rec, "no pending trip after the window to shift")
        t_old, tid = cand[0]
        if lo >= end:
            return _reject(ex, sim, rec, "no feasible time: empty window")
        if not _gap_ok(times, t_old, ctx):
            return _reject(ex, sim, rec, "max headway would be exceeded at the old spot")
        rest = [t for t in times if t != t_old]
        x = _largest_gap_mid(rest, lo, end)
        if not _rate_ok(rest, x, ctx):
            return _reject(ex, sim, rec, "min headway: too many departures around the slot")
        new = cancel_trip(sim, tid)
        new.trains.pop(tid)
        new = add_trip(new, params, tid, direction, x)
        left = ex.reserves_left
        reason = f"moved {tid} from t={t_old:.1f} to t={x:.1f} min"
    out = Outcome(rid, "applied", reason, (tid,))
    return new, ExecState(left, {**ex.seen, rid: out}), out


def execute(
    sim: SimState,
    params: SimParams,
    ex: ExecState,
    rec: Recommendation,
    ctx: ExecContext,
) -> tuple[SimState, ExecState, Outcome]:
    try:
        return _execute(sim, params, ex, rec, ctx)
    except ValueError as e:  # sim helper refused: report, never raise
        return _reject(ex, sim, rec, str(e))
