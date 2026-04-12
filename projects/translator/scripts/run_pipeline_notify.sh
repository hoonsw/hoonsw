#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <input> <output> [extra run_pipeline.py args...]" >&2
  echo "" >&2
  echo "Examples:" >&2
  echo "  $0 input.txt output.txt" >&2
  echo "  $0 input.txt output.txt --preset ja_novel" >&2
  echo "  $0 input.txt output.txt --preset ja_novel --ai-review" >&2
  exit 2
fi

INPUT="$1"
OUTPUT="$2"
shift 2

unset ANTHROPIC_API_KEY
exec "$BASE_DIR/scripts/run_with_notify.sh" \
  "pipeline: $(basename "$INPUT") -> $(basename "$OUTPUT")" \
  python3 run_pipeline.py "$INPUT" -o "$OUTPUT" "$@"
