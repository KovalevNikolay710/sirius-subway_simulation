"""Scenarios (demo overlays on real days), run metrics and baseline-vs-policy comparison (pure)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from metro_control import executor, mock, policy
from metro_control.contracts import EffectComparison, Metrics, SimulationState
from metro_control.dayrun import DAY_END_MIN, build_day, minutes_to_utc
from metro_control.line import LineRef
from metro_control.od import OdParams
from metro_control.sim import SimParams, SimState, new_state, pending_wait_pax_min, run, waiting
from metro_control.timeline import Timeline, snapshot
from metro_control.timeline import dump as dump_timeline

SLOT_MIN = 15
HORIZON_MIN = 120.0


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _msk_clock_min(col: str = "interval_start") -> pl.Expr:
    """Minutes since 00:00 MSK of the calendar day for a UTC slot start."""
    t = pl.col(col).dt.convert_time_zone("Europe/Moscow")
    return t.dt.hour().cast(pl.Int64) * 60 + t.dt.minute().cast(pl.Int64)


def _window_mask(overlay: dict[str, Any]) -> pl.Expr:
    c = _msk_clock_min()
    return (c >= _hm(overlay["start_msk"])) & (c < _hm(overlay["end_msk"]))


def apply_scenario(entries_day: pl.DataFrame, spec: dict[str, Any]) -> pl.DataFrame:
    """Overlay on one day of entries: surge multiplies, shift moves slots later (total kept)."""
    ov = spec.get("overlay")
    if not ov:
        return entries_day
    if ov["type"] == "surge":
        hit = _window_mask(ov) & pl.col("station_id").is_in(ov["stations"])
        return entries_day.with_columns(
            pl.when(hit)
            .then(pl.col("entries") * float(ov["factor"]))
            .otherwise(pl.col("entries"))
            .alias("entries")
        )
    if ov["type"] == "shift":
        steps = round(float(ov["shift_min"]) / SLOT_MIN)
        win = _window_mask(ov)
        stay = entries_day.with_columns(
            pl.when(win).then(0.0).otherwise(pl.col("entries")).alias("entries")
        )
        moved = (
            entries_day.filter(win)
            .with_columns(pl.col("interval_start") + pl.duration(minutes=steps * SLOT_MIN))
            .select("station_id", "interval_start", pl.col("entries").alias("moved"))
        )
        out = (
            stay.join(moved, on=["station_id", "interval_start"], how="left")
            .with_columns((pl.col("entries") + pl.col("moved").fill_null(0.0)).alias("entries"))
            .drop("moved")
        )
        # moved slots past the last row of the day would drop mass: refuse instead
        lost = entries_day["entries"].sum() - out["entries"].sum()
        if abs(lost) > 1e-6:
            raise ValueError("shift pushes entries outside the service day")
        return out.select(entries_day.columns)
    raise ValueError(f"overlay.type: unknown {ov['type']}")


def metrics(state: SimState, params: SimParams, assumptions: dict[str, Any]) -> Metrics:
    fills = [x.load_after / params.capacity for x in state.log]
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    at: dict[str, str] = {}
    hops = 0
    for x in state.log:
        first.setdefault(x.train_id, x.t)
        last[x.train_id] = x.t
        if x.train_id in at and at[x.train_id] != x.station:
            hops += 1
        at[x.train_id] = x.station
    trip_km = float(assumptions["trip_km"]["value"])
    return Metrics(
        wait_pax_min=state.wait_pax_min + pending_wait_pax_min(state),
        queue_left=waiting(state),
        denied_boardings=state.denied,
        max_fill=max(fills, default=0.0),
        mean_fill=sum(fills) / len(fills) if fills else 0.0,
        train_hours=sum(last[k] - first[k] for k in first) / 60.0,
        train_km=hops * trip_km / (len(params.stations) - 1),
    )


@dataclass
class CompareResult:
    scenario: str
    date: date
    baseline: SimState
    policy: SimState
    actions: list[dict[str, Any]]
    effect: EffectComparison
    state_packages: dict[str, SimulationState]
    manifest: dict[str, Any]
    outcomes: dict[str, int] = field(default_factory=dict)
    timeline: Timeline | None = None


def _iso(dt: datetime) -> str:
    return mock._iso(dt)


def _window_utc(spec: dict[str, Any], d: date) -> tuple[datetime, datetime] | None:
    ov = spec.get("overlay")
    if not ov:
        return None
    base = datetime(d.year, d.month, d.day, tzinfo=UTC)  # 03:00 MSK == 00:00 UTC
    off = timedelta(hours=-3)
    day0 = base + off  # 00:00 MSK of the date
    return day0 + timedelta(minutes=_hm(ov["start_msk"])), day0 + timedelta(
        minutes=_hm(ov["end_msk"])
    )


def _surge_for(
    spec: dict[str, Any], d: date, as_of: datetime
) -> tuple[dict[str, float], tuple[datetime, datetime] | None]:
    """Announced events are a known external signal: the policy sees the multiplier only
    when the spec is announced and [as_of, as_of+2h) overlaps the scenario window."""
    ov = spec.get("overlay")
    if not (spec.get("announced") and ov and ov["type"] == "surge"):
        return {}, None
    w = _window_utc(spec, d)
    assert w is not None
    if as_of < w[1] and as_of + timedelta(minutes=HORIZON_MIN) > w[0]:
        return {s: float(ov["factor"]) for s in ov["stations"]}, w
    return {}, None


def _sim_state_pkg(
    state: SimState, params: SimParams, origin: datetime, reserve: int, run_id: str
) -> SimulationState:
    trains = []
    for tr in state.trains.values():
        if not tr.in_service:
            continue
        trains.append(
            {
                "train_id": tr.train_id,
                "segment_id": None,
                "station_id": params.stations[tr.station_idx],
                "load": min(sum(tr.onboard.values()), params.capacity),
            }
        )
    queues = []
    for (st, d), q in sorted(state.queues.items()):
        w = sum(sum(c.by_dest.values()) for c in q)
        if w > 0:
            queues.append({"station_id": st, "direction": d, "waiting": w})
    t = minutes_to_utc(state.t, origin)
    payload = {
        "t": _iso(t),
        "trains": trains,
        "queues": queues,
        "reserve_available": reserve,
        "capacity_per_train": params.capacity,
    }
    env = mock._envelope("simulation_state", run_id, t, f"compare: {run_id}")
    return SimulationState.model_validate({**env, "payload": payload})


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare(
    scenario: str,
    entries: pl.DataFrame,
    line: LineRef,
    od_params: OdParams,
    assumptions: dict[str, Any],
) -> CompareResult:
    """Run one service day twice on the same demand and initial state.

    Baseline = timetable only. Policy = mock policy + executor on every 15-min boundary.
    The policy reads history = all other days + the scenario-day truth, strictly before as_of.
    """
    spec = assumptions["scenarios"]["value"][scenario]
    d = date.fromisoformat(spec["date"])
    utc_day = pl.col("interval_start").dt.convert_time_zone("UTC").dt.date()
    day = entries.filter(utc_day == d)
    if day.height == 0:
        raise ValueError(f"no entries for {d}")
    truth = apply_scenario(day, spec)
    params, demand, trips, origin, dtype = build_day(truth, truth, line, od_params, assumptions)
    others = entries.filter(utc_day != d)
    history_all = pl.concat([others, truth.select(others.columns)]).sort("interval_start")
    init = new_state(params, [], 0.0, trips)
    n_slots = int(DAY_END_MIN) // SLOT_MIN
    frames: dict[str, list[dict[str, Any]]] = {"baseline": [], "policy": []}
    baseline = init
    b_mark = 0

    ctx = executor.ExecContext(origin, dtype, line, assumptions)
    ex = executor.new_exec_state(line)
    mem = policy.PolicyMemory()
    st = new_state(params, [], 0.0, trips)
    actions: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    p_mark = 0
    for k in range(n_slots + 1):
        t = k * float(SLOT_MIN)
        as_of = minutes_to_utc(t, origin)
        baseline = run(baseline, params, demand, t)
        st = run(st, params, demand, t)
        frames["baseline"].append(snapshot(baseline, params, b_mark, as_of))
        frames["policy"].append(snapshot(st, params, p_mark, as_of))
        b_mark, p_mark = len(baseline.log), len(st.log)
        if k == n_slots:
            break
        hist = history_all.filter(pl.col("interval_start") < as_of)
        sg, sw = _surge_for(spec, d, as_of)
        rec, mem = policy.recommend_at(
            hist, as_of, line, od_params, mem, dict(ex.reserves_left), sg, sw
        )
        st, ex, out = executor.execute(st, params, ex, rec, ctx)
        counts[out.status] = counts.get(out.status, 0) + 1
        p = rec.payload
        if p.action != "none":
            actions.append(
                {
                    "as_of": _iso(p.as_of),
                    "recommendation_id": p.recommendation_id,
                    "action": p.action,
                    "target": p.target,
                    "start": _iso(p.start),
                    "end": _iso(p.end),
                    "status": out.status,
                    "reason": p.reason,
                    "outcome": out.reason,
                    "train_ids": list(out.train_ids),
                }
            )

    if abs(baseline.entered - st.entered) > 1e-6 * max(1.0, baseline.entered):
        raise RuntimeError("baseline and policy runs saw different demand")
    for name, x in (("baseline", baseline), ("policy", st)):
        onb = sum(sum(t.onboard.values()) for t in x.trains.values())
        if abs(x.entered - (x.alighted + waiting(x) + onb)) > 1e-6 * max(1.0, x.entered):
            raise RuntimeError(f"{name}: people balance violated")

    run_id = f"compare-{scenario}-{d.isoformat()}"
    end = minutes_to_utc(DAY_END_MIN, origin)
    eff_payload = {
        "scenario": scenario,
        "baseline_run_id": f"{run_id}-baseline",
        "policy_run_id": f"{run_id}-policy",
        "baseline": metrics(baseline, params, assumptions).model_dump(),
        "policy": metrics(st, params, assumptions).model_dump(),
    }
    env = mock._envelope("effect_comparison", run_id, end, f"compare: {run_id}")
    effect = EffectComparison.model_validate({**env, "payload": eff_payload})
    total_reserve = sum(int(v) for v in line.params["reserve_depots"].value.values())
    pk = {
        "baseline": _sim_state_pkg(baseline, params, origin, total_reserve, f"{run_id}-baseline"),
        "policy": _sim_state_pkg(
            st, params, origin, sum(ex.reserves_left.values()), f"{run_id}-policy"
        ),
    }
    day_sorted = day.sort(["interval_start", "station_id"])
    manifest = {
        "run_id": run_id,
        "scenario": scenario,
        "date": d.isoformat(),
        "entries_sha256": _sha(day_sorted.write_csv().encode()),
        "all_entries_sha256": _sha(
            entries.sort(["interval_start", "station_id"]).write_csv().encode()
        ),
        "spec_sha256": _sha(json.dumps(spec, sort_keys=True).encode()),
        "assumptions_sha256": _sha(json.dumps(assumptions, sort_keys=True).encode()),
    }
    return CompareResult(
        scenario,
        d,
        baseline,
        st,
        actions,
        effect,
        pk,
        manifest,
        outcomes=counts,
        timeline=Timeline(
            run_id=run_id,
            scenario=scenario,
            date=d.isoformat(),
            capacity=params.capacity,
            stations=list(params.stations),
            frames=frames,
        ),
    )


def write_run(result: CompareResult, out_dir: Path) -> Path:
    """Write runs/<run_id>/ deterministically; returns the run directory."""
    run_dir = Path(out_dir) / result.manifest["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)

    def dump(name: str, text: str) -> None:
        (run_dir / name).write_text(text, encoding="utf-8")

    dump("metrics.json", result.effect.model_dump_json(indent=1) + "\n")
    dump(
        "actions.jsonl",
        "".join(json.dumps(a, ensure_ascii=False, sort_keys=True) + "\n" for a in result.actions),
    )
    for k, pkg in result.state_packages.items():
        dump(f"state_{k}.json", pkg.model_dump_json(indent=1) + "\n")
    if result.timeline is not None:
        dump("timeline.json", dump_timeline(result.timeline))
    dump("manifest.json", json.dumps(result.manifest, indent=1, sort_keys=True) + "\n")
    return run_dir
