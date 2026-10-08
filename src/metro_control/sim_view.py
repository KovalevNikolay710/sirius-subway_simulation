"""Data for the in-browser simulator player: one JSON payload per compare run (no Streamlit)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from metro_control.screen import action_row
from metro_control.timeline import KPI_KEYS, VARIANTS, Timeline, frame
from metro_control.timeutil import to_msk

DIRS = ("north", "south")
SCENARIO_RU = {
    "rail_surge": "наплыв с вокзалов",
    "snowfall": "снегопад",
    "quiet_weekend": "спокойный выходной",
}
MONTHS_RU = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _date_ru(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {MONTHS_RU[d.month - 1]}"


def sim_payload(
    tl: Timeline, names: dict[str, str], actions: list[dict[str, Any]], policy_label: str
) -> dict[str, Any]:
    """Everything the player draws, indexed [variant][...][k] over all frames.

    Rows go from the north terminal (Девяткино) down to the south one, so `tl.stations`
    (south first) is reversed. Segment i joins rows i and i+1; `north` = i+1 -> i.
    """
    order = list(reversed(tl.stations))
    n = tl.n_frames
    segs = []
    for i in range(len(order) - 1):
        up, down = order[i], order[i + 1]
        segs.append(
            {
                "label": names.get(up, up),
                "tip": f"{names.get(up, up)} – {names.get(down, down)}",
                "north": f"{down}__{up}",
                "south": f"{up}__{down}",
            }
        )
    times = [f"{to_msk(_ts(frame(tl, 'policy', k)['t'])):%H:%M}" for k in range(n)]

    def seg_series(v: str, key: str, field: str) -> list[float | None]:
        out = []
        for k in range(n):
            s = frame(tl, v, k)["segments"].get(key)
            out.append(None if s is None else s[field])
        return out

    data: dict[str, Any] = {}
    for v in VARIANTS:
        fr = [frame(tl, v, k) for k in range(n)]
        data[v] = {
            "fill": {d: [seg_series(v, s[d], "fill") for s in segs] for d in DIRS},
            "left": {d: [seg_series(v, s[d], "left_behind") for s in segs] for d in DIRS},
            "queues": {
                d: [[f["queues"].get(st, {}).get(d, 0.0) for f in fr] for st in order] for d in DIRS
            },
            "kpi": {x: [f[x] for f in fr] for x in KPI_KEYS},
        }

    def moving(k: int) -> bool:
        return any(row[k] is not None for v in VARIANTS for d in DIRS for row in data[v]["fill"][d])

    service = [k for k in range(n) if moving(k)]
    first, last = (service[0], service[-1]) if service else (0, n - 1)
    acts = []
    for ac in actions:
        try:
            t = _ts(ac["as_of"])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        k = next((i for i in range(n) if _ts(frame(tl, "policy", i)["t"]) > t), n)
        if ac.get("status") == "source_error":
            what, target, status = "Ошибка источника", ac.get("source", "?"), ac.get("reason", "")
        else:
            what, target, status = action_row(ac, names)
        acts.append(
            {"k": k, "time": f"{to_msk(t):%H:%M}", "what": what, "target": target, "status": status}
        )
    return {
        "scenario": SCENARIO_RU.get(tl.scenario, tl.scenario),
        "date": _date_ru(tl.date),
        "policy": policy_label,
        "times": times,
        "first": first,
        "last": last,
        "stations": [names.get(s, s) for s in order],
        "segs": [{"label": s["label"], "tip": s["tip"]} for s in segs],
        "data": data,
        "actions": acts,
    }


PLAYER_HTML = Path(__file__).parent / "assets" / "sim_player.html"


def player_html(payload: dict[str, Any]) -> str:
    """The player page with the payload inlined (`</` escaped so data cannot close the script)."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return PLAYER_HTML.read_text(encoding="utf-8").replace("__PAYLOAD__", data)
