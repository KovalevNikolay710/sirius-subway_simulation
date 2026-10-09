# metro-control — Person 3 of the Line 1 SPb metro hackathon team

Owns: shared contracts (task 0), segment load model (10), train simulator (12), Streamlit dispatcher screen (6).
Does NOT own: ML forecast (person 2), recommendations/economics/LLM text (person 4), external data (person 1).
Mock forecast and mock policy live here only so the demo runs offline; always label them `mock`.

Full plan: `docs/plan.md`. Organizer data structure and parameters: `docs/data_notes.md`. Work queue: `docs/backlog.md`. Decision log: `docs/decisions.md`.
Team brief and case: `docs/team_notes.md`, `docs/ТЗ_*.pdf`, `docs/План_человека_3_*.docx`.

## Commands
- `uv run pytest` — all tests (must stay < ~1 min; runs on 3 xdist workers, `-n 0` for serial — `-p no:xdist` fails on addopts)
- `uv run ruff check . && uv run ruff format --check .`
- `uv run metro-control doctor` — environment check; more subcommands appear per slice
- `uv run streamlit run app.py` — dispatcher screen
- `uv run python scripts/ui_check.py --steps runs/briefs/<ID>.steps.json --out runs/ui/<ID>` — scripted UI check (starts the app, 0 JS errors expected)

## Rules
- Work in slices from `docs/backlog.md` via `/next-slice`: the main session orchestrates, the `slice-implementer` subagent (sonnet) writes code, `ecc:python-reviewer` reviews.
- Logic is pure Python in `src/metro_control/`; `app.py` only reads results and draws. No Streamlit imports in the package.
- UI: style, components and acceptance checklist in `docs/design.md` (skill `metro-ui`); shared player code in `src/metro_control/assets/ui.css` + `ui.js`, never copied into a player. Slice IDs: `S…` features, `U…` UI, `C…` contracts, `A…` adapters, `H…` harness, `M…` migration.
- Contracts in `contracts/v0_2/` change only with a version bump and a CHANGELOG entry; other people copy them.
- Every numeric parameter not given by organizers goes to `config/assumptions.json` with `source` and `version`. Never hide an assumption in code.
- Times are stored in UTC, shown in Europe/Moscow. Intervals are `[start, end)`, 15 min.
- No knowledge of the future: a decision at time T may read only data available at T. Demand (truth) is separate from forecast.
- Organizer data stays in `data/raw/` (git-ignored). Commit only code, contracts, configs and small synthetic fixtures.
- Commit after green checks (pytest + ruff); no per-commit approval from the user — a supervisor reviews the pushed history.
  Push `dev` to `origin` (github.com/KovalevNikolay710/sirius-subway_simulation) after each merge; `master` only when the user asks.
- The orchestrator keeps `docs/plan.md` and `docs/backlog.md` current: small corrections itself (reported under "plan changes"),
  scope changes only after asking the user; every change gets a line in `docs/decisions.md`.

## Git flow
- `master` = releases only (demo-ready states); merged from `dev` only when the user asks. `dev` = integration branch, always green.
- All work happens on a branch off `dev`: `feature/<slice-id>-<slug>` for backlog slices (e.g. `feature/S2-excel-loader`),
  `feature/<slug>` for tooling/harness, `fix/<slug>` for bug fixes. Never commit directly to `dev` or `master`.
- Finish a branch: tests + ruff green on the branch → `git switch dev && git merge --no-ff <branch>` → tests green on `dev` → `git branch -d <branch>` → `git push origin dev`.
- Commit messages: conventional (`feat(scope): …`, `fix: …`, `chore: …`, `docs: …`); merge commits `merge: <branch purpose>`.
- Team repo: Deniskish/metro-petersburg, our code lives in `dispatcher/` on their branch `dispatcher` (clone `/root/sirius/metro-petersburg`).
  After a merge to `dev`: `cd /root/sirius/metro-petersburg && git switch dispatcher && git subtree pull --prefix=dispatcher /root/sirius/metro dev -m "merge: sync dispatcher"`.
  The user pushes `dispatcher` and opens PRs; never touch their `main` or tags.
- Unattended slice runs: `scripts/slice_loop.sh` (fresh headless session per slice; own pytest + ruff gate, auto commit + merge + push `dev`;
  stops and notifies on a red gate, a `NEEDS_USER:` question or an unclean merge; `SLICE_CONFIRM=1` brings back the y/n prompt).
  One loop per repo (lock in `.git/slice_loop.lock`): a second start exits with code 3. Gate command: `SLICE_GATE_CMD`.
  Live progress per tool call in the terminal; raw events in `runs/slice_logs/<ID>.events.jsonl`.

## Token budget
- Briefs live in `runs/briefs/<ID>.md` (+ `<ID>.steps.json` for UI); prompts to subagents only point to them.
- At most 2 screenshots per slice in the main context; `ui-checker` judges the rest and replies ≤10 lines.
- Code is written by `slice-implementer` (sonnet); `haiku` for docs/fixture-only work. The main session writes briefs and verifies.
- State lives in files (backlog, decisions, plan, data_notes) — run `/clear` between slices.
- Never re-parse organizer Excel/images; use `docs/data_notes.md`. Peek at data with a few rows only.
- Pipe long output through `| tail -n 30`; read files with offset/limit when only a part is needed.
- Fixes after review go to the same implementer via SendMessage, not a new agent. Subagent reports stay ≤10 lines.
- Backlog rows tagged `[review]` stop `scripts/slice_loop.sh` after merge + push for the user's review.
