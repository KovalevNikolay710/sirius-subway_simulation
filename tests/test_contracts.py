import pytest
from pydantic import ValidationError

from metro_control import examples as ex
from metro_control.contracts import ForecastPackage, Recommendation


def _err(model, doc) -> str:
    with pytest.raises(ValidationError) as e:
        model.model_validate(doc)
    return str(e.value)


def test_forecast_valid():
    ForecastPackage.model_validate(ex.forecast())
    ForecastPackage.model_validate(ex.forecast(False))


def test_forecast_151_rows():
    d = ex.forecast()
    d["payload"]["rows"].pop()
    assert "152" in _err(ForecastPackage, d)


def test_forecast_quantile_order():
    d = ex.forecast()
    d["payload"]["rows"][0]["q50"] = 1e6
    msg = _err(ForecastPackage, d)
    assert "q50" in msg and "q90" in msg


def test_forecast_unknown_station():
    d = ex.forecast()
    d["payload"]["rows"][0]["station_id"] = "nowhere"
    assert "station_id" in _err(ForecastPackage, d)


def test_forecast_past_interval():
    d = ex.forecast()
    d["payload"]["rows"][0]["interval_start"] = "2026-09-30T08:45:00Z"
    assert "interval_start" in _err(ForecastPackage, d)


def test_forecast_no_quantiles_requires_equal():
    d = ex.forecast(False)
    d["payload"]["rows"][0]["q90"] += 1
    assert "quantiles_ready" in _err(ForecastPackage, d)


def test_envelope_failures():
    d = ex.recommendation()
    d["generated_at"] = "2026-09-30T12:00:00+03:00"
    assert "generated_at" in _err(Recommendation, d)
    d = ex.recommendation()
    d["manifest"]["checksum"] = "md5:1"
    assert "checksum" in _err(Recommendation, d)
    d = ex.recommendation()
    d["schema_version"] = "0.2"
    assert "schema_version" in _err(Recommendation, d)
    d = ex.recommendation()
    d["extra"] = 1
    assert "extra" in _err(Recommendation, d)


def test_zero_capacity_rejected():
    from metro_control.contracts import LoadPackage

    d = ex.load()
    d["payload"][0]["capacity_per_train"] = 0
    assert "capacity_per_train" in _err(LoadPackage, d)


def test_nan_rejected():
    from metro_control.contracts import LoadPackage

    d = ex.load()
    d["payload"][0]["demand"] = float("nan")
    _err(LoadPackage, d)


def test_no_future_checks():
    import json

    from metro_control.validate import validate_file  # noqa: F401

    for name in ("forecast_as_of_after_generated", "recommendation_start_before_as_of"):
        d = ex.invalid_cases()[name]
        assert "as_of" in _err(ForecastPackage if "forecast" in name else Recommendation, d)
        json.dumps(d)
