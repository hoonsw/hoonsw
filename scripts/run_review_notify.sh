#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <source> <translated> [extra review.py args...]" >&2
  exit 2
fi

SOURCE="$1"
TRANSLATED="$2"
shift 2

unset ANTHROPIC_API_KEY
exec "$BASE_DIR/scripts/run_with_notify.sh" \
  "review: $(basename "$TRANSLATED")" \
  python3 review.py "$SOURCE" "$TRANSLATED" "$@"
