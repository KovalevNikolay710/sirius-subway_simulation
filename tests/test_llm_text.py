import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from metro_control import llm_text, scenario
from metro_control.cli import main
from metro_control.llm_text import explain_run, forecast_items, sim_items, team_repo
from metro_control.mock import build_mock_bundle, synthetic_entries
from metro_control.screen import (
    actionable,
    explanation_for,
    load_bundle,
    load_recommendations,
    rec_evidence,
)
from metro_control.sim_view import sim_payload

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def run(tmp_path):
    as_of = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
    return build_mock_bundle(tmp_path / "run", synthetic_entries(date(2026, 9, 1), 28), as_of)


def fake(items):
    return {i["rec_id"]: f"Текст для {i['rec_id']}" for i in items}


def boom(items):
    raise RuntimeError("no key\nsecond line")


def test_forecast_items(run):
    items = forecast_items(run)
    assert items
    rec = actionable(load_recommendations(run).items)[0]
    ev = rec_evidence(rec, load_bundle(run)["load"].package)
    it = items[0]
    for k in ("rec_id", "action", "action_text", "segment", "window", "as_of", "reasons"):
        assert k in it
    assert "→" in it["segment"] and len(it["window"]) == 11 and len(it["as_of"]) == 5
    assert it["peak_ratio"] == ev["peak"]["r"] and it["ratio_limit"] == ev["r_on"]
    assert it["demand"] == ev["peak"]["demand"] and it["capacity"] == ev["peak"]["capacity"]


def test_explain_ok_and_screen(run):
    meta = explain_run(run, explain=fake)
    assert meta["source"] == "yandexgpt" and meta["n"] >= 1
    rec = actionable(load_recommendations(run).items)[0]
    text, origin = explanation_for(run, rec)
    assert text == f"Текст для {rec.payload.recommendation_id}" and origin == "yandexgpt"
    meta_path = run / "explanations" / "yandexgpt" / "meta.json"
    assert json.loads(meta_path.read_text())["source"] == "yandexgpt"


def test_explain_failure_keeps_reason(run):
    meta = explain_run(run, explain=boom)
    assert meta == {"source": "mock", "error": "no key"}
    assert not list((run / "explanations").glob("*.txt"))
    rec = actionable(load_recommendations(run).items)[0]
    assert explanation_for(run, rec) == (rec.payload.reason, "reason")


def test_default_import_path(run, tmp_path, monkeypatch):
    pkg = tmp_path / "team" / "external_data"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "dispatcher_text.py").write_text(
        "def explain_recommendations(items, provider=None):\n"
        "    return {i['rec_id']: 'fake ' + i['rec_id'] for i in items}\n"
    )
    monkeypatch.setenv("METRO_TEAM_REPO", str(tmp_path / "team"))
    assert team_repo() == tmp_path / "team"
    assert explain_run(run)["source"] == "yandexgpt"
    monkeypatch.setenv("METRO_TEAM_REPO", str(tmp_path / "none"))
    import sys

    sys.modules.pop("external_data.dispatcher_text", None)
    sys.modules.pop("external_data", None)
    assert explain_run(run)["source"] == "mock"
    assert llm_text.MODULE == "external_data.dispatcher_text"


def test_cli_explain(run, capsys, monkeypatch):
    monkeypatch.setattr(llm_text, "_default_explain", boom)
    assert main(["explain", "--run", str(run)]) == 0
    assert "no texts: no key (mock)" in capsys.readouterr().out


def test_sim_items_and_payload(result, tmp_path):
    sim = scenario.write_run(result, tmp_path / "sims")
    items = sim_items(sim)
    assert items and all(i["rec_id"] for i in items)
    meta = explain_run(sim, kind="sim", explain=fake)
    assert meta["source"] == "yandexgpt"
    names = {s.id: s.name_ru for s in llm_text.load_line().stations}
    acts = json.loads((sim / "actions.jsonl").read_text().splitlines()[0])
    pl_ = sim_payload(
        result.timeline,
        names,
        [json.loads(x) for x in (sim / "actions.jsonl").read_text().splitlines()],
        "mock",
        sim_dir=sim,
    )
    cached = [a for a in pl_["actions"] if a["llm"]]
    assert cached and cached[0]["reason"].startswith("Текст для ")
    assert acts["recommendation_id"]


def test_app_forecast_shows_text(run, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    explain_run(run, explain=fake)
    monkeypatch.setenv("METRO_RUN_DIR", str(run))
    monkeypatch.setenv("METRO_SIM_DIR", str(tmp_path / "none"))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception
    shown = " ".join(m.value for m in at.markdown)
    assert "Текст для " in shown and "YandexGPT" in shown


def test_rerun_failure_clears_and_stray_keys(run):
    rec = actionable(load_recommendations(run).items)[0]
    rid = rec.payload.recommendation_id
    explain_run(run, explain=lambda items: {**fake(items), "../x": "evil", "other": "x"})
    sub = run / "explanations" / "yandexgpt"
    names = {p.name for p in sub.glob("*.txt")}
    assert f"{rid}.txt" in names and "x.txt" not in names and "other.txt" not in names
    assert not (run / "explanations" / "x.txt").exists()
    assert explanation_for(run, rec)[1] == "yandexgpt"
    assert explain_run(run, explain=boom)["source"] == "mock"
    assert explanation_for(run, rec) == (rec.payload.reason, "reason")
    assert explain_run(run, explain=lambda items: {"zzz": "t"})["source"] == "mock"


def test_person4_text_untouched(run):
    rec = actionable(load_recommendations(run).items)[0]
    p4 = rec.model_copy(update={"payload": rec.payload.model_copy(update={"source": "person4"})})
    own = run / "explanations" / f"{rec.payload.recommendation_id}.txt"
    own.parent.mkdir(exist_ok=True)
    own.write_text("Своё", encoding="utf-8")
    explain_run(run, explain=fake)
    assert own.read_text(encoding="utf-8") == "Своё"
    explain_run(run, explain=boom)
    assert explanation_for(run, p4) == ("Своё", "person4")
