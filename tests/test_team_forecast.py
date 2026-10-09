import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from metro_control import team_forecast as tf
from metro_control.adapters import AdapterError, read_forecast
from metro_control.entries import load_holidays
from metro_control.ingest import classify, ingest
from metro_control.mock import synthetic_entries
from metro_control.team_schema import validate_team_rows

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "tests" / "fixtures" / "team_stub.json"
AS_OF = datetime(2026, 11, 12, 14, 0, tzinfo=UTC)  # 17:00 MSK


@pytest.fixture(scope="module")
def history():
    return synthetic_entries(date(2026, 9, 1), 28)


@pytest.fixture(scope="module")
def tfc(history):
    return tf.read_team_forecast(STUB, as_of=AS_OF, history=history)


def test_stub_to_package(tfc):
    pkg = tfc.package
    rows = pkg.payload.rows
    assert len(rows) == 152
    assert tfc.no_data == ["tekhnologichesky_institut"]
    assert min(r.ts for r in rows) == AS_OF
    assert max(r.ts for r in rows) == datetime(2026, 11, 12, 15, 45, tzinfo=UTC)
    assert {r.horizon_min for r in rows} == set(range(15, 121, 15))
    assert pkg.data_mode == "mock" and pkg.payload.status == "mock"
    assert tfc.context is None
    from metro_control.contracts import ForecastPackage

    ForecastPackage.model_validate(pkg.model_dump(mode="json"))


def test_quarters_sum_to_hour(tfc):
    stub = json.loads(STUB.read_text())

    def q50(ts, h):
        return next(
            r["q50"]
            for r in stub
            if r["station_id"] == "devyatkino" and r["ts"].startswith(ts) and r["horizon_min"] == h
        )

    dev = [r for r in tfc.package.payload.rows if r.station_id == "devyatkino"]
    assert sum(r.q50 for r in dev[:4]) == pytest.approx(q50("2026-11-12T17", 60), abs=1e-6)
    assert sum(r.q50 for r in dev[4:]) == pytest.approx(q50("2026-11-12T18", 120), abs=1e-6)


def test_as_of_errors(history, tmp_path):
    with pytest.raises(AdapterError):
        tf.read_team_forecast(STUB, as_of=datetime(2026, 11, 12, 14, 30, tzinfo=UTC))
    with pytest.raises(AdapterError):
        tf.read_team_forecast(STUB, as_of=datetime(2026, 11, 12, 17, 0, tzinfo=UTC))
    rows = json.loads(STUB.read_text())
    rows[0]["q10"] = rows[0]["q50"] + 5
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(rows))
    with pytest.raises(AdapterError, match="q10"):
        tf.read_team_forecast(bad, as_of=AS_OF)


def test_profile():
    prof = tf.load_profile()
    assert sum(prof[("devyatkino", "рабочий", 8)]) == pytest.approx(1.0)
    assert tf.shares(prof, "devyatkino", "суббота", 0) == prof[("devyatkino", "рабочий", 0)]
    assert tf.shares(prof, "nope", "суббота", 0) == (0.25,) * 4
    assert tf.theirs_day_type("holiday") == "воскресенье"
    hol = sorted(load_holidays())[0]  # a weekday holiday -> воскресенье
    assert tf.profile_day_type(datetime(hol.year, hol.month, hol.day, 9, 0, tzinfo=UTC)) == (
        "воскресенье"
    )
    assert tf.profile_day_type(datetime(2026, 11, 12, 9, 0, tzinfo=UTC)) == "рабочий"


@pytest.fixture(scope="module")
def mock_file(history, tmp_path_factory):
    obj = tf.mock_team_serve(history, AS_OF)
    p = tmp_path_factory.mktemp("m") / "serve.json"
    p.write_text(json.dumps(obj, ensure_ascii=False))
    return p, obj


def test_mock_team_serve(history, mock_file):
    p, obj = mock_file
    assert validate_team_rows(obj["records"]) == []
    t = tf.read_team_forecast(p, history=history)
    assert len(t.package.payload.rows) == 152
    assert t.package.data_mode == "mock"
    v = [r for r in t.package.payload.rows if r.station_id == "vosstaniya"]
    assert len(v) == 8 and all(r.is_anomaly for r in v)
    assert t.context["meta"]["weather"]
    assert (
        t.context["explanations"][0]["ts"].endswith("Z")
        or "+00:00" in t.context["explanations"][0]["ts"]
    )


def test_classify_names():
    for n in ("serve.json", "stub_x.json", "team_forecast.json"):
        assert classify(n) == "forecast"


def test_ingest_team_files(mock_file, tmp_path):
    p, _ = mock_file
    files = [("out.json", p.read_bytes())]
    rep = ingest(files, tmp_path, now=datetime(2026, 11, 12, 14, 0, tzinfo=UTC))
    assert rep.run_dir and (rep.run_dir / "team_context.json").is_file()
    assert tf.read_team_context(rep.run_dir)["meta"]["weather"]
    fc = json.loads((rep.run_dir / "forecast.json").read_text())
    assert fc["payload"]["status"] == "mock"
    assert any("нет данных" in n.text for n in rep.notes)
    rep2 = ingest(
        [("stub.json", STUB.read_bytes())],
        tmp_path / "b",
        now=datetime(2026, 11, 12, 14, 0, tzinfo=UTC),
    )
    assert any("нет данных" in n.text and n.level != "error" for n in rep2.notes)
    assert tf.read_team_context(rep2.run_dir) is None


def test_read_forecast_dispatch(history):
    pkg = read_forecast(STUB, as_of=AS_OF, history=history)
    assert len(pkg.payload.rows) == 152


def test_app_no_data_caption(mock_file, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    p, _ = mock_file
    rep = ingest([("out.json", p.read_bytes())], tmp_path, now=AS_OF)
    run = tmp_path / "run"
    shutil.copytree(rep.run_dir, run)
    monkeypatch.setenv("METRO_RUN_DIR", str(run))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    at.session_state["s4_station"] = "tekhnologichesky_institut"
    at.run()
    assert not at.exception
    assert any("Нет данных в прогнозе команды" in c.value for c in at.caption)


def test_duplicate_record_error(tmp_path):
    rows = json.loads(STUB.read_text())
    rows.append(dict(rows[0]))
    bad = tmp_path / "dup.json"
    bad.write_text(json.dumps(rows))
    with pytest.raises(AdapterError, match="duplicate"):
        tf.read_team_forecast(bad, as_of=AS_OF)


def test_as_of_not_on_hour_message():
    with pytest.raises(AdapterError, match="MSK hour"):
        tf.read_team_forecast(STUB, as_of=datetime(2026, 11, 12, 14, 30, tzinfo=UTC))


def test_stale_team_context_removed(mock_file, tmp_path):
    p, _ = mock_file
    now = datetime(2026, 11, 12, 14, 0, tzinfo=UTC)
    rep = ingest([("out.json", p.read_bytes())], tmp_path, now=now)
    assert (rep.run_dir / "team_context.json").is_file()
    rep2 = ingest([("stub.json", STUB.read_bytes())], tmp_path, now=now)
    assert rep2.run_dir == rep.run_dir
    assert not (rep2.run_dir / "team_context.json").exists()


def _stack_file(history, tmp_path, as_of_msk_hm):
    h, m = as_of_msk_hm
    as_of = datetime(2026, 9, 30, h - 3, m, tzinfo=UTC)
    obj = tf.mock_team_serve(history, as_of, model="stack")
    p = tmp_path / f"stack_{h}{m}.json"
    p.write_text(json.dumps(obj, ensure_ascii=False))
    return p, obj, as_of


@pytest.mark.parametrize("hm", [(14, 0), (14, 30)])
def test_stack_mock(history, tmp_path, hm):
    p, obj, as_of = _stack_file(history, tmp_path, hm)
    assert obj["meta"]["model"] == "stack"
    assert {r["horizon_min"] for r in obj["records"]} == {30, 60, 90, 120}
    t = tf.read_team_forecast(p, history=history)
    rows = t.package.payload.rows
    assert len(rows) == 19 * 8 and t.no_data == ["tekhnologichesky_institut"]
    assert {r.horizon_min for r in rows} == set(range(15, 121, 15))
    assert t.package.payload.as_of == as_of and t.package.payload.status == "mock"
    for rec in obj["records"]:
        ts = datetime.fromisoformat(rec["ts"]).astimezone(UTC)
        qs = [
            r.q50
            for r in rows
            if r.station_id == rec["station_id"]
            and ts <= r.ts < ts.replace() + (datetime(2000, 1, 1, 0, 30) - datetime(2000, 1, 1))
        ]
        assert len(qs) == 2 and sum(qs) == pytest.approx(rec["q50"], abs=1e-6)


def test_stack_as_of_errors(history, tmp_path):
    p, _, _ = _stack_file(history, tmp_path, (14, 0))
    with pytest.raises(AdapterError, match=":00 or :30"):
        tf.read_team_forecast(p, as_of=datetime(2026, 9, 30, 11, 15, tzinfo=UTC))
    with pytest.raises(AdapterError, match="MSK hour"):
        tf.read_team_forecast(STUB, as_of=datetime(2026, 11, 12, 14, 30, tzinfo=UTC))


def test_fallback_note(tmp_path):
    from metro_control.events import team_banner

    rows = json.loads(STUB.read_text())
    p = tmp_path / "fb.json"
    p.write_text(
        json.dumps(
            {"records": rows, "meta": {"fallback": {"from": "stack", "reason": "нет весов"}}}
        )
    )
    t = tf.read_team_forecast(p, as_of=AS_OF)
    assert len(t.package.payload.rows) == 152
    b = team_banner(t.context, {})
    assert any("часовая модель" in w and "нет весов" in w for w in b["warnings"])


def test_ingest_stack_and_cli(history, tmp_path):
    p, _, as_of = _stack_file(history, tmp_path, (14, 30))
    rep = ingest([("out.json", p.read_bytes())], tmp_path / "r", now=as_of)
    assert rep.run_dir and (rep.run_dir / "team_context.json").is_file()
    from metro_control.cli import main

    out = tmp_path / "cli.json"
    rc = main(
        ["team-mock", "--as-of", "2026-09-30T14:30:00+03:00", "--model", "stack", "--out", str(out)]
    )
    assert rc == 0 and json.loads(out.read_text())["meta"]["model"] == "stack"


def test_station_reasons_stack_span(history, tmp_path):
    from metro_control.events import station_reasons

    p, _, _ = _stack_file(history, tmp_path, (14, 0))
    ctx = tf.read_team_forecast(p, history=history).context
    ctx["explanations"] = [
        {
            "station_id": "devyatkino",
            "ts": "2026-09-30T11:30:00Z",
            "reasons": [{"text": "дождь", "effect_pct": 5.0}],
        }
    ]
    slots = [
        datetime(2026, 9, 30, 11, 0, tzinfo=UTC)
        + i * (datetime(2000, 1, 1, 0, 15) - datetime(2000, 1, 1))
        for i in range(6)
    ]
    rows = station_reasons(ctx, slots)["devyatkino"]
    assert [bool(r) for r in rows] == [False, False, True, True, False, False]
