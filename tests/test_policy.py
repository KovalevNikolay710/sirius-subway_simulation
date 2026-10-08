from datetime import UTC, datetime, timedelta

import pytest

from metro_control import mock
from metro_control.contracts import LoadPackage
from metro_control.line import load_line
from metro_control.policy import PolicyMemory, mock_policy

T = datetime(2026, 9, 3, 14, 30, tzinfo=UTC)
LINE = load_line()
CAP = 1458.0
FULL = {"avtovo": 2, "severnoye": 2}
NORTH_SEG = "prospekt_veteranov__leninsky_prospekt"
SOUTH_SEG = "leninsky_prospekt__prospekt_veteranov"


def make_load(as_of, base=0.5, over=None, slots=12) -> LoadPackage:
    over = over or {}
    rows = []
    for k in range(slots):
        slot = as_of + timedelta(minutes=15 * k)
        for seg in LINE.segments:
            r = over.get(seg.id, base)
            rows.append(
                {
                    "segment_id": seg.id,
                    "ts": mock._iso(slot),
                    "demand": r * CAP,
                    "departures": 1,
                    "capacity_per_train": CAP,
                    "r": r,
                }
            )
    env = mock._envelope("load", "t", as_of, "test")
    return LoadPackage.model_validate({**env, "payload": rows})


def step(as_of, mem, base=0.9, over=None, reserves=FULL):
    return mock_policy(make_load(as_of, base, over), as_of, mem, reserves)


def test_over_hysteresis_and_unique_as_of():
    hot = {NORTH_SEG: 1.2}
    r1, m1 = step(T, PolicyMemory(), over=hot)
    assert r1.payload.action == "none" and m1.over_streak == 1
    r1b, m1b = step(T, m1, over=hot)
    assert r1b is r1 and m1b is m1 and m1b.over_streak == 1
    r2, m2 = step(T + timedelta(minutes=15), m1, over=hot)
    p = r2.payload
    assert p.action == "add_reserve" and p.target == NORTH_SEG and p.source == "mock"
    assert p.recommendation_id.startswith("mock-policy-") and "(mock)" in p.reason
    assert p.end - p.start == timedelta(minutes=60)
    assert m2.over_streak == 0


def test_south_uses_severnoye_and_empty_depot_shifts():
    hot = {SOUTH_SEG: 1.3}
    _, m = step(T, PolicyMemory(), over=hot)
    r, _ = step(T + timedelta(minutes=15), m, over=hot, reserves={"avtovo": 2, "severnoye": 0})
    assert r.payload.action == "shift_peak" and r.payload.target == SOUTH_SEG
    _, m = step(T, PolicyMemory(), over={NORTH_SEG: 1.2})
    r, _ = step(
        T + timedelta(minutes=15), m, over={NORTH_SEG: 1.2}, reserves={"avtovo": 0, "severnoye": 2}
    )
    assert r.payload.action == "shift_peak"


def test_under_needs_three_consecutive():
    mem = PolicyMemory()
    acts = []
    for k in range(3):
        r, mem = step(T + timedelta(minutes=15 * k), mem, base=0.5)
        acts.append(r.payload.action)
    assert acts == ["none", "none", "remove_train"]
    assert mem.under_streak == 0


def test_under_streak_reset_by_middle_slot():
    mem = PolicyMemory()
    acts = []
    for k, base in enumerate([0.5, 0.9, 0.5]):
        r, mem = step(T + timedelta(minutes=15 * k), mem, base=base)
        acts.append(r.payload.action)
    assert acts == ["none"] * 3


def test_remove_targets_lower_direction():
    mem = PolicyMemory()
    over = {NORTH_SEG: 0.7}
    for k in range(3):
        r, mem = step(T + timedelta(minutes=15 * k), mem, base=0.5, over=over)
    assert r.payload.action == "remove_train" and r.payload.target != NORTH_SEG


def test_earlier_as_of_raises():
    _, m = step(T, PolicyMemory())
    with pytest.raises(ValueError):
        step(T - timedelta(minutes=15), m)


def test_none_rows_ignored_and_horizon_only():
    # only the window [as_of, as_of+30) counts: hot slot at +30 is invisible
    load = make_load(T, base=0.9)
    rows = load.payload
    for r in rows:
        if r.ts >= T + timedelta(minutes=30):
            r.r = 5.0
            r.demand = 5.0 * CAP
    rec, m = mock_policy(load, T, PolicyMemory(), FULL)
    assert m.over_streak == 0 and rec.payload.action == "none"


def test_naive_future_and_empty_load():
    with pytest.raises(ValueError):
        mock_policy(make_load(T), T.replace(tzinfo=None), PolicyMemory(), FULL)
    with pytest.raises(ValueError):
        mock_policy(make_load(T + timedelta(minutes=15)), T, PolicyMemory(), FULL)
    empty = make_load(T).model_copy(update={"payload": []})
    rec, m = mock_policy(empty, T, PolicyMemory(), FULL)
    assert rec.payload.action == "none" and rec.payload.target == NORTH_SEG
