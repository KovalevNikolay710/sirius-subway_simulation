"""Dispatcher screen. Only reads run-dir packages and draws; logic lives in metro_control."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
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
from metro_control.timeline import (
    KPI_KEYS,
    Player,
    actions_until,
    compare_frame,
    find_sim_dir,
    frame,
    load_timeline,
    reset,
    segment_bands,
    series,
    step,
    tick,
    toggle,
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


def line_figure(view, tip_fn, height=720):
    """Line schematic: segments coloured by band; `tip_fn(row)` gives the hover suffix."""
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
            tip = f"{names[row['from_station']]} → {names[row['to_station']]}<br>{tip_fn(row)}"
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
        height=height,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False, range=[-1.6, 4.5]),
        yaxis=dict(visible=False),
        legend=dict(orientation="h", y=-0.02),
    )
    fig.add_annotation(x=-1, y=18.7, text="на север", showarrow=False)
    fig.add_annotation(x=1, y=18.7, text="на юг", showarrow=False)
    return fig


def forecast_tab():
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
            rr = {(r["from_station"], r["to_station"]): r["r"] for r in view.iter_rows(named=True)}

            def r_tip(row):
                v = rr[(row["from_station"], row["to_station"])]
                return "r = —" if v is None else "r = " + format(v, ".0%")

            fig = line_figure(view, r_tip)
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


def sim_tab():
    hint = "Запустите: `uv run metro-control compare --scenario rail_surge`"
    sim_dir = find_sim_dir(os.environ.get("METRO_SIM_DIR"), Path("runs"))
    if sim_dir is None:
        st.info("Нет записи симуляции (runs/compare-* не найден).")
        st.info(hint)
        return
    st.caption(str(sim_dir))
    tl, reason = load_timeline(sim_dir)
    if tl is None:
        st.info(f"Нет записи симуляции ({reason}).")
        st.info(hint)
        return
    actions = read_actions(sim_dir / "actions.jsonl")
    if (
        "player" not in st.session_state
        or st.session_state["player"].n_frames != tl.n_frames
        or st.session_state.get("sim_run_id") != tl.run_id
    ):
        st.session_state["player"] = Player(n_frames=tl.n_frames)
        st.session_state["sim_run_id"] = tl.run_id

    def act(fn):
        st.session_state["player"] = fn(st.session_state["player"])
        st.session_state["last_tick"] = time.monotonic()

    pl_ = st.session_state["player"]
    c1, c2, c3, _ = st.columns([1, 1, 1, 3])
    c1.button("Шаг +15 мин", on_click=act, args=(step,))
    c2.button("Пауза" if pl_.playing else "Пуск", on_click=act, args=(toggle,))
    c3.button("Сброс", on_click=act, args=(reset,))
    st.caption(
        "Политика: mock (демо-политика, не рекомендация ML). Воспроизведение записи compare."
    )
    try:
        mx = json.loads((sim_dir / "metrics.json").read_text(encoding="utf-8"))["payload"]
        st.caption(
            f"Итог дня ({tl.scenario}, {tl.date}): ожидание, пасс·мин — "
            f"без управления {mx['baseline']['wait_pax_min']:,.0f}, "
            f"с политикой {mx['policy']['wait_pax_min']:,.0f}"
        )
    except (OSError, KeyError, ValueError, TypeError):
        st.caption("metrics.json недоступен.")

    @st.fragment(run_every=1 if pl_.playing else None)
    def view():
        was = st.session_state["player"]
        if was.playing:
            st.session_state["player"], st.session_state["last_tick"] = tick(
                was, time.monotonic(), st.session_state.get("last_tick")
            )
            if not st.session_state["player"].playing:
                st.rerun()
        p = st.session_state["player"]
        k = p.k
        fp = frame(tl, "policy", k)
        t = datetime.fromisoformat(fp["t"].replace("Z", "+00:00"))
        st.subheader(f"{to_msk(t):%H:%M} МСК — кадр {k}/{tl.n_frames - 1}")
        a, b = st.columns([3, 2])
        with a:
            view_df = segment_bands(fp, tl.stations)
            fig = line_figure(
                view_df,
                lambda r: (
                    "нет остановок"
                    if r["fill"] is None
                    else f"заполнение {r['fill']:.0%}, остались {r['left_behind']:.0f}"
                ),
                height=640,
            )
            st.plotly_chart(fig, width="stretch")
        with b:
            cf = compare_frame(tl, k)
            labels = {
                "waiting": "Ждут на станциях",
                "denied": "Отказы в посадке",
                "wait_pax_min": "Ожидание, пасс·мин",
                "trains_in_service": "Поездов на линии",
            }
            st.dataframe(
                pl.DataFrame(
                    {
                        "Показатель": [labels[x] for x in KPI_KEYS],
                        "Без управления": [float(cf["baseline"][x]) for x in KPI_KEYS],
                        "Политика (mock)": [float(cf["policy"][x]) for x in KPI_KEYS],
                        "Δ": [float(cf["delta"][x]) for x in KPI_KEYS],
                    }
                ),
                hide_index=True,
            )
            sid = st.selectbox(
                "Станция (симулятор)",
                [s.id for s in stations],
                format_func=lambda i: names[i],
                key="sim_station",
            )
            zero = {"north": 0.0, "south": 0.0}
            qb = frame(tl, "baseline", k)["queues"].get(sid, zero)
            qp = fp["queues"].get(sid, zero)
            st.write(
                f"Очередь на север: {qb['north']:.0f} → {qp['north']:.0f} (без упр. → политика)"
            )
            st.write(f"Очередь на юг: {qb['south']:.0f} → {qp['south']:.0f}")
        ser = series(tl, k)
        fig3 = go.Figure()
        for v, nm in (("baseline", "без управления"), ("policy", "политика (mock)")):
            sv = ser.filter(ser["variant"] == v)
            fig3.add_trace(go.Scatter(x=sv["k"] * 15 / 60, y=sv["waiting"], name=nm, mode="lines"))
        fig3.update_layout(
            height=280,
            margin=dict(l=10, r=10, t=10, b=10),
            yaxis_title="ждут, чел.",
            xaxis=dict(title="часов от начала суток", range=[0, tl.n_frames * 15 / 60]),
        )
        st.plotly_chart(fig3, width="stretch")
        st.markdown("**Действия политики (mock)**")
        done = actions_until(actions, t)
        if not done:
            st.caption("Пока действий нет.")
        for ac in done:
            at_ = datetime.fromisoformat(ac["as_of"].replace("Z", "+00:00"))
            st.write(
                f"{to_msk(at_):%H:%M} — {ac['action']} → {ac['target']} "
                f"[{ac['status']}] {ac['reason']}"
            )

    view()


tab_fc, tab_sim = st.tabs(["Прогноз и рекомендация", "Симулятор"])
with tab_fc:
    forecast_tab()
with tab_sim:
    sim_tab()
