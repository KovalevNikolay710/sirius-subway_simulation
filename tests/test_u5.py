import json
from pathlib import Path

from metro_control.line import load_lines
from metro_control.screen import header_html
from metro_control.sim_view import LLM_BANNER_H, llm_banner_h, player_html, sim_payload

ROOT = Path(__file__).resolve().parents[1]


def _payload(result, sim_dir=None, rec_id="r1"):
    tl = result.timeline
    acts = [
        {
            "as_of": tl.frames["policy"][12]["t"],
            "action": "remove_train",
            "target": f"{tl.stations[1]}__{tl.stations[0]}",
            "status": "applied",
            "recommendation_id": rec_id,
            "reason": "короткая причина",
        }
    ]
    return sim_payload(tl, {}, acts, "mock", sim_dir=sim_dir)


def test_player_demand_select(result):
    p = _payload(result)
    html = player_html(p)
    assert 'id="demand"' in html
    assert "disabled" in html.split('id="demand"')[1].split("</select>")[0]
    twin = dict(p, policy="twin-run-marker")
    html2 = player_html(p, twin)
    assert 'id="demand"' in html2 and html2.count("twin-run-marker") == 1
    assert "disabled" not in html2.split('id="demand"')[1].split("</select>")[0]


def test_llm_text_in_payload(result, tmp_path):
    assert _payload(result)["actions"][0]["llm_text"] == ""
    d = tmp_path / "explanations" / "yandexgpt"
    d.mkdir(parents=True)
    (d / "r1.txt").write_text("Полный текст", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"source": "yandexgpt"}))
    a = _payload(result, tmp_path)["actions"][0]
    assert a["llm_text"] == "Полный текст" and a["llm"] and a["reason"] == "Полный текст"
    assert llm_banner_h(_payload(result, tmp_path)) == LLM_BANNER_H
    assert llm_banner_h(_payload(result)) == 0


def test_lines():
    ls = load_lines()
    assert [x.id for x in ls] == ["1", "2", "3", "4", "5", "6"]
    assert [x.active for x in ls].count(True) == 1 and ls[0].active
    assert len({x.color for x in ls}) == 6


def test_header_html():
    ls = load_lines()
    h = header_html(ls, ls[0])
    assert h.count('role="option"') == 6 and h.count('aria-disabled="true"') == 5
    assert h.split('class="line-name"')[1].count(ls[0].name_ru) == 1
    assert h.count('class="line-name"') == 1 and "margin-left:16px" in h


def test_sim_page_has_no_caption():
    src = (ROOT / "views" / "sim.py").read_text(encoding="utf-8")
    assert "st.caption" not in src and "segmented_control" not in src
