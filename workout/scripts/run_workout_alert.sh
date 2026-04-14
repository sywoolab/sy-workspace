#!/bin/bash
# 운동 알림 로컬 실행 래퍼
# 인자: morning 또는 evening

cd /Users/sywoo/sy-workspace

set -a
source .env
set +a
export BOT_TOKEN="${TRAINING_BOT_TOKEN}"
export CHAT_ID="${TELEGRAM_CHAT_ID}"

LOG_DIR="/Users/sywoo/sy-workspace/data/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/workout_alert_$(date +%Y%m%d).log"

MODE="${1:-morning}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 운동 알림 ($MODE)" >> "$LOG_FILE"

/usr/bin/python3 workout/scripts/workout_alert.py "$MODE" >> "$LOG_FILE" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 완료" >> "$LOG_FILE"
