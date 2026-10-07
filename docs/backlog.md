# Backlog

Status: `todo` / `doing` / `done`. A slice is done when its acceptance tests pass, ruff is clean and review findings are handled.

| ID | Status | Slice | Acceptance |
|----|--------|-------|------------|
| S0 | done | Harness + scaffold: uv project, CLI `doctor`, git, .gitignore, CLAUDE.md, hook, `/next-slice` | `pytest` green; `metro-control doctor` all ok |
| S1 | done | Contracts v0.1 + line reference `config/line.json` (19 stations, 36 directed segments, sourced params) + `metro-control validate` | Valid examples pass; invalid ones fail naming field and file; 152-row forecast check; q10≤q50≤q90; 12:00 MSK = 09:00 UTC; JSON Schemas exported |
| S2 | done | Excel loader → `data/processed/station_entries.parquet` | 24 vestibules → 19 stations; 96 slots/day; day type parsed from sheet name; UTC; day totals match sheet totals |
| S3 | done | OD (gravity) + static assignment to segments | 3-station example A–B=100, B–C=90; rows sum to 1, diagonal 0; A→C never on C→A; 19-station peak ≤ pairs×1458 sanity report |
| S4 | done | Pre-step: `ui-checker` agent + UI check step in `/next-slice`. Dispatcher screen on mock data | Line schematic coloured ≤80/80–100/>100; station chart fact/forecast/q-band; action card; missing/bad package shows reason, no traceback; `ui-checker` confirms on the running app with a screenshot |
| S4a | todo | `ui-checker` screenshot confirmation of the S4 screen (needs system lib `libgbm1`, user OK pending) | `ui-checker` on `runs/demo` and a broken copy: three band colours, station chart, action card, reasons without traceback; screenshot in `runs/ui/` |
| S5 | todo | Simulator core: 3 stations, 1 train | Alight before board; queue 120 / 100 free → 100 board, 20 stay; people balance; state continuity; inputs not mutated |
| S6 | todo | Full line + baseline timetable from pairs + run times | Day run < 1 min; repeat run identical; no train over capacity |
| S7 | todo | Mock policy (hysteresis, reuse `mock.mock_forecast` from S4, D20) + action executor | Reserve appears only after delay and is spent once; repeated recommendation_id is a no-op; invalid action rejected with reason; hysteresis counts unique as_of |
| S8 | todo | Scenarios + `metro-control compare` → `runs/RUN_ID/` | Both policies share demand and initial state; 80 passenger-minute check; metrics.json reproducible |
| S9 | todo | Simulator on screen: Step / Pause / Reset | Station switching does not move the model; reset reproduces start; queue, fill and before/after shown |
| S10 | todo | Adapters for persons 2 and 4, source failures, README clean-clone path, polish | Each failure (no forecast, no recommendation, broken package, no explanation) shows a clear status |
