"""Mock forecast / load / recommendation so the demo runs offline. Everything here is `mock`."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from metro_control import od
from metro_control.contracts import (
    N_SLOTS,
    SLOT,
    ForecastPackage,
    LoadPackage,
    Recommendation,
    StationEntriesPackage,
)
from metro_control.entries import ASSUMPTIONS_PATH, SCHEMA, day_type, load_holidays
from metro_control.line import LineRef, load_line
from metro_control.timeutil import MSK

DEFAULT_SURGE_KEY = "mock_demo_surge"


def _assumption(key: str) -> object:
    return json.loads(Path(ASSUMPTIONS_PATH).read_text(encoding="utf-8"))["items"][key]["value"]


def default_surge() -> dict[str, float]:
    return dict(_assumption(DEFAULT_SURGE_KEY))  # type: ignore[call-overload]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _envelope(kind: str, run_id: str, as_of: datetime, source: str) -> dict:
    digest = hashlib.sha256(source.encode()).hexdigest()
    return {
        "schema_version": "0.1",
        "kind": kind,
        "run_id": run_id,
        "generated_at": _iso(as_of),
        "data_mode": "mock",
        "manifest": {"source": source, "checksum": f"sha256:{digest}"},
    }


def _msk_service_date(t: datetime) -> date:
    return (t.astimezone(MSK) - timedelta(hours=3)).date()


def synthetic_entries(start: date, days: int) -> pl.DataFrame:
    """Deterministic synthetic 15-min entries (MSK days from 00:00), two peaks 08-09 and 18-19."""
    line = load_line()
    holidays = load_holidays()
    stations = sorted(line.stations, key=lambda s: s.order)
    rows_s, rows_t, rows_d, rows_v = [], [], [], []
    for d in range(days):
        day = start + timedelta(days=d)
        dtype = day_type(day, holidays)
        weekend = 0.6 if dtype != "weekday" else 1.0
        midnight = datetime(day.year, day.month, day.day, tzinfo=MSK)
        for slot in range(96):
            h = slot / 4.0
            t = (midnight + timedelta(minutes=15 * slot)).astimezone(UTC)
            if h < 5.5 or h >= 24:
                prof = 0.0
            else:
                prof = (
                    0.12
                    + math.exp(-((h - 8.5) ** 2) / 1.2)
                    + 0.9 * math.exp(-((h - 18.5) ** 2) / 1.6)
                )
            for st in stations:
                scale = 600 + 180 * ((st.order * 7) % 11)
                rows_s.append(st.id)
                rows_t.append(t)
                rows_d.append(dtype)
                rows_v.append(round(scale * prof * weekend, 3))
    return pl.DataFrame(
        {
            "station_id": rows_s,
            "interval_start": rows_t,
            "day_type": rows_d,
            "entries": rows_v,
        },
        schema=SCHEMA,
    )


def mock_forecast(history: pl.DataFrame, as_of: datetime) -> ForecastPackage:
    """Percentiles of history by station x MSK clock slot x day_type, only rows < as_of."""
    line = load_line()
    holidays = load_holidays()
    past = history.filter(pl.col("interval_start") < as_of)
    t = pl.col("interval_start").dt.convert_time_zone("Europe/Moscow")
    cols = (
        pl.col("entries").quantile(0.1, "linear").alias("q10"),
        pl.col("entries").quantile(0.5, "linear").alias("q50"),
        pl.col("entries").quantile(0.9, "linear").alias("q90"),
    )
    clock = (t.dt.hour().cast(pl.Int64) * 60 + t.dt.minute().cast(pl.Int64)).alias("c")
    any_type = {
        (s, c): (a, b, e)
        for s, c, a, b, e in past.with_columns(clock)
        .group_by("station_id", "c")
        .agg(*cols)
        .iter_rows()
    }
    stats = (
        past.with_columns(
            (t.dt.hour().cast(pl.Int64) * 60 + t.dt.minute().cast(pl.Int64)).alias("c")
        )
        .group_by("station_id", "c", "day_type")
        .agg(*cols)
    )
    degraded = False
    look = {(s, c, d): (a, b, e) for s, c, d, a, b, e in stats.iter_rows()}
    rows = []
    for st in sorted(line.stations, key=lambda s: s.order):
        for k in range(N_SLOTS):
            slot = as_of + k * SLOT
            m = slot.astimezone(MSK)
            dt = day_type(_msk_service_date(slot), holidays)
            c = m.hour * 60 + m.minute
            q = look.get((st.id, c, dt)) or any_type.get((st.id, c))
            if q is None:
                q, degraded = (0.0, 0.0, 0.0), True
            rows.append(
                {
                    "station_id": st.id,
                    "interval_start": _iso(slot),
                    "q10": float(q[0]),
                    "q50": float(q[1]),
                    "q90": float(q[2]),
                }
            )
    payload = {
        "as_of": _iso(as_of),
        "model_name": "mock-median",
        "status": "degraded" if degraded else "mock",
        "quantiles_ready": True,
        "rows": rows,
    }
    env = _envelope("forecast", f"mock-{_iso(as_of)}", as_of, "mock: history percentiles")
    return ForecastPackage.model_validate({**env, "payload": payload})


def _slot_day_type(slot: datetime) -> str:
    return day_type(_msk_service_date(slot), load_holidays())


def _departures(slot: datetime, weekday: bool, line: LineRef) -> int:
    m = slot.astimezone(MSK)
    h = 24 if m.hour == 0 else m.hour
    if not 5 <= h <= 24:
        return 0
    key = "planned_pairs_weekday" if weekday else "planned_pairs_weekend"
    pairs = line.params[key].value[h - 5]
    return int(pairs / 4 + 0.5)


def mock_load(
    forecast: ForecastPackage,
    history: pl.DataFrame,
    line: LineRef,
    params: od.OdParams,
    day_type: str,
    surge: dict[str, float],
    surge_window: tuple[datetime, datetime] | None = None,
) -> LoadPackage:
    """surge_window=[w0, w1): the surge factor applies only to forecast rows inside it."""
    as_of = forecast.payload.as_of

    def _f(r) -> float:  # noqa: ANN001
        if surge_window is not None and not surge_window[0] <= r.interval_start < surge_window[1]:
            return 1.0
        return surge.get(r.station_id, 1.0)

    entries = pl.DataFrame(
        {
            "station_id": [r.station_id for r in forecast.payload.rows],
            "interval_start": [r.interval_start for r in forecast.payload.rows],
            "entries": [r.q50 * _f(r) for r in forecast.payload.rows],
        },
        schema={
            "station_id": pl.String,
            "interval_start": pl.Datetime("us", "UTC"),
            "entries": pl.Float64,
        },
    )
    attraction = history.filter(pl.col("interval_start") < as_of)
    dem = {
        (sid, t): v
        for sid, t, v in od.segment_demand(entries, attraction, line, params).iter_rows()
    }
    cap = float(line.params["train_capacity"].value)
    rows = []
    for k in range(N_SLOTS):
        slot = as_of + k * SLOT
        slot_type = _slot_day_type(slot)
        dep = _departures(slot, slot_type == "weekday", line)
        for seg in line.segments:
            d = float(dem.get((seg.id, slot), 0.0))
            rows.append(
                {
                    "segment_id": seg.id,
                    "interval_start": _iso(slot),
                    "demand": d,
                    "departures": dep,
                    "capacity_per_train": cap,
                    "r": None if dep == 0 else d / (dep * cap),
                }
            )
    env = _envelope("load", f"mock-{_iso(as_of)}", as_of, "mock: q50 forecast x surge, static OD")
    return LoadPackage.model_validate({**env, "payload": rows})


def mock_recommendation(load: LoadPackage, as_of: datetime) -> Recommendation:
    r_on = float(_assumption("r_on"))  # type: ignore[arg-type]
    rows = [r for r in load.payload if r.r is not None]
    env = _envelope("recommendation", f"mock-{_iso(as_of)}", as_of, "mock: threshold rule")
    over = [r for r in rows if r.r > r_on]  # type: ignore[operator]
    if over:
        worst = max(over, key=lambda r: r.r)  # type: ignore[arg-type,return-value]
        seg = worst.segment_id
        start = min(r.interval_start for r in over if r.segment_id == seg)
        payload = {
            "action": "add_reserve",
            "target": seg,
            "start": _iso(start),
            "end": _iso(start + timedelta(minutes=60)),
            "reason": (
                f"Загрузка перегона достигает {worst.r:.0%} от вместимости "
                f"(порог {r_on:.0%}): добавить резервный поезд (mock)."
            ),
        }
    else:
        top = max((r.r for r in rows), default=0.0)
        payload = {
            "action": "none",
            "target": load.payload[0].segment_id,
            "start": _iso(as_of),
            "end": _iso(as_of + timedelta(minutes=60)),
            "reason": (
                f"Максимальная загрузка {top:.0%} ниже порога {r_on:.0%}: "
                "действий не требуется (mock)."
            ),
        }
    payload |= {"recommendation_id": f"mock-{_iso(as_of)}", "as_of": _iso(as_of), "source": "mock"}
    return Recommendation.model_validate({**env, "payload": payload})


def default_as_of(entries: pl.DataFrame) -> datetime:
    """17:30 MSK of the last weekday in entries that has a 17:30 slot."""
    t = pl.col("interval_start").dt.convert_time_zone("Europe/Moscow")
    cand = entries.filter(
        (pl.col("day_type") == "weekday") & (t.dt.hour() == 17) & (t.dt.minute() == 30)
    )
    if cand.height == 0:
        raise ValueError("no weekday 17:30 MSK slot in entries")
    return cand["interval_start"].max()


def build_mock_bundle(
    out_dir: Path | str, entries: pl.DataFrame | None = None, as_of: datetime | None = None
) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if entries is None:
        entries = synthetic_entries(date(2026, 9, 1), 28)
    as_of = as_of if as_of is not None else default_as_of(entries)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(UTC)
    line, params = load_line(), od.load_od_params()
    holidays = load_holidays()
    dtype = day_type(_msk_service_date(as_of), holidays)
    fc = mock_forecast(entries, as_of)
    ld = mock_load(fc, entries, line, params, dtype, default_surge())
    rec = mock_recommendation(ld, as_of)
    # service day starts 03:00 MSK
    day_start = (as_of.astimezone(MSK) - timedelta(hours=3)).replace(
        hour=3, minute=0, second=0, microsecond=0
    )
    day_start = day_start.astimezone(UTC)
    fact = entries.filter(
        (pl.col("interval_start") >= day_start) & (pl.col("interval_start") < as_of)
    )
    rows = [
        {
            "station_id": s,
            "interval_start": _iso(t),
            "day_type": d,
            "entries": float(v),
        }
        for s, t, d, v in fact.select("station_id", "interval_start", "day_type", "entries")
        .sort("interval_start", "station_id")
        .iter_rows()
    ]
    env = _envelope("station_entries", f"mock-{_iso(as_of)}", as_of, "mock: service day facts")
    ent = StationEntriesPackage.model_validate({**env, "payload": rows})
    for name, pkg in (
        ("forecast.json", fc),
        ("load.json", ld),
        ("recommendation.json", rec),
        ("entries.json", ent),
    ):
        (out / name).write_text(
            json.dumps(pkg.model_dump(mode="json"), ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
    return out
