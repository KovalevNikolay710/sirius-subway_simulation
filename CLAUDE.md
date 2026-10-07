# metro-control — Person 3 of the Line 1 SPb metro hackathon team

Owns: shared contracts (task 0), segment load model (10), train simulator (12), Streamlit dispatcher screen (6).
Does NOT own: ML forecast (person 2), recommendations/economics/LLM text (person 4), external data (person 1).
Mock forecast and mock policy live here only so the demo runs offline; always label them `mock`.

Full plan: `docs/plan.md`. Organizer data structure and parameters: `docs/data_notes.md`. Work queue: `docs/backlog.md`. Decision log: `docs/decisions.md`.
Team brief and case: `docs/team_notes.md`, `docs/ТЗ_*.pdf`, `docs/План_человека_3_*.docx`.

## Commands
- `uv run pytest` — all tests (must stay < ~1 min)
- `uv run ruff check . && uv run ruff format --check .`
- `uv run metro-control doctor` — environment check; more subcommands appear per slice
- `uv run streamlit run app.py` — dispatcher screen (from slice S4)

## Rules
- Work in slices from `docs/backlog.md` via `/next-slice`: the main session orchestrates, the `slice-implementer` subagent (sonnet) writes code, `ecc:python-reviewer` reviews.
- Logic is pure Python in `src/metro_control/`; `app.py` only reads results and draws. No Streamlit imports in the package.
- Contracts in `contracts/v0_1/` change only with a version bump and a CHANGELOG entry; other people copy them.
- Every numeric parameter not given by organizers goes to `config/assumptions.json` with `source` and `version`. Never hide an assumption in code.
- Times are stored in UTC, shown in Europe/Moscow. Intervals are `[start, end)`, 15 min.
- No knowledge of the future: a decision at time T may read only data available at T. Demand (truth) is separate from forecast.
- Organizer data stays in `data/raw/` (git-ignored). Commit only code, contracts, configs and small synthetic fixtures.
- Commit locally after green checks and the user's OK; never push without being asked.

## Git flow
- `master` = releases only (demo-ready states); merged from `dev` only when the user asks. `dev` = integration branch, always green.
- All work happens on a branch off `dev`: `feature/<slice-id>-<slug>` for backlog slices (e.g. `feature/S2-excel-loader`),
  `feature/<slug>` for tooling/harness, `fix/<slug>` for bug fixes. Never commit directly to `dev` or `master`.
- Finish a branch: tests + ruff green on the branch → user's OK → `git switch dev && git merge --no-ff <branch>` → tests green on `dev` → `git branch -d <branch>`.
- Commit messages: conventional (`feat(scope): …`, `fix: …`, `chore: …`, `docs: …`); merge commits `merge: <branch purpose>`.
- Unattended slice runs: `scripts/slice_loop.sh` (fresh headless session per slice, stops for approval before each commit and notifies).

## Token budget
- Code is written by `slice-implementer` (sonnet); `haiku` for docs/fixture-only work. The main session writes briefs and verifies.
- State lives in files (backlog, decisions, plan, data_notes) — run `/clear` between slices.
- Never re-parse organizer Excel/images; use `docs/data_notes.md`. Peek at data with a few rows only.
- Pipe long output through `| tail -n 30`; read files with offset/limit when only a part is needed.
- Fixes after review go to the same implementer via SendMessage, not a new agent. Subagent reports stay ≤15 lines.
