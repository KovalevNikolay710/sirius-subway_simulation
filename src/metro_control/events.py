"""Why the load rises: weather / event banner, per-station reasons and anomaly marks (no Streamlit).

Source: person 2's `team_context.json` (A1) and, for the simulator, the scenario overlay from
`config/assumptions.json` (assumption: demo event, uncalibrated).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from metro_control.dayrun import load_assumption_items
from metro_control.timeutil import to_msk

WEATHER_PREFIXES = ("fc_", "weather", "precip", "temp", "snow")
MAX_ITEMS = 4
SCENARIO_NOTE = "сценарий (допущение)"
HOUR = timedelta(hours=1)
MINUS = "−"
NBSP = " "


def effect_ru(pct: float | None) -> str:
    if pct is None:
        return ""
    r = int(math.floor(abs(pct) + 0.5)) * (1 if pct >= 0 else -1)
    sign = "+" if r > 0 else MINUS if r < 0 else ""
    return f"{sign}{abs(r)}{NBSP}%"


def _ts(s: Any) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d


def _explanations(ctx: dict | None) -> list[dict]:
    if not isinstance(ctx, dict):
        return []
    return [e for e in ctx.get("explanations") or [] if isinstance(e, dict)]


def _reasons(e: dict) -> list[dict]:
    return [r for r in e.get("reasons") or [] if isinstance(r, dict) and r.get("text")]


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) else None


def _stations_word(n: int) -> str:
    last = n % 10
    word = "станции" if last in (2, 3, 4) and n % 100 not in (12, 13, 14) else "станций"
    if last == 1 and n % 100 != 11:
        word = "станция"
    return f"{n} {word}"


def team_banner(ctx: dict | None, names: dict[str, str]) -> dict | None:
    if not isinstance(ctx, dict):
        return None
    groups: dict[str, dict] = {}
    for e in _explanations(ctx):
        for r in _reasons(e):
            g = groups.setdefault(
                r["text"], {"feature": str(r.get("feature", "")), "eff": None, "st": set()}
            )
            g["st"].add(str(e.get("station_id", "")))
            eff = _num(r.get("effect_pct"))
            if eff is not None and (g["eff"] is None or eff > g["eff"]):
                g["eff"] = eff
    items = []
    for text, g in groups.items():
        weather = g["feature"].startswith(WEATHER_PREFIXES)
        sts = sorted(g["st"])
        where = names.get(sts[0], sts[0]) if len(sts) == 1 else _stations_word(len(sts))
        items.append(
            {
                "kind": "weather" if weather else "event",
                "text": text,
                "effect": effect_ru(g["eff"]),
                "where": where,
                "_eff": g["eff"] if g["eff"] is not None else float("-inf"),
            }
        )
    items.sort(key=lambda i: (i["kind"] != "weather", -i["_eff"]))
    items = [{k: v for k, v in i.items() if k != "_eff"} for i in items[:MAX_ITEMS]]

    meta = ctx.get("meta") if isinstance(ctx.get("meta"), dict) else {}
    weather_meta = meta.get("weather") if isinstance(meta.get("weather"), dict) else {}
    warnings = [
        w
        for w in (meta.get("warning"), weather_meta.get("warning"), meta.get("context"))
        if isinstance(w, str) and w.strip()
    ]
    if not items and not warnings:
        return None
    mock = meta.get("model") == "mock" or str(meta.get("model_version", "")).startswith("mock")
    return {"mock": mock, "source": "прогноз команды", "items": items, "warnings": warnings}


def station_reasons(ctx: dict | None, slots: list[datetime]) -> dict[str, list[list[str]]]:
    """station -> per slot texts; an explanation at hour H covers slots in [H, H+1h)."""
    out: dict[str, list[list[str]]] = {}
    for e in _explanations(ctx):
        h = _ts(e.get("ts"))
        sid = e.get("station_id")
        if h is None or not isinstance(sid, str):
            continue
        texts = [f"{r['text']} {effect_ru(_num(r.get('effect_pct')))}".strip() for r in _reasons(e)]
        rows = out.setdefault(sid, [[] for _ in slots])
        for i, t in enumerate(slots):
            if h <= t < h + HOUR:
                rows[i].extend(x for x in texts if x not in rows[i])
    return out


def anomaly_slots(rows: Iterable[Any]) -> dict[str, set[datetime]]:
    """Forecast rows (station_id, ts, is_anomaly) -> station -> flagged quarter starts."""
    out: dict[str, set[datetime]] = {}
    for r in rows:
        if getattr(r, "is_anomaly", False):
            out.setdefault(r.station_id, set()).add(r.ts)
    return out


def _on_date(ctx: dict | None, day: date) -> bool:
    for e in _explanations(ctx):
        t = _ts(e.get("ts"))
        if t is not None and to_msk(t).date() == day:
            return True
    return False


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _scenario(sim_dir: Path | str) -> tuple[str, dict | None]:
    try:
        name = str(json.loads((Path(sim_dir) / "manifest.json").read_text("utf-8"))["scenario"])
        spec = load_assumption_items()["scenarios"]["value"].get(name)
    except (OSError, KeyError, ValueError, TypeError):
        return "", None
    return name, spec if isinstance(spec, dict) else None


def banner_height(banner: dict | None) -> int:
    """Iframe growth (px) for a banner: one row of chips, a second one for warnings."""
    if not banner:
        return 0
    return 56 + (22 if banner.get("warnings") else 0)


def sim_events(
    sim_dir: Path | str,
    ctx: dict | None,
    frame_ts: list[datetime],
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    names = names or {}
    n = len(frame_ts)
    _, spec = _scenario(sim_dir)
    ov = spec.get("overlay") if spec else None
    items: list[dict] = []
    anomaly: dict[str, list[bool]] = {}
    reasons: dict[str, list[list[str]]] = {}
    if isinstance(ov, dict):
        if ov.get("type") == "surge":
            lo, hi = _hm(ov["start_msk"]), _hm(ov["end_msk"])
            mins = [to_msk(t).hour * 60 + to_msk(t).minute for t in frame_ts]
            inside = [(lo <= m < hi) if lo <= hi else (m >= lo or m < hi) for m in mins]
            sts = list(ov.get("stations") or [])
            factor = ov.get("factor")
            eff = f"×{factor:g}" if isinstance(factor, int | float) else ""
            announced = spec.get("announced", True)
            (items if announced else []).append(
                {
                    "kind": "event",
                    "text": "наплыв с вокзалов",
                    "effect": eff,
                    "where": ", ".join(names.get(s, s) for s in sts),
                    "window": f"{ov['start_msk']}–{ov['end_msk']}",
                    "note": SCENARIO_NOTE,
                }
            )
            for s in sts:
                anomaly[s] = list(inside)
                reasons[s] = [[f"наплыв с вокзалов {eff}".strip()] if f else [] for f in inside]
        elif ov.get("type") == "shift":
            (items if spec.get("announced", True) else []).append(
                {
                    "kind": "event",
                    "text": f"снегопад: сдвиг пика +{ov.get('shift_min', 0)} мин",
                    "effect": "",
                    "where": "вся линия",
                    "window": f"{ov['start_msk']}–{ov['end_msk']}",
                    "note": SCENARIO_NOTE,
                }
            )
    team = None
    day = to_msk(frame_ts[0]).date() if n else None
    if day is not None and _on_date(ctx, day):
        ctx = {
            **ctx,
            "explanations": [
                e
                for e in _explanations(ctx)
                if (t := _ts(e.get("ts"))) is not None and to_msk(t).date() == day
            ],
        }
        team = team_banner(ctx, names)
        for sid, rows in station_reasons(ctx, frame_ts).items():
            cur = reasons.setdefault(sid, [[] for _ in range(n)])
            for i, r in enumerate(rows):
                cur[i] = r + cur[i]
    banner = None
    if team is not None:
        merged = sorted(team["items"] + items, key=lambda i: i["kind"] != "weather")
        banner = {**team, "items": merged}
    elif items:
        banner = {"mock": False, "source": SCENARIO_NOTE, "items": items, "warnings": []}
    return {"banner": banner, "anomaly": anomaly, "reasons": reasons}
