import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from metro_control import team_forecast as tf
from metro_control.adapters import build_team_bundle
from metro_control.events import (
    anomaly_slots,
    effect_ru,
    sim_events,
    station_reasons,
    team_banner,
)
from metro_control.line import load_line
from metro_control.mock import synthetic_entries
from metro_control.sim_view import load_payload, sim_payload

ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)  # 15:00 MSK
NAMES = {s.id: s.name_ru for s in load_line().stations}


@pytest.fixture(scope="module")
def history():
    return synthetic_entries(date(2026, 9, 1), 40)


@pytest.fixture(scope="module")
def serve_file(history, tmp_path_factory):
    p = tmp_path_factory.mktemp("ev") / "serve.json"
    p.write_text(json.dumps(tf.mock_team_serve(history, AS_OF), ensure_ascii=False))
    return p


@pytest.fixture(scope="module")
def team(serve_file, history):
    return tf.read_team_forecast(serve_file, history=history)


def test_effect_ru():
    assert effect_ru(12.0) == "+12 %"
    assert effect_ru(-5.5) == "−6 %"
    assert effect_ru(None) == ""


def test_banner_from_mock_serve(team):
    b = team_banner(team.context, NAMES)
    w, e = b["items"][0], b["items"][1]
    assert (w["kind"], w["text"], w["effect"], w["where"]) == (
        "weather",
        "дождь с 15:00",
        "+12 %",
        "18 станций",
    )
    assert (e["kind"], e["text"], e["effect"], e["where"]) == (
        "event",
        "прибытие поездов на Московский вокзал",
        "+25 %",
        NAMES["vosstaniya"],
    )
    assert b["mock"] is True and any("mock" in x for x in b["warnings"])


def test_banner_none():
    assert team_banner(None, NAMES) is None
    assert (
        team_banner({"meta": {}, "explanations": [{"station_id": "a", "reasons": []}]}, {}) is None
    )


def test_station_reasons(team):
    slots = [AS_OF + timedelta(minutes=15 * k) for k in range(8)]
    r = station_reasons(team.context, slots)
    assert len(r["vosstaniya"][0]) == 2 and len(r["vosstaniya"][7]) == 2
    assert r["vosstaniya"][0][0] == "дождь с 15:00 +12 %"
    assert len(r["devyatkino"][1]) == 1
    assert not any(r.get("tekhnologichesky_institut", []))


def _load_pkg(history, serve_file, tmp_path):
    build_team_bundle(tmp_path / "r", history, forecast=serve_file)
    from metro_control.screen import load_bundle

    return load_bundle(tmp_path / "r")


def test_load_payload_ctx(team, history, serve_file, tmp_path):
    b = _load_pkg(history, serve_file, tmp_path)
    fc = b["forecast"].package
    order = [s.id for s in sorted(load_line().stations, key=lambda s: s.order, reverse=True)]
    p = load_payload(
        b["load"].package,
        NAMES,
        ctx=team.context,
        anomaly=anomaly_slots(fc.payload.rows),
    )
    n = len(p["times"])
    v = order.index("vosstaniya")
    assert all(p["anomaly"][v][:8]) and not any(p["anomaly"][order.index("devyatkino")])
    assert len(p["reasons"]) == len(order) and len(p["reasons"][v][0]) == 2
    assert len(p["reasons"][order.index("devyatkino")][0]) == 1
    assert p["banner"]["mock"] is True and len(p["anomaly"][v]) == n
    q = load_payload(b["load"].package, NAMES)
    assert q["banner"] is None and not any(map(any, q["anomaly"]))
    assert not any(map(any, q["reasons"]))


def _sim_dir(tmp_path, scenario):
    d = tmp_path / scenario
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"scenario": scenario}))
    return d


def _frames(day=date(2026, 9, 30)):
    o = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [o + timedelta(minutes=15 * k) for k in range(96)]


def test_sim_events(team, tmp_path):
    d = _sim_dir(tmp_path, "rail_surge")
    ev = sim_events(d, team.context, _frames(), NAMES)
    texts = [i["text"] for i in ev["banner"]["items"]]
    assert "наплыв с вокзалов" in texts and "дождь с 15:00" in texts
    on = [k for k, x in enumerate(ev["anomaly"]["vosstaniya"]) if x]
    assert on == list(range(48, 56))  # 15:00-16:45 MSK = 12:00-13:45 UTC
    assert not ev["anomaly"].get("devyatkino")
    assert any("наплыв" in t for t in ev["reasons"]["vosstaniya"][48])
    other = sim_events(d, team.context, _frames(date(2026, 11, 12)), NAMES)
    assert all(i["text"] != "дождь с 15:00" for i in other["banner"]["items"])
    assert not other["banner"]["mock"]
    quiet = sim_events(_sim_dir(tmp_path, "quiet_weekend"), None, _frames(date(2026, 9, 27)), NAMES)
    assert quiet["banner"] is None and not any(map(any, quiet["anomaly"].values()))
    snow = sim_events(_sim_dir(tmp_path, "snowfall"), None, _frames(date(2026, 2, 10)), NAMES)
    assert snow["banner"]["items"][0]["where"] == "вся линия"


def test_sim_payload_events(result, team, tmp_path):
    tl = result.timeline
    from metro_control.sim_view import _ts

    d = _sim_dir(tmp_path, "rail_surge")
    ts = [_ts(f["t"]) for f in tl.frames["policy"]]
    p = sim_payload(tl, NAMES, [], "mock", sim_events(d, team.context, ts, NAMES))
    assert len(p["anomaly"]) == len(p["stations"]) and len(p["anomaly"][0]) == tl.n_frames
    assert p["banner"] is not None
    assert sim_payload(tl, NAMES, [], "mock")["banner"] is None


def _apps(serve_file, history, result, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from metro_control import scenario

    build_team_bundle(tmp_path / "run", history, forecast=serve_file)
    sim = scenario.write_run(result, tmp_path / "sims")
    monkeypatch.setenv("METRO_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("METRO_SIM_DIR", str(sim))
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def _html(at):
    return " ".join(getattr(e.proto, "srcdoc", "") for e in at.get("iframe"))


def test_app_pages(serve_file, history, result, tmp_path, monkeypatch):
    at = _apps(serve_file, history, result, tmp_path, monkeypatch).run()
    assert not at.exception
    assert "дождь с 15:00" in _html(at) and '"banner":{"mock":true' in _html(at)
    at.switch_page("views/sim.py").run()
    assert not at.exception
    assert "наплыв с вокзалов" in _html(at)


def test_app_demo_no_banner(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("METRO_RUN_DIR", str(ROOT / "runs" / "demo"))
    monkeypatch.setenv("METRO_SIM_DIR", str(ROOT / "runs" / "none"))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception
    assert '"banner":null' in _html(at) and 'class="banner"' not in _html(at)


def test_review_fixes(team, tmp_path, monkeypatch):
    from metro_control import events

    assert effect_ru(12.5) == "+13 %" and effect_ru(-12.5) == "−13 %"
    # explanations of other days are dropped before counting
    ctx = {
        **team.context,
        "explanations": team.context["explanations"]
        + [
            {
                "station_id": "devyatkino",
                "ts": "2026-10-01T12:00:00Z",
                "reasons": [{"feature": "x", "text": "лишнее", "effect_pct": 99.0}],
            }
        ],
    }
    d = _sim_dir(tmp_path, "quiet_weekend")
    b = sim_events(d, ctx, _frames(), NAMES)["banner"]
    assert all(i["text"] != "лишнее" for i in b["items"])
    spec = {
        "announced": False,
        "overlay": {
            "type": "surge",
            "factor": 2.0,
            "stations": ["vosstaniya"],
            "start_msk": "23:00",
            "end_msk": "01:00",
        },
    }
    monkeypatch.setattr(events, "_scenario", lambda _d: ("x", spec))
    ev = sim_events(d, None, _frames(), NAMES)
    assert ev["banner"] is None
    a = ev["anomaly"]["vosstaniya"]
    assert [k for k, x in enumerate(a) if x] == list(range(80, 88))
