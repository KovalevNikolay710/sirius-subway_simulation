from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path

import openpyxl
import polars as pl
import pytest

from metro_control.cli import DEFAULT_RAW_DIR, main
from metro_control.contracts import StationEntriesRow
from metro_control.entries import (
    day_type,
    load_all,
    load_holidays,
    parse_sheet_date,
    read_entries_workbook,
    write_parquet,
)
from metro_control.line import load_line

LINE = load_line()
HOLIDAYS = load_holidays()
VEST = [v for s in sorted(LINE.stations, key=lambda s: s.order) for v in s.vestibules]
SHEET_WED = "ТВхП 30092026 С 15-мин."
SHEET_FRI = "ТВхП 01052026 П 15-мин."


def _slot_time(i: int) -> time:
    m = (180 + 15 * i) % 1440
    return time(m // 60, m % 60)


def _value(vi: int, i: int) -> float:
    return float((vi * 3 + i) % 7)


def make_workbook(
    path: Path,
    sheets=(SHEET_WED, SHEET_FRI),
    *,
    n_slots: int = 96,
    bad_total: str | None = None,
    rename: tuple[str, str] | None = None,
    drop: str | None = None,
    cell: object = None,
    swap_header: bool = False,
) -> Path:
    wb = openpyxl.Workbook()
    wb.active.title = "Лист1"
    names = [(rename[1] if rename and rename[0] == v else v) for v in VEST]
    if drop:
        names = [n for n in names if n != drop] + ["Автово"]
    for sh in sheets:
        ws = wb.create_sheet(sh)
        ws["A1"] = "Таблица входных потоков."
        for i in range(n_slots):
            ws.cell(2, 3 + i, _slot_time(i))
        if swap_header:
            ws.cell(2, 3 + 40, _slot_time(41))
            ws.cell(2, 3 + 41, _slot_time(40))
        vals = []
        for vi, v in enumerate(VEST):
            row = [_value(vi, i) for i in range(96)]
            if v == "Пл. Восстания-1":
                row[0] = 10.0
            if v == "Пл. Восстания-2":
                row[0] = 7.0
            if v == "Автово":
                row[5] = None
            vals.append(row)
        totals = [sum(r[i] or 0 for r in vals) for i in range(96)]
        ws.cell(3, 2, sum(totals))
        for i, t in enumerate(totals):
            ws.cell(3, 3 + i, t)
        for vi, row in enumerate(vals):
            r = 4 + vi
            ws.cell(r, 1, names[vi])
            ws.cell(r, 2, sum(x or 0 for x in row) + (5 if bad_total == v_name(vi) else 0))
            for i, x in enumerate(row):
                if cell is not None and names[vi] == "Нарвская" and i == 3:
                    x = cell
                if x is not None:
                    ws.cell(r, 3 + i, x)
        ws.cell(28, 2, sum(totals))
    wb.save(path)
    return path


def v_name(vi: int) -> str:
    return VEST[vi]


@pytest.fixture(scope="module")
def df(tmp_path_factory) -> pl.DataFrame:
    p = make_workbook(tmp_path_factory.mktemp("x") / "w.xlsx")
    return read_entries_workbook(p, LINE, HOLIDAYS)


def test_shape_and_skip(df):
    assert df.height == 2 * 19 * 96
    assert df.columns == ["station_id", "interval_start", "day_type", "entries"]


def test_vestibules_summed(df):
    v = df.filter(
        (pl.col("station_id") == "ploshchad_vosstaniya")
        & (pl.col("interval_start") == datetime(2026, 9, 30, 0, 0, tzinfo=UTC))
    )
    assert v["entries"].to_list() == [17.0]
    d = df.filter(
        (pl.col("station_id") == "devyatkino")
        & (pl.col("interval_start") == datetime(2026, 9, 30, 0, 0, tzinfo=UTC))
    )
    i1, i2 = VEST.index("Девяткино-1"), VEST.index("Девяткино-2")
    assert d["entries"].to_list() == [_value(i1, 0) + _value(i2, 0)]


def test_time_conversion(df):
    t = df.filter(pl.col("day_type") == "weekday")["interval_start"]
    assert t.min() == datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
    assert t.max() == datetime(2026, 9, 30, 23, 45, tzinfo=UTC)
    assert df["interval_start"].dtype == pl.Datetime("us", "UTC")
    assert (df["interval_start"].dt.minute() % 15 == 0).all()


def test_day_types(df):
    assert day_type(date(2026, 9, 30), HOLIDAYS) == "weekday"
    assert day_type(date(2026, 5, 1), HOLIDAYS) == "holiday"
    assert day_type(date(2026, 5, 2), HOLIDAYS) == "saturday"
    assert day_type(date(2026, 5, 3), HOLIDAYS) == "sunday"
    assert day_type(date(2026, 5, 9), HOLIDAYS) == "holiday"
    assert set(df["day_type"]) == {"weekday", "holiday"}
    # day type follows the service date: slots after midnight on 02.05 stay holiday
    late = df.filter(pl.col("interval_start") == datetime(2026, 5, 1, 23, 45, tzinfo=UTC))
    assert set(late["day_type"]) == {"holiday"}


def test_parse_sheet_date():
    assert parse_sheet_date(SHEET_WED) == date(2026, 9, 30)
    assert parse_sheet_date("ТВхП 01052026 В 15-мин.") == date(2026, 5, 1)
    assert parse_sheet_date("Лист1") is None
    assert parse_sheet_date("ТВхП 99992026 С 15-мин.") is None


def test_day_sum_equals_sheet_total(df):
    per_day = df.group_by("day_type").agg(pl.col("entries").sum())
    expected = sum(float((vi * 3 + i) % 7) for vi in range(24) for i in range(96))
    expected += (
        10 + 7 - _value(VEST.index("Пл. Восстания-1"), 0) - _value(VEST.index("Пл. Восстания-2"), 0)
    )
    expected -= _value(VEST.index("Автово"), 5)  # empty cell counts as 0
    assert per_day["entries"].to_list() == [expected, expected]


def test_empty_cells_are_zero(df):
    assert df["entries"].null_count() == 0


@pytest.mark.parametrize(
    "kwargs, text",
    [
        ({"bad_total": "Автово"}, "Автово"),
        ({"rename": ("Автово", "Неизвестная")}, "Неизвестная"),
        ({"drop": "Нарвская"}, "Нарвская"),
        ({"n_slots": 95}, "95"),
        ({"cell": -4.0}, "Нарвская"),
        ({"cell": "abc"}, "Нарвская"),
        ({"swap_header": True}, "header"),
    ],
)
def test_failures(tmp_path, kwargs, text):
    p = make_workbook(tmp_path / "w.xlsx", sheets=(SHEET_WED,), **kwargs)
    with pytest.raises(ValueError) as e:
        read_entries_workbook(p, LINE, HOLIDAYS)
    assert SHEET_WED in str(e.value)
    assert text in str(e.value)


def test_rows_validate(df):
    for row in df.sample(50, seed=1).iter_rows(named=True):
        StationEntriesRow.model_validate(row)


def test_wrong_letter_ok(tmp_path):
    p = make_workbook(tmp_path / "w.xlsx", sheets=("ТВхП 30092026 В 15-мин.",))
    out = read_entries_workbook(p, LINE, HOLIDAYS)
    assert out["day_type"].unique().to_list() == ["weekday"]


def test_write_and_load_all(tmp_path):
    make_workbook(tmp_path / "Входные пассажиропотоки Линия 1 сен 2026 по 15-мин.xlsx")
    out = load_all(tmp_path, LINE, HOLIDAYS)
    assert out["interval_start"].is_sorted()
    dest = write_parquet(out, tmp_path / "a" / "b.parquet")
    assert pl.read_parquet(dest).height == out.height
    make_workbook(tmp_path / "Входные пассажиропотоки Линия 1 май 2026 по 15-мин.xlsx")
    with pytest.raises(ValueError, match="duplicate"):
        load_all(tmp_path, LINE, HOLIDAYS)


def test_cli_missing_dir(tmp_path, capsys):
    rc = main(["load-entries", "--raw-dir", str(tmp_path / "nope"), "--out", str(tmp_path / "o")])
    cap = capsys.readouterr()
    assert rc != 0
    assert "nope" in cap.err
    assert "Traceback" not in cap.err + cap.out


def test_cli_ok(tmp_path, capsys):
    make_workbook(tmp_path / "Входные пассажиропотоки Линия 1 сен 2026 по 15-мин.xlsx")
    out = tmp_path / "o" / "e.parquet"
    assert main(["load-entries", "--raw-dir", str(tmp_path), "--out", str(out)]) == 0
    assert out.exists()
    assert "rows: 3648" in capsys.readouterr().out


@pytest.mark.skipif(not DEFAULT_RAW_DIR.is_dir(), reason="raw data absent")
def test_real_data():
    out = load_all(DEFAULT_RAW_DIR, LINE, HOLIDAYS)
    assert out.height == 120 * 19 * 96
    assert not out.select("station_id", "interval_start").is_duplicated().any()
    feb23 = out.filter(pl.col("interval_start") == datetime(2026, 2, 23, 0, 0, tzinfo=UTC))
    assert set(feb23["day_type"]) == {"holiday"}


def test_cli_corrupt_file(tmp_path, capsys):
    (tmp_path / "Входные пассажиропотоки Линия 1 сен 2026 по 15-мин.xlsx").write_bytes(b"junk")
    rc = main(["load-entries", "--raw-dir", str(tmp_path), "--out", str(tmp_path / "o.parquet")])
    cap = capsys.readouterr()
    assert rc != 0
    assert "error" in cap.err
    assert "Traceback" not in cap.err + cap.out
