# metro-control

Line 1 (Kirovsko-Vyborgskaya) of the St. Petersburg metro: shared data contracts, segment load model,
train-level simulator and a dispatcher screen. Part of the Sirius hackathon team solution (person 3).

## Clean clone → demo
Works without organizer data (synthetic entries, everything labelled `mock`):
```bash
git clone https://github.com/KovalevNikolay710/sirius-subway_simulation && cd sirius-subway_simulation
uv sync --frozen
uv run metro-control mock-bundle --out runs/demo
uv run streamlit run app.py
```

## With organizer data
```bash
uv run metro-control load-entries                    # data/raw → data/processed/station_entries.parquet
uv run metro-control compare --scenario rail_surge   # baseline vs policy day → runs/compare-*
uv run metro-control team-bundle --out runs/team --forecast f.csv --recommendation r.json --explanation e.txt
METRO_RUN_DIR=runs/team uv run streamlit run app.py
```

## Team packages
- Person 2 forecast: `.csv` / `.parquet` with columns `station_id`, `interval_start` (tz-aware ISO, e.g. `2026-09-28T14:30:00Z`),
  `q50` and optional `q10`, `q90`; exactly 19 stations x 8 slots = 152 rows. Without `q10`/`q90` the quantiles are not ready.
  A full contract JSON (`kind: forecast`) is accepted too.
- `--forecast-as-of <ISO with offset>` overrides the forecast start (default: earliest `interval_start`).
- Person 4 recommendation: JSON, either a full contract envelope or just the payload (`recommendation_id`, `as_of`,
  `action`, `target`, `start`, `end`, `reason`).
- Person 4 explanation: UTF-8 text `explanation.txt`, shown on the recommendation card.
- A missing or broken source keeps the `mock` file and is reported in the sidebar (`sources.json` in the run dir).

## Setup
```bash
uv sync --frozen
uv run metro-control doctor
uv run pytest
```
Put the organizers' archive into `data/raw/` and unzip it there (it is git-ignored).

## Checks
```bash
uv run pytest -q                                     # all tests, < 1 min
uv run ruff check . && uv run ruff format --check .  # lint + format
```

## Dispatcher screen
```bash
uv run metro-control mock-bundle --out runs/demo     # mock forecast/load/recommendation bundle
uv run streamlit run app.py                          # http://localhost:8501
```
The screen reads `runs/demo` by default; set `METRO_RUN_DIR=<dir>` to show another bundle.
A missing or broken file shows a reason on the screen instead of a traceback.
Forecast, load and recommendation in the demo bundle are `mock`.

## CLI
```bash
uv run metro-control load-entries        # organizer Excel → data/processed/station_entries.parquet
uv run metro-control od-sanity           # OD + segment load vs planned capacity
uv run metro-control validate runs/demo  # check files against contracts v0.1
uv run metro-control export-schemas      # write JSON Schemas for contracts v0.1
```

See `docs/plan.md` for the plan and `docs/backlog.md` for progress.
