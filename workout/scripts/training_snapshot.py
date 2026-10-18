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


def day_brief(schedule, day):
    """Telegram line: workout title + optional short `brief` (full detail stays on the homepage)."""
    workout, _ = day_plan(schedule, day)
    entry = schedule.get('overrides', {}).get(day.isoformat()) or {}
    return workout, entry.get('brief') or ''


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
    return f'📈 이번 주 러닝 {days}일 · {km:.1f}km · 최장 {longest:.1f}km'


def format_plan(log, schedule, today, weeks=None):
    """Short schedule: one line per day, `brief` only for upcoming days that have one."""
    if weeks is None:
        weeks = 2 if today.weekday() >= 5 else 1
    monday = today - timedelta(days=today.weekday())
    lines = []
    for week in range(weeks):
        start = monday + timedelta(days=7 * week)
        lines.append(f"📅 {start:%m/%d}~{start + timedelta(days=6):%m/%d}")
        for offset in range(7):
            day = start + timedelta(days=offset)
            workout, brief = day_brief(schedule, day)
            label = f"{day:%m/%d}({'월화수목금토일'[day.weekday()]})"
            entry = log.get(day.isoformat(), {})
            if entry.get('done'):
                lines.append(f"{label} ✅ {entry.get('actual', '운동 완료')}")
                continue
            lines.append(f"{label} {workout}" + (' 👈' if day == today else ''))
            if brief and day >= today:
                lines.append(f'  {brief}')
        lines.append('')
    return '\n'.join(lines).strip()


def format_snapshot(log, schedule, today, title='운동 스케줄', activity_dates=None):
    lines = [f'📣 {title} ({today:%m/%d})']
    for key in (activity_dates if activity_dates is not None else [today.isoformat()]):
        entry = log.get(key, {})
        if entry.get('done'):
            lines.append(f"✅ {key[5:].replace('-', '/')} {entry.get('actual', '운동 완료')}")
    lines.extend(['', format_progress(log, schedule, today), '', format_plan(log, schedule, today),
                  '', f'📊 상세: {DASHBOARD_URL}'])
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
