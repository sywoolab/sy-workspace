"""
오늘의 운동 스케줄 알림
- WORKOUT_MASTER.md의 8주 플랜 기반
- 아침: 주간 전체 + 오늘 운동 + 주간 목표
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
        1: ("러닝", "5~6km Easy (6:00 OK)"),
        2: ("수영 수업", ""),
        3: ("러닝 + 코어", "6~7km + 코어 15분"),
        4: ("수영 수업", ""),
        5: ("자전거 → 수영", "야외 60분 → 개인교습"),
        6: ("러닝 Long Run", "8~10km"),
    },
    2: {  # Phase 2
        0: ("수영 수업", ""),
        1: ("러닝 템포", "7km (2up→3@5:10→2dn)"),
        2: ("수영 수업", ""),
        3: ("러닝 + 코어", "7~8km Easy"),
        4: ("수영 수업", ""),
        5: ("자전거 → 수영", "야외 75~90분 → 개인교습"),
        6: ("브릭", "자전거 60분 → 러닝 5km"),
    },
    3: {  # Phase 3 (테이퍼)
        0: ("수영 수업", ""),
        1: ("러닝", "6km @5:00~5:10"),
        2: ("수영 수업", ""),
        3: ("러닝", "4km Easy + 스트라이드"),
        4: ("수영 가볍게", "1km"),
        5: ("오픈워터", "수성못"),
        6: ("완전 휴식", ""),
    },
}

# Phase별 주간 목표 & 최소 기준
PHASE_GOALS = {
    1: {
        "goal": "러닝 빈도 확보 + 자전거 감각 회복",
        "weekly_volume": "수영 4회 / 러닝 3회(20~23km) / 자전거 1회(60분)",
        "minimum": "러닝 최소 3회, 페이스 무관",
    },
    2: {
        "goal": "러닝 페이스 5:10 → 5:00 끌어올리기 + 브릭 적응",
        "weekly_volume": "수영 3~4회 / 러닝 3회(21~25km) / 자전거 2회(150분)",
        "minimum": "러닝 최소 3회 + 일요일 브릭 필수",
    },
    3: {
        "goal": "볼륨 줄이고 컨디션 피킹",
        "weekly_volume": "수영 2~3회 / 러닝 2회(10km) / 자전거 1회(30분)",
        "minimum": "과훈련 금지, 감각 유지만",
    },
}

DOW_NAMES = ['월', '화', '수', '목', '금', '토', '일']
DOW_EMOJI = {
    '수영': '🏊',
    '러닝': '🏃',
    '자전거': '🚴',
    '브릭': '🔥',
    '오픈워터': '🌊',
    '휴식': '😴',
}


def get_emoji(workout):
    for key, emoji in DOW_EMOJI.items():
        if key in workout:
            return emoji
    return '▪️'


def get_today_workout():
    if phase == 0 or phase not in SCHEDULE:
        return None, None
    return SCHEDULE[phase].get(DOW, ("휴식", ""))


def format_morning():
    if phase == 0 or phase not in SCHEDULE:
        return None

    lines = []
    # 헤더
    lines.append(f"🏁 대구 철인3종 D-{DAYS_LEFT}")
    lines.append(f"📍 {phase_name}")
    lines.append("")

    # 주간 목표
    goal_info = PHASE_GOALS.get(phase, {})
    lines.append(f"[이번 주 목표]")
    lines.append(f"  {goal_info.get('goal', '')}")
    lines.append(f"  볼륨: {goal_info.get('weekly_volume', '')}")
    lines.append("")

    # 주간 스케줄 (오늘 표시)
    lines.append("[주간 스케줄]")
    week = SCHEDULE[phase]
    for d in range(7):
        workout, detail = week.get(d, ("휴식", ""))
        emoji = get_emoji(workout)
        marker = " 👈 TODAY" if d == DOW else ""
        day_str = f"  {DOW_NAMES[d]} {emoji} {workout}"
        if detail:
            day_str += f" ({detail})"
        day_str += marker
        lines.append(day_str)

    lines.append("")

    # 오늘의 운동 상세
    workout, detail = get_today_workout()
    lines.append(f"[오늘 할 운동]")
    lines.append(f"  {get_emoji(workout)} {workout}")
    if detail:
        lines.append(f"  → {detail}")

    # 최소 기준
    lines.append("")
    lines.append(f"⚠️ 최소 기준: {goal_info.get('minimum', '')}")

    return "\n".join(lines)


def format_evening():
    workout, detail = get_today_workout()
    if workout is None:
        return None
    if "휴식" in workout:
        return None

    lines = []
    lines.append(f"🏁 D-{DAYS_LEFT} | 오늘 운동 했나요?")
    lines.append("")
    lines.append(f"{get_emoji(workout)} {workout}")
    if detail:
        lines.append(f"  → {detail}")
    lines.append("")
    if "러닝" in workout or "브릭" in workout:
        lines.append("🔴 러닝은 절대 빠지면 안 됩니다!")
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
            send_telegram(msg)
    elif mode == 'evening':
        msg = format_evening()
        if msg:
            send_telegram(msg)
