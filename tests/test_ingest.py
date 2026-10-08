import io
import json
import zipfile
from datetime import UTC, datetime

import pytest

from metro_control import scenario
from metro_control.ingest import classify, expand, ingest
from metro_control.mock import build_mock_bundle
from metro_control.screen import load_recommendations

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    return build_mock_bundle(tmp_path_factory.mktemp("team"))


def test_classify():
    assert classify("Данные/1/Входные пассажиропотоки Линия 1 сен 2026 по 15-мин.xlsx") == "entries"
    assert classify("Пассажиропоток 2026 Линия 1.xlsx") == "other"
    assert classify("x/forecast.csv") == "forecast"
    assert classify("forecast_v2.parquet") == "forecast"
    assert classify("recommendations.jsonl") == "recommendation"
    assert classify("explanation.txt") == "explanation"
    assert classify("explanations/mock-1.txt") == "explanation"
    assert classify("notes.txt") == "other"
    assert classify("sim/timeline.json") == "sim"
    assert classify("pack.ZIP") == "zip"


def test_expand_flattens_and_guards():
    files = expand(
        [("a.zip", _zip({"dir/forecast.json": b"{}", "__MACOSX/x": b""})), ("b.txt", b"")]
    )
    assert [n for n, _ in files] == ["dir/forecast.json", "b.txt"]
    with pytest.raises(ValueError, match="не ZIP"):
        expand([("bad.zip", b"not a zip")])


def test_ingest_team_zip_with_sim(team, result, tmp_path):
    sim = scenario.write_run(result, tmp_path / "src_sim")
    files = {
        f"team/{n}": (team / n).read_bytes() for n in ("forecast.json", "recommendations.jsonl")
    }
    files |= {f"sim/{n}": (sim / n).read_bytes() for n in ("timeline.json", "actions.jsonl")}
    files["docs/readme.docx"] = b"x"
    rep = ingest([("all.zip", _zip(files))], tmp_path / "runs", now=NOW)
    assert rep.run_dir.name == "upload-20261008-120000" and (rep.run_dir / "load.json").is_file()
    assert rep.sim_dir is not None and rep.sim_dir.name == "compare-upload-20261008-120000"
    n = len((team / "recommendations.jsonl").read_text(encoding="utf-8").splitlines())
    assert len(load_recommendations(rep.run_dir).items) == n
    levels = {(x.file, x.level) for x in rep.notes}
    assert ("docs/readme.docx", "info") in levels and ("прогноз", "ok") in levels
    assert not any(x.level == "error" for x in rep.notes)


def test_ingest_bad_recommendation_line_reported(team, tmp_path):
    good = (team / "recommendations.jsonl").read_text(encoding="utf-8").splitlines()[0]
    rep = ingest(
        [("recommendations.jsonl", (good + "\n{broken\n").encode())],
        tmp_path / "runs",
        now=NOW,
    )
    assert any(x.level == "error" and "строка 2" in x.file for x in rep.notes)
    assert len(load_recommendations(rep.run_dir).items) == 1


def test_ingest_sim_only_keeps_current_packages(result, tmp_path):
    sim = scenario.write_run(result, tmp_path / "src_sim")
    rep = ingest(
        [("timeline.json", (sim / "timeline.json").read_bytes())], tmp_path / "runs", now=NOW
    )
    assert rep.run_dir is None and rep.sim_dir is not None
    assert not (tmp_path / "runs" / "upload-20261008-120000").exists()


def test_ingest_nothing_useful(tmp_path):
    rep = ingest([("a.pdf", b"%PDF")], tmp_path / "runs", now=NOW)
    assert any(x.level == "error" for x in rep.notes)


def test_ingest_explanations_by_id(team, tmp_path):
    rec_line = (team / "recommendations.jsonl").read_text(encoding="utf-8").splitlines()[0]
    rid = json.loads(rec_line)["payload"]["recommendation_id"]
    rep = ingest(
        [
            ("recommendations.jsonl", rec_line.encode()),
            (f"explanations/{rid}.txt", "Текст".encode()),
        ],
        tmp_path / "runs",
        now=NOW,
    )
    assert (rep.run_dir / "explanations" / f"{rid}.txt").read_text(encoding="utf-8") == "Текст"
