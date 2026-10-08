"""Team forecast row schema (vendored) and conversion of our forecast package to team rows."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from metro_control.contracts import ForecastPackage
from metro_control.timeutil import MSK

TEAM_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "v0_2" / "team" / "contract.schema.json"
)


def to_team_rows(pkg: ForecastPackage) -> list[dict[str, Any]]:
    """JSON-ready team rows; ts is the slot start in Europe/Moscow with offset."""
    return [
        {
            "station_id": r.station_id,
            "ts": r.ts.astimezone(MSK).isoformat(),
            "horizon_min": r.horizon_min,
            "q10": r.q10,
            "q50": r.q50,
            "q90": r.q90,
            "baseline": r.baseline,
            "is_anomaly": r.is_anomaly,
            "model_version": r.model_version,
        }
        for r in pkg.payload.rows
    ]


def validate_team_rows(rows: list[dict]) -> list[str]:
    """Errors from the team JSON Schema plus its description checks; empty list means ok."""
    schema = json.loads(TEAM_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = [
        f"{'.'.join(str(x) for x in e.absolute_path) or '$'}: {e.message}"
        for e in Draft202012Validator(schema).iter_errors(rows)
    ]
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        q = [r.get("q10"), r.get("q50"), r.get("q90")]
        if all(isinstance(x, (int, float)) for x in q) and not (q[0] <= q[1] <= q[2]):
            errors.append(f"{i}: quantiles must satisfy q10 <= q50 <= q90 (check q50, q90)")
        ts = r.get("ts")
        if isinstance(ts, str):
            try:
                t = datetime.fromisoformat(ts)
            except ValueError:
                errors.append(f"{i}.ts: not an ISO datetime")
                continue
            if t.utcoffset() is None:
                errors.append(f"{i}.ts: must carry an offset")
                continue
            if t.minute % 15 or t.second:
                errors.append(f"{i}.ts: minutes must be a multiple of 15 and seconds 0 ({ts})")
    return errors
