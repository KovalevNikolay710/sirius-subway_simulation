"""Page "Прогноз": load player, recommendations, station entries."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from metro_control.figures import CALM_HI, INK, MUTED, OVER, TIGHT
from metro_control.screen import (
    action_card,
    actionable,
    explanation_for,
    fmt_int,
    load_recommendations,
    rec_evidence,
    station_series,
)
from metro_control.sim_view import load_html, load_payload
from metro_control.timeutil import to_msk

ctx = st.session_state["ctx"]
if ctx.bundle is None:
    st.warning(f"Каталог запуска не найден: {ctx.run_dir}")
    st.info(ctx.create_hint)
    st.stop()

run_dir, names, stations, bundle = ctx.run_dir, ctx.names, ctx.stations, ctx.bundle
load_pkg = bundle["load"].package if bundle["load"].ok else None
fc_pkg = bundle["forecast"].package if bundle["forecast"].ok else None
ent_pkg = bundle["station_entries"].package if bundle["station_entries"].ok else None
recs = load_recommendations(run_dir, bundle["recommendation"])


def evidence_figure(ev: dict) -> go.Figure:
    """Bars of the target segment's forecast load per slot, threshold line, window shaded."""
    colors = [
        "#E9ECEF"
        if r is None
        else "#CFE3F3"
        if r < 0.5
        else CALM_HI
        if r <= ev["r_off"]
        else TIGHT
        if r <= ev["r_on"]
        else OVER
        for r in ev["r"]
    ]
    fig = go.Figure(
        go.Bar(
            x=ev["times"],
            y=[None if r is None else r * 100 for r in ev["r"]],
            marker_color=colors,
            hovertemplate="%{x}: загрузка %{y:.0f} %<extra></extra>",
        )
    )
    win = [t for t, w in zip(ev["times"], ev["in_window"], strict=True) if w]
    if win:
        fig.add_vrect(
            x0=win[0],
            x1=win[-1],
            fillcolor=TIGHT,
            opacity=0.1,
            line_width=0,
            annotation_text="окно",
            annotation_position="top left",
        )
    if ev["r_on"] <= ev["y_max"]:
        fig.add_hline(
            y=ev["r_on"] * 100,
            line=dict(color=OVER, dash="dash", width=1.5),
            annotation_text=f"порог {ev['r_on']:.0%}",
            annotation_position="top right",
        )
    fig.update_layout(
        height=190,
        margin=dict(l=10, r=10, t=24, b=10),
        yaxis=dict(title="загрузка, %", range=[0, ev["y_max"] * 100]),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        font=dict(family="Golos Text, sans-serif", color=INK, size=11),
        xaxis=dict(type="category"),
    )
    return fig


def forecast_tab():
    if load_pkg is None:
        st.info("Нет данных о загрузке (load.json недоступен).")
    elif not load_pkg.payload:
        st.info("Пакет загрузки пуст.")
    else:
        marks = []
        for rec in actionable(recs.items):
            card = action_card(rec)
            marks.append(
                dict(
                    card, target_id=rec.payload.target, start=rec.payload.start, end=rec.payload.end
                )
            )
        components.html(
            load_html(load_payload(load_pkg, names, marks)), height=392, scrolling=False
        )

    todo = actionable(recs.items)
    if todo or recs.problems:
        with st.container(border=True):
            st.subheader(f"Рекомендации :blue-badge[{len(todo)}]")
            for problem in recs.problems:
                st.warning(problem)
            for n, rec in enumerate(todo, 1):
                card = action_card(rec)
                ev = rec_evidence(rec, load_pkg)
                peak = ev["peak"] if ev else None
                tail = f", пик {peak['r']:.0%}" if peak and peak["r"] is not None else ""
                with st.expander(
                    f"{n}. {card['window']}  {card['title']}: {card['target']}{tail}",
                    expanded=n == 1,
                ):
                    text, origin = explanation_for(run_dir, rec)
                    st.write(text)
                    tags = [":violet-badge[человек 4]"] if origin == "person4" else []
                    if card["is_mock"]:
                        tags.append(":gray-badge[mock]")
                    if tags:
                        st.markdown(" ".join(tags))
                    if ev is None:
                        continue
                    if peak:
                        st.markdown(
                            f"Пик **{peak['r']:.0%}** в {peak['time']}: "
                            f"{fmt_int(peak['demand'])} пассажиров при вместимости "
                            f"{fmt_int(peak['capacity'])} ({peak['departures']} поездов), "
                            f"порог {ev['r_on']:.0%}."
                        )
                    st.plotly_chart(evidence_figure(ev), width="stretch", key=f"ev_{n}")

    with st.container(border=True):
        st.subheader("Вход на станции")
        # widget state is dropped when the page is left; re-seed from a plain key
        if "s4_station" not in st.session_state:
            st.session_state["s4_station"] = st.session_state.get("_s4_station", stations[0].id)
        sid = st.selectbox(
            "Станция",
            [s.id for s in stations],
            format_func=lambda i: names[i],
            key="s4_station",
        )
        st.session_state["_s4_station"] = sid
        if fc_pkg is not None and any(
            r.model_version == "no_data" for r in fc_pkg.payload.rows if r.station_id == sid
        ):
            st.caption("Нет данных в прогнозе команды — показана норма по истории")
        if fc_pkg is None and ent_pkg is None:
            st.info("Нет данных для графика (forecast.json и entries.json недоступны).")
        else:
            ser = station_series(ent_pkg, fc_pkg, sid)
            fact = ser.filter(ser["kind"] == "fact")
            fcr = ser.filter(ser["kind"] == "forecast")
            fig2 = go.Figure()
            if fcr.height:
                tx = [to_msk(t) for t in fcr["ts"]]
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
                        x=[to_msk(t) for t in fact["ts"]],
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


forecast_tab()
