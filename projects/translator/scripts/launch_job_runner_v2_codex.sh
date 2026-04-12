#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <input> [output] [job_runner options...]" >&2
  exit 2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
INPUT_LABEL="$(basename "$1")"
SAFE_LABEL="$(printf '%s' "$INPUT_LABEL" | tr ' /:' '___')"
mkdir -p "$BASE_DIR/runs"
LAUNCH_LOG="$BASE_DIR/runs/launcher-${STAMP}-${SAFE_LABEL}.log"

{
  printf '[%s] LAUNCH %s\n' "$(date --iso-8601=seconds)" "$INPUT_LABEL"
  printf 'CMD: %s/scripts/run_job_runner_v2_codex.sh' "$BASE_DIR"
  for arg in "$@"; do
    printf ' %q' "$arg"
  done
  printf '\n'
} >>"$LAUNCH_LOG"

CMD=("$BASE_DIR/scripts/run_job_runner_v2_codex.sh" "$@")
if command -v setsid >/dev/null 2>&1; then
  setsid "${CMD[@]}" >>"$LAUNCH_LOG" 2>&1 < /dev/null &
else
  nohup "${CMD[@]}" >>"$LAUNCH_LOG" 2>&1 < /dev/null &
fi
PID=$!
disown "$PID" 2>/dev/null || true

printf 'STARTED\n'
printf 'PID=%s\n' "$PID"
printf 'LAUNCH_LOG=%s\n' "$LAUNCH_LOG"
