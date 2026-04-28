#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <input> <output> [job_runner args...]" >&2
  exit 2
fi

INPUT="$1"
OUTPUT="$2"
shift 2

printf 'run_translate_notify.sh is deprecated; forwarding to job_runner.py.\n' >&2
unset ANTHROPIC_API_KEY
exec "$BASE_DIR/scripts/run_with_notify.sh" \
  "translator-v3: $(basename "$INPUT") -> $(basename "$OUTPUT")" \
  python3 job_runner.py "$INPUT" -o "$OUTPUT" "$@"
