#!/bin/bash
# 가민 동기화 로컬 실행 래퍼
# launchd에서 호출됨. 인터넷 없으면 데이터만 저장, 복구되면 텔레그램 전송.

cd /Users/sywoo/sy-workspace

# .env 로드
export $(grep -v '^#' .env | xargs)

# BOT_TOKEN 매핑 (스크립트에서 BOT_TOKEN으로 읽음)
export BOT_TOKEN="${TRAINING_BOT_TOKEN}"
export CHAT_ID="${TELEGRAM_CHAT_ID}"

# 로그 디렉토리
LOG_DIR="/Users/sywoo/sy-workspace/data/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/garmin_sync_$(date +%Y%m%d).log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 로컬 가민 동기화 시작" >> "$LOG_FILE"

# 동기화 실행
/usr/bin/python3 workout/scripts/garmin_sync.py sync >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

# 변경사항 있으면 git push (인터넷 되면)
if [ $EXIT_CODE -eq 0 ]; then
    cd /Users/sywoo/sy-workspace
    git add workout/workout_log.json workout/data/garmin_health.json workout/data/sync_state.json 2>/dev/null
    if ! git diff --staged --quiet 2>/dev/null; then
        git commit -m "garmin sync: local auto update" >> "$LOG_FILE" 2>&1
        git push >> "$LOG_FILE" 2>&1 || echo "[WARN] git push 실패 (인터넷 없음?) — 다음 실행 시 재시도" >> "$LOG_FILE"
    fi
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 완료 (exit=$EXIT_CODE)" >> "$LOG_FILE"

# 7일 이전 로그 정리
find "$LOG_DIR" -name "garmin_sync_*.log" -mtime +7 -delete 2>/dev/null
