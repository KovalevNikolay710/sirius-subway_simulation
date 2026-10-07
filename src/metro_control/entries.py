"""15-min station entries from the organizer Excel workbooks (pure, polars)."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import fastexcel
import polars as pl

from metro_control.line import LineRef
from metro_control.timeutil import MSK

ASSUMPTIONS_PATH = Path(__file__).resolve().parents[2] / "config" / "assumptions.json"
N_SLOTS = 96
N_VESTIBULES = 24
TOL = 0.5
_SHEET_RE = re.compile(r"^ТВхП (\d{2})(\d{2})(\d{4}) \S 15-мин\.?$")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::\d{2})?\s*$")
_FIRST_ROW, _VEST_ROW, _SLOT_COL = 2, 3, 2

SCHEMA = {
    "station_id": pl.String,
    "interval_start": pl.Datetime("us", "UTC"),
    "day_type": pl.String,
    "entries": pl.Float64,
}


def load_holidays(path: Path | str | None = None) -> set[date]:
    data = json.loads(Path(path or ASSUMPTIONS_PATH).read_text(encoding="utf-8"))
    return {date.fromisoformat(s) for s in data["items"]["holidays_2026"]["value"]}


def parse_sheet_date(sheet_name: str) -> date | None:
    m = _SHEET_RE.match(sheet_name.strip())
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def day_type(d: date, holidays: set[date]) -> str:
    if d in holidays:
        return "holiday"
    return ("weekday", "weekday", "weekday", "weekday", "weekday", "saturday", "sunday")[
        d.weekday()
    ]


def _num(v: Any, w: str, what: str) -> float:
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{w}: {what}: non-numeric value {v!r}") from None
    if not math.isfinite(x) or x < 0:
        raise ValueError(f"{w}: {what}: invalid value {v!r} (must be finite and >= 0)")
    return x


def _hhmm(v: Any) -> tuple[int, int] | None:
    if isinstance(v, (time, datetime)):
        return v.hour, v.minute
    m = _TIME_RE.search(str(v))
    return (int(m[1]), int(m[2])) if m else None


def _where(path: Path, sheet: str) -> str:
    return f"{path.name} / {sheet!r}"


def _parse_sheet(
    rows: list[tuple[Any, ...]], d: date, path: Path, sheet: str, line: LineRef, holidays: set[date]
) -> pl.DataFrame:
    w = _where(path, sheet)
    if len(rows) < _VEST_ROW + N_VESTIBULES or len(rows[1]) < _SLOT_COL:
        raise ValueError(f"{w}: unexpected sheet shape {len(rows)} rows")
    expected = [divmod((180 + 15 * i) % 1440, 60) for i in range(N_SLOTS)]
    header = [_hhmm(v) for v in rows[1][_SLOT_COL : _SLOT_COL + N_SLOTS]]
    for i, (got, exp) in enumerate(zip(header, expected, strict=False)):
        if got != exp:
            raise ValueError(f"{w}: header slot {i} expected {exp[0]:02d}:{exp[1]:02d}, got {got}")
    if len(header) != N_SLOTS:
        raise ValueError(f"{w}: expected {N_SLOTS} time slots in header, got {len(header)}")
    slots = slice(_SLOT_COL, _SLOT_COL + N_SLOTS)

    known = {v: s.id for s in line.stations for v in s.vestibules}
    per_station: dict[str, list[float]] = {s.id: [0.0] * N_SLOTS for s in line.stations}
    body = rows[_VEST_ROW : _VEST_ROW + N_VESTIBULES]
    names = [str(r[0]).strip() if r[0] is not None else "" for r in body]
    for name in names:
        if name not in known:
            raise ValueError(f"{w}: unknown vestibule {name!r}")
    missing = sorted(set(known) - set(names))
    if missing:
        raise ValueError(f"{w}: missing vestibule {missing[0]!r}")
    if len(set(names)) != len(names):
        dup = next(n for n in names if names.count(n) > 1)
        raise ValueError(f"{w}: duplicate vestibule {dup!r}")
    for name, r in zip(names, body, strict=True):
        vals = [_num(v, w, f"vestibule {name!r}") for v in r[slots]]
        total = _num(r[1], w, f"vestibule {name!r} day total")
        if abs(total - sum(vals)) > TOL:
            raise ValueError(f"{w}: vestibule {name!r} day total expected {total}, got {sum(vals)}")
        acc = per_station[known[name]]
        for i, v in enumerate(vals):
            acc[i] += v

    line_tot = [_num(v, w, "line total") for v in rows[_FIRST_ROW][slots]]
    col_sums = [sum(acc[i] for acc in per_station.values()) for i in range(N_SLOTS)]
    for i, (a, b) in enumerate(zip(line_tot, col_sums, strict=True)):
        if abs(a - b) > TOL:
            raise ValueError(f"{w}: slot {i} line total expected {a}, got {b}")
    day_total = (
        _num(rows[_FIRST_ROW][_SLOT_COL + N_SLOTS], w, "line day total")
        if len(rows[_FIRST_ROW]) > (_SLOT_COL + N_SLOTS)
        else sum(line_tot)
    )
    if abs(day_total - sum(col_sums)) > TOL:
        raise ValueError(f"{w}: day total expected {day_total}, got {sum(col_sums)}")

    start = datetime(d.year, d.month, d.day, 3, 0, tzinfo=MSK)
    starts = [(start + timedelta(minutes=15 * i)).astimezone(UTC) for i in range(N_SLOTS)]
    dt = day_type(d, holidays)
    order = [s.id for s in sorted(line.stations, key=lambda s: s.order)]
    return pl.DataFrame(
        {
            "station_id": [sid for sid in order for _ in range(N_SLOTS)],
            "interval_start": [t for _ in order for t in starts],
            "day_type": [dt] * (len(order) * N_SLOTS),
            "entries": [v for sid in order for v in per_station[sid]],
        },
        schema=SCHEMA,
    )


def read_entries_workbook(path: Path | str, line: LineRef, holidays: set[date]) -> pl.DataFrame:
    path = Path(path)
    book = fastexcel.read_excel(str(path))
    frames = []
    for sheet in book.sheet_names:
        d = parse_sheet_date(sheet)
        if d is None:
            continue
        df = book.load_sheet_by_name(sheet, header_row=None).to_polars()
        frames.append(_parse_sheet(df.rows(), d, path, sheet, line, holidays))
    if not frames:
        return pl.DataFrame(schema=SCHEMA)
    return pl.concat(frames)


def load_all(raw_dir: Path | str, line: LineRef, holidays: set[date]) -> pl.DataFrame:
    raw = Path(raw_dir)
    files = sorted(raw.glob("Входные пассажиропотоки Линия 1 * 2026 по 15-мин.xlsx"))
    if not files:
        raise ValueError(f"no entries workbooks found in {raw}")
    df = pl.concat([read_entries_workbook(f, line, holidays) for f in files])
    rank = {s.id: s.order for s in line.stations}
    df = df.with_columns(
        pl.col("station_id").replace_strict(rank, return_dtype=pl.Int64).alias("_o")
    )
    df = df.sort("interval_start", "_o").drop("_o")
    dup = df.filter(df.select("station_id", "interval_start").is_duplicated())
    if dup.height:
        r = dup.row(0)
        raise ValueError(f"duplicate (station_id, interval_start): {r[0]} {r[1]}")
    return df


def write_parquet(df: pl.DataFrame, out_path: Path | str) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    return out
