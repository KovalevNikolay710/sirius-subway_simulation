import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from metro_control.contracts import ForecastRow
from metro_control.line import station_ids
from metro_control.mock import mock_forecast
from metro_control.team_schema import to_team_rows, validate_team_rows
from metro_control.validate import validate_file

FIX = json.loads((Path(__file__).parent / "fixtures" / "team_stub_rows.json").read_text())
ROOT = Path(__file__).resolve().parents[1]


def test_fixture_ok():
    assert validate_team_rows(FIX) == []


def test_q50_gt_q90():
    rows = copy.deepcopy(FIX)
    rows[0]["q50"] = rows[0]["q90"] + 1
    errs = validate_team_rows(rows)
    assert len(errs) == 1 and "q50" in errs[0] and "q90" in errs[0]


def test_bad_minute_and_extra_key():
    rows = copy.deepcopy(FIX)
    rows[0]["ts"] = "2026-11-12T05:10:00+03:00"
    assert validate_team_rows(rows)
    rows = copy.deepcopy(FIX)
    rows[0]["interval_start"] = rows[0]["ts"]
    assert validate_team_rows(rows)


def test_mock_forecast_to_team(fx):
    pkg = mock_forecast(fx, datetime(2026, 9, 30, 9, 0, tzinfo=UTC))
    rows = to_team_rows(pkg)
    assert validate_team_rows(rows) == []
    assert len(rows) == 152
    for sid in station_ids():
        assert [r["horizon_min"] for r in rows if r["station_id"] == sid] == list(
            range(15, 121, 15)
        )


def _row(**kw):
    base = {
        "station_id": "avtovo", "ts": "2026-09-30T09:00:00Z", "horizon_min": 15,
        "q10": 1, "q50": 2, "q90": 3, "baseline": 2, "is_anomaly": False, "model_version": "m",
    }  # fmt: skip
    return ForecastRow.model_validate({**base, **kw})


@pytest.mark.parametrize("kw", [{"horizon_min": 135}, {"baseline": -1}, {"horizon_min": 20}])
def test_row_bad(kw):
    with pytest.raises(ValidationError):
        _row(**kw)


def test_horizon_must_match_ts(fx, tmp_path):
    pkg = mock_forecast(fx, datetime(2026, 9, 30, 9, 0, tzinfo=UTC))
    d = pkg.model_dump(mode="json")
    d["payload"]["rows"][0]["horizon_min"] = 30
    with pytest.raises(ValidationError, match="horizon_min"):
        type(pkg).model_validate(d)


def test_station_ids_team():
    ids = station_ids()
    assert {"prospekt_veteranov", "vosstaniya", "tekhnologichesky_institut"} <= set(ids)


def test_v01_rejected(tmp_path):
    d = json.loads((ROOT / "contracts/v0_2/examples/valid/forecast.json").read_text())
    d["schema_version"] = "0.1"
    p = tmp_path / "f.json"
    p.write_text(json.dumps(d))
    errs = validate_file(p)
    assert errs and "schema_version" in errs[0]


def test_bad_ts_strings():
    rows = copy.deepcopy(FIX)
    rows[0]["ts"] = "not-a-date"
    rows[1]["ts"] = "2026-11-12T05:00:00"
    errs = validate_team_rows(rows)
    assert any("0.ts: not an ISO datetime" in e for e in errs)
    assert any("1.ts: must carry an offset" in e for e in errs)
