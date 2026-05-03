"""
운동 알림 wrapper (cross-platform)

용도:
- 윈도우/맥/리눅스에서 동일하게 실행 가능
- workout_alert.py 호출 (인자: morning/evening)
- 로그 기록

호출 방법:
    python workout/scripts/run_workout_alert.py morning
    python workout/scripts/run_workout_alert.py evening

기존 run_workout_alert.sh(macOS launchd 전용)와 동일 효과.
"""

import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

# L0 §"환경변수 부트스트랩": 부모 경로 거슬러 올라가며 .env 탐색
try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / '.env').exists():
            load_dotenv(_p / '.env')
            break
except ImportError:
    pass

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = REPO_ROOT / 'data' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"workout_alert_{NOW.strftime('%Y%m%d')}.log"


def log(msg):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'morning'
    log(f"운동 알림 ({mode})")

    alert_script = REPO_ROOT / 'workout' / 'scripts' / 'workout_alert.py'
    try:
        r = subprocess.run(
            [sys.executable, str(alert_script), mode],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding='utf-8'
        )
        if r.stdout:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(r.stdout)
        if r.stderr:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(r.stderr)
        log(f"완료 (exit={r.returncode})")
        return r.returncode
    except Exception as e:
        log(f"[ERR] {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
