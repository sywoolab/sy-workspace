#!/bin/bash
# 가민 동기화 로컬 실행 래퍼 (macOS launchd)
# 윈도우 등가: workout/scripts/run_garmin_sync.ps1
# 두 OS의 git sync 정책은 동일하게 유지한다 (L0 §크로스플랫폼 동일성)

cd /Users/sywoo/sy-workspace

# .env 로드 (특수문자 안전 처리). Python 코드는 자체 load_dotenv 사용.
set -a
source .env
set +a
export BOT_TOKEN="${TRAINING_BOT_TOKEN}"
export CHAT_ID="${TELEGRAM_CHAT_ID}"

LOG_DIR="/Users/sywoo/sy-workspace/data/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/garmin_sync_$(date +%Y%m%d).log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 로컬 가민 동기화 시작" >> "$LOG_FILE"

# === [BOOT] origin 변경 항상 먼저 흡수 (분기 누적 차단) ===
# 5/6, 5/8 분기 사고 재발 방지: commit 만들기 전에 항상 origin 최신화부터.
git fetch origin >> "$LOG_FILE" 2>&1
if git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null; then
    git pull --ff-only >> "$LOG_FILE" 2>&1 || echo "[INFO] fast-forward 불가 — commit 단계에서 rebase 시도" >> "$LOG_FILE"
fi

# === [SYNC] 가민 동기화 ===
/usr/bin/python3 workout/scripts/garmin_sync.py sync >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

# === [PUSH] 변경 있으면 commit + rebase + push ===
if [ $EXIT_CODE -eq 0 ]; then
    git add workout/workout_log.json workout/data/garmin_health.json workout/data/sync_state.json 2>/dev/null
    if ! git diff --staged --quiet 2>/dev/null; then
        git commit -m "garmin sync: local auto update" >> "$LOG_FILE" 2>&1
        if ! git pull --rebase --autostash >> "$LOG_FILE" 2>&1; then
            git rebase --abort 2>/dev/null
            echo "[ERROR] pull --rebase 충돌 — 자동 reset 금지. 수동 개입 필요. 로컬 commit 보존" >> "$LOG_FILE"
        else
            git push >> "$LOG_FILE" 2>&1 || echo "[WARN] git push 실패 — 다음 실행 시 재시도" >> "$LOG_FILE"
        fi
    fi
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 완료 (exit=$EXIT_CODE)" >> "$LOG_FILE"

find "$LOG_DIR" -name "garmin_sync_*.log" -mtime +7 -delete 2>/dev/null
