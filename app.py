"""Dispatcher screen. Only reads run-dir packages and draws; logic lives in metro_control."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from metro_control.figures import (
    CALM_HI,
    INK,
    LINE1,
    MUTED,
)
from metro_control.line import load_line
from metro_control.screen import (
    action_card,
    explanation_text,
    load_bundle,
    source_statuses,
    station_series,
)
from metro_control.sim_view import load_html, load_payload, player_html, sim_payload
from metro_control.timeline import (
    find_sim_dir,
    load_timeline,
)
from metro_control.timeutil import to_msk

st.set_page_config(page_title="Диспетчер: Линия 1", layout="wide")
# Less empty space above the title; bordered containers read as dashboard panels.
st.markdown(
    """<style>
[data-testid="stMainBlockContainer"], .block-container { padding-top: 1.2rem; }
[data-testid="stHeader"] { height: 0; background: transparent; }
[data-testid="stVerticalBlockBorderWrapper"] { background: #FFFFFF; border-radius: 10px; }
</style>""",
    unsafe_allow_html=True,
)
st.markdown(
    f"""<div style="display:flex;align-items:center;gap:14px;margin-bottom:4px">
<span style="display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;
border-radius:50%;background:{LINE1};color:#fff;font-weight:700;font-size:22px">1</span>
<div><div style="font-size:26px;font-weight:700;line-height:1.1;color:{INK}">
Кировско-Выборгская линия</div>
<div style="color:{MUTED};font-size:14px">Экран диспетчера</div></div></div>""",
    unsafe_allow_html=True,
)

run_dir = Path(os.environ.get("METRO_RUN_DIR", "runs/demo"))
sim_dir = find_sim_dir(os.environ.get("METRO_SIM_DIR"), Path("runs"))
line = load_line()
names = {s.id: s.name_ru for s in line.stations}
stations = sorted(line.stations, key=lambda s: s.order)

if not run_dir.is_dir():
    st.warning(f"Каталог запуска не найден: {run_dir}")
    st.info("Создайте демо-данные: `uv run metro-control mock-bundle --out runs/demo`")
    st.stop()

MOCK_ICON = ":grey[:material/info:]"
ICONS = {
    "ok": ":green[:material/check_circle:]",
    "info": ":blue[:material/info:]",
    "warning": ":orange[:material/warning:]",
    "error": ":red[:material/error:]",
}
bundle = load_bundle(run_dir)
with st.sidebar:
    st.subheader("Источники данных")
    for line_ in source_statuses(run_dir, bundle):
        mock = line_.level == "warning" and line_.text.endswith(": mock")
        st.markdown(f"{MOCK_ICON if mock else ICONS[line_.level]} {line_.text}")
    st.caption(f"Данные: {run_dir}")
    st.caption(f"Симуляция: {sim_dir if sim_dir is not None else 'нет записи'}")

for line_ in source_statuses(run_dir, bundle):
    if line_.level == "error":
        st.error(line_.text)

load_pkg = bundle["load"].package if bundle["load"].ok else None
fc_pkg = bundle["forecast"].package if bundle["forecast"].ok else None
ent_pkg = bundle["station_entries"].package if bundle["station_entries"].ok else None
rec_pkg = bundle["recommendation"].package if bundle["recommendation"].ok else None


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt(t) -> str:
    return f"{to_msk(t):%H:%M}"


def forecast_tab():
    if load_pkg is None:
        st.info("Нет данных о загрузке (load.json недоступен).")
    elif not load_pkg.payload:
        st.info("Пакет загрузки пуст.")
    else:
        rec = None
        if rec_pkg is not None:
            card = action_card(rec_pkg)
            rec = dict(
                card,
                target_id=rec_pkg.payload.target,
                start=rec_pkg.payload.start,
                end=rec_pkg.payload.end,
            )
        components.html(load_html(load_payload(load_pkg, names, rec)), height=455, scrolling=False)

    left, right = st.columns(2, gap="medium")

    with left, st.container(border=True, height=430):
        st.subheader("Рекомендация")
        if rec_pkg is None:
            st.info("Нет рекомендации (recommendation.json недоступен).")
        else:
            card = action_card(rec_pkg)
            with st.container():
                st.markdown(f"#### {card['title']}")
                if card["is_mock"]:
                    st.caption(":grey[:material/info:] mock, демо-рекомендация")
                st.write(f"**Цель:** {card['target']}")
                st.write(f"**Окно:** {card['window']} МСК")
                text, origin = explanation_text(run_dir, rec_pkg)
                st.write(text)
                st.caption(
                    "Объяснение: человек 4" if origin == "person4" else "Причина из рекомендации"
                )

    with right, st.container(border=True, height=430):
        st.subheader("Вход на станции")
        sid = st.selectbox(
            "Станция",
            [s.id for s in stations],
            format_func=lambda i: names[i],
            index=0,
            key="s4_station",
        )
        if fc_pkg is None and ent_pkg is None:
            st.info("Нет данных для графика (forecast.json и entries.json недоступны).")
        else:
            ser = station_series(ent_pkg, fc_pkg, sid)
            fact = ser.filter(ser["kind"] == "fact")
            fcr = ser.filter(ser["kind"] == "forecast")
            fig2 = go.Figure()
            if fcr.height:
                tx = [to_msk(t) for t in fcr["interval_start"]]
                fig2.add_trace(
                    go.Scatter(
                        x=tx, y=fcr["q90"], mode="lines", line=dict(width=0), showlegend=False
                    )
                )
                fig2.add_trace(
                    go.Scatter(
                        x=tx,
                        y=fcr["q10"],
                        mode="lines",
                        line=dict(width=0),
                        fill="tonexty",
                        fillcolor="rgba(0,120,201,0.16)",
                        name="q10–q90",
                    )
                )
                fig2.add_trace(
                    go.Scatter(
                        x=tx,
                        y=fcr["value"],
                        mode="lines",
                        line=dict(color=CALM_HI, width=2),
                        name="прогноз q50",
                    )
                )
            if fact.height:
                fig2.add_trace(
                    go.Scatter(
                        x=[to_msk(t) for t in fact["interval_start"]],
                        y=fact["value"],
                        mode="lines",
                        line=dict(color=MUTED, width=1.5),
                        name="факт",
                    )
                )
            fig2.update_layout(
                height=250,
                margin=dict(l=10, r=10, t=10, b=10),
                yaxis_title="входы за 15 мин",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="Golos Text, sans-serif", color=INK),
                legend=dict(orientation="h", y=1.15),
                xaxis=dict(tickformat="%H:%M"),
                hoverlabel=dict(font=dict(family="Golos Text, sans-serif")),
            )
            fig2.update_traces(xhoverformat="%H:%M")
            st.plotly_chart(fig2, width="stretch")


def read_actions(path: Path) -> list[dict]:
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


SUBSTEPS = 4  # display sub-steps per 15-min frame while playing (smooth motion)
BTN_BACK, BTN_PLAY, BTN_PAUSE, BTN_STOP, BTN_NEXT = (
    ":material/skip_previous:",
    ":material/play_arrow:",
    ":material/pause:",
    ":material/stop:",
    ":material/skip_next:",
)

VARIANT_LABELS = {
    "baseline": "Без управления",
    "policy": "С политикой (mock)",
    "diff": "Разница",
}


def sim_tab():
    hint = "Запустите: `uv run metro-control compare --scenario rail_surge`"
    if sim_dir is None:
        st.info("Нет записи симуляции (runs/compare-* не найден).")
        st.info(hint)
        return
    tl, reason = load_timeline(sim_dir)
    if tl is None:
        st.info(f"Нет записи симуляции ({reason}).")
        st.info(hint)
        return
    actions = read_actions(sim_dir / "actions.jsonl")
    try:
        policy_label = str(
            json.loads((sim_dir / "manifest.json").read_text(encoding="utf-8"))["policy"]
        )
    except (OSError, KeyError, ValueError, TypeError):
        policy_label = "mock"
    for ac in actions:
        if ac.get("status") == "source_error":
            try:
                when = f"{to_msk(_ts(ac['as_of'])):%H:%M} МСК"
            except (KeyError, TypeError, ValueError, AttributeError):
                when = "?"
            st.warning(
                f"{when}: ошибка источника ({ac.get('source', '?')}, политика {policy_label}): "
                f"{ac.get('reason', '')}. "
                "Шаг политики пропущен, симуляция продолжилась."
            )
    payload = sim_payload(tl, names, actions, policy_label)
    components.html(player_html(payload), height=1700, scrolling=False)


tab_fc, tab_sim = st.tabs(["Прогноз и рекомендация", "Симулятор"])
with tab_fc:
    forecast_tab()
with tab_sim:
    sim_tab()
