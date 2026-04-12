#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

exec "$BASE_DIR/scripts/run_job_runner_v3_codex.sh" "$@"
