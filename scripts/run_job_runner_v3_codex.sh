#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <input> [output] [job_runner options...]" >&2
  exit 2
fi

INPUT="$1"
shift

if [ "$#" -gt 0 ] && [[ "${1:-}" != --* ]]; then
  OUTPUT="$1"
  shift
else
  CANONICAL_INPUT="$(realpath "$INPUT" 2>/dev/null || true)"
  INPUT_FILE="$(basename "$INPUT")"
  if [[ "$INPUT_FILE" == *.* ]]; then
    STEM="${INPUT_FILE%.*}"
    INPUT_EXT=".${INPUT_FILE##*.}"
  else
    STEM="$INPUT_FILE"
    INPUT_EXT=".txt"
  fi

  case "${INPUT_EXT,,}" in
    .txt|.md|.markdown)
      EXT="$INPUT_EXT"
      ;;
    *)
      EXT=".txt"
      ;;
  esac

  DEFAULT_OUTPUT_DIR=""
  case "$CANONICAL_INPUT" in
    "$BASE_DIR/input"|"$BASE_DIR/input"/*)
      DEFAULT_OUTPUT_DIR="$BASE_DIR/output"
      ;;
  esac

  if [ -n "$DEFAULT_OUTPUT_DIR" ]; then
    mkdir -p "$DEFAULT_OUTPUT_DIR"
    OUTPUT="$DEFAULT_OUTPUT_DIR/${STEM}_ko${EXT}"
  else
    INPUT_DIR="$(cd "$(dirname "$INPUT")" && pwd)"
    OUTPUT="$INPUT_DIR/${STEM}_ko${EXT}"
  fi
fi

HAS_CODEX_MODEL=0
HAS_REASONING=0
for arg in "$@"; do
  case "$arg" in
    --codex-model|--codex-model=*)
      HAS_CODEX_MODEL=1
      ;;
    --codex-reasoning-effort|--codex-reasoning-effort=*)
      HAS_REASONING=1
      ;;
  esac
done

CMD=(
  python3 job_runner.py "$INPUT" -o "$OUTPUT"
  --v3
  --v3-backend codex_local
)

if [ "$HAS_CODEX_MODEL" -eq 0 ]; then
  CMD+=(--codex-model gpt-5.4)
fi
if [ "$HAS_REASONING" -eq 0 ]; then
  CMD+=(--codex-reasoning-effort high)
fi

CMD+=("$@")

exec "$BASE_DIR/scripts/run_with_notify.sh" \
  "translator-job-v3-codex: $(basename "$INPUT") -> $(basename "$OUTPUT")" \
  "${CMD[@]}"
