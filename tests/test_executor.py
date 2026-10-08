import copy
from datetime import UTC, datetime, timedelta

import pytest

from metro_control import mock
from metro_control.contracts import Recommendation
from metro_control.dayrun import (
    baseline_trips,
    day_sim_params,
    load_assumption_items,
    pairs_for,
    service_origin,
)
from metro_control.executor import ExecContext, execute, new_exec_state
from metro_control.line import load_line
from metro_control.sim import (
    SimState,
    add_trip,
    cancel_trip,
    departure_list,
    new_state,
    run,
    terminal_departures,
)

LINE = load_line()
ITEMS = load_assumption_items()
PARAMS = day_sim_params(LINE, ITEMS)
ORIGIN = service_origin(datetime(2026, 9, 3, tzinfo=UTC).date())
CTX = ExecContext(ORIGIN, "weekday", LINE, ITEMS)
NORTH_SEG = "veteranov__leninsky_prospekt"
SOUTH_SEG = "leninsky_prospekt__veteranov"


def at(t_min: float) -> datetime:
    return ORIGIN + timedelta(minutes=t_min)


def rec(rid, action, target, as_of_t, start_t=None, end_t=None) -> Recommendation:
    start_t = as_of_t if start_t is None else start_t
    end_t = start_t + 60 if end_t is None else end_t
    asof = at(as_of_t)
    env = mock._envelope("recommendation", rid, asof, "test")
    payload = {
        "recommendation_id": rid,
        "as_of": mock._iso(asof),
        "action": action,
        "target": target,
        "start": mock._iso(at(start_t)),
        "end": mock._iso(at(end_t)),
        "reason": "test",
        "source": "mock",
    }
    return Recommendation.model_validate({**env, "payload": payload})


def baseline_sim(t: float) -> SimState:
    st = new_state(PARAMS, [], 0.0, baseline_trips(pairs_for("weekday", LINE)))
    return run(st, PARAMS, [], t)


def snap(sim: SimState):
    return (
        sim.t,
        sorted(sim.events),
        sorted((k, v.in_service) for k, v in sim.trains.items()),
        len(sim.log),
    )


def test_sim_helpers_pure():
    st = new_state(PARAMS, [], 0.0, [("A", "north", 10.0), ("B", "north", 20.0)])
    before = copy.deepcopy(st)
    st2 = add_trip(st, PARAMS, "C", "north", 15.0)
    assert snap(st) == snap(before) and "C" not in st.trains
    assert terminal_departures(st2, PARAMS, "north") == [10.0, 15.0, 20.0]
    assert terminal_departures(st2, PARAMS, "south") == []
    st3 = cancel_trip(st2, "A")
    assert terminal_departures(st3, PARAMS, "north") == [15.0, 20.0]
    assert not st3.trains["A"].in_service and "A" in st2.trains and st2.trains["A"].in_service
    with pytest.raises(ValueError):
        add_trip(st2, PARAMS, "C", "north", 30.0)
    with pytest.raises(ValueError):
        add_trip(st, PARAMS, "D", "north", -1.0)
    adv = run(st2, PARAMS, [], 12.0)
    assert terminal_departures(adv, PARAMS, "north") == [10.0, 15.0, 20.0]
    with pytest.raises(ValueError):
        cancel_trip(adv, "A")  # already departed
    with pytest.raises(ValueError):
        cancel_trip(adv, "nope")
    with pytest.raises(ValueError):
        add_trip(adv, PARAMS, "E", "north", 5.0)


def test_add_reserve_and_duplicate_and_exhaustion():
    sim = baseline_sim(540)  # 12:00 MSK
    ex = new_exec_state(LINE)
    r1 = rec("r1", "add_reserve", NORTH_SEG, 540)
    s1, ex1, o1 = execute(sim, PARAMS, ex, r1, CTX)
    assert o1.status == "applied" and o1.train_ids == ("R-avtovo-1",)
    assert ex1.reserves_left == {"avtovo": 1, "severnoye": 2} and ex.reserves_left["avtovo"] == 2
    s1d, ex1d, o1d = execute(s1, PARAMS, ex1, r1, CTX)
    assert o1d.status == "duplicate" and s1d is s1 and ex1d is ex1
    s2, ex2, o2 = execute(s1, PARAMS, ex1, rec("r2", "add_reserve", NORTH_SEG, 540), CTX)
    assert o2.status == "applied", o2.reason
    assert ex2.reserves_left["avtovo"] == 0
    s3, ex3, o3 = execute(s2, PARAMS, ex2, rec("r3", "add_reserve", NORTH_SEG, 540), CTX)
    assert o3.status == "rejected" and "no reserve" in o3.reason and s3 is s2
    end = run(s1, PARAMS, [], 1440)
    logs = [x for x in end.log if x.train_id == "R-avtovo-1"]
    assert min(x.t for x in logs) >= 560
    assert len([x for x in logs if x.station == "veteranov"]) == 1
    assert logs[-1].station == "devyatkino" and not end.trains["R-avtovo-1"].in_service


def test_add_reserve_rejected_when_baseline_at_min_headway_floor():
    sim = baseline_sim(870)  # 17:30 MSK, 30/h vs 120 s floor
    s, _, o = execute(
        sim, PARAMS, new_exec_state(LINE), rec("f", "add_reserve", NORTH_SEG, 870), CTX
    )
    assert o.status == "rejected" and "min headway" in o.reason and s is sim


def test_future_as_of_not_stored_and_applies_later():
    sim = baseline_sim(540)
    ex = new_exec_state(LINE)
    r = rec("fut", "add_reserve", NORTH_SEG, 550, 550, 610)
    s, e, o = execute(sim, PARAMS, ex, r, CTX)
    assert o.status == "rejected" and s is sim and "fut" not in e.seen
    later = run(sim, PARAMS, [], 550)
    _, _, o2 = execute(later, PARAMS, e, r, CTX)
    assert o2.status == "applied", o2.reason


def test_remove_ignores_departed_trip_at_now_and_never_raises():
    sim = baseline_sim(540)
    deps = departure_list(sim, PARAMS, "north")
    t_last = max(t for t, _ in deps if t <= 540)
    sim = run(
        new_state(PARAMS, [], 0.0, baseline_trips(pairs_for("weekday", LINE))), PARAMS, [], t_last
    )
    ex = new_exec_state(LINE)
    _, _, o = execute(
        sim, PARAMS, ex, rec("z", "remove_train", NORTH_SEG, t_last, t_last, t_last + 30), CTX
    )
    assert o.status in ("applied", "rejected")
    if o.status == "applied":
        assert (
            dict((i, t) for t, i in departure_list(sim, PARAMS, "north"))[o.train_ids[0]] > t_last
        )


def test_remove_train_evening_and_early_morning():
    sim = baseline_sim(1020)  # 20:00 MSK
    ex = new_exec_state(LINE)
    s1, _, o = execute(sim, PARAMS, ex, rec("x", "remove_train", SOUTH_SEG, 1020), CTX)
    assert o.status == "applied", o.reason
    tid = o.train_ids[0]
    assert tid.startswith("S") and not s1.trains[tid].in_service
    end = run(s1, PARAMS, [], 1440)
    assert not any(x.train_id == tid for x in end.log)
    early = baseline_sim(130)  # 05:10 MSK, 7 pairs/h
    s2, _, o2 = execute(early, PARAMS, ex, rec("y", "remove_train", NORTH_SEG, 130, 130, 170), CTX)
    assert o2.status == "rejected" and "max headway" in o2.reason and s2 is early


def test_shift_peak_moves_trip_into_window():
    sim = baseline_sim(1020)
    ex = new_exec_state(LINE)
    s1, _, o = execute(sim, PARAMS, ex, rec("s", "shift_peak", NORTH_SEG, 1020, 1020, 1050), CTX)
    assert o.status == "applied", o.reason
    tid = o.train_ids[0]
    before = dict(
        (i, t)
        for t, i in __import__("metro_control.sim", fromlist=["x"]).departure_list(
            sim, PARAMS, "north"
        )
    )
    after = dict(
        (i, t)
        for t, i in __import__("metro_control.sim", fromlist=["x"]).departure_list(
            s1, PARAMS, "north"
        )
    )
    assert before[tid] >= 1050 and 1020 <= after[tid] < 1050
    assert len(before) == len(after)


def test_rejections_leave_sim_unchanged():
    sim = baseline_sim(870)
    ex = new_exec_state(LINE)
    saved = (copy.deepcopy(sim), copy.deepcopy(ex))
    cases = [
        rec("a", "limit_entry", "veteranov", 870),
        rec("b", "add_reserve", "veteranov", 870),
        rec("c", "add_reserve", NORTH_SEG, 880),
        rec("d", "add_reserve", NORTH_SEG, 800, 800, 860),
    ]
    for r in cases:
        s, e, o = execute(sim, PARAMS, ex, r, CTX)
        assert o.status == "rejected" and s is sim and o.reason
        s2, _, o2 = execute(s, PARAMS, e, r, CTX)
        assert o2.status == ("rejected" if r.payload.recommendation_id == "c" else "duplicate")
    assert snap(sim) == snap(saved[0]) and ex.reserves_left == saved[1].reserves_left
    assert ex.seen == {}
    _, _, o = execute(sim, PARAMS, ex, rec("n", "none", NORTH_SEG, 870), CTX)
    assert o.status == "noop"


def test_min_headway_rule_rejects_dense_timetable():
    # 105 s apart, 08:00-10:00 MSK weekday (min headway 105 s -> 34/h cap)
    trips = [(f"N{k:04d}", "north", 300.0 + k * 105 / 60) for k in range(100)]
    sim = run(new_state(PARAMS, [], 0.0, trips), PARAMS, [], 360.0)
    ex = new_exec_state(LINE)
    s, _, o = execute(sim, PARAMS, ex, rec("m", "add_reserve", NORTH_SEG, 360), CTX)
    assert o.status == "rejected" and "min headway" in o.reason and s is sim
