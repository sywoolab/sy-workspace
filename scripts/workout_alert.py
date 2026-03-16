"""
오늘의 운동 스케줄 알림
- WORKOUT_MASTER.md의 8주 플랜 기반
- 아침: 오늘 운동 안내
- 저녁: 운동 리마인드
"""

import os
import sys
import requests
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.strftime('%Y-%m-%d')
DOW = NOW.weekday()  # 0=월 ~ 6=일

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']

# 대회일
RACE_DAY = datetime(2026, 5, 9, tzinfo=KST)
DAYS_LEFT = (RACE_DAY.date() - NOW.date()).days

# Phase 판별
PHASE1_END = datetime(2026, 4, 6, tzinfo=KST).date()
PHASE2_END = datetime(2026, 4, 27, tzinfo=KST).date()
PHASE3_END = datetime(2026, 5, 8, tzinfo=KST).date()

today_date = NOW.date()
if today_date <= PHASE1_END:
    phase = 1
    phase_name = "Phase 1: 베이스"
elif today_date <= PHASE2_END:
    phase = 2
    phase_name = "Phase 2: 빌드"
elif today_date <= PHASE3_END:
    phase = 3
    phase_name = "Phase 3: 테이퍼"
else:
    phase = 0
    phase_name = "대회 완료"

# 요일별 운동 스케줄
SCHEDULE = {
    1: {  # Phase 1
        0: ("수영 수업", ""),
        1: ("러닝", "5~6km Easy (6:00 페이스 OK)"),
        2: ("수영 수업", ""),
        3: ("러닝 + 코어", "6~7km + 코어 15분"),
        4: ("수영 수업", ""),
        5: ("자전거 → 수영 개인교습", "야외 자전거 60분 → 수영"),
        6: ("러닝 (Long Run)", "8~10km"),
    },
    2: {  # Phase 2
        0: ("수영 수업", ""),
        1: ("러닝 (템포)", "7km: 2km 업 → 3km @5:10 → 2km 다운"),
        2: ("수영 수업", ""),
        3: ("러닝 + 코어", "7~8km Easy"),
        4: ("수영 수업", ""),
        5: ("자전거 → 수영 개인교습", "야외 자전거 75~90분 → 수영"),
        6: ("브릭 훈련", "자전거 60분 → 러닝 5km"),
    },
    3: {  # Phase 3 (테이퍼)
        0: ("수영 수업", ""),
        1: ("러닝", "6km 레이스 페이스 (5:00~5:10)"),
        2: ("수영 수업", ""),
        3: ("러닝", "4km Easy + 스트라이드"),
        4: ("수영 가볍게", "1km"),
        5: ("오픈워터 연습", "수성못"),
        6: ("완전 휴식", ""),
    },
}

DOW_NAMES = ['월', '화', '수', '목', '금', '토', '일']


def get_today_workout():
    if phase == 0 or phase not in SCHEDULE:
        return None, None
    return SCHEDULE[phase].get(DOW, ("휴식", ""))


def format_morning():
    workout, detail = get_today_workout()
    if workout is None:
        return None

    lines = []
    lines.append(f"D-{DAYS_LEFT} | {phase_name}")
    lines.append("")
    lines.append(f"[{DOW_NAMES[DOW]}] {workout}")
    if detail:
        lines.append(f"  {detail}")

    # 내일 미리보기
    tmr_dow = (DOW + 1) % 7
    if phase in SCHEDULE:
        tmr_workout, tmr_detail = SCHEDULE[phase].get(tmr_dow, ("휴식", ""))
        lines.append(f"\n내일({DOW_NAMES[tmr_dow]}): {tmr_workout}")

    return "\n".join(lines)


def format_evening():
    workout, detail = get_today_workout()
    if workout is None:
        return None
    if "휴식" in workout:
        return None

    lines = []
    lines.append(f"D-{DAYS_LEFT} | 오늘 운동 했나요?")
    lines.append("")
    lines.append(f"[{DOW_NAMES[DOW]}] {workout}")
    if detail:
        lines.append(f"  {detail}")
    lines.append("")
    if "러닝" in workout:
        lines.append("러닝은 절대 빠지면 안 됩니다!")
    else:
        lines.append("꾸준함이 실력입니다.")

    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text})


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'morning'

    if mode == 'morning':
        msg = format_morning()
        if msg:
            send_telegram(f"🏋️ 오늘의 운동\n\n{msg}")
    elif mode == 'evening':
        msg = format_evening()
        if msg:
            send_telegram(f"🏋️ 운동 리마인드\n\n{msg}")
