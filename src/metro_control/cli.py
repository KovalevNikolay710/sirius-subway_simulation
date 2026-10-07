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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="metro-control")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="check environment, config and write access")
    doctor.add_argument("--root", default=str(PROJECT_ROOT))
    doctor.set_defaults(func=_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
