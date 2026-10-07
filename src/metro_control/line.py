"""Line 1 reference (config/line.json)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "line.json"


class Param(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any
    unit: str
    source: str


class Station(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name_ru: str
    order: int
    vestibules: list[str]


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    from_station: str
    to_station: str
    direction: Literal["north", "south"]
    order: int


class LineRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    stations: list[Station]
    segments: list[Segment]
    params: dict[str, Param]


def load_line(path: Path | str | None = None) -> LineRef:
    p = Path(path) if path else DEFAULT_PATH
    return LineRef.model_validate(json.loads(p.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def _default() -> LineRef:
    return load_line()


def station_ids() -> list[str]:
    return [s.id for s in sorted(_default().stations, key=lambda s: s.order)]


def segment_ids() -> list[str]:
    return [s.id for s in _default().segments]
