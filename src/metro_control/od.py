"""Gravity OD model and static assignment of station entries to directed segments (pure)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl

from metro_control.entries import ASSUMPTIONS_PATH
from metro_control.line import LineRef


def travel_times(n: int, hop_min: float) -> np.ndarray:
    idx = np.arange(n)
    return np.abs(idx[:, None] - idx[None, :]) * float(hop_min)


def od_shares(attraction: np.ndarray, times: np.ndarray, beta: float) -> np.ndarray:
    a = np.asarray(attraction, dtype=float)
    if not np.isfinite(a).all():
        raise ValueError("attraction must be finite")
    if (a < 0).any():
        raise ValueError("attraction must be non-negative")
    w = a[None, :] * np.exp(-beta * np.asarray(times, dtype=float))
    np.fill_diagonal(w, 0.0)
    tot = w.sum(axis=1)
    bad = np.flatnonzero(tot <= 0)
    if bad.size:
        raise ValueError(f"zero total attraction weight for row {int(bad[0])}")
    return w / tot[:, None]


def assign(origins: np.ndarray, shares: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(north, south) loads; origins may be 1-D (n,) or 2-D (slots, n)."""
    o = np.atleast_2d(np.asarray(origins, dtype=float))
    n = shares.shape[0]
    north = np.zeros((o.shape[0], n - 1))
    south = np.zeros_like(north)
    for k in range(n - 1):
        north[:, k] = o[:, : k + 1] @ shares[: k + 1, k + 1 :].sum(axis=1)
        south[:, k] = o[:, k + 1 :] @ shares[k + 1 :, : k + 1].sum(axis=1)
    if np.ndim(origins) == 1:
        return north[0], south[0]
    return north, south


@dataclass(frozen=True)
class OdParams:
    hop_min: float
    beta: float
    mirror_morning: tuple[str, str]
    mirror_evening: tuple[str, str]
    transfer_factor: dict[str, float]


def load_od_params(path: Path | str | None = None) -> OdParams:
    items = json.loads(Path(path or ASSUMPTIONS_PATH).read_text(encoding="utf-8"))["items"]
    return OdParams(
        hop_min=float(items["od_hop_min"]["value"]),
        beta=float(items["od_gravity_beta"]["value"]),
        mirror_morning=tuple(items["od_mirror_morning_msk"]["value"]),
        mirror_evening=tuple(items["od_mirror_evening_msk"]["value"]),
        transfer_factor=dict(items["od_transfer_factor"]["value"]),
    )


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _msk_minutes(df: pl.DataFrame) -> np.ndarray:
    t = df["interval_start"].dt.convert_time_zone("Europe/Moscow")
    return (t.dt.hour().cast(pl.Int64) * 60 + t.dt.minute().cast(pl.Int64)).to_numpy()


def _station_sums(df: pl.DataFrame, mask: np.ndarray, index: dict[str, int]) -> np.ndarray:
    out = np.zeros(len(index))
    sub = df.filter(pl.Series(mask)) if df.height else df
    for sid, v in sub.group_by("station_id").agg(pl.col("entries").sum()).iter_rows():
        if sid in index:
            out[index[sid]] += v
    return out


def slot_od(
    entries: pl.DataFrame,
    attraction_entries: pl.DataFrame,
    line: LineRef,
    params: OdParams,
) -> tuple[list[datetime], np.ndarray]:
    """Per-slot OD matrices (pax per slot), od[s, i, j] = origins[s, i] * shares[i, j].

    Origins of a slot = entries x transfer factor. Attraction of station j comes from
    `attraction_entries` (caller decides: same day = truth, historical = forecast-time),
    multiplied by the transfer factor and mirrored by MSK clock time: a slot in the morning
    period uses evening-period entries and vice versa; other slots use the station's total.
    Attraction aggregates all rows of `attraction_entries` in the clock period regardless
    of date. Stations missing in a slot count as 0 entries.
    """
    stations = sorted(line.stations, key=lambda s: s.order)
    index = {s.id: i for i, s in enumerate(stations)}
    n = len(stations)
    if entries.height == 0:
        return [], np.zeros((0, n, n))
    for name, df in (("entries", entries), ("attraction_entries", attraction_entries)):
        if df.height == 0:
            continue
        unknown = sorted(set(df["station_id"].unique().to_list()) - set(index))
        if unknown:
            raise ValueError(f"unknown station_id in {name}: {unknown}")
        if df["entries"].null_count():
            raise ValueError(f"null values in {name}.entries")
    factor = np.array([params.transfer_factor.get(s.id, 1.0) for s in stations])

    slots = entries["interval_start"].unique().sort()
    pos = {t: i for i, t in enumerate(slots.to_list())}
    o = np.zeros((len(slots), n))
    rows = entries.select("station_id", "interval_start", "entries").iter_rows()
    for sid, t, v in rows:
        if sid in index:
            o[pos[t], index[sid]] += v
    o = o * factor

    am = _msk_minutes(attraction_entries) if attraction_entries.height else np.array([], int)
    m0, m1 = (_minutes(x) for x in params.mirror_morning)
    e0, e1 = (_minutes(x) for x in params.mirror_evening)
    in_m, in_e = (am >= m0) & (am < m1), (am >= e0) & (am < e1)
    total = np.ones(len(am), bool)
    att = {
        "morning": _station_sums(attraction_entries, in_e, index) * factor,
        "evening": _station_sums(attraction_entries, in_m, index) * factor,
        "other": _station_sums(attraction_entries, total, index) * factor,
    }
    times = travel_times(n, params.hop_min)

    sm = _msk_minutes(pl.DataFrame({"interval_start": slots}))
    kind = np.where((sm >= m0) & (sm < m1), 0, np.where((sm >= e0) & (sm < e1), 1, 2))
    od = np.zeros((len(slots), n, n))
    for code, name in enumerate(("morning", "evening", "other")):
        sel = kind == code
        if not sel.any():
            continue
        a = att[name]
        if not (a > 0).any():
            a = att["other"]
            if not (a > 0).any():
                raise ValueError("no attraction data")
        shares = od_shares(a, times, params.beta)
        od[sel] = o[sel][:, :, None] * shares[None, :, :]
    return slots.to_list(), od


def segment_demand(
    entries: pl.DataFrame,
    attraction_entries: pl.DataFrame,
    line: LineRef,
    params: OdParams,
) -> pl.DataFrame:
    """Static directed segment demand per slot (pax per 15-min slot).

    All trips of a slot load segments in that same slot (static); see `slot_od` for the
    origin / attraction rules.
    """
    n = len(line.stations)
    schema = {
        "segment_id": pl.String,
        "interval_start": pl.Datetime("us", "UTC"),
        "demand": pl.Float64,
    }
    if entries.height == 0:
        return pl.DataFrame(schema=schema)
    slot_list, od = slot_od(entries, attraction_entries, line, params)
    north = np.zeros((len(slot_list), n - 1))
    south = np.zeros_like(north)
    for k in range(n - 1):
        north[:, k] = od[:, : k + 1, k + 1 :].sum(axis=(1, 2))
        south[:, k] = od[:, k + 1 :, : k + 1].sum(axis=(1, 2))

    seg_n = {s.order: s.id for s in line.segments if s.direction == "north"}
    seg_s = {s.order: s.id for s in line.segments if s.direction == "south"}
    ids, starts, dem = [], [], []
    for i, t in enumerate(slot_list):
        for k in range(n - 1):
            ids += [seg_n[k], seg_s[k]]
            starts += [t, t]
            dem += [north[i, k], south[i, k]]
    return pl.DataFrame({"segment_id": ids, "interval_start": starts, "demand": dem}, schema=schema)


def sanity_report(entries: pl.DataFrame, line: LineRef, params: OdParams) -> pl.DataFrame:
    """Per (service date, MSK hour 05..24): peak hourly segment demand vs planned capacity.

    Service day starts 03:00 MSK; hour 24 is 00:00-01:00 after midnight. Attraction = same day.
    oracle: uses same-day truth, never use for decisions.
    """
    schema = {
        "date": pl.Date,
        "hour": pl.Int64,
        "max_hourly_demand": pl.Float64,
        "segment_id": pl.String,
        "pairs": pl.Int64,
        "capacity": pl.Float64,
        "ratio": pl.Float64,
    }
    if entries.height == 0:
        return pl.DataFrame(schema=schema)
    cap = float(line.params["train_capacity"].value)
    wd = line.params["planned_pairs_weekday"].value
    we = line.params["planned_pairs_weekend"].value

    msk = entries["interval_start"].dt.convert_time_zone("Europe/Moscow")
    shifted = msk - pl.duration(hours=3)
    keyed = entries.with_columns(shifted.dt.date().alias("_date"))
    out = []
    for d in sorted(keyed["_date"].unique().to_list()):
        day = keyed.filter(pl.col("_date") == d).drop("_date")
        types = day["day_type"].unique().to_list()
        if len(types) != 1:
            raise ValueError(f"service day {d}: expected one day_type, got {sorted(types)}")
        dt = types[0]
        pairs_list = wd if dt == "weekday" else we
        dem = segment_demand(day, day, line, params)
        t = dem["interval_start"].dt.convert_time_zone("Europe/Moscow")
        h = t.dt.hour()
        dem = dem.with_columns(pl.when(h < 3).then(h + 24).otherwise(h).alias("hour"))
        hourly = (
            dem.filter(pl.col("hour").is_between(5, 24))
            .group_by("segment_id", "hour")
            .agg(pl.col("demand").sum())
            .sort("hour", "demand", descending=[False, True])
            .group_by("hour", maintain_order=True)
            .first()
        )
        for hour, seg, v in hourly.select("hour", "segment_id", "demand").iter_rows():
            pairs = int(pairs_list[hour - 5])
            c = pairs * cap
            out.append((d, hour, v, seg, pairs, c, v / c if c else float("inf")))
    cols = list(schema)
    return pl.DataFrame(out, schema=schema, orient="row").select(cols)
