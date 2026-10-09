"""Dispatcher screen shell: header, navigation, shared context. Pages live in views/."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import streamlit as st

from metro_control.line import load_line, load_lines
from metro_control.screen import header_html, load_bundle, source_statuses
from metro_control.timeline import find_sim_dir

line = load_line()
network = load_lines()
active = next(x for x in network if x.active)
if line.id != active.id:
    raise ValueError(f"line.json id {line.id!r} is not the active line {active.id!r} in lines.json")

st.set_page_config(page_title=f"Диспетчер: {line.name_ru}", layout="wide")
st.markdown(
    """<style>
[data-testid="stMainBlockContainer"], .block-container { padding: 1.2rem 1.5rem 3rem 1.5rem; }
[data-testid="stHeader"] { height: 0; background: transparent; }
[data-testid="stVerticalBlockBorderWrapper"] { background: #FFFFFF; border-radius: 10px; }
/* sidebar: hidden at rest, a 16 px handle stays at the left edge; hover / focus slides it in */
[data-testid="stSidebar"] { position: fixed !important; left: 0; top: 0; bottom: 0; z-index: 1000;
  min-width: 0 !important; transform: translateX(calc(-100% + 16px)) !important; visibility: visible !important;
  transition: transform .22s ease-out, box-shadow .22s ease-out; box-shadow: none; }
[data-testid="stSidebar"]:hover, [data-testid="stSidebar"]:has(:focus-visible) {
  transform: translateX(0) !important; box-shadow: 6px 0 24px rgba(27,36,48,.18); }
[data-testid="stSidebar"]::after { content: "\\2630"; position: absolute; right: 0; top: 0; bottom: 0; width: 16px;
  display: flex; align-items: center; justify-content: center; font-size: 12px; color: #5B6675;
  background: #EEF1F4; transition: opacity .15s; pointer-events: none; }
[data-testid="stSidebar"]:hover::after, [data-testid="stSidebar"]:has(:focus-visible)::after { opacity: 0; }
[data-testid="stSidebarCollapseButton"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"] { display: none !important; }
[data-testid="stSidebarContent"] { display: flex; flex-direction: column; height: 100%; padding-right: 16px; }
[data-testid="stSidebarUserContent"] { margin-top: auto; }
</style>""",  # noqa: E501
    unsafe_allow_html=True,
)

st.markdown(header_html(network, line), unsafe_allow_html=True)

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
