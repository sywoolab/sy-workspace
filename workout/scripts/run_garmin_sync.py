"""
가민 동기화 + git push 통합 wrapper (cross-platform)

용도:
- 윈도우/맥/리눅스에서 동일하게 실행 가능
- garmin_sync.py 호출 → 변경 시 git add/commit/pull/push
- 로그 기록 + 7일 이전 자동 삭제

호출 방법:
    python workout/scripts/run_garmin_sync.py

기존 run_garmin_sync.sh(macOS launchd 전용)와 동일 효과.
GitHub Actions는 garmin_sync.py 직접 호출하므로 영향 없음.
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

# Repo root = workout/scripts → 상위 두 단계
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = REPO_ROOT / 'data' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"garmin_sync_{NOW.strftime('%Y%m%d')}.log"


def log(msg):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def run(cmd, cwd=None):
    """서브프로세스 실행 + 로그 기록. (returncode, stdout) 반환."""
    try:
        r = subprocess.run(
            cmd, cwd=cwd or REPO_ROOT, capture_output=True, text=True, encoding='utf-8'
        )
        if r.stdout:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(r.stdout)
        if r.stderr:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(r.stderr)
        return r.returncode, r.stdout
    except Exception as e:
        log(f"[ERR] {cmd}: {e}")
        return 1, ''


def main():
    log("로컬 가민 동기화 시작")

    # 1. 동기화 실행 (sys.executable로 현재 파이썬 인터프리터 사용)
    sync_script = REPO_ROOT / 'workout' / 'scripts' / 'garmin_sync.py'
    rc, _ = run([sys.executable, str(sync_script), 'sync'])
    log(f"garmin_sync.py exit={rc}")

    # 2. git add → diff 있으면 commit/pull/push
    if rc == 0:
        targets = [
            'workout/workout_log.json',
            'workout/data/garmin_health.json',
            'workout/data/sync_state.json',
            'workout/workout_schedule.json',
        ]
        run(['git', 'add', *targets])

        # 스테이지된 변경 있는지
        diff_rc, _ = run(['git', 'diff', '--staged', '--quiet'])
        if diff_rc != 0:  # 변경 있음
            run(['git', 'commit', '-m', 'garmin sync: local auto update'])
            run(['git', 'pull', '--rebase'])
            push_rc, _ = run(['git', 'push'])
            if push_rc != 0:
                log("[WARN] git push 실패 — 다음 실행 시 재시도")
        else:
            log("변경사항 없음 — git push 생략")

    # 3. 7일 이전 로그 정리
    cutoff = NOW - timedelta(days=7)
    for old in LOG_DIR.glob('garmin_sync_*.log'):
        try:
            mtime = datetime.fromtimestamp(old.stat().st_mtime, tz=KST)
            if mtime < cutoff:
                old.unlink()
        except OSError:
            pass

    log(f"완료 (exit={rc})")
    return rc


if __name__ == '__main__':
    sys.exit(main())
