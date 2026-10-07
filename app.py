"""Dispatcher screen. Only reads run-dir packages and draws; logic lives in metro_control."""

from __future__ import annotations

import os
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from metro_control.line import load_line
from metro_control.screen import (
    BAND_COLORS,
    BAND_LABELS_RU,
    action_card,
    load_bundle,
    segment_view,
    station_series,
)
from metro_control.timeutil import to_msk

st.set_page_config(page_title="Диспетчер: Линия 1", layout="wide")
st.title("Линия 1 — экран диспетчера")

run_dir = Path(os.environ.get("METRO_RUN_DIR", "runs/demo"))
line = load_line()
names = {s.id: s.name_ru for s in line.stations}
stations = sorted(line.stations, key=lambda s: s.order)

if not run_dir.is_dir():
    st.warning(f"Каталог запуска не найден: {run_dir}")
    st.info("Создайте демо-данные: `uv run metro-control mock-bundle --out runs/demo`")
    st.stop()

bundle = load_bundle(run_dir)
with st.sidebar:
    st.subheader("Пакеты данных")
    st.caption(str(run_dir))
    for kind, res in bundle.items():
        if not res.ok:
            st.error(f"{kind}: ошибка — {res.reason}")
        elif res.package.data_mode == "mock":
            st.warning(f"{kind}: mock")
        else:
            st.success(f"{kind}: ok ({res.package.data_mode})")

for res in bundle.values():
    if not res.ok:
        st.warning(res.reason)

load_pkg = bundle["load"].package if bundle["load"].ok else None
fc_pkg = bundle["forecast"].package if bundle["forecast"].ok else None
ent_pkg = bundle["station_entries"].package if bundle["station_entries"].ok else None
rec_pkg = bundle["recommendation"].package if bundle["recommendation"].ok else None


def fmt(t) -> str:
    return f"{to_msk(t):%H:%M}"


left, right = st.columns([3, 2])

with left:
    st.subheader("Загрузка перегонов")
    if load_pkg is None:
        st.info("Нет данных о загрузке (load.json недоступен).")
    elif not load_pkg.payload:
        st.info("Пакет загрузки пуст.")
    else:
        slots = sorted({r.interval_start for r in load_pkg.payload})
        at = st.select_slider("Интервал (МСК)", options=slots, format_func=fmt)
        view = segment_view(load_pkg, at)
        xs = {"south": 1.0, "north": -1.0}
        y = {s.id: s.order for s in stations}
        fig = go.Figure()
        for b in ("low", "mid", "high", "none"):
            px, py, txt = [], [], []
            for row in view.filter(view["band"] == b).iter_rows(named=True):
                x = xs[row["direction"]]
                y0, y1 = y[row["from_station"]], y[row["to_station"]]
                px += [x, x, None]
                py += [y0, y1, None]
                r = row["r"]
                tip = (
                    f"{names[row['from_station']]} → {names[row['to_station']]}"
                    f"<br>r = {'—' if r is None else f'{r:.0%}'}"
                )
                txt += [tip, tip, None]
            fig.add_trace(
                go.Scatter(
                    x=px,
                    y=py,
                    mode="lines",
                    line=dict(color=BAND_COLORS[b], width=9),
                    name=BAND_LABELS_RU[b],
                    text=txt,
                    hoverinfo="text",
                )
            )
        fig.add_trace(
            go.Scatter(
                x=[0] * len(stations),
                y=[s.order for s in stations],
                mode="markers+text",
                marker=dict(size=9, color="#333"),
                text=[s.name_ru for s in stations],
                textposition="middle right",
                showlegend=False,
                hoverinfo="skip",
            )
        )
        fig.update_layout(
            height=720,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(visible=False, range=[-1.6, 4.5]),
            yaxis=dict(visible=False),
            legend=dict(orientation="h", y=-0.02),
        )
        fig.add_annotation(x=-1, y=18.7, text="на север", showarrow=False)
        fig.add_annotation(x=1, y=18.7, text="на юг", showarrow=False)
        st.plotly_chart(fig, width="stretch")

with right:
    st.subheader("Рекомендация")
    if rec_pkg is None:
        st.info("Нет рекомендации (recommendation.json недоступен).")
    else:
        card = action_card(rec_pkg)
        with st.container(border=True):
            if card["is_mock"]:
                st.caption("mock")
            st.markdown(f"### {card['title']}")
            st.write(f"**Цель:** {card['target']}")
            st.write(f"**Окно:** {card['window']} МСК")
            st.write(card["reason"])

    st.subheader("Вход на станции")
    sid = st.selectbox("Станция", [s.id for s in stations], format_func=lambda i: names[i], index=0)
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
                go.Scatter(x=tx, y=fcr["q90"], mode="lines", line=dict(width=0), showlegend=False)
            )
            fig2.add_trace(
                go.Scatter(
                    x=tx,
                    y=fcr["q10"],
                    mode="lines",
                    line=dict(width=0),
                    fill="tonexty",
                    fillcolor="rgba(214,69,69,0.2)",
                    name="q10–q90",
                )
            )
            fig2.add_trace(
                go.Scatter(
                    x=tx,
                    y=fcr["value"],
                    mode="lines",
                    line=dict(color="#d64545"),
                    name="прогноз q50",
                )
            )
        if fact.height:
            fig2.add_trace(
                go.Scatter(
                    x=[to_msk(t) for t in fact["interval_start"]],
                    y=fact["value"],
                    mode="lines",
                    line=dict(color="#1f77b4"),
                    name="факт",
                )
            )
        fig2.update_layout(
            height=340, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="входы за 15 мин"
        )
        st.plotly_chart(fig2, width="stretch")
