import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from metro_control import od
from metro_control.cli import main
from metro_control.contracts import ForecastPackage, LoadPackage, Recommendation
from metro_control.line import load_line, segment_ids, station_ids
from metro_control.mock import (
    build_mock_bundle,
    default_surge,
    mock_forecast,
    mock_load,
    mock_recommendation,
    synthetic_entries,
)
from metro_control.screen import (
    BAND_LABELS_RU,
    action_card,
    band,
    load_bundle,
    load_package,
    segment_view,
    station_series,
)
from metro_control.validate import validate_file

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def entries():
    return synthetic_entries(date(2026, 9, 1), 28)


@pytest.fixture(scope="module")
def as_of():
    # 2026-09-28 is Monday; 17:30 MSK = 14:30 UTC
    return datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory, entries, as_of):
    return build_mock_bundle(tmp_path_factory.mktemp("bundle"), entries, as_of)


def test_band_boundaries():
    assert band(0.8) == "low"
    assert band(0.8001) == "mid"
    assert band(1.0) == "mid"
    assert band(1.0001) == "high"
    assert band(None) == "none"
    assert BAND_LABELS_RU["none"] == "нет движения"


def test_synthetic_shape(entries):
    assert entries.height == 19 * 96 * 28
    assert set(entries.columns) == {"station_id", "interval_start", "day_type", "entries"}


def test_forecast_ignores_future_and_valid(entries, as_of):
    base = mock_forecast(entries, as_of)
    assert len(base.payload.rows) == 152
    assert base.data_mode == "mock" and base.payload.status == "mock"
    assert base.payload.model_name == "mock-median"
    poisoned = entries.with_columns(
        pl.when(pl.col("interval_start") >= as_of)
        .then(1e9)
        .otherwise(pl.col("entries"))
        .alias("entries")
    )
    assert mock_forecast(poisoned, as_of).payload.rows == base.payload.rows


def test_forecast_no_history_zero(as_of):
    empty = synthetic_entries(date(2026, 9, 1), 1).clear()
    fc = mock_forecast(empty, as_of)
    assert all(r.q50 == 0 for r in fc.payload.rows)
    assert fc.payload.status == "degraded"


def test_load_formula_and_night(entries, as_of):
    line, params = load_line(), od.load_od_params()
    fc = mock_forecast(entries, as_of)
    ld = mock_load(fc, entries, line, params, "weekday", default_surge())
    assert len(ld.payload) == 36 * 8
    # 17:30 MSK weekday: pairs index 12 -> 30 pairs -> int(7.5+.5)=8
    row = ld.payload[0]
    assert row.departures == 8
    assert row.r == pytest.approx(row.demand / (8 * 1458))
    night = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)  # 03:00 MSK
    fc_n = mock_forecast(entries, night)
    ld_n = mock_load(fc_n, entries, line, params, "weekday", {})
    assert all(r.departures == 0 and r.r is None for r in ld_n.payload)


def test_recommendation_max_r(entries, as_of):
    line, params = load_line(), od.load_od_params()
    ld = mock_load(mock_forecast(entries, as_of), entries, line, params, "weekday", default_surge())
    rec = mock_recommendation(ld, as_of)
    assert rec.payload.action == "add_reserve" and rec.payload.source == "mock"
    best = max((r for r in ld.payload if r.r is not None), key=lambda r: r.r)
    assert rec.payload.target == best.segment_id
    assert rec.payload.start >= as_of
    assert rec.payload.end - rec.payload.start == timedelta(minutes=60)
    quiet = mock_load(mock_forecast(entries, as_of), entries, line, params, "weekday", {})
    assert mock_recommendation(quiet, as_of).payload.action == "none"


def test_bundle_valid_and_bands(bundle_dir, as_of):
    for name in ("forecast", "load", "recommendation", "entries"):
        assert validate_file(bundle_dir / f"{name}.json") == []
    res = load_bundle(bundle_dir)
    assert all(r.ok for r in res.values())
    bands = set()
    for k in range(8):
        v = segment_view(res["load"].package, as_of + k * timedelta(minutes=15))
        assert v.height == 36
        bands |= set(v["band"].to_list())
    assert {"low", "mid", "high"} <= bands
    ent = res["station_entries"].package.payload
    assert all(r.interval_start < as_of for r in ent)


def test_load_package_reasons(bundle_dir, tmp_path):
    r = load_package(tmp_path / "load.json", LoadPackage)
    assert not r.ok and r.reason == "load.json: file not found"
    (tmp_path / "load.json").write_text('{\n"a":\n', encoding="utf-8")
    r = load_package(tmp_path / "load.json", LoadPackage)
    assert r.reason.startswith("load.json: invalid JSON (line ")
    data = json.loads((bundle_dir / "forecast.json").read_text(encoding="utf-8"))
    data["payload"]["rows"][0]["q50"] = data["payload"]["rows"][0]["q90"] + 5
    (tmp_path / "forecast.json").write_text(json.dumps(data), encoding="utf-8")
    r = load_package(tmp_path / "forecast.json", ForecastPackage)
    assert not r.ok and r.reason.startswith("forecast.json: payload.rows.0")
    assert "q50" in r.reason


def test_station_series_drops_future(bundle_dir, as_of):
    res = load_bundle(bundle_dir)
    ent = res["station_entries"].package
    late = ent.model_copy(deep=True)
    row = late.payload[0].model_copy(update={"interval_start": as_of})
    late.payload.append(row)
    s = station_series(late, res["forecast"].package, late.payload[0].station_id)
    facts = s.filter(pl.col("kind") == "fact")
    assert facts.height and facts["interval_start"].max() < as_of
    assert s.filter(pl.col("kind") == "forecast").height == 8


def test_action_card_window():
    from metro_control.examples import recommendation

    d = recommendation()
    d["payload"]["start"] = "2026-09-30T09:00:00Z"
    d["payload"]["end"] = "2026-09-30T10:00:00Z"
    d["payload"]["target"] = segment_ids()[0]
    card = action_card(Recommendation.model_validate(d))
    assert card["window"] == "12:00–13:00"
    assert "→" in card["target"] and card["is_mock"]


def test_cli_mock_bundle(tmp_path, capsys):
    assert main(["mock-bundle", "--out", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "load.json").is_file()
    assert main(["validate", str(tmp_path / "o")]) == 0
    assert station_ids()


def _app(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("METRO_RUN_DIR", str(tmp_path))
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def test_app_good_bundle(bundle_dir, monkeypatch):
    at = _app(bundle_dir, monkeypatch).run()
    assert not at.exception


def test_app_broken_bundle(bundle_dir, tmp_path, monkeypatch):
    import shutil

    run = tmp_path / "run"
    shutil.copytree(bundle_dir, run)
    (run / "load.json").unlink()
    (run / "forecast.json").write_text("{not json", encoding="utf-8")
    at = _app(run, monkeypatch).run()
    assert not at.exception
    errs = " ".join(w.value for w in at.error)
    assert "Загрузка перегонов: нет файла load.json" in errs
    assert "Прогноз: пакет повреждён — forecast.json: invalid JSON" in errs


def test_app_missing_dir(tmp_path, monkeypatch):
    at = _app(tmp_path / "nope", monkeypatch).run()
    assert not at.exception


def test_service_day_type_per_slot(entries):
    line, params = load_line(), od.load_od_params()
    # Fri 2026-09-25 21:30 UTC = 00:30 MSK Sat, still Friday service day; 8 slots cross 03:00 MSK
    as_of = datetime(2026, 9, 25, 21, 30, tzinfo=UTC)
    fc = mock_forecast(entries, as_of)
    ld = mock_load(fc, entries, line, params, "weekday", {})
    dep = {r.interval_start: r.departures for r in ld.payload}
    assert dep[as_of] == 2  # 00:30 MSK Sat is Friday service day: hour 24, pairs[19]=7
    sat = datetime(2026, 9, 26, 5, 0, tzinfo=UTC)  # 08:00 MSK Saturday: weekend pairs[3]=21
    ld2 = mock_load(
        mock_forecast(
            entries,
            sat,
        ),
        entries,
        line,
        params,
        "weekday",
        {},
    )
    assert ld2.payload[0].departures == int(21 / 4 + 0.5)


def test_fallback_any_day_type(entries):
    only_wk = entries.filter(pl.col("day_type") == "weekday")
    sat = datetime(2026, 9, 26, 5, 0, tzinfo=UTC)
    fc = mock_forecast(only_wk, sat)
    assert fc.payload.status == "mock" and any(r.q50 > 0 for r in fc.payload.rows)


def test_naive_as_of_rejected(tmp_path):
    with pytest.raises(ValueError):
        build_mock_bundle(tmp_path, None, datetime(2026, 9, 28, 14, 30))


def test_not_utf8(tmp_path):
    (tmp_path / "load.json").write_bytes(b"\xff\xfe")
    r = load_package(tmp_path / "load.json", LoadPackage)
    assert r.reason == "load.json: unreadable (not UTF-8)"


def test_app_empty_load(bundle_dir, tmp_path, monkeypatch):
    import shutil

    run = tmp_path / "run"
    shutil.copytree(bundle_dir, run)
    d = json.loads((run / "load.json").read_text(encoding="utf-8"))
    d["payload"] = []
    (run / "load.json").write_text(json.dumps(d), encoding="utf-8")
    assert not _app(run, monkeypatch).run().exception


def test_cli_bad_parquet(tmp_path, capsys):
    bad = tmp_path / "x.parquet"
    bad.write_bytes(b"nope")
    assert main(["mock-bundle", "--out", str(tmp_path / "o"), "--entries", str(bad)]) == 1
    pl.DataFrame({"a": [1]}).write_parquet(tmp_path / "y.parquet")
    assert (
        main(
            ["mock-bundle", "--out", str(tmp_path / "o"), "--entries", str(tmp_path / "y.parquet")]
        )
        == 1
    )
    assert "Traceback" not in capsys.readouterr().err
