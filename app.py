"""Dispatcher screen shell: header, navigation, shared context. Pages live in views/."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import streamlit as st

from metro_control.figures import INK
from metro_control.line import load_line, load_lines
from metro_control.screen import load_bundle, source_statuses
from metro_control.timeline import find_sim_dir

line = load_line()
network = load_lines()
active = next(x for x in network if x.active)
if line.id != active.id:
    raise ValueError(f"line.json id {line.id!r} is not the active line {active.id!r} in lines.json")

st.set_page_config(page_title=f"Диспетчер: {line.name_ru}", layout="wide")
st.markdown(
    """<style>
[data-testid="stMainBlockContainer"], .block-container { padding-top: 1.2rem; }
[data-testid="stHeader"] { height: 0; background: transparent; }
[data-testid="stVerticalBlockBorderWrapper"] { background: #FFFFFF; border-radius: 10px; }
</style>""",
    unsafe_allow_html=True,
)


def roundel(x) -> str:
    base = (
        "display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;"
        f"border-radius:50%;background:{x.color};color:#fff;font-weight:700;font-size:16px;"
    )
    if x.active:
        style = base + f"box-shadow:0 0 0 2px #fff,0 0 0 4px {x.color};"
        return f'<span title="{x.name_ru}" style="{style}">{x.id}</span>'
    style = base + "opacity:.35;cursor:not-allowed;"
    return f'<span title="{x.name_ru}: нет данных" style="{style}">{x.id}</span>'


st.markdown(
    f"""<div style="margin-bottom:22px"><div style="font-size:26px;font-weight:700;
line-height:1.2;color:{INK}">Метро Петербурга</div>
<div style="display:flex;align-items:center;gap:10px;margin-top:8px">
{"".join(roundel(x) for x in network)}
<span style="margin-left:6px;font-size:18px;font-weight:600;color:{line.color}">
{line.name_ru}</span></div></div>""",
    unsafe_allow_html=True,
)

# An upload on the "Данные" page switches the screen to its run dir for this browser session.
run_dir = Path(st.session_state.get("run_dir") or os.environ.get("METRO_RUN_DIR", "runs/demo"))
sim_dir = (
    Path(st.session_state["sim_dir"])
    if st.session_state.get("sim_dir")
    else find_sim_dir(os.environ.get("METRO_SIM_DIR"), Path("runs"))
)
ICONS = {
    "ok": ":green[:material/check_circle:]",
    "info": ":blue[:material/info:]",
    "warning": ":orange[:material/warning:]",
    "error": ":red[:material/error:]",
}
bundle = load_bundle(run_dir) if run_dir.is_dir() else None
st.session_state["ctx"] = SimpleNamespace(
    run_dir=run_dir,
    sim_dir=sim_dir,
    line=line,
    names={s.id: s.name_ru for s in line.stations},
    stations=sorted(line.stations, key=lambda s: s.order),
    bundle=bundle,
    icons=ICONS,
    mock_icon=":grey[:material/info:]",
    create_hint=(
        "Загрузите данные на странице «Данные» или создайте демо: "
        "`uv run metro-control mock-bundle --out runs/demo`"
    ),
)

pg = st.navigation(
    [
        st.Page("views/forecast.py", title="Прогноз", icon=":material/insights:", default=True),
        st.Page("views/sim.py", title="Симулятор", icon=":material/train:"),
        st.Page("views/data.py", title="Данные", icon=":material/database:"),
        st.Page("views/schema.py", title="Схема метро", icon=":material/map:"),
    ],
    position="sidebar",
)

if bundle is not None:
    statuses = source_statuses(run_dir, bundle)
    worst = next(
        (lv for lv in ("error", "warning", "info") if any(s.level == lv for s in statuses)), "ok"
    )
    n_ok = sum(s.level == "ok" for s in statuses)
    with st.sidebar:
        st.markdown(
            f"{ICONS[worst]} Источники: {n_ok}/{len(statuses)} ok",
            help="\n\n".join(s.text for s in statuses),
        )
    for s in statuses:
        if s.level == "error":
            st.error(s.text)

pg.run()
