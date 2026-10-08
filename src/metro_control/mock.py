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
    LoadRow,
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
        "schema_version": "0.2",
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
            "ts": rows_t,
            "day_type": rows_d,
            "entries": rows_v,
        },
        schema=SCHEMA,
    )


def mock_forecast(history: pl.DataFrame, as_of: datetime) -> ForecastPackage:
    """Percentiles of history by station x MSK clock slot x day_type, only rows < as_of."""
    line = load_line()
    holidays = load_holidays()
    past = history.filter(pl.col("ts") < as_of)
    t = pl.col("ts").dt.convert_time_zone("Europe/Moscow")
    cols = (
        pl.col("entries").quantile(0.1, "linear").alias("q10"),
        pl.col("entries").quantile(0.5, "linear").alias("q50"),
        pl.col("entries").quantile(0.9, "linear").alias("q90"),
        pl.col("entries").mean().alias("mean"),
    )
    clock = (t.dt.hour().cast(pl.Int64) * 60 + t.dt.minute().cast(pl.Int64)).alias("c")
    any_type = {
        (s, c): (a, b, e, m_)
        for s, c, a, b, e, m_ in past.with_columns(clock)
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
    look = {(s, c, d): (a, b, e, m_) for s, c, d, a, b, e, m_ in stats.iter_rows()}
    rows = []
    for st in sorted(line.stations, key=lambda s: s.order):
        for k in range(N_SLOTS):
            slot = as_of + k * SLOT
            m = slot.astimezone(MSK)
            dt = day_type(_msk_service_date(slot), holidays)
            c = m.hour * 60 + m.minute
            q = look.get((st.id, c, dt)) or any_type.get((st.id, c))
            if q is None:
                q, degraded = (0.0, 0.0, 0.0, 0.0), True
            rows.append(
                {
                    "station_id": st.id,
                    "ts": _iso(slot),
                    "q10": float(q[0]),
                    "q50": float(q[1]),
                    "q90": float(q[2]),
                    "horizon_min": 15 * (k + 1),
                    "baseline": float(q[3]),
                    "is_anomaly": False,
                    "model_version": "mock_percentile_v1",
                }
            )
    payload = {
        "as_of": _iso(as_of),
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
        if surge_window is not None and not surge_window[0] <= r.ts < surge_window[1]:
            return 1.0
        return surge.get(r.station_id, 1.0)

    entries = pl.DataFrame(
        {
            "station_id": [r.station_id for r in forecast.payload.rows],
            "ts": [r.ts for r in forecast.payload.rows],
            "entries": [r.q50 * _f(r) for r in forecast.payload.rows],
        },
        schema={
            "station_id": pl.String,
            "ts": pl.Datetime("us", "UTC"),
            "entries": pl.Float64,
        },
    )
    attraction = history.filter(pl.col("ts") < as_of)
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
                    "ts": _iso(slot),
                    "demand": d,
                    "departures": dep,
                    "capacity_per_train": cap,
                    "r": None if dep == 0 else d / (dep * cap),
                }
            )
    env = _envelope("load", f"mock-{_iso(as_of)}", as_of, "mock: q50 forecast x surge, static OD")
    return LoadPackage.model_validate({**env, "payload": rows})


def mock_recommendations(
    load: LoadPackage, as_of: datetime, limit: int = 6
) -> list[Recommendation]:
    """One add_reserve per overloaded segment (r > r_on), worst first; or a single "none"."""
    r_on = float(_assumption("r_on"))  # type: ignore[arg-type]
    rows = [r for r in load.payload if r.r is not None]
    env = _envelope("recommendation", f"mock-{_iso(as_of)}", as_of, "mock: threshold rule")
    over = [r for r in rows if (r.r or 0.0) > r_on]
    worst: dict[str, LoadRow] = {}
    for r in over:
        if r.segment_id not in worst or (r.r or 0.0) > (worst[r.segment_id].r or 0.0):
            worst[r.segment_id] = r
    ranked = sorted(worst.values(), key=lambda r: -(r.r or 0.0))[:limit]
    payloads = []
    for w in ranked:
        seg = w.segment_id
        start = min(r.ts for r in over if r.segment_id == seg)
        payloads.append(
            {
                "action": "add_reserve",
                "target": seg,
                "start": _iso(start),
                "end": _iso(start + timedelta(minutes=60)),
                "reason": (
                    f"Загрузка перегона достигает {w.r or 0.0:.0%} от вместимости "
                    f"(порог {r_on:.0%}): добавить резервный поезд (mock)."
                ),
            }
        )
    if not payloads:
        busiest = max(rows, key=lambda r: r.r or 0.0, default=None)
        top = busiest.r if busiest else 0.0
        target = busiest.segment_id if busiest else load.payload[0].segment_id
        payloads.append(
            {
                "action": "none",
                "target": target,
                "start": _iso(as_of),
                "end": _iso(as_of + timedelta(minutes=60)),
                "reason": (
                    f"Максимальная загрузка {top:.0%} ниже порога {r_on:.0%}: "
                    "действий не требуется (mock)."
                ),
            }
        )
    out = []
    for i, payload in enumerate(payloads):
        rid = f"mock-{_iso(as_of)}" if i == 0 else f"mock-{_iso(as_of)}-{i + 1}"
        payload |= {"recommendation_id": rid, "as_of": _iso(as_of), "source": "mock"}
        out.append(Recommendation.model_validate({**env, "payload": payload}))
    return out


def mock_recommendation(load: LoadPackage, as_of: datetime) -> Recommendation:
    """The most urgent of `mock_recommendations` (kept for recommendation.json)."""
    return mock_recommendations(load, as_of)[0]


def default_as_of(entries: pl.DataFrame) -> datetime:
    """17:30 MSK of the last weekday in entries that has a 17:30 slot."""
    t = pl.col("ts").dt.convert_time_zone("Europe/Moscow")
    cand = entries.filter(
        (pl.col("day_type") == "weekday") & (t.dt.hour() == 17) & (t.dt.minute() == 30)
    )
    if cand.height == 0:
        raise ValueError("no weekday 17:30 MSK slot in entries")
    return cand["ts"].max()


def mark_entries_real(
    run_dir: Path | str, source: str = "organizers: 15-min entries workbooks"
) -> None:
    """The facts in entries.json came from the organizers' data, not from the generator."""
    p = Path(run_dir) / "entries.json"
    ent = json.loads(p.read_text(encoding="utf-8"))
    ent["data_mode"] = "real"
    ent["manifest"]["source"] = source
    p.write_text(json.dumps(ent, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def build_mock_bundle(
    out_dir: Path | str,
    entries: pl.DataFrame | None = None,
    as_of: datetime | None = None,
    surge: dict[str, float] | None = None,
) -> Path:
    """surge: per-station demand factor for the demo (None = `default_surge()`, {} = real day)."""
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
    ld = mock_load(fc, entries, line, params, dtype, default_surge() if surge is None else surge)
    recs = mock_recommendations(ld, as_of)
    rec = recs[0]
    # service day starts 03:00 MSK
    day_start = (as_of.astimezone(MSK) - timedelta(hours=3)).replace(
        hour=3, minute=0, second=0, microsecond=0
    )
    day_start = day_start.astimezone(UTC)
    fact = entries.filter((pl.col("ts") >= day_start) & (pl.col("ts") < as_of))
    rows = [
        {
            "station_id": s,
            "ts": _iso(t),
            "day_type": d,
            "entries": float(v),
        }
        for s, t, d, v in fact.select("station_id", "ts", "day_type", "entries")
        .sort("ts", "station_id")
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
    (out / "recommendations.jsonl").write_text(
        "".join(json.dumps(r.model_dump(mode="json"), ensure_ascii=False) + "\n" for r in recs),
        encoding="utf-8",
    )
    return out
