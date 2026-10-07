"""Regenerate contracts/v0_1/examples from metro_control.examples."""

import json
from pathlib import Path

from metro_control import examples as ex

root = Path(__file__).resolve().parents[1] / "contracts" / "v0_1" / "examples"
valid = {
    "forecast": ex.forecast(),
    "forecast_no_quantiles": ex.forecast(False),
    "station_entries": ex.station_entries(),
    "load": ex.load(),
    "simulation_state": ex.simulation_state(),
    "recommendation": ex.recommendation(),
    "effect_comparison": ex.effect_comparison(),
}
for sub, items in (("valid", valid), ("invalid", ex.invalid_cases())):
    for name, doc in items.items():
        (root / sub / f"{name}.json").write_text(json.dumps(doc, indent=1) + "\n")
