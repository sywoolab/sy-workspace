"""
오늘의 운동 스케줄 알림
- WORKOUT_MASTER.md의 8주 플랜 기반
- 아침: 향후 2주 스케줄 + 오늘 운동 + 주간 목표
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

# 대회일 & 훈련 시작일
RACE_DAY = datetime(2026, 5, 9, tzinfo=KST)
TRAIN_START = datetime(2026, 3, 16, tzinfo=KST)  # 복귀일 (월요일)
DAYS_LEFT = (RACE_DAY.date() - NOW.date()).days

# 주차 계산 (훈련 시작일 기준, 월요일 시작)
def get_week_number(dt):
    delta = (dt.date() - TRAIN_START.date()).days
    return delta // 7

CURRENT_WEEK = get_week_number(NOW)

# Phase 판별 (날짜 기반)
PHASE1_END = datetime(2026, 4, 6, tzinfo=KST).date()
PHASE2_END = datetime(2026, 4, 27, tzinfo=KST).date()
PHASE3_END = datetime(2026, 5, 9, tzinfo=KST).date()

def get_phase(dt):
    d = dt.date() if hasattr(dt, 'date') else dt
    if d <= PHASE1_END:
        return 1, "Phase 1: 베이스"
    elif d <= PHASE2_END:
        return 2, "Phase 2: 빌드"
    elif d <= PHASE3_END:
        return 3, "Phase 3: 테이퍼"
    return 0, "대회 완료"

phase, phase_name = get_phase(NOW)

# 주차별 이름
WEEK_NAMES = {
    0: "Week 0: 복귀 주",
    1: "Week 1: 베이스 ①",
    2: "Week 2: 베이스 ②",
    3: "Week 3: 빌드 ①",
    4: "Week 4: 빌드 ②",
    5: "Week 5: 빌드 ③",
    6: "Week 6: 테이퍼",
    7: "Week 7: 대회 주",
}

# 요일별 운동 스케줄
SCHEDULE = {
    1: {  # Phase 1
        0: ("수영 수업", ""),
        1: ("러닝", "5~6km Easy (6:00 OK)"),
        2: ("수영 수업", ""),
        3: ("러닝 + 코어", "6~7km + 코어 15분"),
        4: ("수영 수업 (회식 후 선택)", ""),
        5: ("미니브릭 → 수영", "자전거 60분 → 러닝 2~3km → 개인교습"),
        6: ("러닝 Long Run", "8~10km"),
    },
    2: {  # Phase 2
        0: ("수영 수업", ""),
        1: ("러닝 템포", "7km (2up→3@5:10→2dn)"),
        2: ("수영 수업", ""),
        3: ("러닝 + 코어", "7~8km Easy"),
        4: ("수영 수업 (회식 후 선택)", ""),
        5: ("미니브릭 → 수영", "자전거 75~90분 → 러닝 3km → 개인교습"),
        6: ("풀 브릭", "자전거 60분 → 러닝 5km"),
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

# Week 0: 복귀 주 (3/16 월요일에 러닝 → 연속러닝 금지 적용)
WEEK0_SCHEDULE = {
    0: ("러닝 ✅", "7.57km @5:33 (완료)"),
    1: ("수영", "연속러닝 금지 → 수영 대체"),
    2: ("수영 수업", ""),
    3: ("러닝 + 코어", "6~7km + 코어 15분"),
    4: ("수영 수업 (회식 후 선택)", ""),
    5: ("미니브릭 → 수영", "자전거 60분 → 러닝 2~3km → 개인교습"),
    6: ("러닝 Long Run", "8~10km"),
}

# 대회 주 (Week 7) 특수 스케줄
WEEK7_SCHEDULE = {
    0: ("수영 가볍게", "1km"),
    1: ("러닝", "3km 조깅"),
    2: ("자전거 가볍게", "30분"),
    3: ("완전 휴식", ""),
    4: ("대회", "수영 1.5 + 자전거 40 + 러닝 10km"),
    5: ("완전 휴식", ""),
    6: ("완전 휴식", ""),
}

# Phase별 주간 목표 & 최소 기준
PHASE_GOALS = {
    1: {
        "goal": "러닝 빈도 확보 + 자전거 감각 회복",
        "volume": "수영 4 / 러닝 3(20~23km) / 자전거 1(60분)",
        "min": "러닝 최소 3회, 페이스 무관",
    },
    2: {
        "goal": "페이스 5:10→5:00 + 브릭 적응",
        "volume": "수영 3~4 / 러닝 3(21~25km) / 자전거 2(150분)",
        "min": "러닝 최소 3회 + 일요일 브릭 필수",
    },
    3: {
        "goal": "볼륨 줄이고 컨디션 피킹",
        "volume": "수영 2~3 / 러닝 2(10km) / 자전거 1(30분)",
        "min": "과훈련 금지, 감각 유지만",
    },
}

DOW_NAMES = ['월', '화', '수', '목', '금', '토', '일']
DOW_EMOJI = {
    '미니브릭': '🔥',
    '풀 브릭': '🔥',
    '브릭': '🔥',
    '대회': '🏁',
    '오픈워터': '🌊',
    '러닝': '🏃',
    '자전거': '🚴',
    '수영': '🏊',
    '휴식': '😴',
}


def get_emoji(workout):
    for key, emoji in DOW_EMOJI.items():
        if key in workout:
            return emoji
    return '▪️'


def get_schedule_for_date(dt):
    """특정 날짜의 운동 스케줄 반환"""
    wk = get_week_number(dt)
    dow = dt.weekday()
    if wk == 0:
        return WEEK0_SCHEDULE.get(dow, ("휴식", ""))
    if wk >= 7:
        return WEEK7_SCHEDULE.get(dow, ("완전 휴식", ""))
    p, _ = get_phase(dt)
    if p == 0 or p not in SCHEDULE:
        return ("완전 휴식", "")
    return SCHEDULE[p].get(dow, ("휴식", ""))


def format_week(week_num, is_current_week=False):
    """한 주 스케줄을 포매팅"""
    lines = []
    week_name = WEEK_NAMES.get(week_num, f"Week {week_num}")

    # 해당 주의 월요일 날짜 계산
    mon = TRAIN_START + timedelta(days=week_num * 7)
    sun = mon + timedelta(days=6)
    p, p_name = get_phase(mon)
    goal_info = PHASE_GOALS.get(p, {})

    header = f"{'▶ ' if is_current_week else ''}{week_name}"
    lines.append(header)
    lines.append(f"  {mon.strftime('%m/%d')}~{sun.strftime('%m/%d')} | {p_name}")
    if goal_info:
        lines.append(f"  목표: {goal_info.get('goal', '')}")
        lines.append(f"  최소: {goal_info.get('min', '')}")

    for d in range(7):
        dt = mon + timedelta(days=d)
        if dt.date() > RACE_DAY.date():
            break
        workout, detail = get_schedule_for_date(dt)
        emoji = get_emoji(workout)
        date_str = dt.strftime('%m/%d')
        is_today = (dt.date() == NOW.date())
        marker = " 👈" if is_today else ""
        day_str = f"  {DOW_NAMES[d]}({date_str}) {emoji} {workout}"
        if detail:
            day_str += f" ({detail})"
        day_str += marker
        lines.append(day_str)

    return "\n".join(lines)


def get_today_workout():
    return get_schedule_for_date(NOW)


def format_morning():
    if phase == 0:
        return None

    lines = []
    # 헤더
    lines.append(f"🏁 대구 철인3종 D-{DAYS_LEFT}")
    lines.append("")

    # 이번 주
    lines.append(format_week(CURRENT_WEEK, is_current_week=True))
    lines.append("")

    # 다음 주 (대회 후가 아니면)
    next_week = CURRENT_WEEK + 1
    if next_week <= 7:
        lines.append(format_week(next_week, is_current_week=False))
        lines.append("")

    # 오늘의 운동
    workout, detail = get_today_workout()
    lines.append(f"[오늘] {get_emoji(workout)} {workout}")
    if detail:
        lines.append(f"  → {detail}")

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
