from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
from test_scenario import ASS, LINE

from metro_control import mock, scenario
from metro_control.cli import main
from metro_control.dayrun import build_day, service_origin
from metro_control.od import load_od_params
from metro_control.timeline import find_sim_dir, forecast_twin

D = date(2026, 9, 30)
SPEC = ASS["scenarios"]["value"]["rail_surge"]


def _split(fx):
    utc_day = pl.col("ts").dt.convert_time_zone("UTC").dt.date()
    day = fx.filter(utc_day == D)
    others = fx.filter(utc_day != D)
    return day, pl.concat([others, day]).sort("ts")


def _fake(calls):
    o = service_origin(D)

    def fn(hist, as_of):
        calls.append((as_of, hist["ts"].max() if hist.height else None))
        pkg = mock.mock_forecast(hist, as_of).model_dump(mode="json")
        k = int((as_of - o) / timedelta(minutes=15))
        for r in pkg["payload"]["rows"]:
            r.update(q10=0.0, q50=1000.0 + k, q90=1e6)
        return pkg

    return fn


def test_entries_from_forecast_without_future(fx):
    day, hist_all = _split(fx)
    calls = []
    out, errs = scenario.forecast_day_entries(day, hist_all, _fake(calls), {}, D)
    assert errs == []
    assert out.columns == day.columns and out.schema == day.schema and out.height == day.height
    o = service_origin(D)
    for ts, v in zip(out["ts"], out["entries"], strict=True):
        assert v == pytest.approx(1000.0 + (ts - o) / timedelta(minutes=15))
    assert len(calls) == 96
    for as_of, hmax in calls:
        assert hmax is None or hmax < as_of


def test_future_truth_does_not_leak(fx):
    day, hist_all = _split(fx)
    a, _ = scenario.forecast_day_entries(day, hist_all, None, {}, D)
    cut = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)  # 18:00 MSK
    day2 = day.with_columns(
        pl.when(pl.col("ts") > cut).then(999.0).otherwise(pl.col("entries")).alias("entries")
    )
    hist2 = pl.concat([hist_all.filter(pl.col("ts") < day["ts"].min()), day2]).sort("ts")
    b, _ = scenario.forecast_day_entries(day2, hist2, None, {}, D)
    keep = pl.col("ts") <= cut
    assert a.filter(keep)["entries"].to_list() == b.filter(keep)["entries"].to_list()


def test_plugin_error_falls_back_to_mock(fx):
    day, hist_all = _split(fx)
    bad = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
    good = _fake([])

    def fn(hist, as_of):
        if as_of == bad:
            raise RuntimeError("boom")
        return good(hist, as_of)

    out, errs = scenario.forecast_day_entries(day, hist_all, fn, {}, D)
    assert (
        len(errs) == 1
        and errs[0]["source"] == "forecast"
        and errs[0]["as_of"].startswith("2026-09-30T08:00")
    )
    ref, _ = scenario.forecast_day_entries(day, hist_all, None, {}, D)
    sel = pl.col("ts") == bad
    assert out.filter(sel)["entries"].to_list() == ref.filter(sel)["entries"].to_list()


def test_surge_applied_inside_window_only(fx):
    day, hist_all = _split(fx)
    out, _ = scenario.forecast_day_entries(day, hist_all, None, SPEC, D)
    ref, _ = scenario.forecast_day_entries(day, hist_all, None, {}, D)
    msk = pl.col("ts").dt.convert_time_zone("Europe/Moscow")
    mins = msk.dt.hour().cast(pl.Int64) * 60 + msk.dt.minute().cast(pl.Int64)
    inside = (mins >= 900) & (mins < 1020) & pl.col("station_id").is_in(SPEC["overlay"]["stations"])
    j = out.join(ref, on=["station_id", "ts"], suffix="_r").with_columns(
        pl.when(inside).then(5.0).otherwise(1.0).alias("f")
    )
    assert j.filter(inside).height > 0
    assert (j["entries"] - j["entries_r"] * j["f"]).abs().max() < 1e-9


@pytest.fixture(scope="session")
def fc_result(fx):
    bad = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)  # after the surge actions

    def fn(hist, as_of):
        if as_of == bad:
            raise RuntimeError("boom")
        return mock.mock_forecast(hist, as_of)

    return scenario.compare(
        "rail_surge", fx, LINE, load_od_params(), ASS, forecast_fn=fn, demand="forecast"
    )


@pytest.mark.xdist_group("fc")
def test_compare_forecast(fc_result, fx, tmp_path):
    r = fc_result
    assert r.manifest["demand"] == "forecast"
    assert r.timeline.run_id == "compare-rail_surge-2026-09-30-forecast"
    day, hist_all = _split(fx)
    truth = scenario.apply_scenario(day, SPEC)
    hist = pl.concat([hist_all.filter(pl.col("ts") < truth["ts"].min()), truth]).sort("ts")
    fc_day, _ = scenario.forecast_day_entries(truth, hist, None, SPEC, D)
    _, arrivals, *_ = build_day(fc_day, fc_day, LINE, load_od_params(), ASS)
    assert r.baseline.entered == pytest.approx(sum(a.amount for a in arrivals), rel=1e-6)
    assert r.baseline.entered == pytest.approx(r.policy.entered, rel=1e-6)
    assert scenario.write_run(r, tmp_path).name == "compare-rail_surge-2026-09-30-forecast"


@pytest.mark.xdist_group("result")
def test_truth_run_unchanged(result):
    assert result.manifest["demand"] == "truth"
    assert result.manifest["run_id"] == "compare-rail_surge-2026-09-30"


def test_find_sim_dir_skips_forecast_and_twin(tmp_path):
    assert forecast_twin(None) is None
    t = tmp_path / "compare-rail_surge-2026-09-30"
    f = tmp_path / "compare-rail_surge-2026-09-30-forecast"
    for d in (t, f):
        d.mkdir()
        (d / "timeline.json").write_text("{}")
    assert find_sim_dir(None, tmp_path) == t
    assert forecast_twin(t) == f
    (f / "timeline.json").unlink()
    assert forecast_twin(t) is None


@pytest.mark.xdist_group("fc")
def test_cli_demand_forecast(tmp_path, fx, fc_result, capsys, monkeypatch):
    p = tmp_path / "e.parquet"
    fx.head(10).write_parquet(p)
    seen = {}

    def fake(*a, **k):
        seen.update(k)
        return fc_result

    monkeypatch.setattr(scenario, "compare", fake)
    args = ["compare", "--scenario", "rail_surge", "--entries", str(p), "--out", str(tmp_path)]
    assert main([*args, "--demand", "forecast"]) == 0
    assert seen["demand"] == "forecast"
    assert (tmp_path / "compare-rail_surge-2026-09-30-forecast" / "timeline.json").is_file()
    assert "demand: forecast (mock)" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([*args, "--demand", "bogus"])


@pytest.mark.xdist_group("fc")
def test_forecast_mode_actions_ordered(fc_result):
    ts = [a["as_of"] for a in fc_result.actions]
    assert ts == sorted(ts) and fc_result.outcomes.get("source_error", 0) > 0
    assert any(a["status"] != "source_error" for a in fc_result.actions)
