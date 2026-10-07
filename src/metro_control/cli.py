"""Command-line entry point `metro-control`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw" / "Данные Сириус" / "1.Пассажиропоток+ ГД"


def _doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("python>=3.11", sys.version_info >= (3, 11), sys.version.split()[0]))
    root = Path(args.root)
    for name in ("config", "contracts"):
        path = root / name
        checks.append((f"{name}/ exists", path.is_dir(), str(path)))
    runs = root / "runs"
    checks.append(("runs/ writable", runs.is_dir() and os.access(runs, os.W_OK), str(runs)))
    for name, ok, detail in checks:
        print(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def _validate(args: argparse.Namespace) -> int:
    from metro_control.validate import validate_file

    files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        files.extend(
            sorted(f for f in p.rglob("*.json") if not f.name.endswith(".schema.json"))
            if p.is_dir()
            else [p]
        )
    failed = False
    for f in files:
        errors = validate_file(f)
        if errors:
            failed = True
            for e in errors:
                print(f"FAIL {e}")
        else:
            print(f"ok {f}")
    return 1 if failed or not files else 0


def export_schemas(out_dir: Path) -> list[Path]:
    import json

    from metro_control.contracts import PACKAGES

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for kind, model in PACKAGES.items():
        path = out_dir / f"{kind}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(), indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def _export(args: argparse.Namespace) -> int:
    for p in export_schemas(Path(args.out)):
        print(f"wrote {p}")
    return 0


def _load_entries(args: argparse.Namespace) -> int:
    import fastexcel
    import polars as pl

    from metro_control.entries import load_all, load_holidays, write_parquet
    from metro_control.line import load_line
    from metro_control.timeutil import MSK

    raw = Path(args.raw_dir)
    if not raw.is_dir():
        print(f"error: raw dir not found: {raw}", file=sys.stderr)
        return 1
    try:
        df = load_all(raw, load_line(), load_holidays())
    except (ValueError, OSError, fastexcel.FastExcelError) as e:
        print(f"error: {e}".splitlines()[0], file=sys.stderr)
        return 1
    write_parquet(df, args.out)
    days = (
        df.select(pl.col("interval_start").dt.convert_time_zone("Europe/Moscow"))
        .with_columns((pl.col("interval_start") - pl.duration(hours=3)).dt.date().alias("d"))["d"]
        .n_unique()
    )
    print(f"days loaded: {days}")
    print(f"rows: {df.height}")
    for dt, n in df.group_by("day_type").len().sort("day_type").iter_rows():
        print(f"  {dt}: {n}")
    lo, hi = df["interval_start"].min(), df["interval_start"].max()
    print(f"first: {lo.astimezone(MSK):%Y-%m-%d %H:%M} MSK")
    print(f"last: {hi.astimezone(MSK):%Y-%m-%d %H:%M} MSK")
    print(f"wrote {args.out}")
    return 0


def _od_sanity(args: argparse.Namespace) -> int:
    import polars as pl

    from metro_control.line import load_line
    from metro_control.od import load_od_params, sanity_report

    path = Path(args.entries)
    if not path.is_file():
        print(f"error: entries parquet not found: {path}", file=sys.stderr)
        return 1
    rep = sanity_report(pl.read_parquet(path), load_line(), load_od_params())
    if rep.height == 0:
        print("error: no rows in range 05..24", file=sys.stderr)
        return 1
    print(f"days: {rep['date'].n_unique()}")
    worst = rep.sort("ratio", descending=True)
    print(f"max ratio: {worst['ratio'][0]:.3f}")
    print("date       hour segment                          demand capacity ratio")
    for d, h, seg, v, c, r in (
        worst.head(5)
        .select("date", "hour", "segment_id", "max_hourly_demand", "capacity", "ratio")
        .iter_rows()
    ):
        print(f"{d} {h:>4} {seg:<32} {v:>8.0f} {c:>8.0f} {r:.3f}")
    return 0


def _mock_bundle(args: argparse.Namespace) -> int:
    from datetime import datetime

    import polars as pl

    from metro_control.mock import build_mock_bundle

    entries = None
    path = (
        Path(args.entries)
        if args.entries
        else PROJECT_ROOT / "data/processed/station_entries.parquet"
    )
    if args.entries and not path.is_file():
        print(f"error: entries parquet not found: {path}", file=sys.stderr)
        return 1
    if path.is_file():
        try:
            entries = pl.read_parquet(path)
        except (pl.exceptions.PolarsError, OSError) as e:
            print(f"error: cannot read {path}: {e}".splitlines()[0], file=sys.stderr)
            return 1
        need = {"station_id", "interval_start", "day_type", "entries"}
        if need - set(entries.columns):
            print(
                f"error: {path} lacks columns {sorted(need - set(entries.columns))}",
                file=sys.stderr,
            )
            return 1
    try:
        as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
        if as_of is not None and as_of.tzinfo is None:
            raise ValueError("--as-of must include a timezone offset")
        out = build_mock_bundle(args.out, entries, as_of)
    except ValueError as e:
        print(f"error: {e}".splitlines()[0], file=sys.stderr)
        return 1
    print(f"wrote mock bundle to {out} ({'parquet' if entries is not None else 'synthetic'})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="metro-control")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="check environment, config and write access")
    doctor.add_argument("--root", default=str(PROJECT_ROOT))
    doctor.set_defaults(func=_doctor)
    val = sub.add_parser("validate", help="validate contract package files or directories")
    val.add_argument("paths", nargs="+")
    val.set_defaults(func=_validate)
    exp = sub.add_parser("export-schemas", help="write JSON Schemas for contracts v0.1")
    exp.add_argument("--out", default=str(PROJECT_ROOT / "contracts" / "v0_1" / "schemas"))
    exp.set_defaults(func=_export)
    le = sub.add_parser("load-entries", help="load organizer 15-min entries to parquet")
    le.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    le.add_argument("--out", default="data/processed/station_entries.parquet")
    le.set_defaults(func=_load_entries)
    od = sub.add_parser("od-sanity", help="OD assignment sanity report vs planned capacity")
    od.add_argument("--entries", default="data/processed/station_entries.parquet")
    od.set_defaults(func=_od_sanity)
    mb = sub.add_parser("mock-bundle", help="write a mock forecast/load/recommendation bundle")
    mb.add_argument("--out", default="runs/demo")
    mb.add_argument("--entries", default=None)
    mb.add_argument("--as-of", default=None, help="ISO datetime with offset")
    mb.set_defaults(func=_mock_bundle)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
