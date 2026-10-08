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
from metro_control.ingest import ingest
from metro_control.line import load_line
from metro_control.screen import (
    action_card,
    explanation_for,
    fmt_int,
    load_bundle,
    load_recommendations,
    rec_evidence,
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

# An upload on the "Данные" tab switches the screen to its run dir for this browser session.
run_dir = Path(st.session_state.get("run_dir") or os.environ.get("METRO_RUN_DIR", "runs/demo"))
sim_dir = (
    Path(st.session_state["sim_dir"])
    if st.session_state.get("sim_dir")
    else find_sim_dir(os.environ.get("METRO_SIM_DIR"), Path("runs"))
)
line = load_line()
names = {s.id: s.name_ru for s in line.stations}
stations = sorted(line.stations, key=lambda s: s.order)

MOCK_ICON = ":grey[:material/info:]"
ICONS = {
    "ok": ":green[:material/check_circle:]",
    "info": ":blue[:material/info:]",
    "warning": ":orange[:material/warning:]",
    "error": ":red[:material/error:]",
}

DATA_TABLE = (
    (Path(__file__).parent / "src/metro_control/assets/data_guide.md")
    .read_text(encoding="utf-8")
    .split("\n", 1)[1]
)

DATA_NOTES = (
    "Загрузку перегонов приложение считает само: прогноз входов раскладывается по маршрутам "
    "из истории и делится на вместимость (поезда в интервале × 1458 мест). Время в файлах — "
    "UTC с указанием зоны, на экране — МСК; интервалы по 15 минут. Остальные файлы архива "
    "организаторов (графики, показатели, характеристики составов) приложение не читает: "
    "нужные из них числа уже внесены в `config/line.json` и `config/assumptions.json`."
)

UPLOAD_KINDS = {
    "entries": ("Входы по станциям (Excel)", ["xlsx"]),
    "forecast": ("Прогноз (json, csv, parquet)", ["json", "csv", "parquet"]),
    "recommendation": ("Рекомендации (json, jsonl)", ["json", "jsonl"]),
    "explanation": ("Объяснения (txt)", ["txt"]),
    "sim": ("Запись симуляции (json, jsonl)", ["json", "jsonl"]),
}


def data_tab(status_lines: list | None) -> None:
    st.subheader("Какие данные нужны")
    st.markdown(DATA_TABLE)
    st.caption(DATA_NOTES)

    st.subheader("Сейчас на экране")
    st.markdown(f"Пакеты: `{run_dir}`  \nСимуляция: `{sim_dir if sim_dir else 'нет записи'}`")
    for line_ in status_lines or []:
        st.markdown(f"{ICONS.get(line_.level, '')} {line_.text}")
    overridden = st.session_state.get("run_dir") or st.session_state.get("sim_dir")
    if overridden and st.button("Вернуться к демо-данным", icon=":material/undo:"):
        st.session_state.pop("run_dir", None)
        st.session_state.pop("sim_dir", None)
        st.session_state.pop("ingest_notes", None)
        st.rerun()

    st.subheader("Загрузить")
    archive = st.file_uploader(
        "Архив целиком (.zip): архив организаторов, пакет команды или всё вместе",
        type=["zip"],
        key="up_zip",
    )
    singles: dict[str, list] = {}
    with st.expander("Или файлы по отдельности"):
        for kind, (label, types) in UPLOAD_KINDS.items():
            singles[kind] = st.file_uploader(
                label, type=types, accept_multiple_files=True, key=f"up_{kind}"
            )
    if st.button("Загрузить и показать", type="primary", icon=":material/upload:"):
        files, kinds = [], {}
        if archive is not None:
            files.append((archive.name, archive.getvalue()))
        for kind, items in singles.items():
            for f in items or []:
                name = f.name
                if kind == "explanation" and not name.lower().startswith("explanation"):
                    name = f"explanations/{name}"  # per-recommendation text, named by its id
                files.append((name, f.getvalue()))
                kinds[name] = kind
        if not files:
            st.warning("Выберите архив или файлы.")
        else:
            try:
                with st.spinner("Разбираю файлы…"):
                    rep = ingest(
                        files,
                        Path("runs"),
                        kinds=kinds,
                        default_entries=Path("data/processed/station_entries.parquet"),
                    )
            except ValueError as e:
                st.error(str(e))
            else:
                if rep.run_dir is not None and (rep.run_dir / "load.json").is_file():
                    st.session_state["run_dir"] = str(rep.run_dir)
                if rep.sim_dir is not None:
                    st.session_state["sim_dir"] = str(rep.sim_dir)
                st.session_state["ingest_notes"] = [(n.level, n.file, n.text) for n in rep.notes]
                st.rerun()
    notes = st.session_state.get("ingest_notes")
    if notes:
        st.markdown("**Результат последней загрузки**")
        for level, file, text in notes:
            where = f"`{file.split('/')[-1]}`: " if file else ""
            st.markdown(f"{ICONS.get(level, '')} {where}{text}")


if not run_dir.is_dir():
    st.warning(f"Каталог запуска не найден: {run_dir}")
    st.info(
        "Загрузите данные ниже или создайте демо: "
        "`uv run metro-control mock-bundle --out runs/demo`"
    )
    data_tab(None)
    st.stop()

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
recs = load_recommendations(run_dir, bundle["recommendation"])


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt(t) -> str:
    return f"{to_msk(t):%H:%M}"


def evidence_figure(ev: dict) -> go.Figure:
    """Bars of the target segment's forecast load per slot, threshold line, window shaded."""
    colors = [
        "#E9ECEF"
        if r is None
        else "#CFE3F3"
        if r < 0.5
        else CALM_HI
        if r <= ev["r_off"]
        else "#EA7125"
        if r <= ev["r_on"]
        else "#D6083B"
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
            fillcolor="#EA7125",
            opacity=0.1,
            line_width=0,
            annotation_text="окно",
            annotation_position="top left",
        )
    fig.add_hline(
        y=ev["r_on"] * 100,
        line=dict(color="#D6083B", dash="dash", width=1.5),
        annotation_text=f"порог {ev['r_on']:.0%}",
        annotation_position="top right",
    )
    fig.update_layout(
        height=190,
        margin=dict(l=10, r=10, t=24, b=10),
        yaxis_title="загрузка, %",
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
        for rec in recs.items:
            card = action_card(rec)
            marks.append(
                dict(
                    card, target_id=rec.payload.target, start=rec.payload.start, end=rec.payload.end
                )
            )
        components.html(
            load_html(load_payload(load_pkg, names, marks)), height=420, scrolling=False
        )

    with st.container(border=True):
        st.subheader(f"Рекомендации :blue-badge[{len(recs.items)}]")
        for problem in recs.problems:
            st.warning(problem)
        if not recs.items:
            st.info("Нет рекомендаций (recommendation.json и recommendations.jsonl недоступны).")
        for n, rec in enumerate(recs.items, 1):
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


tab_fc, tab_sim, tab_data = st.tabs(["Прогноз и рекомендации", "Симулятор", "Данные"])
with tab_fc:
    forecast_tab()
with tab_sim:
    sim_tab()
with tab_data:
    data_tab(source_statuses(run_dir, bundle))
