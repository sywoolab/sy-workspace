"""Send the snapshot used for the dashboard after deploying it."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / '.env').exists():
            load_dotenv(_p / '.env')
            break
except ImportError:
    pass

from training_snapshot import load_snapshot, format_snapshot, send_messages


def changed_dates(log, before):
    return sorted(key for key in log if log[key] != before.get(key))


def main():
    log, schedule = load_snapshot()
    before_ref = os.environ.get('TRAINING_BEFORE_SHA', '')
    before = {}
    before_schedule = None
    if before_ref and set(before_ref) != {'0'}:
        result = subprocess.run(['git', 'show', f'{before_ref}:workout/workout_log.json'],
                                cwd=Path(__file__).resolve().parents[2],
                                capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            before = json.loads(result.stdout)
            plan_result = subprocess.run(['git', 'show', f'{before_ref}:workout/workout_schedule.json'],
                                         cwd=Path(__file__).resolve().parents[2],
                                         capture_output=True, text=True, timeout=15)
            if plan_result.returncode == 0:
                before_schedule = json.loads(plan_result.stdout)
        else:
            print('[안내] 이전 로그 비교 불가 — 오늘 기록과 최신 확정 계획 전송')
    today = datetime.now(timezone(timedelta(hours=9))).date()
    dates = changed_dates(log, before) if before else [today.isoformat()]
    msg = format_snapshot(log, schedule, today, title='홈페이지 업데이트 · 최신 확정 내용', activity_dates=dates)
    if before_schedule is not None:
        previous = before_schedule.get('overrides', {})
        current = schedule.get('overrides', {})
        changes = sorted(key for key in set(previous) | set(current)
                         if previous.get(key) != current.get(key))
        if changes:
            msg += '\n\n📌 이번 반영에서 변경한 확정 세션 (전체)'
            for key in changes:
                entry = current.get(key)
                msg += f'\n{key}: ' + (entry.get('workout', '') if entry else '확정 세션 삭제')
                if entry and entry.get('detail'):
                    msg += '\n  ' + entry['detail']
    print(msg)
    if '--dry-run' in sys.argv:
        return 0
    token = os.environ.get('BOT_TOKEN') or os.environ.get('TRAINING_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')
    if not send_messages(msg, token, chat_id):
        print('[실패] 홈페이지 업데이트 알림을 전달하지 못했습니다')
        return 1
    print('[성공] 홈페이지와 동일한 운동·주간 목표·일별 계획 전송 완료')
    return 0


if __name__ == '__main__':
    sys.exit(main())
