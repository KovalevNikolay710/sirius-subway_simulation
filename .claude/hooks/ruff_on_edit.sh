#!/usr/bin/env bash
# PostToolUse: format and lint the Python file that was just edited.
file=$(jq -r '.tool_input.file_path // empty')
[[ "$file" == *.py && -f "$file" ]] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
uv run -q ruff format "$file" >/dev/null 2>&1
if ! out=$(uv run -q ruff check --fix "$file" 2>&1); then
  echo "ruff found problems in $file:" >&2
  echo "$out" >&2
  exit 2
fi
exit 0
