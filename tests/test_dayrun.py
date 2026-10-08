import time
from datetime import UTC, date, datetime, timedelta, timezone

import numpy as np
import polars as pl
import pytest

from metro_control.dayrun import (
    DAY_END_MIN,
    arrivals_from_od,
    baseline_trips,
    day_sim_params,
    load_assumption_items,
    minutes_to_utc,
    pairs_for,
    service_origin,
    simulate_day,
    to_minutes,
)
from metro_control.entries import SCHEMA
from metro_control.line import load_line
from metro_control.od import OdParams, load_od_params, slot_od
from metro_control.sim import SimParams, new_state, onboard, run, waiting

MSK3 = timezone(timedelta(hours=3))


def test_baseline_trips():
    tr = baseline_trips([7, 20])
    n = [t for t in tr if t[1] == "north"]
    s = [t for t in tr if t[1] == "south"]
    assert len(n) == len(s) == 27
    assert n[0] == ("N0001", "north", 120.0)
    assert s[0][0] == "S0001"
    hour2 = [t[2] for t in n[7:10]]
    assert hour2[0] == 180.0 and hour2[1] - hour2[0] == pytest.approx(3.0)
    assert len({t[0] for t in tr}) == 54
    assert baseline_trips([0, 2])[0][2] == 180.0


def test_time_conversion():
    o = service_origin(date(2026, 9, 30))
    assert o == datetime(2026, 9, 30, 3, 0, tzinfo=MSK3)
    assert to_minutes(datetime(2026, 9, 30, 5, 0, tzinfo=MSK3), o) == 120.0
    assert minutes_to_utc(120.0, o) == datetime(2026, 9, 30, 2, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        to_minutes(datetime(2026, 9, 30, 5, 0), o)
    with pytest.raises(ValueError):
        minutes_to_utc(1.0, datetime(2026, 9, 30))


def test_day_params():
    line = load_line()
    p = day_sim_params(line, load_assumption_items())
    assert len(p.stations) == 19
    assert p.run_min[0] == pytest.approx(93 / 36 - 0.5)
    assert 2 * 18 * (p.run_min[0] + p.dwell_min) + 2 * p.turnback_min == pytest.approx(99)
    assert pairs_for("weekday", line)[1] == 20
    assert pairs_for("holiday", line) == pairs_for("sunday", line)
    assert pairs_for("saturday", line)[1] == 16


def test_arrivals_from_od():
    od = np.zeros((1, 3, 3))
    od[0, 0, 2] = 150
    o = service_origin(date(2026, 3, 2))
    arr = arrivals_from_od([o], od, o, ("A", "B", "C"), 5.0)
    assert [(a.t, a.origin, a.dest, a.amount) for a in arr] == [
        (0.0, "A", "C", 50.0),
        (5.0, "A", "C", 50.0),
        (10.0, "A", "C", 50.0),
    ]


def test_trip_runs_to_terminal():
    p = SimParams(("A", "B", "C"), (2.0, 2.0), 0.5, 3.0, 100.0)
    from metro_control.sim import Arrival

    st = new_state(p, [], 0.0, [("X", "north", 0.0)])
    d = [Arrival(0, "A", "C", 30), Arrival(0, "B", "C", 20)]
    st = run(st, p, d, 100.0)
    assert not st.events and not st.trains["X"].in_service
    assert st.alighted == pytest.approx(50)
    assert onboard(st) == 0 and waiting(st) == 0
    s = new_state(p, [], 0.0, [("Y", "south", 1.0)])
    assert s.trains["Y"].station_idx == 2
    with pytest.raises(ValueError):
        new_state(p, [("Y", "A", "north", 0.0)], 0.0, [("Y", "south", 1.0)])
    with pytest.raises(ValueError):
        new_state(p, [], 5.0, [("Y", "south", 1.0)])


def _day(line, per_slot, day_type="weekday", surge=1.0):
    rows = []
    origin = service_origin(date(2026, 3, 2))
    for k in range(96):
        t = origin + timedelta(minutes=15 * k)
        msk_min = (k * 15 + 180) % 1440
        on = msk_min >= 300 or msk_min < 60
        for s in line.stations:
            rows.append((s.id, t, day_type, per_slot * surge if on else 0.0))
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def test_slot_od_rowsums_and_segment_demand():
    from metro_control.od import assign, segment_demand

    line = load_line()
    par = load_od_params()
    rng = np.random.default_rng(3)
    stations = [s.id for s in sorted(line.stations, key=lambda s: s.order)]
    rows = []
    for h in (4, 8, 13, 18):
        t = datetime(2026, 3, 2, h, 0, tzinfo=UTC) - timedelta(hours=3)
        rows += [(sid, t, "weekday", float(rng.uniform(1, 500))) for sid in stations]
    e = pl.DataFrame(rows, schema=SCHEMA, orient="row")
    slots, od = slot_od(e, e, line, par)
    assert od.shape == (4, 19, 19) and len(slots) == 4
    factor = np.array([par.transfer_factor.get(x, 1.0) for x in stations])
    raw = np.zeros((4, 19))
    pos = {t: i for i, t in enumerate(slots)}
    for sid, t, _, v in rows:
        raw[pos[t], stations.index(sid)] = v
    assert np.allclose(od.sum(axis=2), raw * factor)
    dem = segment_demand(e, e, line, par)
    north = np.array([od[0, : k + 1, k + 1 :].sum() for k in range(18)])
    got = dem.filter(pl.col("ts") == slots[0])["demand"].to_numpy()
    assert np.allclose(got[0::2], north)
    assert assign is not None


def test_simulate_day_synthetic():
    line = load_line()
    par = OdParams(**{**load_od_params().__dict__})
    ass = load_assumption_items()
    e = _day(line, 300.0)
    t0 = time.perf_counter()
    st, p = simulate_day(e, e, line, par, ass)
    assert time.perf_counter() - t0 < 30
    st2, _ = simulate_day(e, e, line, par, ass)
    assert st.log == st2.log
    assert (st.entered, st.alighted, st.denied) == (st2.entered, st2.alighted, st2.denied)
    assert st.t == DAY_END_MIN
    assert all(x.load_after <= p.capacity + 1e-6 for x in st.log)
    assert st.entered == pytest.approx(st.alighted + onboard(st) + waiting(st), rel=1e-9)
    assert st.entered > 0 and st.alighted > 0


def test_simulate_day_surge_and_errors():
    line = load_line()
    par = load_od_params()
    ass = load_assumption_items()
    e = _day(line, 300.0, surge=20.0)
    st, p = simulate_day(e, e, line, par, ass)
    assert st.denied > 0
    assert all(x.load_after <= p.capacity + 1e-6 for x in st.log)
    mixed = e.with_columns(
        pl.when(pl.col("station_id") == "prospekt_veteranov")
        .then(pl.lit("sunday"))
        .otherwise(pl.col("day_type"))
        .alias("day_type")
    )
    with pytest.raises(ValueError):
        simulate_day(mixed, mixed, line, par, ass)


def test_step_and_run_min_errors():
    o = service_origin(date(2026, 3, 2))
    od = np.zeros((1, 3, 3))
    od[0, 0, 2] = 1
    with pytest.raises(ValueError, match="step_min"):
        arrivals_from_od([o], od, o, ("A", "B", "C"), 4.0)
    with pytest.raises(ValueError, match="step_min"):
        arrivals_from_od([o], od, o, ("A", "B", "C"), 0.0)
    ass = load_assumption_items()
    bad = {**ass, "sim_dwell_min": {**ass["sim_dwell_min"], "value": 10.0}}
    with pytest.raises(ValueError, match="run_min"):
        day_sim_params(load_line(), bad)
