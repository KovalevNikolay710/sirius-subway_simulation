"""Dispatcher screen. Only reads run-dir packages and draws; logic lives in metro_control."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from metro_control.figures import (
    CALM_HI,
    DIFF_BANDS,
    HEAT_BANDS,
    INK,
    LINE1,
    MUTED,
    heatmap_figure,
    line_strip_figure,
    waiting_figure,
)
from metro_control.line import load_line
from metro_control.screen import (
    action_card,
    action_row,
    explanation_text,
    fmt_int,
    load_bundle,
    segment_view,
    source_statuses,
    station_series,
)
from metro_control.timeline import (
    Player,
    actions_until,
    blend_frames,
    find_sim_dir,
    frame,
    heat_grid,
    load_timeline,
    reset,
    segment_bands,
    series,
    step,
    toggle,
)
from metro_control.timeutil import to_msk

st.set_page_config(page_title="Диспетчер: Линия 1", layout="wide")
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
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Загрузка перегонов")
        st.caption("Прогнозная загрузка по данным load.json")
        if load_pkg is None:
            st.info("Нет данных о загрузке (load.json недоступен).")
        elif not load_pkg.payload:
            st.info("Пакет загрузки пуст.")
        else:
            slots = sorted({r.interval_start for r in load_pkg.payload})
            at = st.select_slider("Интервал (МСК)", options=slots, format_func=fmt)
            view = segment_view(load_pkg, at).with_columns(
                pl.col("r").alias("fill"), pl.lit(0.0).alias("left_behind")
            )
            st.plotly_chart(line_strip_figure(view, {}, names), width="stretch")

    with right:
        st.subheader("Рекомендация")
        if rec_pkg is None:
            st.info("Нет рекомендации (recommendation.json недоступен).")
        else:
            card = action_card(rec_pkg)
            with st.container(border=True):
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
                        fillcolor="rgba(47,93,140,0.18)",
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
                height=300,
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
    first_k = min(
        (g.ks[0] for g in (heat_grid(tl, "policy", d) for d in ("north", "south")) if g.ks),
        default=0,
    )
    if (
        "player" not in st.session_state
        or st.session_state["player"].n_frames != tl.n_frames
        or st.session_state.get("sim_run_id") != tl.run_id
    ):
        st.session_state["player"] = Player(n_frames=tl.n_frames, k=first_k)
        st.session_state["sim_run_id"] = tl.run_id

    def act(fn):
        st.session_state["player"] = fn(st.session_state["player"])
        st.session_state["sim_sub"] = 0
        st.session_state["last_tick"] = time.monotonic()

    def on_slider():
        st.session_state["player"] = replace(
            st.session_state["player"], k=slider_k(st.session_state["sim_k"]), playing=False
        )
        st.session_state["sim_sub"] = 0

    t0 = to_msk(_ts(frame(tl, "policy", 0)["t"])).replace(tzinfo=None)
    step_td = timedelta(minutes=tl.step_min)

    def slider_k(v: datetime) -> int:
        return max(0, min(round((v - t0) / step_td), tl.n_frames - 1))

    def back(p: Player) -> Player:
        return replace(p, k=max(first_k, p.k - 1), playing=False)

    def stop(p: Player) -> Player:
        return replace(reset(p), k=first_k)

    pl_ = st.session_state["player"]
    st.caption(
        f"Политика: {policy_label} (демо-политика, не рекомендация ML). "
        "Воспроизведение записи compare."
    )

    @st.fragment(run_every=1 / SUBSTEPS if pl_.playing else None)
    def view():
        was = st.session_state["player"]
        sub = st.session_state.get("sim_sub", 0)
        if was.playing:
            now_s, last = time.monotonic(), st.session_state.get("last_tick")
            if last is None or now_s - last >= 0.9 / SUBSTEPS:
                sub += 1
                if sub >= SUBSTEPS:
                    was, sub = step(was), 0
                st.session_state["player"], st.session_state["last_tick"] = was, now_s
                st.session_state["sim_sub"] = sub
        p = st.session_state["player"]
        if pl_.playing != p.playing:
            st.rerun()
        k = p.k
        frac = sub / SUBSTEPS if p.playing and k < tl.n_frames - 1 else 0.0

        # Player bar: ⏮ ▶/⏸ ■ ⏭ + time slider in one row.
        bar = st.columns([0.5, 0.5, 0.5, 0.5, 8], gap="small", vertical_alignment="bottom")
        bar[0].button(BTN_BACK, on_click=act, args=(back,), help="Назад на 15 минут")
        bar[1].button(
            BTN_PAUSE if p.playing else BTN_PLAY,
            on_click=act,
            args=(toggle,),
            help="Пауза" if p.playing else "Пуск",
            type="primary",
        )
        bar[2].button(BTN_STOP, on_click=act, args=(stop,), help="Стоп: к началу движения")
        bar[3].button(BTN_NEXT, on_click=act, args=(step,), help="Вперёд на 15 минут")
        st.session_state["sim_k"] = t0 + k * step_td
        bar[4].slider(
            "Время (МСК)",
            min_value=t0,
            max_value=t0 + (tl.n_frames - 1) * step_td,
            step=step_td,
            format="HH:mm",
            key="sim_k",
            on_change=on_slider,
        )
        variant = st.selectbox(
            "Режим",
            list(VARIANT_LABELS.values()),
            index=1,
            key="sim_variant",
        )
        vkey = next(v for v, lbl in VARIANT_LABELS.items() if lbl == variant)
        fp = frame(tl, "policy", k)
        t = _ts(fp["t"])
        now = f"{to_msk(t):%H:%M}"

        grids = {d: heat_grid(tl, vkey, d, names) for d in ("north", "south")}
        head = grids["north"]
        ph: str | float = now
        if head.ks:
            ph = min(max(k + frac, head.ks[0]), head.ks[-1]) - head.ks[0]
        bands = DIFF_BANDS if vkey == "diff" else HEAT_BANDS
        chips = " ".join(
            f'<span style="display:inline-block;width:14px;height:14px;background:{c};'
            f'border:1px solid #C9CED6;vertical-align:-2px;margin:0 4px 0 12px"></span>{lbl}'
            for lbl, c in bands
        )
        st.markdown(
            "<div style='font-size:0.9rem'>Каждая строка — перегон от станции к следующей, "
            "по горизонтали — время суток, цвет — заполнение поездов. "
            f"Чёрная линия — текущий момент.<br>{chips}</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            heatmap_figure(grids["north"], grids["south"], ph, vkey == "diff"),
            width="stretch",
            key="sim_heat",
        )

        st.subheader(f"Сейчас {now}")
        shown = "baseline" if vkey == "baseline" else "policy"
        k1 = min(k + 1, tl.n_frames - 1)
        fr = blend_frames(frame(tl, shown, k), frame(tl, shown, k1), frac)
        st.plotly_chart(
            line_strip_figure(
                segment_bands(fr, tl.stations),
                fr["queues"],
                names,
            ),
            width="stretch",
            key="sim_strip",
        )

        fb = blend_frames(frame(tl, "baseline", k), frame(tl, "baseline", k1), frac)
        fpb = blend_frames(fp, frame(tl, "policy", k1), frac)
        labels = {
            "waiting": "Ждут на станциях",
            "denied": "Отказы в посадке",
            "wait_pax_min": "Ожидание, пасс·мин",
        }
        for col, key in zip(st.columns(3), labels, strict=True):
            delta = fpb[key] - fb[key]
            col.metric(
                labels[key],
                fmt_int(fpb[key]),
                delta=(
                    fmt_int(delta, signed=True) + " к режиму без управления"
                    if round(delta)
                    else None
                ),
                delta_color="inverse",
            )

        st.plotly_chart(
            waiting_figure(series(tl, tl.n_frames - 1), k + frac),
            width="stretch",
            key="sim_wait",
        )

        sid = st.selectbox(
            "Станция",
            [s.id for s in stations],
            format_func=lambda i: names[i],
            key="sim_station",
        )
        zero = {"north": 0.0, "south": 0.0}
        qb = frame(tl, "baseline", k)["queues"].get(sid, zero)
        qp = fp["queues"].get(sid, zero)
        for label, d in (("север", "north"), ("юг", "south")):
            st.write(
                f"Очередь на {label}: {fmt_int(qb[d])} без управления, {fmt_int(qp[d])} с политикой"
            )

        st.markdown(f"#### Действия политики ({policy_label})")
        done = actions_until(actions, t)
        if not done:
            st.caption("Пока действий нет.")
        rows = []
        for ac in done:
            at_ = _ts(ac["as_of"])
            if ac.get("status") == "source_error":
                st.warning(
                    f"{to_msk(at_):%H:%M}: ошибка источника ({ac.get('source', '?')}): "
                    f"{ac.get('reason', '')}"
                )
                continue
            rows.append((f"{to_msk(at_):%H:%M}", *action_row(ac, names)))
        if rows:
            st.dataframe(
                pl.DataFrame(rows, schema=["Время", "Действие", "Цель", "Статус"], orient="row"),
                hide_index=True,
            )

    view()


tab_fc, tab_sim = st.tabs(["Прогноз и рекомендация", "Симулятор"])
with tab_fc:
    forecast_tab()
with tab_sim:
    sim_tab()
