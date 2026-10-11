#!/usr/bin/env bash
# PostToolUse on Edit|Write: format a Python file Claude just changed, fix what ruff can, and hand back what it can't.
# --no-sync: `uv sync` fails while roadbook-gui.exe runs, the file locked.
f=$(jq -r '.tool_input.file_path // empty')
case "$f" in *.py) ;; *) exit 0 ;; esac
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
uv run --no-sync ruff format --quiet "$f" >/dev/null 2>&1
out=$(uv run --no-sync ruff check --fix --quiet "$f" 2>&1)
# ruff exits 1 for violations left; anything else (ruff missing, no env) is not Claude's to fix here
if [ $? -eq 1 ]; then
  echo "ruff found issues it can't fix in $f:" >&2
  echo "$out" >&2
  exit 2
fi
exit 0
