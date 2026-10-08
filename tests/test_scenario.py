import json
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from metro_control import contracts, policy, scenario
from metro_control.cli import main
from metro_control.dayrun import load_assumption_items, service_origin
from metro_control.entries import SCHEMA
from metro_control.line import load_line
from metro_control.od import load_od_params
from metro_control.sim import Arrival, SimParams, new_state, run

LINE = load_line()
ASS = load_assumption_items()
ST = [s.id for s in sorted(LINE.stations, key=lambda s: s.order)]


def day_frame(d: date, per_slot: float = 20.0, day_type: str = "weekday") -> pl.DataFrame:
    o = service_origin(d)
    rows = []
    for k in range(96):
        msk_min = (k * 15 + 180) % 1440
        on = msk_min >= 300 or msk_min < 60
        for s in ST:
            rows.append((s, o + timedelta(minutes=15 * k), day_type, per_slot if on else 0.0))
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def test_wait_pax_min_control():
    p = SimParams(("A", "B", "C"), (2.0, 2.0), 0.5, 3.0, 100.0)
    st = new_state(p, [], 0.0, [("X", "north", 3.0), ("Y", "north", 50.0)])
    d = [Arrival(0, "A", "C", 20), Arrival(6, "A", "C", 5)]
    st = run(st, p, d, 10.0)
    m = scenario.metrics(st, p, {"trip_km": {"value": 27.48}})
    assert m.wait_pax_min == pytest.approx(80)
    assert m.queue_left == pytest.approx(5) and m.denied_boardings == 0
    assert 0 < m.max_fill <= 1 and m.train_hours > 0 and m.train_km > 0


def test_apply_shift_and_surge():
    day = day_frame(date(2026, 9, 30))
    spec = {
        "snowfall": ASS["scenarios"]["value"]["snowfall"],
        "quiet_weekend": {"date": "2026-09-27", "overlay": None, "announced": False},
        "rail_surge": {
            "date": "2026-09-30",
            "announced": True,
            "overlay": {
                "type": "surge",
                "factor": 3.0,
                "stations": ["ploshchad_vosstaniya", "ploshchad_lenina"],
                "start_msk": "07:00",
                "end_msk": "09:00",
            },
        },
    }
    # distinct per-slot values
    day = day.with_columns(
        (pl.col("entries") + pl.col("interval_start").dt.minute()).alias("entries")
    )
    sh = scenario.apply_scenario(day, spec["snowfall"])
    assert sh["entries"].sum() == pytest.approx(day["entries"].sum())
    t0 = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)  # 06:00 MSK
    f = lambda df, t: df.filter((pl.col("station_id") == ST[0]) & (pl.col("interval_start") == t))[  # noqa: E731
        "entries"
    ][0]
    assert f(sh, t0 + timedelta(minutes=30)) == f(day, t0)
    su = scenario.apply_scenario(day, spec["rail_surge"])
    j = day.join(su, on=["station_id", "interval_start"], suffix="_n")
    ch = j.filter((pl.col("entries") != pl.col("entries_n")) & (pl.col("entries") > 0))
    assert set(ch["station_id"]) == {"ploshchad_vosstaniya", "ploshchad_lenina"}
    msk_h = ch["interval_start"].dt.convert_time_zone("Europe/Moscow").dt.hour()
    assert set(msk_h) == {7, 8}
    assert (ch["entries_n"] / ch["entries"]).to_list() == pytest.approx([3.0] * ch.height)
    assert scenario.apply_scenario(day, spec["quiet_weekend"]).equals(day)


@pytest.fixture(scope="module")
def fx():
    days = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]
    return pl.concat([day_frame(d, 20.0) for d in days])


@pytest.fixture(scope="module")
def result(fx):
    return scenario.compare("rail_surge", fx, LINE, load_od_params(), ASS)


def test_compare_identical_inputs_and_balance(result):
    b, p = result.baseline, result.policy
    assert b.entered == pytest.approx(p.entered)
    for s in (b, p):
        onb = sum(sum(t.onboard.values()) for t in s.trains.values())
        wait = sum(sum(c.by_dest.values()) for q in s.queues.values() for c in q)
        assert s.entered == pytest.approx(s.alighted + wait + onb, rel=1e-6)
    for a in result.actions:
        assert a["status"] in {"applied", "rejected", "duplicate", "noop"}
        assert a["action"] != "none"
    eff = result.effect
    assert eff.payload.scenario == "rail_surge"
    assert eff.payload.baseline_run_id != eff.payload.policy_run_id


def test_history_never_future(fx, monkeypatch):
    seen = []
    orig = policy.recommend_at

    def spy(history, as_of, *a, **k):
        if history.height:
            assert history["interval_start"].max() < as_of
        seen.append(as_of)
        rec, mem = orig(history, as_of, *a, **k)
        assert rec.payload.as_of == as_of
        return rec, mem

    monkeypatch.setattr(policy, "recommend_at", spy)
    scenario.compare("rail_surge", fx, LINE, load_od_params(), ASS)
    assert len(seen) == 96
    assert seen == sorted(seen)
    assert all(s.minute % 15 == 0 for s in seen)


def test_write_run_deterministic(result, tmp_path):
    a = scenario.write_run(result, tmp_path / "a")
    b = scenario.write_run(result, tmp_path / "b")
    assert a.name == "compare-rail_surge-2026-09-30"
    for f in ("metrics.json", "manifest.json", "actions.jsonl", "state_policy.json"):
        assert (a / f).read_bytes() == (b / f).read_bytes()
    raw = (a / "metrics.json").read_bytes()
    contracts.EffectComparison.model_validate_json(raw)
    contracts.SimulationState.model_validate_json((a / "state_baseline.json").read_bytes())
    man = json.loads((a / "manifest.json").read_text())
    assert man["scenario"] == "rail_surge" and man["date"] == "2026-09-30"
    assert len(man["entries_sha256"]) == 64
    for line in (a / "actions.jsonl").read_text().splitlines():
        assert {"as_of", "recommendation_id", "status", "train_ids"} <= set(json.loads(line))


def test_cli_missing_parquet(tmp_path, capsys):
    rc = main(["compare", "--scenario", "rail_surge", "--entries", str(tmp_path / "no.parquet")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_cli_missing_date(tmp_path, capsys, fx):
    p = tmp_path / "e.parquet"
    fx.filter(pl.col("interval_start") < datetime(2026, 9, 29, tzinfo=UTC)).write_parquet(p)
    rc = main(["compare", "--scenario", "rail_surge", "--entries", str(p), "--out", str(tmp_path)])
    assert rc == 1
    assert "2026-09-30" in capsys.readouterr().err


def test_surge_window_limits_multiplier(fx):
    from metro_control import mock

    as_of = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)  # 05:00 MSK
    hist = fx.filter(pl.col("interval_start") < as_of)
    fc = mock.mock_forecast(hist, as_of)
    f = {s: 3.0 for s in ST}
    w = (datetime(2026, 9, 30, 3, 0, tzinfo=UTC), datetime(2026, 9, 30, 4, 0, tzinfo=UTC))
    args = (fc, hist, LINE, load_od_params(), "weekday")
    base = mock.mock_load(*args, {})
    win = mock.mock_load(*args, f, w)
    flat = mock.mock_load(*args, f)

    def by_t(pkg):
        out = {}
        for r in pkg.payload:
            out[r.interval_start] = out.get(r.interval_start, 0.0) + r.demand
        return out

    b, x, y = by_t(base), by_t(win), by_t(flat)
    early = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)
    assert x[early] == pytest.approx(b[early]) and y[early] > b[early] * 1.5
    inside = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
    assert x[inside] > b[inside] * 1.5
