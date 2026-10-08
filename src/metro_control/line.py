"""Line 1 reference (config/line.json)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    id: str
    name_ru: str
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    stations: list[Station]
    segments: list[Segment]
    params: dict[str, Param]


LINES_PATH = Path(__file__).resolve().parents[2] / "config" / "lines.json"


class NetworkLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name_ru: str
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    active: bool = False


class _Lines(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    source: str
    lines: list[NetworkLine]

    @model_validator(mode="after")
    def _check(self) -> _Lines:
        if sum(x.active for x in self.lines) != 1:
            raise ValueError("exactly one line must be active")
        if len({x.id for x in self.lines}) != len(self.lines):
            raise ValueError("line ids must be unique")
        return self


def load_lines(path: Path | str | None = None) -> list[NetworkLine]:
    p = Path(path) if path else LINES_PATH
    return _Lines.model_validate(json.loads(p.read_text(encoding="utf-8"))).lines


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
