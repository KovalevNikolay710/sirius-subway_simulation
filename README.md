# metro-control

Line 1 (Kirovsko-Vyborgskaya) of the St. Petersburg metro: shared data contracts, segment load model,
train-level simulator and a dispatcher screen. Part of the Sirius hackathon team solution (person 3).

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
The simulator (`src/metro_control/sim.py`) has no CLI yet; it is covered by `tests/test_sim.py`.

See `docs/plan.md` for the plan and `docs/backlog.md` for progress.
