import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from metro_control.line import load_line, load_lines

ROOT = Path(__file__).resolve().parents[1]


def test_load_lines():
    lines = load_lines()
    assert [x.id for x in lines] == ["1", "2", "3", "4", "5"]
    assert [x.id for x in lines if x.active] == ["1"]


def test_lines_validation(tmp_path):
    d = json.loads((ROOT / "config" / "lines.json").read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(d))
    bad["lines"][1]["color"] = "blue"
    two = json.loads(json.dumps(d))
    two["lines"][1]["active"] = True
    for i, doc in enumerate((bad, two)):
        p = tmp_path / f"l{i}.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_lines(p)


def test_line_identity():
    line = load_line()
    assert line.name_ru == "Кировско-Выборгская линия"
    assert line.color == "#D6083B"


def _at(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("METRO_RUN_DIR", str(tmp_path / "none"))
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def test_header_and_pages(tmp_path, monkeypatch):
    at = _at(tmp_path, monkeypatch).run()
    assert not at.exception
    head = " ".join(m.value for m in at.markdown)
    assert "Метро Петербурга" in head
    assert head.count("нет данных") == 4
    for x in load_lines():
        assert f'title="{x.name_ru}' in head
    assert at.warning  # missing run_dir on the forecast page
    at.switch_page("views/sim.py").run()
    assert not at.exception
    at.switch_page("views/data.py").run()
    assert not at.exception
    assert any("Источники данных" in s.value for s in at.subheader)
    at.switch_page("views/schema.py").run()
    assert not at.exception
    assert any("скоро" in m.value for m in at.markdown)


def test_no_hardcoded_line_identity():
    for p in [ROOT / "app.py", *(ROOT / "views").glob("*.py")]:
        text = p.read_text(encoding="utf-8")
        assert "Кировско" not in text and "#D6083B" not in text, p


def test_line_id_matches_active_line():
    assert load_line().id == next(x.id for x in load_lines() if x.active)


def test_station_choice_survives_navigation(tmp_path, monkeypatch):
    from metro_control.cli import main

    run = tmp_path / "run"
    assert main(["mock-bundle", "--out", str(run)]) == 0
    monkeypatch.setenv("METRO_RUN_DIR", str(run))
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    sb = at.selectbox(key="s4_station")
    other = "avtovo"
    sb.select("avtovo").run()
    at.switch_page("views/sim.py").run()
    at.switch_page("views/forecast.py").run()
    assert at.selectbox(key="s4_station").value == other
