"""Plug-in loading and guarded calls for external policy / forecast implementations."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from metro_control.contracts import ForecastPackage, LoadPackage, Recommendation


def load_callable(spec: str) -> Callable[..., Any]:
    """`pkg.module:func` or `path/to/file.py:func` -> callable; ValueError on any problem."""
    mod_part, sep, attr = spec.rpartition(":")
    if not sep or not mod_part or not attr:
        raise ValueError(f"bad plug-in spec {spec!r}: expected module:func or file.py:func")
    try:
        if mod_part.endswith(".py"):
            path = Path(mod_part)
            if not path.is_file():
                raise ValueError(f"plug-in file not found: {mod_part}")
            ms = importlib.util.spec_from_file_location(f"_plugin_{path.stem}", path)
            if ms is None or ms.loader is None:
                raise ValueError(f"cannot load plug-in file: {mod_part}")
            mod = importlib.util.module_from_spec(ms)
            ms.loader.exec_module(mod)
        else:
            mod = importlib.import_module(mod_part)
    except ValueError:
        raise
    except Exception as e:  # import-time failure of user code
        raise ValueError(_reason(e, f"cannot import {mod_part}")) from e
    fn = getattr(mod, attr, None)
    if fn is None:
        raise ValueError(f"{mod_part} has no attribute {attr!r}")
    if not callable(fn):
        raise ValueError(f"{spec} is not callable")
    return fn


def _reason(e: BaseException, prefix: str | None = None) -> str:
    lines = str(e).splitlines()
    first = lines[0] if lines else ""
    text = f"{type(e).__name__}: {first}"
    if prefix:
        text = f"{prefix}: {text}"
    return text[:200]


def call_policy(
    fn: Callable[..., Any], load: LoadPackage, as_of: datetime
) -> tuple[Recommendation | None, str | None]:
    try:
        res = fn(load.model_copy(deep=True))
        rec = res if isinstance(res, Recommendation) else Recommendation.model_validate(res)
        if rec.payload.as_of > as_of:
            return None, f"future as_of {rec.payload.as_of.isoformat()} > {as_of.isoformat()}"
        return rec, None
    except Exception as e:
        return None, _reason(e)


def call_forecast(
    fn: Callable[..., Any], history: pl.DataFrame, as_of: datetime
) -> tuple[ForecastPackage | None, str | None]:
    try:
        res = fn(history.clone(), as_of)
        pkg = res if isinstance(res, ForecastPackage) else ForecastPackage.model_validate(res)
        if pkg.payload.as_of != as_of:
            return None, f"forecast as_of {pkg.payload.as_of.isoformat()} != {as_of.isoformat()}"
        if pkg.generated_at > as_of:
            return None, f"future generated_at {pkg.generated_at.isoformat()} > {as_of.isoformat()}"
        return pkg, None
    except Exception as e:
        return None, _reason(e)
