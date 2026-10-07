# Decision log

| # | Date | Decision | Why |
|---|------|----------|-----|
| D1 | 2026-10-07 | Own repo `metro-control`; exchange with team via versioned contracts and files in `runs/` | Team uses 4 repos |
| D2 | 2026-10-07 | Real 15-min entries are the demand base; scenarios overlay real days | Organizers provided data |
| D3 | 2026-10-07 | OD by gravity model, attraction = entries in mirrored period, transfers as coefficients | No exits/OD/transfers in data |
| D4 | 2026-10-07 | Train-level event simulator, fixed run times from 99-min cycle, hard capacity 1458, fluid passengers | 1–2 day budget, honest wait/queue metrics |
| D5 | 2026-10-07 | r = segment demand / (departures × 1458), control horizon +30 min, on r>1 ×2, off r<0.8 ×3 | ТЗ rules; person 4 may override in config |
| D6 | 2026-10-07 | Mock forecast = median station×slot×day type; mock policy = ТЗ rules; both labelled `mock` | Demo must run without persons 2 and 4 |
| D7 | 2026-10-07 | Contracts v0.1 = Pydantic models in `contracts.py`; exported JSON Schemas carry shapes only, rules (UTC slots, 152 rows, q order, r formula, no-future) live in validators | JSON Schema cannot express cross-field checks |
| D8 | 2026-10-07 | Station ids latin snake case south→north (`veteranov` … `devyatkino`); segment id `<from>__<to>`, 36 directed | Stable keys across 4 repos |
| D9 | 2026-10-07 | Forecast package = 19 stations × 8 slots from `as_of` (first slot starts at `as_of`), `as_of ≤ generated_at`; recommendation `start ≥ as_of` | No knowledge of the future |
| D10 | 2026-10-07 | `capacity_per_train` default 1458, overridable per row (>0) | Baltiets 1478 exists; organizers' 1458 is the default |
| D11 | 2026-10-07 | Git flow: `master` releases on request, `dev` integration, `feature/*` per slice, merge `--no-ff` after OK | User request; cleaner history and review points |
| D12 | 2026-10-07 | `ui-checker` agent (chrome-devtools, screenshot) added as S4 pre-step, not earlier; no designer role, researcher via `research` skill ad hoc | Only UI slices need it; saves tokens |
| D13 | 2026-10-07 | Orchestrator updates plan/backlog itself for small corrections, asks the user for scope changes | Plan must follow what slices reveal |
| D14 | 2026-10-07 | Service day = sheet date, slots 03:00–02:45 MSK; after-midnight slots keep the service date's day_type; loader fails loudly on any total/header/vestibule mismatch | Matches organizer sheets; all 120 real days pass the checks |
| D15 | 2026-10-07 | Holidays (`holidays_2026`: 23.02, 01.05, 09.05, 11.05) are an assumption in `config/assumptions.json`, holiday beats weekend | No calendar in organizer data |
| D16 | 2026-10-07 | OD: P_ij ∝ A_j·exp(−β·t), β=0.03/min, hop 2.75 min (99/36), transfer factors 1.3/1.5 on 4 transfer stations, mirror 06–11↔16–21 MSK, static same-slot assignment; all in assumptions.json, uncalibrated | No OD/exits/transfers in data (D3) |
| D17 | 2026-10-07 | `segment_demand` takes attraction from a separate frame: same day only for truth/offline (`od-sanity` is an oracle), history for forecast-time loads; empty mirrored window falls back to all-rows attraction; unknown stations/nulls fail loudly | No knowledge of the future; no silent demand loss |
| D18 | 2026-10-07 | Real-data sanity: 120 days, peak hourly segment load ≤ 0.605 of planned pairs×1458 (vyborgskaya→lesnaya, 18h) | Baseline is under capacity; surge scenarios needed to show add_reserve |
