"""
오늘의 운동 스케줄 알림
- WORKOUT_MASTER.md의 8주 플랜 기반
- workout_log.json으로 운동 완료 추적
- 아침: 이번 주 현황(✅/❌) + 오늘 운동 + 주간 목표
- 저녁: 미완료 시만 리마인드
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.strftime('%Y-%m-%d')
DOW = NOW.weekday()  # 0=월 ~ 6=일

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LOG_FILE = os.path.join(BASE_DIR, 'workout_log.json')

# 대회일 & 훈련 시작일
RACE_DAY = datetime(2026, 5, 9, tzinfo=KST)
TRAIN_START = datetime(2026, 3, 16, tzinfo=KST)  # 복귀일 (월요일)
DAYS_LEFT = (RACE_DAY.date() - NOW.date()).days

# 주차 계산 (훈련 시작일 기준, 월요일 시작)
def get_week_number(dt):
    delta = (dt.date() - TRAIN_START.date()).days
    return delta // 7

CURRENT_WEEK = get_week_number(NOW)

# Phase 판별 (날짜 기반, 주 단위 경계 = 일요일)
PHASE1_END = datetime(2026, 4, 5, tzinfo=KST).date()   # 일요일 (Week 2 끝)
PHASE2_END = datetime(2026, 4, 26, tzinfo=KST).date()  # 일요일 (Week 5 끝)
PHASE3_END = datetime(2026, 5, 9, tzinfo=KST).date()   # 토요일 (대회일)

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
    4: "Week 4: 빌드 ② (아쿠아슬론)",
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
        5: ("수영 개인교습", "가볍게 (사이팅 연습)"),
        6: ("완전 휴식", ""),
    },
}

# Week 0: 복귀 주
WEEK0_SCHEDULE = {
    0: ("러닝", "복귀런"),
    1: ("수영", "연속러닝 금지 → 수영 대체"),
    2: ("수영 수업", ""),
    3: ("러닝 + 코어", "6~7km + 코어 15분"),
    4: ("수영 수업 (회식 후 선택)", ""),
    5: ("미니브릭 → 수영", "자전거 60분 → 러닝 2~3km → 개인교습"),
    6: ("러닝 Long Run", "8~10km"),
}

# Week 4: 아쿠아슬론 대회 주 (4/13~4/19)
WEEK4_SCHEDULE = {
    0: ("수영 수업", ""),
    1: ("러닝 템포", "7km (2up→3@5:10→2dn)"),
    2: ("수영 수업", ""),
    3: ("러닝 Easy", "5km (대회 전 볼륨 축소)"),
    4: ("수영 가볍게 or 휴식", "대회 전날 — 컨디션 우선"),
    5: ("아쿠아슬론 대회", "인천체육고"),
    6: ("완전 휴식 or 자전거 가볍게", "회복일"),
}

# 대회 주 (Week 7: 5/4~5/10, 대회일 5/9=토요일=weekday 5)
WEEK7_SCHEDULE = {
    0: ("수영 가볍게", "1km"),
    1: ("러닝", "3km 조깅"),
    2: ("자전거 가볍게", "30분"),
    3: ("완전 휴식", ""),
    4: ("수성못 사전 입수", "대구 이동 + 200~300m 가볍게 (수온·감각 확인)"),
    5: ("대회", "수영 1.5km + 자전거 40km + 러닝 10km"),
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
    '아쿠아슬론': '🏁',
    '오픈워터': '🌊',
    '러닝': '🏃',
    '자전거': '🚴',
    '수영': '🏊',
    '휴식': '😴',
}


# ============================================================
# 운동 로그 (workout_log.json)
# ============================================================
def load_workout_log():
    """workout_log.json 로드"""
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


WORKOUT_LOG = load_workout_log()


def get_log_for_date(date_str):
    """특정 날짜의 운동 로그 반환"""
    return WORKOUT_LOG.get(date_str, None)


def is_done(date_str):
    """해당 날짜 운동 완료 여부"""
    log = get_log_for_date(date_str)
    if log is None:
        return None  # 기록 없음
    return log.get('done', False)


def get_actual(date_str):
    """해당 날짜 실제 운동 내용"""
    log = get_log_for_date(date_str)
    if log is None:
        return None
    return log.get('actual', '')


# ============================================================
# 스케줄
# ============================================================
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
    if wk == 4:
        return WEEK4_SCHEDULE.get(dow, ("휴식", ""))
    if wk >= 7:
        return WEEK7_SCHEDULE.get(dow, ("완전 휴식", ""))
    p, _ = get_phase(dt)
    if p == 0 or p not in SCHEDULE:
        return ("완전 휴식", "")
    return SCHEDULE[p].get(dow, ("휴식", ""))


def format_day_line(dt, is_current_week=False):
    """한 날짜의 스케줄 라인 포매팅 (완료 여부 포함)"""
    dow = dt.weekday()
    date_str = dt.strftime('%m/%d')
    date_key = dt.strftime('%Y-%m-%d')
    workout, detail = get_schedule_for_date(dt)
    emoji = get_emoji(workout)
    is_today = (dt.date() == NOW.date())

    # 완료 여부 표시 (지난 날 또는 오늘)
    done = is_done(date_key)
    actual = get_actual(date_key)

    if dt.date() < NOW.date():
        # 지난 날: 완료/미완료 표시
        if done is True:
            status = "✅"
            # 실제 운동 내용이 있으면 표시
            if actual:
                workout = f"{workout} → {actual}"
        elif done is False:
            status = "❌"
        else:
            # 로그 없음 = 미기록 (휴식일이면 무시)
            if "휴식" in workout:
                status = "😴"
            else:
                status = "⬜"  # 미기록
    elif is_today:
        if done is True:
            status = "✅"
            if actual:
                workout = f"{workout} → {actual}"
        else:
            status = "👈"
    else:
        # 미래
        status = ""

    day_str = f"  {DOW_NAMES[dow]}({date_str}) {emoji} {workout}"
    if detail and done is not True:
        day_str += f" ({detail})"
    if status:
        day_str += f" {status}"

    return day_str


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

    # 이번 주 완료 통계 (현재 주만)
    if is_current_week:
        done_count = 0
        total_count = 0
        run_count = 0
        for d in range(7):
            dt = mon + timedelta(days=d)
            if dt.date() > RACE_DAY.date():
                break
            w, _ = get_schedule_for_date(dt)
            if "휴식" not in w:
                total_count += 1
                date_key = dt.strftime('%Y-%m-%d')
                if is_done(date_key):
                    done_count += 1
                    actual = get_actual(date_key) or w
                    if "러닝" in w or "러닝" in actual:
                        run_count += 1
        lines.append(f"  진행: {done_count}/{total_count} 완료 | 러닝 {run_count}회")

    for d in range(7):
        dt = mon + timedelta(days=d)
        if dt.date() > RACE_DAY.date():
            break
        lines.append(format_day_line(dt, is_current_week))

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
    today_done = is_done(TODAY)
    if today_done:
        actual = get_actual(TODAY)
        lines.append(f"[오늘] ✅ {workout} 완료!")
        if actual:
            lines.append(f"  → {actual}")
    else:
        lines.append(f"[오늘] {get_emoji(workout)} {workout}")
        if detail:
            lines.append(f"  → {detail}")

    # 피로 관리 팁 (월요일 = 능동적 휴식 대체 가능일)
    if DOW == 0:  # 월요일
        lines.append("")
        lines.append("💡 피로 누적 시 월요일 수영 → 완전 휴식 대체 가능 (주 1회 한정)")

    return "\n".join(lines)


def format_evening():
    workout, detail = get_today_workout()
    if workout is None:
        return None
    if "휴식" in workout:
        return None

    # 오늘 운동 완료했으면 칭찬 메시지
    today_done = is_done(TODAY)
    if today_done:
        actual = get_actual(TODAY)
        lines = []
        lines.append(f"🏁 D-{DAYS_LEFT} | ✅ 오늘 운동 완료!")
        lines.append("")
        lines.append(f"{get_emoji(workout)} {workout}")
        if actual:
            lines.append(f"  → {actual}")
        lines.append("")
        lines.append("💪 잘했습니다. 내일도 화이팅!")
        return "\n".join(lines)

    # 미완료 → 리마인드
    lines = []
    lines.append(f"🏁 D-{DAYS_LEFT} | ⚠️ 오늘 운동 기록이 없습니다!")
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
