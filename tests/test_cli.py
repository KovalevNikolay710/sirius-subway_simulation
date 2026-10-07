from pathlib import Path

from metro_control.cli import main


def _make_root(tmp_path: Path) -> Path:
    for name in ("config", "contracts", "runs"):
        (tmp_path / name).mkdir()
    return tmp_path


def test_doctor_passes_on_complete_layout(tmp_path, capsys):
    assert main(["doctor", "--root", str(_make_root(tmp_path))]) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_doctor_reports_missing_contracts(tmp_path, capsys):
    root = _make_root(tmp_path)
    (root / "contracts").rmdir()
    assert main(["doctor", "--root", str(root)]) == 1
    assert "[FAIL] contracts/ exists" in capsys.readouterr().out
