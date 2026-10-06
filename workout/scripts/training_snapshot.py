"""Homepage/Telegram snapshot from the authoritative log and confirmed plan."""
import json
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_URL = 'https://sywoolab.github.io/training-dashboard/'


def load_snapshot():
    return tuple(json.loads((BASE_DIR / name).read_text(encoding='utf-8'))
                 for name in ('workout_log.json', 'workout_schedule.json'))


def day_plan(schedule, day):
    entry = schedule.get('overrides', {}).get(day.isoformat())
    if entry:
        return entry.get('workout') or '계획 미등록', entry.get('detail') or ''
    # Same fallback as the homepage; never substitute an old training phase.
    return ('🏊 수영 강습 06시', '') if day.weekday() in (0, 2, 4) else ('계획 미등록', '')


def run_stats(log, start, end):
    days, total, longest = 0, 0.0, 0.0
    for key, entry in log.items():
        if not start.isoformat() <= key <= end.isoformat() or not entry.get('done'):
            continue
        runs = [m for m in (entry.get('all_metrics') or [entry.get('metrics', {})])
                if m.get('type') == 'run']
        distances = [float(m.get('distance_km') if m.get('distance_km') is not None
                           else (m.get('distance_m') or 0) / 1000) for m in runs]
        if distances:
            days += 1
            total += sum(distances)
            longest = max(longest, *distances)
    return days, total, longest


def format_progress(log, schedule, today):
    monday = today - timedelta(days=today.weekday())
    days, km, longest = run_stats(log, monday, today)
    goal = schedule.get('weekly_goals', {}).get(monday.isoformat(), {})
    return '\n'.join([
        f'📈 금주 실제 러닝: {days}일 · {km:.2f}km · 최장 {longest:.2f}km',
        f"확정 목표: {goal.get('target_load') or '주간 목표 미등록'}",
        '일별 조건과 회복 기준을 우선하며 미완료 운동을 몰아서 보충하지 않습니다.',
    ])


def format_plan(log, schedule, today, weeks=2):
    monday = today - timedelta(days=today.weekday())
    lines = []
    for week in range(weeks):
        start = monday + timedelta(days=7 * week)
        goal = schedule.get('weekly_goals', {}).get(start.isoformat(), {})
        lines.append(f"📅 {start:%m/%d}~{start + timedelta(days=6):%m/%d} {goal.get('title', '주간 계획')}")
        for field, label in [('target_load', '확정 목표'), ('goal', '목적'), ('sessions', '세션')]:
            if goal.get(field):
                lines.append(f'{label}: {goal[field]}')
        for offset in range(7):
            day = start + timedelta(days=offset)
            workout, detail = day_plan(schedule, day)
            lines.append(f"{day:%m/%d}({'월화수목금토일'[day.weekday()]}) {workout}")
            if detail:
                lines.append(f'  {detail}')
            entry = log.get(day.isoformat(), {})
            if entry.get('done'):
                lines.append(f"  ✅ 실제: {entry.get('actual', '운동 완료')}")
            elif day < today and '휴식' not in workout:
                lines.append('  기록 없음 (미수행으로 단정하지 않음)')
        lines.append('')
    return '\n'.join(lines).strip()


def format_snapshot(log, schedule, today, title='운동·확정 계획 최신 반영', activity_dates=None):
    lines = [f'📣 {title} ({today.isoformat()})', schedule.get('season_target', {}).get('target_time', ''), '']
    for key in (activity_dates if activity_dates is not None else [today.isoformat()]):
        entry = log.get(key, {})
        if entry.get('done'):
            lines.append(f"✅ {key}: {entry.get('actual', '운동 완료')}")
            if entry.get('note'):
                lines.append(f"기록 메모: {entry['note']}")
    lines.extend(['', format_progress(log, schedule, today), '', format_plan(log, schedule, today),
                  '', f'📊 홈페이지: {DASHBOARD_URL}'])
    return '\n'.join(lines)


def message_chunks(text, limit=3500):
    """Preserve all content and keep UTF-16 length below Telegram's limit."""
    chunk, size = '', 0
    for char in text:
        units = len(char.encode('utf-16-le')) // 2
        if size + units > limit:
            yield chunk
            chunk, size = '', 0
        chunk += char
        size += units
    if chunk:
        yield chunk


def send_messages(text, token, chat_id):
    import requests
    if not token or not chat_id:
        print('[알림 실패] Telegram 설정 없음')
        return False
    for chunk in message_chunks(text):
        try:
            response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                     data={'chat_id': chat_id, 'text': chunk}, timeout=30)
            if not response.json().get('ok'):
                print(f'[알림 실패] Telegram HTTP {response.status_code}')
                return False
        except (requests.RequestException, ValueError) as exc:
            print(f'[알림 실패] {type(exc).__name__}')
            return False
    return True
