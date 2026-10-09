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
            sorted(
                f
                for f in p.rglob("*.json")
                if not f.name.endswith(".schema.json") and f.name != "sources.json"
            )
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
        df.select(pl.col("ts").dt.convert_time_zone("Europe/Moscow"))
        .with_columns((pl.col("ts") - pl.duration(hours=3)).dt.date().alias("d"))["d"]
        .n_unique()
    )
    print(f"days loaded: {days}")
    print(f"rows: {df.height}")
    for dt, n in df.group_by("day_type").len().sort("day_type").iter_rows():
        print(f"  {dt}: {n}")
    lo, hi = df["ts"].min(), df["ts"].max()
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


def _sim_day(args: argparse.Namespace) -> int:
    import time
    from datetime import date

    import polars as pl

    from metro_control.dayrun import load_assumption_items, simulate_day
    from metro_control.line import load_line
    from metro_control.od import load_od_params
    from metro_control.sim import onboard, waiting

    path = Path(args.entries)
    if not path.is_file():
        print(f"error: entries parquet not found: {path}", file=sys.stderr)
        return 1
    d = date.fromisoformat(args.date)
    df = pl.read_parquet(path)
    msk = df["ts"].dt.convert_time_zone("Europe/Moscow") - pl.duration(hours=3)
    day = df.filter(msk.dt.date() == d)
    if day.height == 0:
        print(f"error: no rows for {d}", file=sys.stderr)
        return 1
    t0 = time.perf_counter()
    st, params = simulate_day(day, day, load_line(), load_od_params(), load_assumption_items())
    wall = time.perf_counter() - t0
    max_load = max((x.load_after for x in st.log), default=0.0)
    print(f"trips: {sum(1 for t in st.trains.values() if t.one_way)}")
    print(f"entered: {st.entered:.0f}")
    print(f"alighted: {st.alighted:.0f}")
    print(f"waiting at end: {waiting(st):.0f}")
    print(f"onboard: {onboard(st):.0f}")
    print(f"denied (refusals per train, not people): {st.denied:.0f}")
    print(f"max load/capacity: {max_load / params.capacity:.3f}")
    print(f"wall: {wall:.1f} s")
    return 0


def _compare(args: argparse.Namespace) -> int:
    import time

    import polars as pl

    from metro_control.dayrun import load_assumption_items
    from metro_control.line import load_line
    from metro_control.od import load_od_params
    from metro_control.scenario import compare, write_run

    path = Path(args.entries)
    if not path.is_file():
        print(f"error: entries parquet not found: {path}", file=sys.stderr)
        return 1
    from metro_control.plugins import load_callable

    try:
        policy_fn = load_callable(args.policy) if args.policy else None
        forecast_fn = load_callable(args.forecast) if args.forecast else None
    except ValueError as e:
        print(f"error: {e}".splitlines()[0], file=sys.stderr)
        return 1
    t0 = time.perf_counter()
    try:
        res = compare(
            args.scenario,
            pl.read_parquet(path),
            load_line(),
            load_od_params(),
            load_assumption_items(),
            policy_fn=policy_fn,
            forecast_fn=forecast_fn,
            policy_label=args.policy or "mock",
            forecast_label=args.forecast or "mock",
            demand=args.demand,
        )
    except ValueError as e:
        print(f"error: {e}".splitlines()[0], file=sys.stderr)
        return 1
    run_dir = write_run(res, Path(args.out))
    pl_ = res.effect.payload
    print(
        f"{'':9}{'wait_pax_min':>14}{'denied':>9}{'queue_left':>11}{'max_fill':>9}{'train_km':>10}"
    )
    for name, m in (("baseline", pl_.baseline), ("policy", pl_.policy)):
        print(
            f"{name:<9}{m.wait_pax_min:>14.0f}{m.denied_boardings:>9.0f}{m.queue_left:>11.0f}"
            f"{m.max_fill:>9.3f}{m.train_km:>10.0f}"
        )
    print(f"actions: {dict(sorted(res.outcomes.items()))}")
    if args.demand == "forecast":
        print(f"demand: forecast ({args.forecast or 'mock'})")
    print(f"wrote {run_dir}")
    print(f"wall: {time.perf_counter() - t0:.1f} s")
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
        need = {"station_id", "ts", "day_type", "entries"}
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
        out = build_mock_bundle(args.out, entries, as_of, surge={} if args.no_surge else None)
    except ValueError as e:
        print(f"error: {e}".splitlines()[0], file=sys.stderr)
        return 1
    if entries is not None:  # facts from the organizers' parquet, not synthetic
        from metro_control.mock import mark_entries_real

        mark_entries_real(out)
    print(f"wrote mock bundle to {out} ({'parquet' if entries is not None else 'synthetic'})")
    return 0


def _explain(args: argparse.Namespace) -> int:
    from metro_control.llm_text import explain_run

    run = Path(args.run)
    kind = args.kind or ("sim" if (run / "actions.jsonl").is_file() else "forecast")
    meta = explain_run(run, kind=kind)
    if meta["source"] == "yandexgpt":
        print(f"wrote {meta['n']} texts (yandexgpt)")
    else:
        print(f"no texts: {meta.get('error')} (mock)")
    return 0


def _team_bundle(args: argparse.Namespace) -> int:
    from datetime import datetime

    import polars as pl

    from metro_control.adapters import build_team_bundle
    from metro_control.mock import synthetic_entries

    path = (
        Path(args.entries)
        if args.entries
        else PROJECT_ROOT / "data/processed/station_entries.parquet"
    )
    try:
        if args.entries and not path.is_file():
            raise ValueError(f"entries parquet not found: {path}")
        if path.is_file():
            entries = pl.read_parquet(path)
            need = {"station_id", "ts", "day_type", "entries"}
            if need - set(entries.columns):
                raise ValueError(f"{path} lacks columns {sorted(need - set(entries.columns))}")
        else:
            from datetime import date

            entries = synthetic_entries(date(2026, 9, 1), 28)
        fa = datetime.fromisoformat(args.forecast_as_of) if args.forecast_as_of else None
        if fa is not None and fa.tzinfo is None:
            raise ValueError("--forecast-as-of must include a timezone offset")
        st = build_team_bundle(
            args.out,
            entries,
            forecast=args.forecast,
            recommendation=args.recommendation,
            explanation=args.explanation,
            forecast_as_of=fa,
        )
    except (ValueError, OSError, pl.exceptions.PolarsError) as e:
        print(f"error: {e}".splitlines()[0], file=sys.stderr)
        return 1
    for kind, s in st.items():
        if s.status == "ok":
            print(f"{kind}: ok ({s.origin})")
        elif s.status == "error":
            print(f"{kind}: error: {s.reason}")
        else:
            print(f"{kind}: missing → mock")
    print(f"wrote {args.out}")
    return 0


def _team_mock(args: argparse.Namespace) -> int:
    import json
    from datetime import date, datetime

    import polars as pl

    from metro_control.mock import synthetic_entries
    from metro_control.team_forecast import mock_team_serve

    try:
        as_of = datetime.fromisoformat(args.as_of)
        if as_of.tzinfo is None:
            raise ValueError("--as-of must include a timezone offset")
        if args.entries:
            if not Path(args.entries).is_file():
                raise ValueError(f"entries parquet not found: {args.entries}")
            entries = pl.read_parquet(args.entries)
        else:
            entries = synthetic_entries(date(2026, 9, 1), 28)
        obj = mock_team_serve(entries, as_of, model=args.model)
        Path(args.out).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    except (ValueError, OSError, pl.exceptions.PolarsError) as e:
        print(f"error: {e}".splitlines()[0], file=sys.stderr)
        return 1
    print(f"wrote mock team serve file to {args.out} (mock)")
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
    exp = sub.add_parser("export-schemas", help="write JSON Schemas for contracts v0.2")
    exp.add_argument("--out", default=str(PROJECT_ROOT / "contracts" / "v0_2" / "schemas"))
    exp.set_defaults(func=_export)
    le = sub.add_parser("load-entries", help="load organizer 15-min entries to parquet")
    le.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    le.add_argument("--out", default="data/processed/station_entries.parquet")
    le.set_defaults(func=_load_entries)
    od = sub.add_parser("od-sanity", help="OD assignment sanity report vs planned capacity")
    od.add_argument("--entries", default="data/processed/station_entries.parquet")
    od.set_defaults(func=_od_sanity)
    sd = sub.add_parser("sim-day", help="simulate one service day on the baseline timetable")
    sd.add_argument("--date", required=True)
    sd.add_argument("--entries", default="data/processed/station_entries.parquet")
    sd.set_defaults(func=_sim_day)
    mb = sub.add_parser("mock-bundle", help="write a mock forecast/load/recommendation bundle")
    mb.add_argument("--out", default="runs/demo")
    mb.add_argument("--entries", default=None)
    mb.add_argument("--as-of", default=None, help="ISO datetime with offset")
    mb.add_argument(
        "--no-surge", action="store_true", help="no demo demand surge (real day as in the data)"
    )
    mb.set_defaults(func=_mock_bundle)
    ex = sub.add_parser("explain", help="YandexGPT dispatcher texts for a run, cached as files")
    ex.add_argument("--run", required=True)
    ex.add_argument("--kind", choices=["forecast", "sim"], default=None)
    ex.set_defaults(func=_explain)
    tb = sub.add_parser("team-bundle", help="import person 2 / person 4 files into a run dir")
    tb.add_argument("--out", default="runs/team")
    tb.add_argument("--forecast", default=None)
    tb.add_argument("--recommendation", default=None)
    tb.add_argument("--explanation", default=None)
    tb.add_argument("--entries", default=None)
    tb.add_argument("--forecast-as-of", default=None, help="ISO datetime with offset (tables)")
    tb.set_defaults(func=_team_bundle)
    tm = sub.add_parser("team-mock", help="write a mock `serve --json` file for the team adapter")
    tm.add_argument(
        "--as-of", required=True, help="MSK ISO datetime with offset (:00; stack also :30)"
    )
    tm.add_argument("--model", choices=["lgbm", "stack"], default="lgbm")
    tm.add_argument("--out", required=True)
    tm.add_argument("--entries", default=None)
    tm.set_defaults(func=_team_mock)
    cp = sub.add_parser("compare", help="run a scenario day: baseline vs mock policy")
    cp.add_argument(
        "--scenario", required=True, choices=["quiet_weekend", "rail_surge", "snowfall"]
    )
    cp.add_argument("--entries", default="data/processed/station_entries.parquet")
    cp.add_argument("--out", default="runs")
    cp.add_argument("--policy", default=None, help="plug-in policy, module:func or file.py:func")
    cp.add_argument(
        "--forecast", default=None, help="plug-in forecast, module:func or file.py:func"
    )
    cp.add_argument(
        "--demand",
        choices=["truth", "forecast"],
        default="truth",
        help="demand of the replay: organizer truth (default) or rolling forecast q50",
    )
    cp.set_defaults(func=_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
