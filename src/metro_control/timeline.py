"""Recorded 15-min timeline of a compare run and pure replay helpers (no Streamlit)."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from metro_control.screen import band
from metro_control.sim import NORTH, SimParams, SimState, onboard, pending_wait_pax_min, waiting

FILE = "timeline.json"
STEP_MIN = 15
VARIANTS = ("baseline", "policy")
KPI_KEYS = ("waiting", "denied", "wait_pax_min", "trains_in_service")


def snapshot(state: SimState, params: SimParams, log_from: int, t_utc: datetime) -> dict[str, Any]:
    """State at a 15-min boundary; segment loads come from stops logged since `log_from`."""
    departed = {x.train_id for x in state.log}
    queues: dict[str, dict[str, float]] = {}
    for (st, d), q in sorted(state.queues.items()):
        w = sum(sum(c.by_dest.values()) for c in q)
        if w > 0:
            queues.setdefault(st, {NORTH: 0.0, "south": 0.0})[d] = round(w, 1)
    segs: dict[str, dict[str, float]] = {}
    names = params.stations
    for x in state.log[log_from:]:
        i = names.index(x.station)
        j = i + 1 if x.direction == NORTH else i - 1
        if not 0 <= j < len(names):
            continue
        s = segs.setdefault(f"{x.station}__{names[j]}", {"fill": 0.0, "left_behind": 0.0})
        s["fill"] = max(s["fill"], x.load_after / params.capacity)
        s["left_behind"] += x.left_behind
    return {
        "t": t_utc.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entered": round(state.entered, 1),
        "alighted": round(state.alighted, 1),
        "denied": round(state.denied, 1),
        "waiting": round(waiting(state), 1),
        "onboard": round(onboard(state), 1),
        "wait_pax_min": round(state.wait_pax_min + pending_wait_pax_min(state), 1),
        "trains_in_service": sum(
            1 for t in state.trains.values() if t.in_service and t.train_id in departed
        ),
        "queues": queues,
        "segments": {
            k: {"fill": round(v["fill"], 3), "left_behind": round(v["left_behind"], 1)}
            for k, v in sorted(segs.items())
        },
    }


class Timeline(BaseModel):
    run_id: str
    scenario: str
    date: str
    step_min: int = STEP_MIN
    capacity: float
    stations: list[str]
    frames: dict[str, list[dict[str, Any]]]

    @field_validator("frames")
    @classmethod
    def _frames(cls, v: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        if set(v) != set(VARIANTS):
            raise ValueError("frames: need baseline and policy")
        if len(v["baseline"]) != len(v["policy"]) or not v["baseline"]:
            raise ValueError("frames: baseline and policy must be non-empty and equal length")
        return v

    @model_validator(mode="after")
    def _keys(self) -> Timeline:
        for fr in self.frames.values():
            for f in fr:
                missing = {"t", "queues", "segments", *KPI_KEYS} - set(f)
                if missing:
                    raise ValueError(f"frame: missing {sorted(missing)}")
        return self

    @property
    def n_frames(self) -> int:
        return len(self.frames["baseline"])


def dump(tl: Timeline) -> str:
    body = json.dumps(tl.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return body + "\n"


def load_timeline(run_dir: Path | str) -> tuple[Timeline | None, str | None]:
    path = Path(run_dir) / FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"{FILE}: file not found"
    except OSError as e:
        return None, f"{FILE}: cannot read ({e.__class__.__name__})"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"{FILE}: invalid JSON"
    try:
        return Timeline.model_validate(data), None
    except ValidationError as e:
        return None, f"{FILE}: schema error ({e.errors()[0]['msg']})"


def frame(tl: Timeline, variant: str, k: int) -> dict[str, Any]:
    return tl.frames[variant][max(0, min(k, tl.n_frames - 1))]


def compare_frame(tl: Timeline, k: int) -> dict[str, dict[str, float]]:
    b, p = frame(tl, "baseline", k), frame(tl, "policy", k)
    return {
        "baseline": {x: b[x] for x in KPI_KEYS},
        "policy": {x: p[x] for x in KPI_KEYS},
        "delta": {x: round(p[x] - b[x], 1) for x in KPI_KEYS},
    }


def segment_bands(fr: dict[str, Any], stations: list[str]) -> pl.DataFrame:
    rows = []
    for i in range(len(stations) - 1):
        for d, a, b in (
            ("north", stations[i], stations[i + 1]),
            ("south", stations[i + 1], stations[i]),
        ):
            s = fr["segments"].get(f"{a}__{b}")
            if s is None:
                rows.append((f"{a}__{b}", a, b, d, None, 0.0, "none"))
                continue
            bd = "high" if s["left_behind"] > 0 else band(s["fill"])
            rows.append((f"{a}__{b}", a, b, d, s["fill"], s["left_behind"], bd))
    return pl.DataFrame(
        rows,
        schema={
            "segment_id": pl.Utf8,
            "from_station": pl.Utf8,
            "to_station": pl.Utf8,
            "direction": pl.Utf8,
            "fill": pl.Float64,
            "left_behind": pl.Float64,
            "band": pl.Utf8,
        },
        orient="row",
    )


def series(tl: Timeline, upto_k: int) -> pl.DataFrame:
    rows = []
    for v in VARIANTS:
        for k in range(min(upto_k, tl.n_frames - 1) + 1):
            f = tl.frames[v][k]
            rows.append((k, f["t"], v, float(f["waiting"]), float(f["denied"])))
    return pl.DataFrame(
        rows,
        schema={
            "k": pl.Int64,
            "t": pl.Utf8,
            "variant": pl.Utf8,
            "waiting": pl.Float64,
            "denied": pl.Float64,
        },
        orient="row",
    )


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def actions_until(actions: list[dict[str, Any]], t_utc: datetime) -> list[dict[str, Any]]:
    """Actions decided strictly before t: frame k is recorded before the policy acts at k."""
    return [a for a in actions if _parse(a["as_of"]) < t_utc]


@dataclass(frozen=True)
class Player:
    n_frames: int
    k: int = 0
    playing: bool = False


def step(p: Player) -> Player:
    k = min(p.k + 1, p.n_frames - 1)
    return replace(p, k=k, playing=p.playing and k < p.n_frames - 1)


def toggle(p: Player) -> Player:
    return replace(p, playing=not p.playing and p.k < p.n_frames - 1)


def reset(p: Player) -> Player:
    return replace(p, k=0, playing=False)


def tick(
    p: Player, now: float, last: float | None, period: float = 1.0
) -> tuple[Player, float | None]:
    """Advance one frame only when playing and a period has elapsed since `last`."""
    if not p.playing or not (last is None or now - last >= period * 0.9):
        return p, last
    return step(p), now
