#!/usr/bin/env bash
# UserPromptSubmit hook.
#
# This hook produces the *reasoning* layer only: intent, decision, verdict.
# It is NOT the compliance artifact for "provide each and every prompt along
# with every response" -- a model-written summary can never satisfy that.
# That requirement is met by docs/ai-transcript.md, rebuilt deterministically
# from the session logs by ai-transcript-build.py. The two are complementary:
#   docs/ai-transcript.md -> what was said, verbatim, complete (generated)
#   docs/decision-log.md  -> why it was decided, and the user's verdict (curated)
set -euo pipefail

payload="$(</dev/stdin)"

project_dir="${CLAUDE_PROJECT_DIR:-$PWD}"
log_file="$project_dir/docs/decision-log.md"
pending_file="$project_dir/.claude/.decision-log-pending"

prompt="$(printf '%s' "$payload" | jq -r '.prompt // ""')"
session="$(printf '%s' "$payload" | jq -r '.session_id // "unknown"' | cut -c1-8)"

# Nothing to log for empty prompts or slash commands.
if [[ -z "$prompt" || "$prompt" == /* ]]; then
  exit 0
fi

entry_id="$(date +%Y%m%d-%H%M%S)-$session"
stamp="$(date '+%Y-%m-%d %H:%M')"

# Inspecting this hook by hand must not leave a pending entry behind: the Stop
# hook would then demand a decision-log entry for an exchange that never
# happened. Run with DECISION_LOG_DRY_RUN=1 to preview the output only.
if [[ "${DECISION_LOG_DRY_RUN:-0}" != "1" ]]; then
  {
    printf 'id: %s\n' "$entry_id"
    printf 'stamp: %s\n' "$stamp"
    printf 'prompt:\n%s\n' "$prompt"
  } > "$pending_file"
fi

cat <<INSTRUCTIONS
<decision-log>
Antes de cerrar el turno, append a \`docs/decision-log.md\`:

## $stamp — <título imperativo>
<!-- id: $entry_id -->
- **Qué intentaba:** <intención real, 1 línea>
- **Prompt:** <cita corta; el texto completo ya está en docs/ai-transcript.md>
- **Respuesta:** <qué decidiste, 1-3 líneas, sin narrar tool calls>
- **Veredicto:** ⏳ pendiente

Es la capa de RAZONAMIENTO (por qué + veredicto), no un transcript.
Si el intercambio no aporta decisión/cambio/hallazgo, escribí sólo:
<!-- skip: $entry_id -->
Si este prompt reacciona al anterior, actualizá su **Veredicto**
(✅ aprobado | 🔁 ajustado — <qué> | ❌ rechazado — <por qué>).
</decision-log>
INSTRUCTIONS
