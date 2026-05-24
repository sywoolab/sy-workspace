"""workout_schedule.json 변경 감지 알림 (2026-05-24 추가).

schedule-update.yml에서 push 시 호출. git HEAD~1과 HEAD를 비교해
overrides의 추가/수정/삭제 날짜 entry를 텔레그램으로 즉시 확인 발송한다.

배경 (2026-05-24 사고):
- 메인이 schedule 변경 후 git push 누락 → 텔레/HTML 모두 미반영
- 사용자가 "텔레 HTML 반영됐냐" 묻고 발견
- 어제(5/23) INBOX #5 자동화는 workout_log.json 중심 → schedule 변경 즉시 알림 갭 존재
- 본 스크립트가 그 갭을 메운다. notify_log_change.py 패턴 복제.
"""
import os
import sys
import json
import subprocess
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / '.env').exists():
            load_dotenv(_p / '.env')
            break
except ImportError:
    pass

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
SCHEDULE_REL = 'workout/workout_schedule.json'

BOT_TOKEN = (
    os.environ.get('BOT_TOKEN')
    or os.environ.get('TRAINING_BOT_TOKEN')
    or os.environ.get('TELEGRAM_BOT_TOKEN', '')
)
CHAT_ID = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')


def _git_show(ref):
    r = subprocess.run(
        ['git', 'show', f'{ref}:{SCHEDULE_REL}'],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        encoding='utf-8', errors='replace',
    )
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _summarize_entry(entry, max_detail=120):
    workout = (entry.get('workout') or '').strip()
    detail = (entry.get('detail') or '').strip()
    if detail and len(detail) > max_detail:
        detail = detail[:max_detail] + '...'
    if workout and detail:
        return f"{workout}\n    └ {detail}"
    return workout or detail or '(빈 entry)'


def _send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print('[SKIP] BOT_TOKEN/CHAT_ID 없음')
        print(text)
        return False
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    r = requests.post(url, data={'chat_id': CHAT_ID, 'text': text}, timeout=30)
    return r.json().get('ok', False)


def main():
    head = _git_show('HEAD')
    prev = _git_show('HEAD~1')

    if not isinstance(head, dict):
        print('[SKIP] HEAD workout_schedule.json 파싱 실패')
        return 0
    if not isinstance(prev, dict):
        print('[SKIP] HEAD~1 비교 대상 없음 (신규 또는 첫 commit)')
        return 0

    head_overrides = head.get('overrides', {})
    prev_overrides = prev.get('overrides', {})

    added = sorted(set(head_overrides.keys()) - set(prev_overrides.keys()))
    deleted = sorted(set(prev_overrides.keys()) - set(head_overrides.keys()))
    modified = []
    for d in sorted(set(head_overrides.keys()) & set(prev_overrides.keys())):
        if json.dumps(head_overrides[d], sort_keys=True, ensure_ascii=False) != json.dumps(
            prev_overrides[d], sort_keys=True, ensure_ascii=False
        ):
            modified.append(d)

    if not added and not modified and not deleted:
        print('[SKIP] schedule overrides 변경 없음')
        return 0

    lines = ['📅 운동 스케줄 변경 반영']
    if added:
        lines.append('')
        lines.append(f'➕ 추가 {len(added)}건:')
        for d in added[-5:]:
            lines.append(f'  {d}: {_summarize_entry(head_overrides[d])}')
    if modified:
        lines.append('')
        lines.append(f'✏️ 수정 {len(modified)}건:')
        for d in modified[-5:]:
            lines.append(f'  {d}: {_summarize_entry(head_overrides[d])}')
    if deleted:
        lines.append('')
        lines.append(f'🗑️ 삭제 {len(deleted)}건 (⚠️ 의도된 삭제인지 확인):')
        for d in deleted[-5:]:
            lines.append(f'  {d}: {_summarize_entry(prev_overrides[d])}')

    lines.append('')
    lines.append('📊 다음 morning(05:40)/evening(18:00) cron부터 새 스케줄 적용.')

    msg = '\n'.join(lines)
    print(msg)
    ok = _send_telegram(msg)
    print(f'  텔레그램: {"성공" if ok else "실패"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
