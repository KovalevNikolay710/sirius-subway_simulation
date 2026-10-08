import os

os.environ.setdefault("POLARS_MAX_THREADS", "1")  # xdist workers: avoid thread oversubscription

from datetime import date

import polars as pl
import pytest
from test_scenario import ASS, LINE, day_frame

from metro_control import policy, scenario
from metro_control.od import load_od_params


@pytest.fixture(scope="session")
def fx():
    days = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]
    return pl.concat([day_frame(d, 20.0) for d in days])


@pytest.fixture(scope="session")
def _result_run(fx):
    """One rail_surge compare with a recorder around policy.recommend_at (no asserts here)."""
    calls = []
    orig = policy.recommend_at

    def spy(history, as_of, *a, **k):
        rec, mem = orig(history, as_of, *a, **k)
        hmax = history["ts"].max() if history.height else None
        calls.append((as_of, hmax, rec.payload.as_of))
        return rec, mem

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(policy, "recommend_at", spy)
        res = scenario.compare("rail_surge", fx, LINE, load_od_params(), ASS)
    return res, calls


@pytest.fixture(scope="session")
def result(_result_run):
    return _result_run[0]


@pytest.fixture(scope="session")
def result_calls(_result_run):
    return _result_run[1]


def pytest_collection_modifyitems(items):
    """Keep tests sharing the expensive session `result` on one xdist worker (--dist loadgroup)."""
    for item in items:
        if {"result", "result_calls"} & set(item.fixturenames):
            item.add_marker(pytest.mark.xdist_group("result"))
