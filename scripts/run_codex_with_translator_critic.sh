#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLAN_PATH="docs/plans/active/translator-quality.md"
CRITIC_ONLY=0
CRITIC_PATHS=()

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run_codex_with_translator_critic.sh [--plan <path>] [--critic-only] [--paths <path> ...] -- [codex exec prompt or args]

Examples:
  ./scripts/run_codex_with_translator_critic.sh -- "Read AGENTS.md first. Update docs only."
  ./scripts/run_codex_with_translator_critic.sh --critic-only --paths AGENTS.md docs/plans/active/translator-quality.md
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan)
      PLAN_PATH="$2"
      shift 2
      ;;
    --critic-only)
      CRITIC_ONLY=1
      shift
      ;;
    --paths)
      shift
      while [[ $# -gt 0 && "$1" != "--" ]]; do
        CRITIC_PATHS+=("$1")
        shift
      done
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

CODEX_STATUS=0
if [[ "$CRITIC_ONLY" -eq 0 ]]; then
  if ! command -v codex >/dev/null 2>&1; then
    echo "codex executable not found" >&2
    exit 127
  fi
  (
    cd "$ROOT_DIR" &&
    codex exec --cd "$ROOT_DIR" "$@"
  )
  CODEX_STATUS=$?
fi

CRITIC_CMD=(python3 scripts/translator_critic.py --plan "$PLAN_PATH")
if [[ "${#CRITIC_PATHS[@]}" -gt 0 ]]; then
  CRITIC_CMD+=(--paths "${CRITIC_PATHS[@]}")
fi

(
  cd "$ROOT_DIR" &&
  "${CRITIC_CMD[@]}"
)
CRITIC_STATUS=$?

if [[ "$CODEX_STATUS" -ne 0 ]]; then
  exit "$CODEX_STATUS"
fi
exit "$CRITIC_STATUS"
