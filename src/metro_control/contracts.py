"""Shared contracts v0.2. Other team members copy this file; change only with a version bump."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from metro_control.line import segment_ids, station_ids

CONTRACT_VERSION = "0.2"
SLOT = timedelta(minutes=15)
N_SLOTS = 8


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC (offset 0)")
    return dt


def _on_slot(dt: datetime) -> datetime:
    _utc(dt)
    if dt.minute not in (0, 15, 30, 45) or dt.second or dt.microsecond:
        raise ValueError("datetime must lie on a 15-minute boundary")
    return dt


UtcDt = Annotated[datetime, AfterValidator(_utc)]
SlotDt = Annotated[datetime, AfterValidator(_on_slot)]
StationId = Annotated[str, Field(min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _check_station(v: str) -> str:
    if v not in station_ids():
        raise ValueError(f"unknown station_id {v!r}")
    return v


def _check_segment(v: str) -> str:
    if v not in segment_ids():
        raise ValueError(f"unknown segment_id {v!r}")
    return v


class Manifest(_Model):
    source: str
    checksum: str

    @field_validator("checksum")
    @classmethod
    def _checksum(cls, v: str) -> str:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", v):
            raise ValueError("checksum must be 'sha256:<64 lowercase hex>'")
        return v


class _Envelope(_Model):
    schema_version: Literal["0.2"]
    run_id: str
    generated_at: UtcDt
    data_mode: Literal["real", "mock", "synthetic"]
    manifest: Manifest


# ---------- payload models ----------
class StationEntriesRow(_Model):
    station_id: str
    ts: SlotDt
    day_type: Literal["weekday", "saturday", "sunday", "holiday"]
    entries: float = Field(ge=0)

    _st = field_validator("station_id")(_check_station)


class ForecastRow(_Model):
    station_id: str
    ts: SlotDt
    horizon_min: int = Field(ge=15, le=120)
    q10: float = Field(ge=0)
    q50: float = Field(ge=0)
    q90: float = Field(ge=0)
    baseline: float = Field(ge=0)
    is_anomaly: bool
    model_version: str = Field(min_length=1)

    _st = field_validator("station_id")(_check_station)

    @field_validator("horizon_min")
    @classmethod
    def _h(cls, v: int) -> int:
        if v % 15:
            raise ValueError("horizon_min must be a multiple of 15")
        return v

    @model_validator(mode="after")
    def _order(self) -> ForecastRow:
        if not (self.q10 <= self.q50 <= self.q90):
            raise ValueError("quantiles must satisfy q10 <= q50 <= q90 (check q50, q90)")
        return self


class ForecastPayload(_Model):
    as_of: SlotDt
    status: Literal["ok", "mock", "degraded"]
    quantiles_ready: bool
    rows: list[ForecastRow]

    @model_validator(mode="after")
    def _rows(self) -> ForecastPayload:
        n = len(station_ids()) * N_SLOTS
        if len(self.rows) != n:
            raise ValueError(f"rows: expected exactly {n} rows, got {len(self.rows)}")
        keys = [(r.station_id, r.ts) for r in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("rows: duplicate (station_id, ts)")
        allowed = {self.as_of + i * SLOT for i in range(N_SLOTS)}
        bad = [r for r in self.rows if r.ts not in allowed]
        if bad:
            raise ValueError(f"ts {bad[0].ts.isoformat()} outside as_of + 0..{N_SLOTS - 1} slots")
        for r in self.rows:
            exp = int((r.ts - self.as_of) / timedelta(minutes=1)) + 15
            if r.horizon_min != exp:
                raise ValueError(f"horizon_min must be {exp} for ts {r.ts.isoformat()}")
        if not self.quantiles_ready:
            for r in self.rows:
                if not (r.q10 == r.q50 == r.q90):
                    raise ValueError("quantiles_ready=false requires q10 == q50 == q90")
        return self


class LoadRow(_Model):
    segment_id: str
    ts: SlotDt
    demand: float = Field(ge=0)
    departures: int = Field(ge=0)
    capacity_per_train: float = Field(default=1458, gt=0)
    r: float | None = None

    _sg = field_validator("segment_id")(_check_segment)

    @model_validator(mode="after")
    def _r(self) -> LoadRow:
        if self.departures == 0:
            if self.r is not None:
                raise ValueError("r must be null when departures == 0")
        else:
            exp = self.demand / (self.departures * self.capacity_per_train)
            if self.r is None or abs(self.r - exp) > 1e-6:
                raise ValueError(f"r must equal demand/(departures*capacity) = {exp:.6f}")
        return self


class TrainState(_Model):
    train_id: str
    segment_id: str | None = None
    station_id: str | None = None
    load: float = Field(ge=0)

    @field_validator("segment_id")
    @classmethod
    def _sg(cls, v: str | None) -> str | None:
        return None if v is None else _check_segment(v)

    @field_validator("station_id")
    @classmethod
    def _st(cls, v: str | None) -> str | None:
        return None if v is None else _check_station(v)


class QueueState(_Model):
    station_id: str
    direction: Literal["north", "south"]
    waiting: float = Field(ge=0)

    _st = field_validator("station_id")(_check_station)


class SimulationPayload(_Model):
    t: UtcDt
    trains: list[TrainState]
    queues: list[QueueState]
    reserve_available: int = Field(ge=0, le=4)
    capacity_per_train: float = Field(default=1458, gt=0)

    @model_validator(mode="after")
    def _load(self) -> SimulationPayload:
        for tr in self.trains:
            if tr.load > self.capacity_per_train:
                raise ValueError(f"trains.load: {tr.train_id} exceeds capacity")
        return self


class RecommendationPayload(_Model):
    recommendation_id: str
    as_of: UtcDt
    action: Literal["add_reserve", "remove_train", "shift_peak", "limit_entry", "none"]
    target: str
    start: UtcDt
    end: UtcDt
    reason: str
    source: Literal["mock", "person4"]

    @model_validator(mode="after")
    def _check(self) -> RecommendationPayload:
        if self.target not in station_ids() and self.target not in segment_ids():
            raise ValueError(f"target: unknown station or segment {self.target!r}")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Metrics(_Model):
    wait_pax_min: float
    queue_left: float
    denied_boardings: float
    max_fill: float
    mean_fill: float
    train_hours: float
    train_km: float


class EffectPayload(_Model):
    scenario: str
    baseline_run_id: str
    policy_run_id: str
    baseline: Metrics
    policy: Metrics


# ---------- packages ----------
class StationEntriesPackage(_Envelope):
    kind: Literal["station_entries"]
    payload: list[StationEntriesRow]


class ForecastPackage(_Envelope):
    kind: Literal["forecast"]
    payload: ForecastPayload

    @model_validator(mode="after")
    def _no_future(self) -> ForecastPackage:
        if self.payload.as_of > self.generated_at:
            raise ValueError("payload.as_of must be <= generated_at")
        return self


class LoadPackage(_Envelope):
    kind: Literal["load"]
    payload: list[LoadRow]


class SimulationState(_Envelope):
    kind: Literal["simulation_state"]
    payload: SimulationPayload


class Recommendation(_Envelope):
    kind: Literal["recommendation"]
    payload: RecommendationPayload

    @model_validator(mode="after")
    def _no_future(self) -> Recommendation:
        if self.payload.as_of > self.generated_at:
            raise ValueError("payload.as_of must be <= generated_at")
        if self.payload.start < self.payload.as_of:
            raise ValueError("payload.start must be >= payload.as_of")
        return self


class EffectComparison(_Envelope):
    kind: Literal["effect_comparison"]
    payload: EffectPayload


PACKAGES: dict[str, type[_Envelope]] = {
    "station_entries": StationEntriesPackage,
    "forecast": ForecastPackage,
    "load": LoadPackage,
    "simulation_state": SimulationState,
    "recommendation": Recommendation,
    "effect_comparison": EffectComparison,
}
