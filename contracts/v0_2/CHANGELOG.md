# Contracts changelog

## 0.2
- Breaking: envelope `schema_version` is "0.2"; v0.1 packages are rejected.
- `interval_start` renamed to `ts` everywhere (contracts, parquet column, examples).
- Station ids follow the team list: `veteranov` -> `prospekt_veteranov`, `tekhnologichesky_institut_1` -> `tekhnologichesky_institut`, `ploshchad_vosstaniya` -> `vosstaniya`; segment ids follow.
- ForecastRow gains `horizon_min` (15..120, multiple of 15, = ts - as_of + 15 min), `baseline` (>=0), `is_anomaly`, `model_version`.
- ForecastPayload drops `model_name` (use `rows[0].model_version`).
- Table adapter: a missing `baseline` column falls back to q50 (placeholder until A1).
- Vendored team schema `team/contract.schema.json` (see `team/SOURCE.md`); `metro-control validate` also checks forecasts against it.
- Invalid example `envelope_version_0_2` renamed `envelope_version_0_1`.

## 0.1
- Initial version: envelope, station_entries, forecast, load, simulation_state, recommendation, effect_comparison.
- Schemas: `schemas/*.schema.json` (`metro-control export-schemas`). Examples: `examples/valid`, `examples/invalid` (`scripts/make_examples.py`).
