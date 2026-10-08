"""Event simulator core: one line, few stations, trains with FIFO boarding (pure Python).

Time is float minutes. All parameters are explicit arguments.
"""

from __future__ import annotations

import copy
import heapq
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

NORTH = "north"
SOUTH = "south"


@dataclass(frozen=True)
class SimParams:
    stations: tuple[str, ...]
    run_min: tuple[float, ...]
    dwell_min: float
    turnback_min: float
    capacity: float

    def __post_init__(self) -> None:
        if len(self.stations) < 2 or len(set(self.stations)) != len(self.stations):
            raise ValueError("stations: need >=2 unique stations")
        if len(self.run_min) != len(self.stations) - 1:
            raise ValueError("run_min: length must be len(stations)-1")
        if any(r <= 0 for r in self.run_min):
            raise ValueError("run_min: values must be > 0")
        if self.dwell_min < 0:
            raise ValueError("dwell_min: must be >= 0")
        if self.turnback_min < 0:
            raise ValueError("turnback_min: must be >= 0")
        if self.capacity <= 0:
            raise ValueError("capacity: must be > 0")


@dataclass(frozen=True)
class Arrival:
    t: float
    origin: str
    dest: str
    amount: float

    def __post_init__(self) -> None:
        if self.origin == self.dest:
            raise ValueError("dest: equals origin")
        if self.amount <= 0:
            raise ValueError("amount: must be > 0")


@dataclass
class Cohort:
    t_arrive: float
    by_dest: dict[str, float]


@dataclass
class Train:
    train_id: str
    station_idx: int
    direction: str
    onboard: dict[str, float] = field(default_factory=dict)
    in_service: bool = True
    one_way: bool = False  # trip train: leaves service at the far terminal


@dataclass(frozen=True)
class StopLog:
    t: float
    train_id: str
    station: str
    direction: str
    alighted: float
    boarded: float
    left_behind: float
    load_after: float


@dataclass
class SimState:
    """Simulation state. `denied` = boarding refusals, counted per passenger per train
    that leaves them behind (not unique people)."""

    t: float
    trains: dict[str, Train]
    queues: dict[tuple[str, str], deque[Cohort]]
    events: list[tuple[float, int, str, int, str]]  # (t, seq, train_id, station_idx, dir)
    seq: int
    entered: float
    alighted: float
    denied: float
    wait_pax_min: float
    log: list[StopLog]


def _direction(params: SimParams, a: Arrival) -> str:
    return NORTH if params.stations.index(a.dest) > params.stations.index(a.origin) else SOUTH


def new_state(
    params: SimParams,
    trains: list[tuple[str, str, str, float]],
    t0: float = 0.0,
    trips: Sequence[tuple[str, str, float]] = (),
) -> SimState:
    if not params.stations:
        raise ValueError("stations: empty")
    st = SimState(
        t=t0,
        trains={},
        queues={(s, d): deque() for s in params.stations for d in (NORTH, SOUTH)},
        events=[],
        seq=0,
        entered=0.0,
        alighted=0.0,
        denied=0.0,
        wait_pax_min=0.0,
        log=[],
    )
    for train_id, station, direction, t_first in trains:
        if station not in params.stations:
            raise ValueError(f"station: unknown {station}")
        if t_first < t0:
            raise ValueError("t_first: before t0")
        if train_id in st.trains:
            raise ValueError(f"train_id: duplicate {train_id}")
        if direction not in (NORTH, SOUTH):
            raise ValueError("direction: must be north or south")
        idx = params.stations.index(station)
        st.trains[train_id] = Train(train_id, idx, direction)
        heapq.heappush(st.events, (t_first, st.seq, train_id, idx, direction))
        st.seq += 1
    for train_id, direction, t_depart in trips:
        if train_id in st.trains:
            raise ValueError(f"train_id: duplicate {train_id}")
        if direction not in (NORTH, SOUTH):
            raise ValueError("direction: must be north or south")
        if t_depart < t0:
            raise ValueError("t_depart: before t0")
        idx = 0 if direction == NORTH else len(params.stations) - 1
        st.trains[train_id] = Train(train_id, idx, direction, one_way=True)
        heapq.heappush(st.events, (t_depart, st.seq, train_id, idx, direction))
        st.seq += 1
    return st


def _apply_arrival(state: SimState, params: SimParams, a: Arrival) -> None:
    for s in (a.origin, a.dest):
        if s not in params.stations:
            raise ValueError(f"station: unknown {s}")
    q = state.queues[(a.origin, _direction(params, a))]
    if q and q[-1].t_arrive == a.t:
        q[-1].by_dest[a.dest] = q[-1].by_dest.get(a.dest, 0.0) + a.amount
    else:
        q.append(Cohort(a.t, {a.dest: a.amount}))
    state.entered += a.amount


def _process_stop(state: SimState, params: SimParams, ev: tuple[float, int, str, int, str]) -> None:
    t, _, train_id, idx, direction = ev
    train = state.trains[train_id]
    train.station_idx, train.direction = idx, direction
    station = params.stations[idx]

    alighted = train.onboard.pop(station, 0.0)
    state.alighted += alighted

    load = sum(train.onboard.values())
    boarded = 0.0
    q = state.queues[(station, direction)]
    while q and params.capacity - load > 1e-12:
        c = q[0]
        total = sum(c.by_dest.values())
        room = params.capacity - load
        if total <= room:
            moved = dict(c.by_dest)
            q.popleft()
        else:
            frac = room / total
            moved = {d: v * frac for d, v in c.by_dest.items()}
            c.by_dest = {d: v - moved[d] for d, v in c.by_dest.items()}
        amt = sum(moved.values())
        for d, v in moved.items():
            train.onboard[d] = train.onboard.get(d, 0.0) + v
        state.wait_pax_min += amt * (t - c.t_arrive)
        boarded += amt
        load += amt
    left = sum(sum(c.by_dest.values()) for c in q)
    state.denied += left
    state.log.append(
        StopLog(
            t, train_id, station, direction, alighted, boarded, left, sum(train.onboard.values())
        )
    )

    n = len(params.stations)
    if train.one_way and (idx == n - 1 if direction == NORTH else idx == 0):
        train.in_service = False
        return
    if direction == NORTH and idx + 1 < n:
        nxt = (t + params.dwell_min + params.run_min[idx], idx + 1, NORTH)
    elif direction == SOUTH and idx - 1 >= 0:
        nxt = (t + params.dwell_min + params.run_min[idx - 1], idx - 1, SOUTH)
    else:
        rev = SOUTH if direction == NORTH else NORTH
        nxt = (t + params.turnback_min, idx, rev)
    heapq.heappush(state.events, (nxt[0], state.seq, train_id, nxt[1], nxt[2]))
    state.seq += 1


def run(state: SimState, params: SimParams, demand: Sequence[Arrival], until: float) -> SimState:
    if until < state.t:
        raise ValueError("until: before state.t")
    log = list(state.log)
    shallow = copy.copy(state)
    shallow.log = []
    st = copy.deepcopy(shallow)
    st.log = log
    st.queues = {k: deque(v) for k, v in st.queues.items()}
    arrivals = sorted((a for a in demand if st.t <= a.t < until), key=lambda a: a.t)
    i = 0
    while True:
        ta = arrivals[i].t if i < len(arrivals) else None
        te = st.events[0][0] if st.events and st.events[0][0] < until else None
        if ta is not None and (te is None or ta <= te):
            _apply_arrival(st, params, arrivals[i])
            i += 1
        elif te is not None:
            _process_stop(st, params, heapq.heappop(st.events))
        else:
            break
    st.t = until
    return st


def waiting(state: SimState) -> float:
    return sum(sum(c.by_dest.values()) for q in state.queues.values() for c in q)


def onboard(state: SimState) -> float:
    return sum(sum(tr.onboard.values()) for tr in state.trains.values())


def pending_wait_pax_min(state: SimState) -> float:
    return sum(
        sum(c.by_dest.values()) * (state.t - c.t_arrive) for q in state.queues.values() for c in q
    )
