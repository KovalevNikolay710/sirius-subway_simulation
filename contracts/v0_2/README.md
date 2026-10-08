# Contracts v0.2

Code: `src/metro_control/contracts.py`; line reference `config/line.json`. Check files with `metro-control validate PATH...`.
All models forbid extra fields. Times are UTC (offset 0), 15-min slots, `[start, end)`.

Envelope (all packages): `schema_version` "0.2", `kind`, `run_id`, `generated_at` (UTC), `data_mode` real|mock|synthetic, `manifest{source, checksum "sha256:<64 hex>"}`, `payload`.

| kind | payload |
|---|---|
| station_entries | list of {station_id, ts, day_type, entries>=0} |
| forecast | {as_of, status ok/mock/degraded, quantiles_ready, rows[152]: station_id, ts, horizon_min, q10<=q50<=q90, baseline, is_anomaly, model_version}; intervals = as_of + 0..7 slots; if not quantiles_ready, q10=q50=q90 |
| load | list of {segment_id, ts, demand, departures, capacity_per_train=1458, r=demand/(departures*capacity) or null} |
| simulation_state | {t, trains[{train_id, segment_id?, station_id?, load<=capacity}], queues[{station_id, direction, waiting}], reserve_available 0..4} |
| recommendation | {recommendation_id, as_of, action, target, start, end>start, reason, source mock/person4} |
| effect_comparison | {scenario, baseline_run_id, policy_run_id, baseline, policy: metrics{wait_pax_min, queue_left, denied_boardings, max_fill, mean_fill, train_hours, train_km}} |

Note: the exported JSON Schemas (`schemas/`) carry only shapes. UTC and 15-min slots, 152 rows, quantile ordering, the `r` formula and the no-future checks (as_of <= generated_at, recommendation start >= as_of) live in validators. Run `metro-control validate` or copy `contracts.py`.

Team forecast schema: `team/contract.schema.json` (vendored, see `team/SOURCE.md`); `src/metro_control/team_schema.py` converts and validates.
