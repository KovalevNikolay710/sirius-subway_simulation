#!/usr/bin/env bash
# Run backlog slices one after another, each in a fresh headless Claude session
# (same effect as /clear + /next-slice) on its own feature branch off dev
# (git flow, see CLAUDE.md). Before every commit the loop stops,
# sends a notification and asks for approval in this terminal.
#
#   scripts/slice_loop.sh            # run until the backlog has no todo/doing rows
#   NTFY_TOPIC=my-secret-topic scripts/slice_loop.sh   # also push to phone via ntfy.sh
#
# Answers at the prompt: y = commit and go on, n = stop here, anything else =
# feedback sent to the same slice session (then you are asked again).
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${SLICE_PERMISSION_MODE:-auto}"
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
  local row
  row=$(grep -m1 -E '^\| S[0-9]+ \| doing \|' docs/backlog.md ||
    grep -m1 -E '^\| S[0-9]+ \| todo \|' docs/backlog.md || true)
  awk -F'|' '{gsub(/ /, "", $2); print $2}' <<<"$row"
}

# run_claude LOG [claude args...] -> prints the result, stores session id in $SID
run_claude() {
  local log=$1 out
  shift
  out=$(claude -p --permission-mode "$MODE" --output-format json "$@")
  SID=$(jq -r '.session_id' <<<"$out")
  jq -r '.result' <<<"$out" | tee -a "$log"
  echo | tee -a "$log" >/dev/null
}

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean; commit or stash first." >&2
  exit 1
fi
# Git flow: each slice branches off dev and is merged back into dev by /next-slice.
git switch -q dev

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

  while true; do
    notify "metro-control: $slice is waiting for your approval"
    read -r -p "[$slice] y = commit, n = stop, or type feedback: " ans
    case "$ans" in
      y | Y) run_claude "$log" -r "$SID" "OK: commit and merge into dev as in step 7. Do not push."; break ;;
      n | N | "") echo "Stopped. Continue later with: claude -r $SID"; exit 0 ;;
      *) run_claude "$log" -r "$SID" "$ans" ;;
    esac
  done

  branch=$(git branch --show-current)
  if [[ -n "$(git status --porcelain)" || "$branch" != "dev" ]]; then
    notify "metro-control: $slice not merged into dev cleanly, loop stopped"
    echo "After $slice: branch '$branch', tree clean: $([[ -z $(git status --porcelain) ]] && echo yes || echo no)." >&2
    echo "Expected a clean dev with the slice merged. Loop stopped. Session: claude -r $SID" >&2
    exit 1
  fi
done
