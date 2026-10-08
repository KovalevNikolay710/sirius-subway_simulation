import copy

import pytest

from metro_control.sim import (
    Arrival,
    Cohort,
    SimParams,
    new_state,
    onboard,
    pending_wait_pax_min,
    run,
    waiting,
)

P = SimParams(("A", "B", "C"), (2.0, 2.0), 0.5, 3.0, 100.0)


def s0():
    return new_state(P, [("T1", "A", "north", 0.0)])


def test_scenario_1():
    d = [Arrival(0, "A", "B", 100), Arrival(0, "B", "C", 120)]
    st = run(s0(), P, d, 20.0)
    key = [(round(x.t, 6), x.station, x.direction) for x in st.log]
    assert key == [
        (0.0, "A", "north"),
        (2.5, "B", "north"),
        (5.0, "C", "north"),
        (8.0, "C", "south"),
        (10.5, "B", "south"),
        (13.0, "A", "south"),
        (16.0, "A", "north"),
        (18.5, "B", "north"),
    ]
    b = st.log[1]
    assert (b.alighted, b.boarded, b.left_behind) == (100, 100, 20)
    assert st.log[4].boarded == 0
    assert st.log[-1].boarded == 20
    assert st.wait_pax_min == pytest.approx(620)
    assert st.denied == pytest.approx(20)
    assert waiting(st) == pytest.approx(0)


DEMAND = [
    Arrival(0.0, "A", "B", 70.5),
    Arrival(0.3, "A", "C", 80.25),
    Arrival(0.5, "B", "C", 120.7),
    Arrival(1.1, "B", "A", 33.3),
    Arrival(2.2, "C", "B", 150.1),
    Arrival(3.0, "C", "A", 41.9),
    Arrival(6.0, "A", "C", 99.9),
    Arrival(9.5, "B", "C", 60.6),
    Arrival(12.0, "C", "A", 130.0),
]


@pytest.mark.parametrize("until", [1, 4.9, 7.3, 15, 40])
def test_balance(until):
    st = run(s0(), P, DEMAND, until)
    assert st.entered == pytest.approx(st.alighted + onboard(st) + waiting(st), abs=1e-9)
    assert all(x.load_after <= P.capacity + 1e-9 for x in st.log)


@pytest.mark.parametrize("mid", [7.3, 2.5])
def test_continuity(mid):
    a = run(run(s0(), P, DEMAND, mid), P, DEMAND, 20.0)
    assert a == run(s0(), P, DEMAND, 20.0)


def test_no_mutation():
    s, d = s0(), list(DEMAND)
    s_c, d_c = copy.deepcopy(s), copy.deepcopy(d)
    run(s, P, d, 20.0)
    assert s == s_c and d == d_c and s.t == 0.0


def test_fifo_and_split():
    s = new_state(P, [("T1", "B", "north", 0.0)])
    s.queues[("B", "north")] = [Cohort(0.0, {"C": 60}), Cohort(1.0, {"C": 60})]
    r = run(s, P, [], 0.1)
    assert r.log[0].boarded == pytest.approx(100)
    rest = r.queues[("B", "north")]
    assert len(rest) == 1 and rest[0].t_arrive == 1.0
    assert sum(rest[0].by_dest.values()) == pytest.approx(20)

    s = new_state(P, [("T1", "A", "north", 0.0)])
    s.queues[("A", "north")] = [Cohort(0.0, {"B": 60, "C": 60})]
    r = run(s, P, [], 0.1)
    assert r.trains["T1"].onboard == {"B": pytest.approx(50), "C": pytest.approx(50)}


def test_half_open_and_validation():
    d = [Arrival(5.0, "A", "B", 10)]
    r = run(s0(), P, d, 5.0)
    assert r.entered == 0
    r2 = run(r, P, d, 6.0)
    assert r2.entered == 10
    assert pending_wait_pax_min(r2) >= 0
    with pytest.raises(ValueError, match="capacity"):
        SimParams(("A", "B"), (1.0,), 0, 0, 0)
    with pytest.raises(ValueError, match="run_min"):
        SimParams(("A", "B"), (0.0,), 0, 0, 1)
    with pytest.raises(ValueError, match="stations"):
        SimParams(("A", "A"), (1.0,), 0, 0, 1)
    with pytest.raises(ValueError):
        Arrival(0, "A", "A", 1)
    with pytest.raises(ValueError):
        Arrival(0, "A", "B", 0)
    with pytest.raises(ValueError):
        run(s0(), P, [Arrival(0, "A", "Z", 1)], 1.0)


def test_until_before_t_and_new_state_validation():
    with pytest.raises(ValueError, match="until"):
        run(run(s0(), P, [], 5.0), P, [], 4.0)
    with pytest.raises(ValueError, match="t_first"):
        new_state(P, [("T1", "A", "north", -1.0)], t0=0.0)
    with pytest.raises(ValueError, match="train_id"):
        new_state(P, [("T1", "A", "north", 0.0), ("T1", "B", "north", 1.0)])


def test_denied_counts_per_train():
    p = SimParams(("A", "B", "C"), (2.0, 2.0), 0.5, 3.0, 100.0)
    # B north: 100 board at 2.5 (T1 full from A), 20 left; T2 also full -> 20 left again
    d = [Arrival(0, "A", "B", 1), Arrival(0, "B", "C", 120)]
    s = new_state(p, [("T1", "B", "north", 1.0), ("T2", "B", "north", 2.0)])
    s.trains["T1"].onboard["C"] = 100.0
    s.trains["T2"].onboard["C"] = 100.0
    r = run(s, p, d[1:], 3.0)
    assert r.denied == pytest.approx(240)  # 120 left by T1, 120 by T2


def test_old_arrivals_ignored_by_later_run():
    r5 = run(s0(), P, DEMAND, 5.0)
    r10 = run(r5, P, DEMAND, 10.0)
    applied = sum(a.amount for a in DEMAND if 5.0 <= a.t < 10.0)
    assert r10.entered == pytest.approx(r5.entered + applied)


def test_arrival_at_stop_time_boards_that_train():
    d = [Arrival(0.0, "A", "B", 10)]
    r = run(s0(), P, d, 0.1)
    assert r.log[0].boarded == 10
