from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from metro_control.cli import main
from metro_control.entries import SCHEMA
from metro_control.line import LineRef, load_line
from metro_control.od import (
    OdParams,
    assign,
    load_od_params,
    od_shares,
    sanity_report,
    segment_demand,
    travel_times,
)

P3 = np.array([[0, 0.4, 0.6], [0.4, 0, 0.6], [0.25, 0.75, 0]])


def test_assign_hand_checked():
    n, s = assign(np.array([100.0, 50, 40]), P3)
    assert np.allclose(n, [100, 90])
    assert np.allclose(s, [30, 40])


def test_assign_direction_separation():
    n, s = assign(np.array([100.0, 0, 0]), np.array([[0, 0, 1.0], [0.5, 0, 0.5], [1, 0, 0]]))
    assert np.allclose(s, [0, 0])
    assert np.allclose(n, [100, 100])


def test_od_shares_properties():
    rng = np.random.default_rng(1)
    a = rng.uniform(1, 100, 19)
    t = travel_times(19, 2.75)
    p = od_shares(a, t, 0.03)
    assert np.allclose(p.sum(axis=1), 1, atol=1e-12)
    assert (np.diag(p) == 0).all() and (p >= 0).all()
    q = od_shares(np.ones(5), travel_times(5, 1.0), 0.1)
    assert q[0, 1] > q[0, 2] > q[0, 4]


def test_od_shares_errors():
    t = travel_times(3, 1.0)
    with pytest.raises(ValueError, match="row 0"):
        od_shares(np.array([0, 0, 0.0]), t, 0.03)
    with pytest.raises(ValueError, match="negative|non-negative"):
        od_shares(np.array([1, -1, 1.0]), t, 0.03)


def test_balance():
    rng = np.random.default_rng(2)
    n = 19
    p = od_shares(rng.uniform(1, 9, n), travel_times(n, 2.75), 0.03)
    o = rng.uniform(0, 500, n)
    ns, ss = assign(o, p)
    d = np.abs(np.arange(n)[:, None] - np.arange(n)[None, :])
    assert ns.sum() + ss.sum() == pytest.approx((o[:, None] * p * d).sum())


def _line3() -> LineRef:
    base = load_line()
    st = [s.model_copy() for s in base.stations[:3]]
    sg = []
    for k in range(2):
        a, b = st[k].id, st[k + 1].id
        sg.append(
            base.segments[0].model_copy(
                update={
                    "id": f"{a}__{b}",
                    "from_station": a,
                    "to_station": b,
                    "direction": "north",
                    "order": k,
                }
            )
        )
        sg.append(
            base.segments[1].model_copy(
                update={
                    "id": f"{b}__{a}",
                    "from_station": b,
                    "to_station": a,
                    "direction": "south",
                    "order": k,
                }
            )
        )
    return base.model_copy(update={"stations": st, "segments": sg})


def _params(tf=None) -> OdParams:
    p = load_od_params()
    return OdParams(p.hop_min, p.beta, p.mirror_morning, p.mirror_evening, tf or {})


def _entries(line, msk_hours, vals):
    rows = []
    for h in msk_hours:
        t = datetime(2026, 3, 2, h, 0, tzinfo=UTC) - timedelta(hours=3)
        for sid, v in zip([s.id for s in line.stations], vals(h), strict=True):
            rows.append((sid, t, "weekday", float(v)))
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def test_segment_demand_shape_transfer_mirror_immutable():
    line = _line3()
    e = _entries(line, [8, 12, 18], lambda h: [100, 50, 80])
    before = e.clone()
    d = segment_demand(e, e, line, _params())
    assert d.height == 3 * 4 and (d["demand"] >= 0).all()
    assert d.schema["ts"] == pl.Datetime("us", "UTC")
    assert e.equals(before)
    # transfer factor 2 on station 0 doubles its origin contribution on the north seg 0
    d2 = segment_demand(e, e, line, _params({line.stations[1].id: 1.0}))
    assert d2.equals(d)
    f = segment_demand(
        e.filter(pl.col("station_id") == line.stations[0].id),
        e,
        line,
        _params({line.stations[0].id: 2.0}),
    )
    g = segment_demand(e.filter(pl.col("station_id") == line.stations[0].id), e, line, _params())
    assert f["demand"].sum() > g["demand"].sum()
    # mirror: only evening differs; morning slot result changes, midday does not
    e_b = _entries(line, [8, 12, 18], lambda h: [100, 50, 80] if h != 18 else [100, 50, 800])
    mid = pl.col("ts").dt.convert_time_zone("Europe/Moscow").dt.hour()
    da = segment_demand(e, e, line, _params())
    db = segment_demand(e, e_b, line, _params())
    assert not np.allclose(
        da.filter(mid == 8)["demand"].to_numpy(), db.filter(mid == 8)["demand"].to_numpy()
    )
    # the evening slot uses morning entries only, which are identical
    assert np.allclose(
        da.filter(mid == 18)["demand"].to_numpy(), db.filter(mid == 18)["demand"].to_numpy()
    )


def test_missing_station_is_zero():
    line = _line3()
    e = _entries(line, [12], lambda h: [100, 50, 80]).filter(
        pl.col("station_id") != line.stations[2].id
    )
    d = segment_demand(e, _entries(line, [12], lambda h: [1, 1, 1]), line, _params())
    assert d.height == 4 and (d["demand"] >= 0).all()


def _day19(day_type="weekday"):
    line = load_line()
    rows = []
    for k in range(96):
        t = datetime(2026, 3, 2, 0, 0, tzinfo=UTC) + timedelta(minutes=15 * k)
        for s in line.stations:
            rows.append((s.id, t, day_type, 10.0))
    return line, pl.DataFrame(rows, schema=SCHEMA, orient="row")


def test_sanity_report_hours_and_ratio():
    line, e = _day19()
    rep = sanity_report(e, line, load_od_params())
    assert sorted(rep["hour"].to_list()) == list(range(5, 25))
    assert rep["date"].n_unique() == 1
    r = rep.filter(pl.col("hour") == 8).row(0, named=True)
    assert r["pairs"] == 20 or r["pairs"] == line.params["planned_pairs_weekday"].value[3]
    assert r["capacity"] == r["pairs"] * 1458
    assert r["ratio"] == pytest.approx(r["max_hourly_demand"] / (r["pairs"] * 1458))
    # uniform 10/station/slot: hour demand on central segment bounded by hour total entries
    assert 0 < r["max_hourly_demand"] <= 19 * 10 * 4 * 1.5 * 1.3
    h24 = rep.filter(pl.col("hour") == 24).row(0, named=True)
    assert h24["pairs"] == line.params["planned_pairs_weekday"].value[19]


def test_sanity_weekend_pairs():
    line, e = _day19("sunday")
    rep = sanity_report(e, line, load_od_params())
    assert (
        rep.filter(pl.col("hour") == 6)["pairs"][0] == line.params["planned_pairs_weekend"].value[1]
    )


def test_cli_od_sanity(tmp_path, capsys):
    assert main(["od-sanity", "--entries", str(tmp_path / "no.parquet")]) == 1
    assert "not found" in capsys.readouterr().err
    _, e = _day19()
    p = tmp_path / "e.parquet"
    e.write_parquet(p)
    assert main(["od-sanity", "--entries", str(p)]) == 0
    assert "max ratio" in capsys.readouterr().out


def test_od_shares_non_finite():
    t = travel_times(3, 1.0)
    for bad in (np.nan, np.inf):
        with pytest.raises(ValueError, match="finite"):
            od_shares(np.array([1, bad, 1.0]), t, 0.03)


def test_segment_demand_rejects_bad_input():
    line = _line3()
    e = _entries(line, [12], lambda h: [1, 2, 3])
    bad = e.with_columns(
        pl.when(pl.col("station_id") == line.stations[0].id)
        .then(pl.lit("zzz"))
        .otherwise(pl.col("station_id"))
        .alias("station_id")
    )
    with pytest.raises(ValueError, match="zzz"):
        segment_demand(bad, e, line, _params())
    with pytest.raises(ValueError, match="zzz"):
        segment_demand(e, bad, line, _params())
    nul = e.with_columns(
        pl.when(pl.col("station_id") == line.stations[0].id)
        .then(None)
        .otherwise(pl.col("entries"))
        .alias("entries")
    )
    with pytest.raises(ValueError, match="null"):
        segment_demand(nul, e, line, _params())


def test_mirror_fallback_and_no_data():
    line = _line3()
    morning = _entries(line, [8], lambda h: [100, 50, 80])
    hist = _entries(line, [12], lambda h: [100, 50, 80])  # no evening rows
    d = segment_demand(morning, hist, line, _params())
    assert d.height == 4 and (d["demand"] >= 0).all()
    empty = hist.clear()
    with pytest.raises(ValueError, match="no attraction data"):
        segment_demand(morning, empty, line, _params())
    zeros = _entries(line, [12], lambda h: [0, 0, 0])
    with pytest.raises(ValueError, match="no attraction data"):
        segment_demand(morning, zeros, line, _params())


def test_sanity_mixed_day_type_and_unsorted():
    line, e = _day19()
    ok = e.sort("ts", descending=True)  # first row is the late 00:15 MSK slot
    assert sanity_report(ok, line, load_od_params()).height == 20
    mixed = e.with_columns(
        pl.when(pl.col("ts") == e["ts"][100])
        .then(pl.lit("sunday"))
        .otherwise(pl.col("day_type"))
        .alias("day_type")
    )
    with pytest.raises(ValueError, match="2026-03-02"):
        sanity_report(mixed, line, load_od_params())
