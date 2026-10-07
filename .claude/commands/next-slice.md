---
description: Run one development slice from docs/backlog.md (orchestrate → sonnet implementer → review → report)
argument-hint: "[slice id, e.g. S1; default = first todo]"
---
You are the orchestrator for one slice: $ARGUMENTS (if empty, the first `todo` row in `docs/backlog.md`). You do not write production code yourself; the `slice-implementer` subagent (sonnet) does. Spend tokens on the spec and on verification, not on typing code.

1. Read the slice's backlog row, `docs/decisions.md`, and the slice's part of `docs/plan.md` (grep the slice ID and decision numbers; do not read the whole plan). For organizer data facts use `docs/data_notes.md`. Mark the slice `doing`.
2. Write a brief of at most 40 lines: goal, files to create/change, function signatures and data shapes, acceptance tests with concrete numbers, things out of scope. Spawn `slice-implementer` with it. Pass `model: haiku` only for slices that touch docs/fixtures without logic.
3. When it reports back, check `git status --short` and `git diff --stat`, then read only the critical parts (formulas, invariants, contract fields), not every file. Run `uv run pytest 2>&1 | tail -n 15` yourself.
4. Spawn `ecc:python-reviewer` on the slice: "Review the uncommitted changes (git diff + untracked files under src/ and tests/). Report only real defects — correctness, invariants, leaks of future data — at most 10 lines, no praise, no style nits ruff already covers."
5. Send real findings back to the same implementer with SendMessage (its context is warm), not to a new agent. Note the findings you reject and why.
6. Re-run `uv run pytest 2>&1 | tail -n 5` and `uv run ruff check .`. Mark the slice `done`; add new decisions to `docs/decisions.md` in one line each.
7. Report to the user in at most 10 lines: what works, the real test summary, open assumptions, the proposed commit message. Commit after the user's OK; never push. Then suggest `/clear` before the next slice — all state lives in the docs files.
