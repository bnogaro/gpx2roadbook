#!/usr/bin/env bash
# PreToolUse on Edit|Write: files only tools write.
f=$(jq -r '.tool_input.file_path // empty')
f="${f//\\//}"  # Windows paths
case "$f" in
  */uv.lock | uv.lock)
    echo "uv.lock is written by uv: use \`uv add\`, \`uv remove\` or \`uv lock\`, not an edit." >&2
    exit 2 ;;
  */CHANGELOG.md | CHANGELOG.md)
    echo "CHANGELOG.md is written by \`make release\` (commitizen, from the Conventional Commits): don't edit it by hand." >&2
    exit 2 ;;
esac
exit 0
