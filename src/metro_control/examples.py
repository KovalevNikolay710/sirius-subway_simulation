"""Builders for contract example packages (used by tests and scripts/make_examples.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from metro_control.line import segment_ids, station_ids

AS_OF = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
ZERO = "sha256:" + "0" * 64


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def envelope(kind: str, payload: Any, mode: str = "synthetic") -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "kind": kind,
        "run_id": "example-run-001",
        "generated_at": iso(AS_OF),
        "data_mode": mode,
        "manifest": {"source": "synthetic example", "checksum": ZERO},
        "payload": payload,
    }


def forecast(quantiles_ready: bool = True) -> dict[str, Any]:
    rows = []
    for si, s in enumerate(station_ids()):
        for k in range(8):
            base = 100.0 + 10 * si + 5 * k
            q = (0.8 * base, base, 1.2 * base) if quantiles_ready else (base,) * 3
            rows.append(
                {
                    "station_id": s,
                    "interval_start": iso(AS_OF + timedelta(minutes=15 * k)),
                    "q10": q[0],
                    "q50": q[1],
                    "q90": q[2],
                }
            )
    payload = {
        "as_of": iso(AS_OF),
        "model_name": "mock",
        "status": "mock",
        "quantiles_ready": quantiles_ready,
        "rows": rows,
    }
    return envelope("forecast", payload, "mock")


def station_entries() -> dict[str, Any]:
    rows = [
        {
            "station_id": "avtovo",
            "interval_start": iso(AS_OF),
            "day_type": "weekday",
            "entries": 123.0,
        }
    ]
    return envelope("station_entries", rows)


def load() -> dict[str, Any]:
    rows = [
        {
            "segment_id": segment_ids()[0],
            "interval_start": iso(AS_OF),
            "demand": 2916.0,
            "departures": 2,
            "capacity_per_train": 1458,
            "r": 1.0,
        },
        {
            "segment_id": segment_ids()[1],
            "interval_start": iso(AS_OF),
            "demand": 0.0,
            "departures": 0,
            "capacity_per_train": 1458,
            "r": None,
        },
    ]
    return envelope("load", rows)


def simulation_state() -> dict[str, Any]:
    payload = {
        "t": iso(AS_OF),
        "trains": [
            {"train_id": "T1", "segment_id": segment_ids()[0], "station_id": None, "load": 900},
            {"train_id": "T2", "segment_id": None, "station_id": "avtovo", "load": 0},
        ],
        "queues": [{"station_id": "avtovo", "direction": "north", "waiting": 42}],
        "reserve_available": 4,
    }
    return envelope("simulation_state", payload)


def recommendation() -> dict[str, Any]:
    payload = {
        "recommendation_id": "rec-001",
        "as_of": iso(AS_OF),
        "action": "add_reserve",
        "target": "chernyshevskaya",
        "start": iso(AS_OF + timedelta(minutes=15)),
        "end": iso(AS_OF + timedelta(minutes=75)),
        "reason": "mock: load ratio above threshold",
        "source": "mock",
    }
    return envelope("recommendation", payload, "mock")


def effect_comparison() -> dict[str, Any]:
    m = {
        "wait_pax_min": 1000.0,
        "queue_left": 10.0,
        "denied_boardings": 5.0,
        "max_fill": 1.1,
        "mean_fill": 0.7,
        "train_hours": 50.0,
        "train_km": 2000.0,
    }
    payload = {
        "scenario": "mock-peak",
        "baseline_run_id": "base-001",
        "policy_run_id": "policy-001",
        "baseline": m,
        "policy": {**m, "wait_pax_min": 800.0},
    }
    return envelope("effect_comparison", payload, "mock")


def invalid_cases() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    f = forecast()
    f["payload"]["rows"] = f["payload"]["rows"][:-1]
    out["forecast_151_rows"] = f
    f = forecast()
    f["payload"]["rows"][3]["q50"] = f["payload"]["rows"][3]["q90"] + 1
    out["forecast_q50_gt_q90"] = f
    f = forecast()
    f["payload"]["rows"][0]["station_id"] = "nowhere"
    out["forecast_unknown_station"] = f
    f = forecast()
    f["payload"]["rows"][0]["interval_start"] = iso(AS_OF - SLOT15)
    out["forecast_interval_before_as_of"] = f
    f = forecast()
    f["generated_at"] = "2026-09-30T12:00:00+03:00"
    out["envelope_msk_offset"] = f
    f = recommendation()
    f["manifest"]["checksum"] = "sha256:xyz"
    out["envelope_bad_checksum"] = f
    f = load()
    f["schema_version"] = "0.2"
    out["envelope_version_0_2"] = f
    f = forecast()
    f["payload"]["as_of"] = iso(AS_OF + SLOT15)
    for r in f["payload"]["rows"]:
        r["interval_start"] = iso(datetime.fromisoformat(r["interval_start"]) + SLOT15)
    out["forecast_as_of_after_generated"] = f
    f = recommendation()
    f["payload"]["start"] = iso(AS_OF - SLOT15)
    f["payload"]["end"] = iso(AS_OF + SLOT15)
    out["recommendation_start_before_as_of"] = f
    f = load()
    f["payload"][0]["r"] = 0.5
    out["load_inconsistent_r"] = f
    return out


SLOT15 = timedelta(minutes=15)
