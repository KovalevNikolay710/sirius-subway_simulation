from pathlib import Path

from metro_control.cli import export_schemas, main
from metro_control.validate import validate_file

EX = Path(__file__).resolve().parents[1] / "contracts" / "v0_1"


def test_valid_examples():
    files = sorted((EX / "examples" / "valid").glob("*.json"))
    assert len(files) >= 6
    for f in files:
        assert validate_file(f) == [], f


def test_invalid_examples():
    files = sorted((EX / "examples" / "invalid").glob("*.json"))
    assert len(files) >= 6
    for f in files:
        errs = validate_file(f)
        assert errs, f
        for e in errs:
            file, field, _msg = e.split(": ", 2)
            assert f.name in file and field


def test_cli_exit_codes(capsys):
    assert main(["validate", str(EX / "examples" / "valid")]) == 0
    assert main(["validate", str(EX / "examples" / "invalid")]) == 1
    assert "Traceback" not in capsys.readouterr().out


def test_schemas_no_drift(tmp_path):
    fresh = export_schemas(tmp_path)
    assert fresh
    for p in fresh:
        assert (EX / "schemas" / p.name).read_text() == p.read_text()


def test_doctor_real_root(capsys):
    assert main(["doctor"]) == 0


def test_dir_skips_schemas(capsys):
    assert main(["validate", str(EX / "schemas")]) == 1  # no package files
    assert main(["validate", str(EX / "examples" / "valid"), str(EX / "schemas")]) == 0
    assert "schema.json" not in capsys.readouterr().out


def test_missing_line_no_traceback(monkeypatch):
    import metro_control.line as line

    line._default.cache_clear()
    monkeypatch.setattr(line, "DEFAULT_PATH", Path("/nonexistent/line.json"))
    try:
        errs = validate_file(EX / "examples" / "valid" / "forecast.json")
    finally:
        line._default.cache_clear()
    assert errs and "forecast.json" in errs[0]
