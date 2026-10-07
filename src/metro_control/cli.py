"""Command-line entry point `metro-control`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
