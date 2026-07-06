"""workout_log.json 변경 감지 알림 (수동 입력 흐름 — 2026-05-23 추가).

workout-update.yml에서 push 시 호출. git HEAD~1과 HEAD를 비교해
새로 추가/수정된 날짜 entry를 텔레그램으로 즉시 확인 발송한다.

사용자 보고(INBOX #5): "내가 너희한테 지시한 내용은 html이나 운동스케쥴
반영되도 텔레 반영이 안돼" → 수동 입력이 GH Actions에서 처리됐다는 사실을
당일 즉시 인지할 수 있게 한다.

workout_analysis.py는 종합 분석 메시지를 보내지만, "어떤 entry가 새로
들어왔는지" 확인용은 아님. 이 스크립트가 그 갭을 메운다.
"""
import os
import sys
import json
import subprocess
from pathlib import Path

# 크로스플랫폼 한글 출력
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

# L0 §"환경변수 부트스트랩": .env 자동 로드
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

BASE_DIR = Path(__file__).resolve().parent.parent  # sy-workspace/workout
REPO_ROOT = BASE_DIR.parent
LOG_REL = 'workout/workout_log.json'

BOT_TOKEN = (
    os.environ.get('BOT_TOKEN')
    or os.environ.get('TRAINING_BOT_TOKEN')
    or os.environ.get('TELEGRAM_BOT_TOKEN', '')
)
CHAT_ID = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')
DASHBOARD_URL = 'https://sywoolab.github.io/training-dashboard/'


def _git_show(ref):
    r = subprocess.run(
        ['git', 'show', f'{ref}:{LOG_REL}'],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        encoding='utf-8', errors='replace',
    )
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _summarize_entry(entry):
    """entry에서 운동 요약 1줄.

    P5 (2026-06-07): all_metrics가 있으면 start_time 정렬 후
    '[HH:MM] 종목 거리@페이스' 형태로 ' + ' 연결. 없으면 actual fallback.
    """
    all_m = entry.get('all_metrics', [])
    if all_m:
        sorted_m = sorted(all_m, key=lambda m: m.get('start_time') or '99:99')
        parts = []
        type_name = {'run': '러닝', 'swim': '수영', 'bike': '자전거'}
        for m in sorted_m:
            wtype = m.get('type', '?')
            name = type_name.get(wtype, wtype)
            st = m.get('start_time', '')
            prefix = f"[{st}] " if st else ''
            if wtype == 'run':
                dist = m.get('distance_km') or round(m.get('distance_m', 0) / 1000, 2)
                pace = m.get('pace_per_km') or m.get('avg_pace') or '?'
                parts.append(f"{prefix}{name} {dist}km@{pace}")
            elif wtype == 'swim':
                dist = m.get('distance_m', '?')
                pace = m.get('pace_per_100m', '?')
                parts.append(f"{prefix}{name} {dist}m@{pace}/100m")
            elif wtype == 'bike':
                # PATH A/B 필드 호환 (레드팀 BUG-2)
                dist = m.get('distance_km') or (round(m['distance_m'] / 1000, 2) if m.get('distance_m') else '?')
                speed = m.get('avg_speed_kmh') or (round(m['avg_speed'] * 3.6, 1) if m.get('avg_speed') else '?')
                parts.append(f"{prefix}{name} {dist}km@{speed}km/h")
            else:
                parts.append(f"{prefix}{name}")
        if parts:
            return ' + '.join(parts)

    # fallback: actual 1줄 또는 metrics 단일 요약
    actual = entry.get('actual', '').strip()
    if actual:
        return actual
    metrics = entry.get('metrics', {})
    t = metrics.get('type', '?')
    if t == 'run':
        return f"러닝 {metrics.get('distance_km', '?')}km @{metrics.get('pace_per_km', '?')}"
    if t == 'swim':
        return f"수영 {metrics.get('distance_m', '?')}m"
    if t == 'bike':
        return f"자전거 {metrics.get('distance_km', '?')}km"
    return f"{t}"


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
        print('[SKIP] HEAD workout_log.json 파싱 실패')
        return 0
    if not isinstance(prev, dict):
        # 신규 추가 (HEAD~1에 파일 없음) — 전체를 변경으로 보지 않고 조용히 종료
        print('[SKIP] HEAD~1 비교 대상 없음 (신규 또는 첫 commit)')
        return 0

    added_dates = sorted(set(head.keys()) - set(prev.keys()))
    deleted_dates = sorted(set(prev.keys()) - set(head.keys()))
    modified_dates = []
    for d in sorted(set(head.keys()) & set(prev.keys())):
        if json.dumps(head[d], sort_keys=True, ensure_ascii=False) != json.dumps(
            prev[d], sort_keys=True, ensure_ascii=False
        ):
            modified_dates.append(d)

    if not added_dates and not modified_dates and not deleted_dates:
        print('[SKIP] workout_log 변경 없음')
        return 0

    lines = ['📝 운동 기록 변경 반영']
    lines.append(
        f"추가 {len(added_dates)}건 · 수정 {len(modified_dates)}건 · 삭제 {len(deleted_dates)}건"
    )
    if added_dates:
        lines.append(f"➕ 추가: {', '.join(added_dates[-5:])}")
    if modified_dates:
        lines.append(f"✏️ 수정: {', '.join(modified_dates[-5:])}")
    if deleted_dates:
        # L0 §"권한·데이터 보호" 보호 파일군 — 삭제는 가장 위험한 변경. 항상 알림.
        lines.append(f"🗑️ 삭제: {', '.join(deleted_dates[-5:])} (⚠️ 의도 확인)")

    lines.append('')
    lines.append(f'📊 상세/분석: {DASHBOARD_URL}')

    msg = '\n'.join(lines)
    print(msg)
    ok = _send_telegram(msg)
    print(f'  텔레그램: {"성공" if ok else "실패"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
