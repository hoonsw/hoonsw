#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <input> <output> [run_pipeline options...]" >&2
  exit 2
fi

INPUT="$1"
OUTPUT="$2"
shift 2

unset ANTHROPIC_API_KEY
exec python3 job_runner.py "$INPUT" -o "$OUTPUT" "$@"
