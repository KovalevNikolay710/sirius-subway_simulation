import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_scenario import ASS, LINE

from metro_control import mock, plugins, scenario
from metro_control.cli import main
from metro_control.od import load_od_params

ROOT = Path(__file__).resolve().parent.parent


def _run(fx, **kw):
    return scenario.compare("rail_surge", fx, LINE, load_od_params(), ASS, **kw)


def _errs(r, source=None):
    return [
        a
        for a in r.actions
        if a["status"] == "source_error" and (source is None or a["source"] == source)
    ]


def _boom(load):
    raise RuntimeError("boom\nsecond line")


@pytest.fixture(scope="module")
def boom(fx):
    return _run(fx, policy_fn=_boom, policy_label="t.py:_boom")


_calls = {"n": 0, "t": None}


def _mixed(load):
    """Cycle over four behaviours: raise, invalid, future as_of, valid person4 add_reserve."""
    as_of = min(x.interval_start for x in load.payload)
    i = _calls["n"] % 4
    _calls["n"] += 1
    if i == 0:
        raise RuntimeError("boom")
    if i == 1:
        return {"bad": 1}
    env = mock.mock_recommendation(load, as_of).model_dump(mode="json")
    pl_ = env["payload"]
    pl_["source"] = "person4"
    if i == 2:
        later = as_of + timedelta(minutes=15)
        pl_["as_of"] = pl_["start"] = env["generated_at"] = later.isoformat()
        pl_["end"] = (later + timedelta(minutes=60)).isoformat()
    else:
        pl_["action"] = "add_reserve"
        pl_["target"] = load.payload[0].segment_id
    return env


@pytest.fixture(scope="module")
def mixed(fx):
    _calls["n"] = 0
    return _run(fx, policy_fn=_mixed, policy_label="p4")


def test_policy_raises(boom, result):
    r = boom
    assert r.effect.payload.baseline == result.effect.payload.baseline
    assert r.policy.entered == pytest.approx(result.baseline.entered)
    errs = _errs(r, "policy")
    assert len(errs) == 96 and len(r.actions) == 96
    assert len({a["as_of"] for a in errs}) == 96
    assert all(a["reason"] == "RuntimeError: boom" for a in errs)
    assert errs[0]["outcome"] == "step skipped"
    assert r.outcomes == {"source_error": 96}
    assert r.manifest["policy"] == "t.py:_boom" and r.manifest["forecast"] == "mock"


def test_write_run_has_errors(boom, tmp_path):
    d = scenario.write_run(boom, tmp_path)
    rows = [json.loads(x) for x in (d / "actions.jsonl").read_text().splitlines()]
    assert len(rows) == 96 and rows[0]["status"] == "source_error"


def test_invalid_future_and_valid(mixed):
    errs = _errs(mixed, "policy")
    assert len(errs) == 72
    reasons = [a["reason"] for a in errs]
    assert sum(r.startswith("RuntimeError: boom") for r in reasons) == 24
    assert sum(r.startswith("ValidationError") for r in reasons) == 24
    assert sum("future as_of" in r for r in reasons) == 24
    ok = [a for a in mixed.actions if a["status"] != "source_error"]
    assert len(ok) == 24 and all(a["source"] == "p4" for a in ok)
    assert all(a["action"] == "add_reserve" for a in ok)
    assert all(a["status"] in {"applied", "rejected", "duplicate", "noop"} for a in ok)
    assert sum(mixed.outcomes.values()) == 96


def test_policy_gets_copy(fx):
    as_of = fx["interval_start"].max()
    hist = fx.filter(fx["interval_start"] < as_of)
    ld = mock.mock_load(
        mock.mock_forecast(hist, as_of), hist, LINE, load_od_params(), "weekday", {}
    )
    before = ld.model_dump_json()

    def mutate(load):
        load.payload[0].demand = 1e9
        load.payload.clear()
        raise RuntimeError("x")

    plugins.call_policy(mutate, ld, as_of)
    assert ld.model_dump_json() == before


def test_forecast_raises(fx, result):
    def fboom(h, t):
        raise ValueError("no model")

    r = _run(fx, forecast_fn=fboom, forecast_label="f.py:fboom")
    e = _errs(r, "forecast")
    assert len(e) == 96 and e[0]["outcome"] == "mock forecast used"
    assert e[0]["reason"] == "ValueError: no model"
    assert [a for a in r.actions if a["status"] != "source_error"] == [
        {**a, "source": "mock"} for a in result.actions
    ]
    assert r.manifest["forecast"] == "f.py:fboom"


def test_forecast_history_copy(fx):
    as_of = fx["interval_start"].max()
    h = fx.filter(fx["interval_start"] < as_of)
    n = h.height

    def f(hist, t):
        hist.clear()
        return mock.mock_forecast(hist, t)

    pkg, err = plugins.call_forecast(f, h, as_of)
    assert h.height == n


def test_default_manifest(result):
    assert result.manifest["policy"] == "mock" and result.manifest["forecast"] == "mock"
    assert all(a["source"] == "mock" for a in result.actions)


def test_load_callable(tmp_path):
    for bad in ("nope", "no_such_mod:f", "json:nope", "json:__name__", ":f", "a.py:f"):
        with pytest.raises(ValueError):
            plugins.load_callable(bad)
    assert plugins.load_callable("json:dumps") is __import__("json").dumps
    f = tmp_path / "pl.py"
    f.write_text("def func(x):\n    return x + 1\n")
    assert plugins.load_callable(f"{f}:func")(1) == 2


def test_cli_bad_plugin(capsys, tmp_path):
    rc = main(["compare", "--scenario", "rail_surge", "--entries", str(tmp_path / "x.parquet")])
    assert rc == 1
    capsys.readouterr()
    p = tmp_path / "e.parquet"
    p.write_bytes(b"")
    rc = main(
        ["compare", "--scenario", "rail_surge", "--entries", str(p), "--policy", "no_such_mod:f"]
    )
    assert rc == 1
    assert capsys.readouterr().err.startswith("error:")


def test_app_source_error(tmp_path, monkeypatch, result):
    from streamlit.testing.v1 import AppTest

    d = scenario.write_run(result, tmp_path)
    row = {
        "as_of": result.timeline.frames["policy"][0]["t"],
        "recommendation_id": None,
        "action": "none",
        "target": "",
        "start": None,
        "end": None,
        "status": "source_error",
        "source": "policy",
        "reason": "RuntimeError: boom",
        "outcome": "step skipped",
        "train_ids": [],
    }
    (d / "actions.jsonl").write_text(json.dumps(row) + "\n")
    (d / "manifest.json").write_text(json.dumps({"policy": "x.py:f"}))
    monkeypatch.setenv("METRO_RUN_DIR", str(d))
    monkeypatch.setenv("METRO_SIM_DIR", str(d))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    at.switch_page("views/sim.py").run()
    assert not at.exception
    text = " ".join(x.value for x in at.warning) + " ".join(x.value for x in at.markdown)
    assert "ошибка источника" in text and "x.py:f" in text
