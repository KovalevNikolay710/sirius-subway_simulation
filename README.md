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

See `docs/plan.md` for the plan and `docs/backlog.md` for progress.
