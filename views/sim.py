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
from metro_control.timeline import frame, load_timeline
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
    frame_ts = [_ts(frame(tl, "policy", k)["t"]) for k in range(tl.n_frames)]
    events = sim_events(sim_dir, read_team_context(ctx.run_dir), frame_ts, names)
    payload = sim_payload(tl, names, actions, policy_label, events)
    height = 1700 + banner_height(payload["banner"])
    components.html(player_html(payload), height=height, scrolling=False)


if ctx.bundle is None:
    st.warning(f"Каталог запуска не найден: {ctx.run_dir}")
    st.info(ctx.create_hint)
else:
    sim_tab()
