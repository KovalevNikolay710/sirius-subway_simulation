# ruff: noqa: F811
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_scenario import ASS, LINE, fx, result  # noqa: F401

from metro_control import scenario, timeline
from metro_control.sim import Arrival, SimParams, new_state, run
from metro_control.timeline import Player

ROOT = Path(__file__).resolve().parent.parent


def test_frames_shape_and_totals(result):
    tl = result.timeline
    assert tl.n_frames == 97
    assert len(tl.frames["baseline"]) == len(tl.frames["policy"]) == 97
    f0 = tl.frames["baseline"][0]
    assert f0["entered"] == f0["waiting"] == f0["denied"] == f0["wait_pax_min"] == 0
    assert f0["queues"] == {} and tl.frames["policy"][0]["queues"] == {}
    for v, st in (("baseline", result.baseline), ("policy", result.policy)):
        m = scenario.metrics(st, scenario_params(result), ASS)
        last = tl.frames[v][-1]
        assert last["entered"] == pytest.approx(st.entered, abs=0.1)
        assert last["denied"] == pytest.approx(m.denied_boardings, abs=0.1)
        assert last["wait_pax_min"] == pytest.approx(m.wait_pax_min, abs=0.1)


def scenario_params(result):
    # metrics() only reads capacity and station count from params
    n = len(result.timeline.stations)
    return SimParams(
        tuple(result.timeline.stations),
        (1.0,) * (n - 1),
        0.5,
        3.0,
        result.timeline.capacity,
    )


def test_stepwise_baseline_equals_single_run(fx, result):
    from metro_control.dayrun import DAY_END_MIN, build_day
    from metro_control.od import load_od_params
    from metro_control.scenario import apply_scenario

    spec = ASS["scenarios"]["value"]["rail_surge"]
    from datetime import date

    import polars as pl

    d = date.fromisoformat(spec["date"])
    day = fx.filter(pl.col("interval_start").dt.date() == d)
    truth = apply_scenario(day, spec)
    params, demand, trips, _, _ = build_day(truth, truth, LINE, load_od_params(), ASS)
    one = run(new_state(params, [], 0.0, trips), params, demand, DAY_END_MIN)
    b = result.baseline
    assert b.entered == pytest.approx(one.entered)
    assert b.denied == pytest.approx(one.denied)
    assert b.wait_pax_min == pytest.approx(one.wait_pax_min)
    assert len(b.log) == len(one.log)


def test_snapshot_segment_high_and_terminal():
    p = SimParams(("A", "B", "C"), (2.0, 2.0), 0.5, 3.0, 100.0)
    st = new_state(p, [("T1", "A", "north", 0.0)], 0.0, [])
    st = run(st, p, [Arrival(0.0, "A", "C", 120.0)], 10.0)
    snap = timeline.snapshot(st, p, 0, datetime(2026, 1, 1, tzinfo=UTC))
    seg = snap["segments"]["A__B"]
    assert seg["fill"] == 1.0 and seg["left_behind"] == 20.0
    tl = _tiny_tl(snap)
    df = timeline.segment_bands(snap, tl.stations)
    row = df.filter(df["segment_id"] == "A__B").row(0, named=True)
    assert row["band"] == "high"
    # stops at the terminal C (north) produce no segment
    assert all(not k.startswith("C__") or k == "C__B" for k in snap["segments"])
    assert "C__None" not in snap["segments"]


def _tiny_tl(snap):
    return timeline.Timeline(
        run_id="r",
        scenario="s",
        date="2026-01-01",
        capacity=100.0,
        stations=["A", "B", "C"],
        frames={"baseline": [snap], "policy": [snap]},
    )


def test_player():
    p = Player(n_frames=5)
    for _ in range(3):
        p = timeline.step(p)
    assert p.k == 3
    p = timeline.reset(p)
    assert p.k == 0 and not p.playing
    assert timeline.tick(p, 10.0, None) == (p, None)
    p = timeline.toggle(p)
    q, last = timeline.tick(p, 10.0, None)
    assert q.k == 1 and last == 10.0
    assert timeline.tick(q, 10.2, last) == (q, last)
    q2, last2 = timeline.tick(q, 11.0, last)
    assert q2.k == 2 and last2 == 11.0
    last = Player(n_frames=5, k=4)
    assert timeline.step(last).k == 4


def test_load_timeline_reasons(tmp_path, result):
    tl, why = timeline.load_timeline(tmp_path)
    assert tl is None and "not found" in why
    (tmp_path / "timeline.json").write_text("{oops")
    assert "invalid JSON" in timeline.load_timeline(tmp_path)[1]
    (tmp_path / "timeline.json").write_text('{"run_id": "x"}')
    assert "schema" in timeline.load_timeline(tmp_path)[1]
    run_dir = scenario.write_run(result, tmp_path / "ok")
    tl, why = timeline.load_timeline(run_dir)
    assert why is None and tl.n_frames == 97


def test_helpers(result):
    tl = result.timeline
    cf = timeline.compare_frame(tl, 96)
    assert cf["delta"]["waiting"] == pytest.approx(
        cf["policy"]["waiting"] - cf["baseline"]["waiting"], abs=0.11
    )
    assert timeline.series(tl, 4).height == 10
    assert timeline.segment_bands(timeline.frame(tl, "policy", 40), tl.stations).height == 36
    t = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
    acts = [{"as_of": "2026-09-30T05:00:00Z"}, {"as_of": "2026-09-30T07:00:00Z"}]
    assert timeline.actions_until(acts, t) == acts[:1]
    assert timeline.actions_until(acts, datetime(2026, 9, 30, 5, 0, tzinfo=UTC)) == []


def _app(run_dir, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("METRO_RUN_DIR", str(run_dir))
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def _kpis(at):
    return [df.value.to_dict() for df in at.dataframe]


def test_app_sim_tab(result, tmp_path, monkeypatch):
    run_dir = scenario.write_run(result, tmp_path)
    at = _app(run_dir, monkeypatch).run()
    assert not at.exception
    first = _kpis(at)
    _btn(at, "Шаг +15 мин").click().run()
    _btn(at, "Шаг +15 мин").click().run()
    assert at.session_state["player"].k == 2
    at.selectbox(key="sim_station").select("ploshchad_lenina").run()
    assert at.session_state["player"].k == 2
    at.selectbox(key="s4_station").select_index(1).run()
    assert at.session_state["player"].k == 2
    _btn(at, "Сброс").click().run()
    assert at.session_state["player"].k == 0
    assert not at.exception
    assert _kpis(at) == first


def test_app_no_timeline(tmp_path, monkeypatch):
    (tmp_path / "metrics.json").write_text("{}")
    at = _app(tmp_path, monkeypatch).run()
    assert not at.exception
    assert any("compare --scenario rail_surge" in i.value for i in at.info)


def test_trains_in_service_counts_departed_only(result):
    for v in ("baseline", "policy"):
        fr = result.timeline.frames[v]
        assert fr[0]["trains_in_service"] == 0
        assert 0 < fr[48]["trains_in_service"] < 100
    assert result.timeline.frames["baseline"][-1]["trains_in_service"] == 0


def test_app_playing_is_time_driven(result, tmp_path, monkeypatch):
    import time

    run_dir = scenario.write_run(result, tmp_path)
    at = _app(run_dir, monkeypatch).run()
    at.session_state["player"] = Player(n_frames=result.timeline.n_frames, k=5, playing=True)
    at.session_state["last_tick"] = time.monotonic()
    at.selectbox(key="sim_station").select("ploshchad_lenina").run()
    assert at.session_state["player"].k == 5
    at.selectbox(key="s4_station").select_index(1).run()
    assert at.session_state["player"].k == 5


def test_find_sim_dir(tmp_path):
    assert timeline.find_sim_dir("/x/y", tmp_path) == Path("/x/y")
    assert timeline.find_sim_dir(None, tmp_path) is None
    for n, has in (("compare-a", True), ("compare-b", False), ("compare-c", True)):
        (tmp_path / n).mkdir()
        if has:
            (tmp_path / n / timeline.FILE).write_text("{}")
    assert timeline.find_sim_dir(None, tmp_path) == tmp_path / "compare-c"
    assert timeline.find_sim_dir(None, tmp_path / "missing") is None


def test_actions_until_skips_malformed():
    good = {"as_of": "2026-09-30T10:00:00Z", "kind": "x"}
    acts = [good, 5, {"x": 1}, {"as_of": "bad"}, {"as_of": 7}]
    t = datetime(2026, 9, 30, 11, 0, tzinfo=UTC)
    assert timeline.actions_until(acts, t) == [good]
