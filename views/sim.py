"""Page "Симулятор": in-browser player of the recorded simulation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from metro_control.events import banner_height, sim_events
from metro_control.sim_view import player_html, sim_payload
from metro_control.team_forecast import read_team_context
from metro_control.timeline import forecast_twin, frame, load_timeline
from metro_control.timeutil import to_msk

ctx = st.session_state["ctx"]
sim_dir, names = ctx.sim_dir, ctx.names


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


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
    cur = sim_dir
    hint = "Запустите: `uv run metro-control compare --scenario rail_surge`"
    if sim_dir is None:
        st.info("Нет записи симуляции (runs/compare-* не найден).")
        st.info(hint)
        return
    twin = forecast_twin(sim_dir)
    try:
        scen = str(json.loads((sim_dir / "manifest.json").read_text(encoding="utf-8"))["scenario"])
    except (OSError, KeyError, ValueError, TypeError):
        scen = sim_dir.name.removeprefix("compare-").removesuffix("-forecast").rsplit("-", 3)[0]
    choice = st.segmented_control(
        "Спрос",
        ["Факт", "Прогноз"],
        default="Факт",
        key="sim_demand",
        disabled=twin is None,
    )
    if twin is None:
        st.caption(
            f"нет прогона по прогнозу: `uv run metro-control compare --scenario {scen} "
            "--demand forecast`"
        )
    elif choice == "Прогноз":
        cur = twin
    tl, reason = load_timeline(cur)
    if tl is None:
        st.info(f"Нет записи симуляции ({reason}).")
        st.info(hint)
        return
    actions = read_actions(cur / "actions.jsonl")
    try:
        manifest = json.loads((cur / "manifest.json").read_text(encoding="utf-8"))
        policy_label = str(manifest["policy"])
    except (OSError, KeyError, ValueError, TypeError):
        manifest, policy_label = {}, "mock"
    if twin is not None and cur == twin:
        src = manifest.get("forecast", "mock") if isinstance(manifest, dict) else "mock"
        st.caption(f"Спрос = прогноз q50 на каждый 15-мин слот (источник: {src})")
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
    frame_ts = [_ts(frame(tl, "policy", k)["t"]) for k in range(tl.n_frames)]
    events = sim_events(cur, read_team_context(ctx.run_dir), frame_ts, names)
    payload = sim_payload(tl, names, actions, policy_label, events, cur)
    height = 1700 + banner_height(payload["banner"])
    components.html(player_html(payload), height=height, scrolling=False)


if ctx.bundle is None:
    st.warning(f"Каталог запуска не найден: {ctx.run_dir}")
    st.info(ctx.create_hint)
else:
    sim_tab()
