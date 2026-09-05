#!/usr/bin/env bash
# Stop hook.
# Blocks the turn from ending while the pending decision-log entry is unwritten.
set -euo pipefail

payload="$(</dev/stdin)"

project_dir="${CLAUDE_PROJECT_DIR:-$PWD}"
log_file="$project_dir/docs/decision-log.md"
pending_file="$project_dir/.claude/.decision-log-pending"

# Never block twice in a row: avoids a stop loop if the model keeps failing.
if [[ "$(printf '%s' "$payload" | jq -r '.stop_hook_active // false')" == "true" ]]; then
  rm -f "$pending_file"
  exit 0
fi

[[ -f "$pending_file" ]] || exit 0

entry_id="$(rg -m1 -o '^id: (.+)$' -r '$1' "$pending_file" 2>/dev/null || true)"
[[ -n "$entry_id" ]] || { rm -f "$pending_file"; exit 0; }

if [[ -f "$log_file" ]] && rg -qF "$entry_id" "$log_file"; then
  rm -f "$pending_file"
  exit 0
fi

cat >&2 <<MSG
Falta la entrada del decision log para el id $entry_id.
Escribila en docs/decision-log.md con el formato indicado, o dejá
la línea <!-- skip: $entry_id --> si el intercambio no fue relevante.
MSG
exit 2
