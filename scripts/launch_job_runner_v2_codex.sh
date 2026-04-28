#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

printf 'launch_job_runner_v2_codex.sh is deprecated; forwarding to the v3 launcher.\n' >&2
exec "$BASE_DIR/scripts/launch_job_runner_codex.sh" "$@"
