#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <label> <command...>" >&2
  exit 2
fi

LABEL="$1"
shift

LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
SAFE_LABEL="$(printf '%s' "$LABEL" | tr ' /:' '___')"
LOG_FILE="$LOG_DIR/${STAMP}-${SAFE_LABEL}.log"

{
  echo "[$(date --iso-8601=seconds)] START $LABEL"
  echo "CMD: $*"
} >> "$LOG_FILE"

set +e
"$@" >> "$LOG_FILE" 2>&1
STATUS=$?
set -e

SUMMARY="[$LABEL] 완료"
if grep -q '^\[JOB\] state=blocked$' "$LOG_FILE" 2>/dev/null || [ $STATUS -eq 3 ]; then
  SUMMARY="[$LABEL] 검수 보류"
elif [ $STATUS -ne 0 ]; then
  SUMMARY="[$LABEL] 실패 (exit $STATUS)"
fi

TAIL="$(grep -vE '^\[[0-9].* START |^CMD:' "$LOG_FILE" 2>/dev/null | tail -n 20 | tr '\n' ' ' | sed 's/  */ /g' | cut -c1-500)"
MSG="$SUMMARY | 로그: $LOG_FILE"
DELIVERY_ERROR="$(grep '^\[JOB\] delivery_error=' "$LOG_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2-)"
SHARE_ERROR="$(grep '^\[JOB\] share_error=' "$LOG_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2-)"
if [ -n "$DELIVERY_ERROR" ]; then
  MSG="$MSG | delivery_error: $DELIVERY_ERROR"
fi
if [ -n "$SHARE_ERROR" ]; then
  MSG="$MSG | share_error: $SHARE_ERROR"
fi
if [ -n "$TAIL" ]; then
  MSG="$MSG | 최근 출력: $TAIL"
fi

openclaw system event --mode now --text "$MSG" >/dev/null 2>&1 || true
exit $STATUS
