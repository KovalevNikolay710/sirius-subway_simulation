#!/usr/bin/env bash
# Run backlog slices one after another, each in a fresh headless Claude session
# (same effect as /clear + /next-slice) on its own feature branch off dev
# (git flow, see CLAUDE.md). No human approval per commit: the loop runs its own
# green gate (pytest + ruff), then tells the session to commit and merge into dev,
# then pushes dev to origin. A supervisor watches the pushed history.
#
#   scripts/slice_loop.sh                       # run until no todo/doing rows are left
#   NTFY_TOPIC=my-secret-topic scripts/slice_loop.sh   # also push notifications to phone (ntfy.sh)
#   SLICE_CONFIRM=1 scripts/slice_loop.sh       # old mode: ask y/n/feedback before each commit
#   SLICE_PUSH=0 scripts/slice_loop.sh          # commit and merge locally, do not push
#
# The loop stops (and notifies) when the gate stays red after SLICE_FIX_TRIES fix rounds,
# when the session writes a line starting with NEEDS_USER:, or when dev is not clean after merge.
# A failed push only warns: commits stay on local dev and go out with the next push.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${SLICE_PERMISSION_MODE:-auto}"
CONFIRM="${SLICE_CONFIRM:-0}"
PUSH="${SLICE_PUSH:-1}"
FIX_TRIES="${SLICE_FIX_TRIES:-2}"
REMOTE="${SLICE_REMOTE:-origin}"
LOG_DIR=runs/slice_logs
mkdir -p "$LOG_DIR"

notify() {
  local msg="${1//\'/}"
  printf '\a'
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; \
\$n=New-Object System.Windows.Forms.NotifyIcon; \$n.Icon=[System.Drawing.SystemIcons]::Information; \
\$n.Visible=\$true; \$n.ShowBalloonTip(15000,'metro-control','$msg','Info'); Start-Sleep 15; \$n.Dispose()" \
      >/dev/null 2>&1 &
  fi
  if [[ -n "${NTFY_TOPIC:-}" ]]; then
    curl -fsS -m 10 -d "$msg" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
  fi
}

next_slice() {
  # A slice left `doing` (interrupted run) comes first, then the first `todo`.
  # IDs may carry a letter suffix (S4a, S8a); `blocked` rows are skipped.
  local row
  row=$(grep -m1 -E '^\| S[0-9]+[a-z]? \| doing \|' docs/backlog.md ||
    grep -m1 -E '^\| S[0-9]+[a-z]? \| todo \|' docs/backlog.md || true)
  awk -F'|' '{gsub(/ /, "", $2); print $2}' <<<"$row"
}

# run_claude LOG [claude args...] -> prints the result, stores session id in $SID, result in $RESULT
run_claude() {
  local log=$1 out
  shift
  out=$(claude -p --permission-mode "$MODE" --output-format json "$@")
  SID=$(jq -r '.session_id' <<<"$out")
  RESULT=$(jq -r '.result' <<<"$out")
  tee -a "$log" <<<"$RESULT"
  echo >>"$log"
}

# gate -> 0 if tests and ruff are green on the working tree; failure output in $GATE_OUT
gate() {
  GATE_OUT=$({ uv run pytest -q 2>&1 | tail -n 20; } &&
    { uv run ruff check . 2>&1 | tail -n 20; } &&
    { uv run ruff format --check . 2>&1 | tail -n 20; }) && return 0
  return 1
}

stop_if_needs_user() {
  local q
  q=$(grep -m1 '^NEEDS_USER:' <<<"$RESULT" || true)
  if [[ -n "$q" ]]; then
    notify "metro-control: $slice needs a decision"
    echo "$q" >&2
    echo "Loop stopped. Answer in the session: claude -r $SID" >&2
    exit 2
  fi
}

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean; commit or stash first." >&2
  exit 1
fi
# Git flow: each slice branches off dev and is merged back into dev by /next-slice.
git switch -q dev

OK_MSG="OK from the loop (green gate passed, supervisor reviews pushed history): commit on the feature branch \
with your proposed message, merge into dev as in step 7, run the tests on dev, delete the branch. \
Do not push; the loop pushes dev."

while true; do
  slice=$(next_slice)
  if [[ -z "$slice" ]]; then
    notify "metro-control: backlog finished, nothing left to do"
    echo "Backlog finished."
    break
  fi
  log="$LOG_DIR/$slice.md"
  echo "=== $slice: starting fresh session (log: $log) ==="
  run_claude "$log" -n "slice-$slice" "/next-slice $slice"
  echo "session: $SID (resume by hand: claude -r $SID)"
  stop_if_needs_user

  tries=0
  until gate; do
    if ((tries >= FIX_TRIES)); then
      notify "metro-control: $slice gate is red, loop stopped"
      echo "$GATE_OUT" >&2
      echo "Gate still red after $tries fix rounds. Session: claude -r $SID" >&2
      exit 1
    fi
    tries=$((tries + 1))
    echo "--- gate red, fix round $tries ---"
    run_claude "$log" -r "$SID" "The loop's gate (pytest + ruff check + ruff format --check) is red. \
Fix it on the feature branch, do not commit, then report again:
$GATE_OUT"
    stop_if_needs_user
  done

  if [[ "$CONFIRM" == "1" ]]; then
    while true; do
      notify "metro-control: $slice is waiting for your approval"
      read -r -p "[$slice] y = commit, n = stop, or type feedback: " ans
      case "$ans" in
        y | Y) break ;;
        n | N | "") echo "Stopped. Continue later with: claude -r $SID"; exit 0 ;;
        *) run_claude "$log" -r "$SID" "$ans" ;;
      esac
    done
  fi
  run_claude "$log" -r "$SID" "$OK_MSG"

  branch=$(git branch --show-current)
  if [[ -n "$(git status --porcelain)" || "$branch" != "dev" ]]; then
    notify "metro-control: $slice not merged into dev cleanly, loop stopped"
    echo "After $slice: branch '$branch', tree clean: $([[ -z $(git status --porcelain) ]] && echo yes || echo no)." >&2
    echo "Expected a clean dev with the slice merged. Loop stopped. Session: claude -r $SID" >&2
    exit 1
  fi

  if [[ "$PUSH" == "1" ]]; then
    if GIT_TERMINAL_PROMPT=0 git push -q "$REMOTE" dev; then
      notify "metro-control: $slice merged and pushed to $REMOTE/dev"
    else
      notify "metro-control: $slice merged, push to $REMOTE failed (commits kept locally)"
      echo "Push to $REMOTE failed; dev is ahead locally. Continuing." >&2
    fi
  else
    notify "metro-control: $slice merged into local dev"
  fi
done
