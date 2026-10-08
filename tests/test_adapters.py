import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from metro_control.adapters import (
    AdapterError,
    build_team_bundle,
    read_explanation,
    read_forecast,
    read_recommendation,
)
from metro_control.cli import main
from metro_control.line import load_line
from metro_control.mock import mock_forecast, mock_recommendation, synthetic_entries
from metro_control.screen import explanation_text, load_bundle, source_statuses

ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def entries():
    return synthetic_entries(date(2026, 9, 1), 28)


def _rows(as_of=AS_OF, quant=True, tz="Z"):
    out = []
    for s in load_line().stations:
        for k in range(8):
            t = (as_of + timedelta(minutes=15 * k)).strftime("%Y-%m-%dT%H:%M:%S")
            row = {"station_id": s.id, "ts": t + tz, "q50": 100.0 + k}
            if quant:
                row |= {"q10": 80.0, "q90": 120.0 + k}
            out.append(row)
    return out


def _csv(path, rows):
    pl.DataFrame(rows).write_csv(path)
    return path


def test_csv_full(tmp_path):
    fc = read_forecast(_csv(tmp_path / "f.csv", _rows()))
    assert fc.data_mode == "real" and fc.payload.status == "ok"
    assert fc.payload.quantiles_ready and fc.payload.as_of == AS_OF
    assert fc.payload.rows[0].model_version == "person2"


def test_csv_no_quantiles(tmp_path):
    fc = read_forecast(_csv(tmp_path / "f.csv", _rows(quant=False)))
    assert not fc.payload.quantiles_ready
    assert all(r.q10 == r.q50 == r.q90 for r in fc.payload.rows)


def test_parquet_and_offset_time(tmp_path):
    p = tmp_path / "f.parquet"
    pl.DataFrame(_rows(tz="+00:00")).write_parquet(p)
    assert read_forecast(p).payload.as_of == AS_OF


def test_csv_errors(tmp_path):
    with pytest.raises(AdapterError, match="152"):
        read_forecast(_csv(tmp_path / "a.csv", _rows()[:-1]))
    rows = [{k: v for k, v in r.items() if k != "q50"} for r in _rows()]
    with pytest.raises(AdapterError, match="q50"):
        read_forecast(_csv(tmp_path / "b.csv", rows))
    with pytest.raises(AdapterError, match="ts"):
        read_forecast(_csv(tmp_path / "c.csv", _rows(tz="")))
    with pytest.raises(AdapterError) as e:
        read_forecast(_csv(tmp_path / "d.csv", _rows()[:-1]))
    assert str(e.value).startswith("d.csv")


def test_json_forecast_roundtrip(tmp_path, entries):
    fc = mock_forecast(entries, AS_OF)
    p = tmp_path / "f.json"
    p.write_text(fc.model_dump_json(), encoding="utf-8")
    assert read_forecast(p).payload.as_of == AS_OF
    p.write_text("{", encoding="utf-8")
    with pytest.raises(AdapterError, match="f.json"):
        read_forecast(p)


def _rec_payload(entries, **over):
    rec = mock_recommendation(
        __import__("metro_control.mock", fromlist=["x"]).mock_load(
            mock_forecast(entries, AS_OF),
            entries,
            load_line(),
            __import__("metro_control.od", fromlist=["x"]).load_od_params(),
            "weekday",
            {},
        ),
        AS_OF,
    )
    pl_ = rec.model_dump(mode="json")["payload"]
    pl_.pop("source")
    return pl_ | over


def test_recommendation_bare(tmp_path, entries):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(_rec_payload(entries)), encoding="utf-8")
    rec = read_recommendation(p)
    assert rec.payload.source == "person4" and rec.data_mode == "real"
    p.write_text(json.dumps(_rec_payload(entries, action="fly")), encoding="utf-8")
    with pytest.raises(AdapterError, match="action"):
        read_recommendation(p)


def test_explanation(tmp_path):
    p = tmp_path / "e.txt"
    p.write_text("  привет \n", encoding="utf-8")
    assert read_explanation(p) == "привет"
    p.write_text(" \n", encoding="utf-8")
    with pytest.raises(AdapterError):
        read_explanation(p)


@pytest.fixture(scope="module")
def sources(tmp_path_factory, entries):
    d = tmp_path_factory.mktemp("src")
    fc = _csv(d / "f.csv", _rows())
    r = d / "r.json"
    r.write_text(json.dumps(_rec_payload(entries)), encoding="utf-8")
    e = d / "e.txt"
    e.write_text("Потому что пик.", encoding="utf-8")
    return fc, r, e


def test_team_bundle_empty(tmp_path, entries):
    st = build_team_bundle(tmp_path / "o", entries)
    assert {s.status for s in st.values()} == {"missing"}
    assert {s.origin for s in st.values()} == {"mock"}
    assert main(["validate", str(tmp_path / "o")]) == 0
    assert not (tmp_path / "o" / "explanation.txt").exists()


@pytest.fixture(scope="module")
def full_dir(tmp_path_factory, entries, sources):
    out = tmp_path_factory.mktemp("full")
    fc, r, e = sources
    st = build_team_bundle(out, entries, forecast=fc, recommendation=r, explanation=e)
    assert all(s.status == "ok" for s in st.values())
    return out


def test_team_bundle_full(full_dir):
    b = load_bundle(full_dir)
    assert all(x.ok for x in b.values())
    assert b["forecast"].package.data_mode == "real"
    assert b["load"].package.data_mode == "real"
    assert b["load"].package.payload[0].ts == AS_OF
    assert b["recommendation"].package.payload.source == "person4"
    assert (full_dir / "explanation.txt").read_text(encoding="utf-8").strip()
    assert main(["validate", str(full_dir)]) == 0
    texts = [s.text for s in source_statuses(full_dir, b)]
    assert not any("Объяснение: нет" in t or "Прогноз: mock" in t for t in texts)


def test_team_bundle_broken_forecast(tmp_path, entries, sources):
    bad = tmp_path / "f.json"
    bad.write_text("{", encoding="utf-8")
    st = build_team_bundle(tmp_path / "o", entries, forecast=bad)
    assert st["forecast"].status == "error" and "f.json" in st["forecast"].reason
    assert load_bundle(tmp_path / "o")["forecast"].package.data_mode == "mock"
    ss = source_statuses(tmp_path / "o", load_bundle(tmp_path / "o"))
    assert any("Прогноз человека 2: ошибка" in s.text and "mock" in s.text for s in ss)


def test_no_future(tmp_path, entries, sources, full_dir):
    fc, _, _ = sources
    t = pl.col("ts")
    bumped = entries.with_columns(
        pl.when(t >= AS_OF)
        .then(pl.col("entries") * 100)
        .otherwise(pl.col("entries"))
        .alias("entries")
    )
    build_team_bundle(tmp_path / "o", bumped, forecast=fc)
    a = json.loads((tmp_path / "o" / "load.json").read_text(encoding="utf-8"))["payload"]
    b = json.loads((full_dir / "load.json").read_text(encoding="utf-8"))["payload"]
    assert [r | {"demand": 0, "r": 0} for r in a] == [r | {"demand": 0, "r": 0} for r in b]
    assert [r["demand"] for r in a] == pytest.approx([r["demand"] for r in b])


def test_statuses_failures(tmp_path, full_dir):
    def broken(name, mutate):
        d = tmp_path / name
        shutil.copytree(full_dir, d)
        mutate(d)
        return d

    cases = {
        "nof": (lambda d: (d / "forecast.json").unlink(), "Прогноз: нет файла forecast.json"),
        "norec": (lambda d: (d / "recommendation.json").unlink(), "Рекомендация: нет"),
        "bad": (
            lambda d: (d / "load.json").write_text("{", encoding="utf-8"),
            "Загрузка перегонов: пакет повреждён",
        ),
        "noexp": (
            lambda d: (d / "explanation.txt").unlink(),
            "Объяснение: нет текста от человека 4 — показана причина из рекомендации",
        ),
    }
    from streamlit.testing.v1 import AppTest

    for name, (mut, expect) in cases.items():
        d = broken(name, mut)
        texts = [s.text for s in source_statuses(d, load_bundle(d))]
        assert any(expect in t for t in texts), (name, texts)
        import os

        os.environ["METRO_RUN_DIR"] = str(d)
        os.environ["METRO_SIM_DIR"] = str(tmp_path / "nosim")
        try:
            at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
            at.switch_page("views/data.py").run()
        finally:
            del os.environ["METRO_RUN_DIR"], os.environ["METRO_SIM_DIR"]
        assert not at.exception, name
        shown = " ".join(x.value for x in [*at.error, *at.warning, *at.markdown])
        assert expect in shown, (name, shown)


def test_statuses_bad_sources_json(tmp_path, full_dir):
    d = tmp_path / "s"
    shutil.copytree(full_dir, d)
    (d / "sources.json").write_text("{", encoding="utf-8")
    ss = source_statuses(d, load_bundle(d))
    assert any("sources.json" in s.text and s.level == "warning" for s in ss)


def test_explanation_text(full_dir, tmp_path):
    rec = load_bundle(full_dir)["recommendation"].package
    assert explanation_text(full_dir, rec)[1] == "person4"
    assert explanation_text(tmp_path, rec) == (rec.payload.reason, "reason")


def test_cli_team_bundle(tmp_path, sources, capsys):
    fc, r, e = sources
    out = tmp_path / "t"
    rc = main(["team-bundle", "--out", str(out), "--forecast", str(fc), "--explanation", str(e)])
    assert rc == 0
    txt = capsys.readouterr().out
    assert "forecast: ok (person2)" in txt and "recommendation: missing" in txt
    rc = main(["team-bundle", "--out", str(out), "--forecast", str(tmp_path / "none.csv")])
    assert rc == 0 and "forecast: error:" in capsys.readouterr().out
    assert main(["team-bundle", "--out", str(out), "--entries", str(tmp_path / "x.parquet")]) == 1


def test_rec_naive_as_of(tmp_path, entries):
    p = tmp_path / "r.json"
    pay = _rec_payload(entries, as_of="2026-09-28T17:30:00")
    p.write_text(json.dumps(pay), encoding="utf-8")
    with pytest.raises(AdapterError, match="as_of"):
        read_recommendation(p)


@pytest.mark.parametrize("bad", ["abc", "nan", "inf"])
def test_csv_bad_numbers(tmp_path, bad):
    rows = _rows()
    rows[3]["q90"] = bad
    p = tmp_path / "n.csv"
    pl.DataFrame(rows, schema_overrides={"q90": pl.String}).write_csv(p)
    with pytest.raises(AdapterError, match="q90"):
        read_forecast(p)


def test_rec_after_forecast_rejected(tmp_path, entries, sources):
    fc, _, e = sources
    r = tmp_path / "r.json"
    later = AS_OF + timedelta(minutes=15)
    r.write_text(
        json.dumps(_rec_payload(entries, as_of=later.isoformat(), start=later.isoformat())),
        encoding="utf-8",
    )
    st = build_team_bundle(tmp_path / "o", entries, forecast=fc, recommendation=r, explanation=e)
    assert st["recommendation"].status == "error"
    assert "future data" in st["recommendation"].reason
    b = load_bundle(tmp_path / "o")
    assert b["recommendation"].package.payload.source == "mock"
    assert st["explanation"].status == "error"
    assert not (tmp_path / "o" / "explanation.txt").exists()


def test_rec_drives_as_of(tmp_path, entries, sources):
    _, r, _ = sources
    build_team_bundle(tmp_path / "o", entries, recommendation=r)
    assert load_bundle(tmp_path / "o")["forecast"].package.payload.as_of == AS_OF


def test_explanation_ignored_for_mock_rec(tmp_path, entries, sources):
    _, _, e = sources
    st = build_team_bundle(tmp_path / "o", entries, explanation=e)
    assert st["explanation"].status == "error"
    rec = load_bundle(tmp_path / "o")["recommendation"].package
    (tmp_path / "o" / "explanation.txt").write_text("чужой текст", encoding="utf-8")
    assert explanation_text(tmp_path / "o", rec) == (rec.payload.reason, "reason")


def test_cli_forecast_as_of(tmp_path, capsys):
    rows = _rows()
    p = _csv(tmp_path / "f.csv", rows)
    assert (
        main(
            [
                "team-bundle",
                "--out",
                str(tmp_path / "o"),
                "--forecast",
                str(p),
                "--forecast-as-of",
                "2026-09-28T17:30:00+03:00",
            ]
        )
        == 0
    )
    assert "forecast: ok" in capsys.readouterr().out
    assert (
        main(["team-bundle", "--out", str(tmp_path / "o"), "--forecast-as-of", "2026-09-28T17:30"])
        == 1
    )


def test_csv_team_columns(tmp_path):
    rows = _rows()
    for i, r in enumerate(rows):
        r["baseline"] = 7.5
        r["is_anomaly"] = i == 1
        r["model_version"] = "xgb_7"
        r["horizon_min"] = 15 * (i % 8 + 1)
    fc = read_forecast(_csv(tmp_path / "f.csv", rows))
    r0, r1 = fc.payload.rows[0], fc.payload.rows[1]
    assert (r0.baseline, r0.is_anomaly, r0.model_version) == (7.5, False, "xgb_7")
    assert r1.is_anomaly
    rows[3]["horizon_min"] = 90
    with pytest.raises(AdapterError, match="row 3"):
        read_forecast(_csv(tmp_path / "g.csv", rows))
