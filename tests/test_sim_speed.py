import heapq
from collections import deque

import pytest

from metro_control import sim
from metro_control.sim import Arrival, StopLog, _copy_state, new_state

FP = {
    "baseline": (35071.999999995205, 35031.61313792126, 0.0, 61536.71488393235, 15960,
                 180170.16811422646, 35031.61313792044),
    "policy": (35071.999999995205, 35031.61313792127, 0.0, 63879.248158792354, 15580,
               180170.16811422654, 35031.61313792047),
}  # fmt: skip


def test_compare_fingerprint_unchanged(result):
    for name, exp in FP.items():
        s = getattr(result, name)
        got = (
            s.entered,
            s.alighted,
            s.denied,
            s.wait_pax_min,
            len(s.log),
            sum(x.load_after for x in s.log),
            sum(x.boarded for x in s.log),
        )
        assert got == pytest.approx(exp, rel=1e-12), name


def test_copy_state_independent():
    p = sim.SimParams(("A", "B", "C"), (2.0, 2.0), 0.5, 1.0, 10.0)
    st = new_state(p, [("T1", "A", "north", 0.0)], 0.0, [("X", "south", 5.0)])
    st = sim.run(st, p, [Arrival(0.1, "A", "C", 30.0), Arrival(0.2, "B", "C", 3.0)], 3.0)
    assert st.log and any(st.queues.values())
    cp = _copy_state(st)
    assert cp == st
    assert cp is not st and cp.trains is not st.trains and cp.queues is not st.queues
    snap_onboard = {k: dict(t.onboard) for k, t in st.trains.items()}
    snap_q = {k: [(c.t_arrive, dict(c.by_dest)) for c in q] for k, q in st.queues.items()}
    snap_events, snap_log, snap_seq = list(st.events), list(st.log), st.seq

    for t in cp.trains.values():
        t.onboard["Z"] = 1.0
        t.station_idx = 99
    for q in cp.queues.values():
        for c in q:
            c.by_dest["Z"] = 1.0
        if q:
            q.popleft()
    assert isinstance(next(iter(cp.queues.values())), deque)
    heapq.heappush(cp.events, (0.0, 999, "Q", 0, "north"))
    cp.log.append(StopLog(0, "Q", "A", "north", 0, 0, 0, 0))
    cp.seq += 1

    assert {k: dict(t.onboard) for k, t in st.trains.items()} == snap_onboard
    assert {k: [(c.t_arrive, dict(c.by_dest)) for c in q] for k, q in st.queues.items()} == snap_q
    assert st.events == snap_events and st.log == snap_log and st.seq == snap_seq
    assert all(t.station_idx != 99 for t in st.trains.values())
