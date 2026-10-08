"""Mock dispatcher policy with hysteresis. Everything here is `mock` (pure)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import polars as pl

from metro_control import mock, od
from metro_control.contracts import LoadPackage, Recommendation
from metro_control.line import LineRef
from metro_control.line import station_ids as _station_ids

DEPOT_NORTH = "avtovo"
DEPOT_SOUTH = "severnoye"
_FALLBACK_SEGMENT = "veteranov__leninsky_prospekt"  # first line segment


@dataclass(frozen=True)
class PolicyMemory:
    last_as_of: datetime | None = None
    over_streak: int = 0
    under_streak: int = 0
    last_rec: Recommendation | None = None


def segment_direction(segment_id: str) -> str:
    """north if the segment goes from a lower to a higher station index."""
    a, b = segment_id.split("__")
    order = _station_ids()
    return "north" if order.index(a) < order.index(b) else "south"


def _rec(
    as_of: datetime, action: str, target: str, window_min: float, reason: str
) -> Recommendation:
    env = mock._envelope("recommendation", f"mock-{mock._iso(as_of)}", as_of, "mock: hysteresis")
    payload = {
        "recommendation_id": f"mock-policy-{mock._iso(as_of)}",
        "as_of": mock._iso(as_of),
        "action": action,
        "target": target,
        "start": mock._iso(as_of),
        "end": mock._iso(as_of + timedelta(minutes=window_min)),
        "reason": reason,
        "source": "mock",
    }
    return Recommendation.model_validate({**env, "payload": payload})


def mock_policy(
    load: LoadPackage,
    as_of: datetime,
    memory: PolicyMemory,
    reserves_left: dict[str, int],
) -> tuple[Recommendation, PolicyMemory]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of: naive datetime not allowed")
    as_of = as_of.astimezone(UTC)
    if load.generated_at > as_of:
        raise ValueError("load: generated_at is later than as_of (future data)")
    if memory.last_as_of is not None:
        if as_of < memory.last_as_of:
            raise ValueError("as_of: earlier than the last decision")
        if as_of == memory.last_as_of and memory.last_rec is not None:
            return memory.last_rec, memory
    r_on = float(mock._assumption("r_on"))  # type: ignore[arg-type]
    r_off = float(mock._assumption("r_off"))  # type: ignore[arg-type]
    n_on = int(mock._assumption("r_on_consecutive"))  # type: ignore[call-overload]
    n_off = int(mock._assumption("r_off_consecutive"))  # type: ignore[call-overload]
    horizon = timedelta(minutes=float(mock._assumption("control_horizon_min")))  # type: ignore[arg-type]
    window = float(mock._assumption("policy_action_window_min"))  # type: ignore[arg-type]

    rows = [
        r for r in load.payload if r.r is not None and as_of <= r.interval_start < as_of + horizon
    ]
    best: dict[str, tuple[float, str]] = {}  # direction -> (max r, segment)
    for r in rows:
        d = segment_direction(r.segment_id)
        if d not in best or r.r > best[d][0]:  # type: ignore[operator]
            best[d] = (r.r, r.segment_id)  # type: ignore[assignment]
    worst = max(best.values()) if best else None
    over = worst is not None and worst[0] > r_on
    under = bool(rows) and all(r.r < r_off for r in rows)  # type: ignore[operator]
    over_streak = memory.over_streak + 1 if over else 0
    under_streak = memory.under_streak + 1 if under else 0

    if worst is None:
        target = load.payload[0].segment_id if load.payload else _FALLBACK_SEGMENT
        rec = _rec(
            as_of,
            "none",
            target,
            window,
            "Нет данных о загрузке на горизонте: действий нет (mock).",
        )
    elif over_streak >= n_on:
        target = worst[1]
        d = segment_direction(target)
        depot = DEPOT_NORTH if d == "north" else DEPOT_SOUTH
        if reserves_left.get(depot, 0) > 0:
            action, what = "add_reserve", f"добавить резервный поезд из депо {depot}"
        else:
            action, what = "shift_peak", "резерв исчерпан, сдвинуть рейс в пик"
        reason = (
            f"Загрузка {target} {worst[0]:.0%} выше порога {r_on:.0%} "
            f"{over_streak} шага подряд: {what} (mock)."
        )
        rec = _rec(as_of, action, target, window, reason)
        over_streak = 0
    elif under_streak >= n_off:
        d = min(best, key=lambda k: best[k][0])
        mx, target = best[d]
        reason = (
            f"Загрузка ниже порога {r_off:.0%} {under_streak} шага подряд "
            f"(макс. {mx:.0%} на {target}): снять поезд (mock)."
        )
        rec = _rec(as_of, "remove_train", target, window, reason)
        under_streak = 0
    else:
        target = worst[1]
        reason = (
            f"Максимальная загрузка {worst[0]:.0%} на {target}, пороги {r_off:.0%}/{r_on:.0%}: "
            "действий не требуется (mock)."
        )
        rec = _rec(as_of, "none", target, window, reason)
    return rec, replace(
        memory,
        last_as_of=as_of,
        over_streak=over_streak,
        under_streak=under_streak,
        last_rec=rec,
    )


def recommend_at(
    history: pl.DataFrame,
    as_of: datetime,
    line: LineRef,
    od_params: od.OdParams,
    memory: PolicyMemory,
    reserves_left: dict[str, int],
    surge: dict[str, float] | None = None,
    surge_window: tuple[datetime, datetime] | None = None,
) -> tuple[Recommendation, PolicyMemory]:
    """Mock forecast -> mock load -> mock policy; reads history strictly before as_of."""
    fc = mock.mock_forecast(history, as_of)
    dtype = mock._slot_day_type(as_of)
    sg = {} if surge is None else surge  # no implicit demo surge (D22)
    ld = mock.mock_load(fc, history, line, od_params, dtype, sg, surge_window)
    return mock_policy(ld, as_of, memory, reserves_left)
