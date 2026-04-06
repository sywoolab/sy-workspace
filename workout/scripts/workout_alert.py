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
SCHEDULE_FILE = os.path.join(BASE_DIR, 'workout_schedule.json')

# 대회일 & 훈련 시작일
RACE_DAY = datetime(2026, 5, 10, tzinfo=KST)
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
PHASE3_END = datetime(2026, 5, 10, tzinfo=KST).date()  # 일요일 (대회일)

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
        1: ("러닝", "5~6km Easy (6:16+/km)"),
        2: ("수영 수업", ""),
        3: ("러닝 + 코어", "6~7km + 코어 15분"),
        4: ("수영 수업", ""),
        5: ("브릭 → 수영", "자전거 60분 → 러닝 5km Easy → 개인교습"),
        6: ("완전 휴식", ""),
    },
    2: {  # Phase 2
        0: ("수영 수업", ""),
        1: ("러닝 템포", "7km (2up→3@5:10→2dn)"),
        2: ("수영 수업", ""),
        3: ("러닝 + 코어", "7~8km Easy"),
        4: ("수영 수업", ""),
        5: ("브릭 → 수영", "자전거 75~90분 → 러닝 5km → 개인교습"),
        6: ("완전 휴식", ""),
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
    4: ("수영 수업", ""),
    5: ("브릭 → 수영", "자전거 60분 → 러닝 5km Easy → 개인교습"),
    6: ("완전 휴식", ""),
}

# Week 4: 아쿠아슬론 대회 주 (4/13~4/19)
WEEK4_SCHEDULE = {
    0: ("수영 수업", ""),
    1: ("러닝 템포", "7km (2up→3@5:10→2dn)"),
    2: ("수영 수업", ""),
    3: ("러닝 Easy", "5km (대회 전 볼륨 축소)"),
    4: ("수영 가볍게 or 휴식", "대회 2일 전 — 컨디션 우선"),
    5: ("이동 + 검수 + 수영 개인교습", "인천 이동, 가볍게"),
    6: ("아쿠아슬론 대회", "수영 1km + 러닝 10km"),
}

# 대회 주 (Week 7: 5/4~5/10, 대회일 5/10=일요일=weekday 6)
# 5/9(토) 대구 이동+검수, 5/10(일) 대회
WEEK7_SCHEDULE = {
    0: ("수영 가볍게", "1km"),
    1: ("러닝", "3km 조깅"),
    2: ("자전거 가볍게", "30분"),
    3: ("완전 휴식", ""),
    4: ("수영 가볍게", "1km (감각 유지)"),
    5: ("수성못 사전 입수", "대구 이동 + 검수 + 200~300m 가볍게 (수온·감각 확인)"),
    6: ("대회", "수영 1.5km + 자전거 40km + 러닝 10km"),
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


def load_schedule_overrides():
    """workout_schedule.json에서 스케줄 오버라이드 로드"""
    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('overrides', {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


SCHEDULE_OVERRIDES = load_schedule_overrides()


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
    """특정 날짜의 운동 스케줄 반환 (오버라이드 우선)"""
    date_key = dt.strftime('%Y-%m-%d')

    # 오버라이드가 있으면 우선 적용
    override = SCHEDULE_OVERRIDES.get(date_key)
    if override:
        workout = override.get('workout', '휴식')
        detail = override.get('detail', '')
        reason = override.get('reason', '')
        if reason:
            detail = f"{detail} [{reason}]" if detail else f"[{reason}]"
        return (workout, detail)

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


def load_last_analysis():
    """workout_schedule.json에서 마지막 분석 결과 로드"""
    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('last_analysis', {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


HEALTH_FILE = os.path.join(BASE_DIR, 'data', 'garmin_health.json')


def format_training_progress(analysis):
    """전체 훈련 진척도 — 대회 목표 달성 트래킹"""
    lines = []
    lines.append("📈 훈련 진척도")

    # 전체 진행률 (훈련 시작 ~ 대회)
    total_days = (RACE_DAY.date() - TRAIN_START.date()).days
    elapsed = (NOW.date() - TRAIN_START.date()).days
    pct = min(100, round(elapsed / total_days * 100)) if total_days > 0 else 0
    bar_filled = pct // 10
    bar_empty = 10 - bar_filled
    progress_bar = "█" * bar_filled + "░" * bar_empty
    lines.append(f"  [{progress_bar}] {pct}% ({elapsed}/{total_days}일)")

    # 핵심 지표 현황 vs 목표
    vdot = analysis.get('vdot', '?')
    vdot_icon = "🟢" if isinstance(vdot, (int, float)) and vdot >= 39 else (
        "🟡" if isinstance(vdot, (int, float)) and vdot >= 37 else "🔴")
    lines.append(f"  VDOT: {vdot} → 목표 39 {vdot_icon}")

    # workout_schedule.json에서 브릭/OW 카운트
    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            schedule = json.load(f)
        brick_count = schedule.get('brick_count', 0)
        ow_count = schedule.get('ow_count', 0)
    except (FileNotFoundError, json.JSONDecodeError):
        brick_count = 0
        ow_count = 0

    brick_icon = "🟢" if brick_count >= 6 else ("🟡" if brick_count >= 3 else "🔴")
    ow_icon = "🟢" if ow_count >= 3 else ("🟡" if ow_count >= 1 else "🔴")
    lines.append(f"  브릭: {brick_count}/6회 {brick_icon} | OW: {ow_count}/3회 {ow_icon}")

    # 러닝 주간 빈도 (이번 주)
    weekly = analysis.get('weekly_summary', {})
    run_info = weekly.get('run', {})
    run_count = run_info.get('count', 0)
    run_target = run_info.get('target', 3)
    run_icon = "🟢" if run_count >= run_target else ("🟡" if run_count >= run_target - 1 else "🔴")
    lines.append(f"  금주 러닝: {run_count}/{run_target}회 {run_icon}")

    # 목표 달성 전망
    est = analysis.get('estimated_finish', '?')
    status = analysis.get('status', '')
    if status == 'green':
        lines.append(f"  ✅ 현재 페이스 유지하면 목표 달성 가능")
    elif status == 'yellow':
        lines.append(f"  ⚠️ 예상 {est} — 러닝 빈도+브릭 쌓으면 🟢 전환 가능")
    elif status == 'red':
        lines.append(f"  🔴 예상 {est} — 스케줄 강화 필요")

    return "\n".join(lines)


def load_condition():
    """garmin_health.json에서 컨디션 요약 생성"""
    try:
        with open(HEALTH_FILE, 'r', encoding='utf-8') as f:
            health_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    # 오늘 또는 가장 최근 데이터
    today_str = NOW.strftime('%Y-%m-%d')
    health = health_data.get(today_str)
    if not health:
        dates = sorted(health_data.keys(), reverse=True)
        if dates:
            health = health_data[dates[0]]
    if not health:
        return None

    parts = ["📊 컨디션"]

    bb = health.get('body_battery', {})
    if bb.get('max') is not None:
        icon = "🟢" if bb['max'] >= 60 else ("🟡" if bb['max'] >= 40 else "🔴")
        parts.append(f"  BB {bb.get('min', '?')}~{bb['max']} {icon}")

    sleep = health.get('sleep', {})
    if sleep.get('duration_min'):
        h, m = sleep['duration_min'] // 60, sleep['duration_min'] % 60
        score = sleep.get('score', '?')
        icon = "🟢" if (score and score != '?' and score >= 70) else "🟡"
        parts.append(f"  수면 {h}h{m}m (점수 {score}) {icon}")

    hrv = health.get('hrv', {})
    if hrv.get('last_night'):
        status = hrv.get('status', '?')
        icon = "🟢" if status == 'BALANCED' else ("🟡" if status in ('UNBALANCED', 'LOW') else "⚪")
        parts.append(f"  HRV {hrv['last_night']}ms [{status}] {icon}")

    tr = health.get('training_readiness', {})
    if tr.get('score') is not None:
        s = tr['score']
        icon = "🟢" if s >= 60 else ("🟡" if s >= 40 else "🔴")
        parts.append(f"  Readiness {s} ({tr.get('level', '?')}) {icon}")

    rhr = health.get('resting_hr')
    if rhr:
        parts.append(f"  안정시HR {rhr}bpm")

    if len(parts) <= 1:
        return None
    return "\n".join(parts)


def format_morning():
    if phase == 0:
        return None

    lines = []
    # 헤더 + 예상 완주시간
    analysis = load_last_analysis()
    est = analysis.get('estimated_finish', '?')
    status_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(analysis.get('status', ''), '⚪')
    vdot = analysis.get('vdot', '?')
    lines.append(f"🏁 대구 철인3종 D-{DAYS_LEFT} | 예상 {est} {status_icon} | VDOT {vdot}")
    lines.append("")

    # 훈련 진척도
    progress = format_training_progress(analysis)
    if progress:
        lines.append(progress)
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
        # 러닝이면 페이스 존 가이드 표시
        if "러닝" in workout and "휴식" not in workout:
            # VDOT 기반 페이스 존 (workout_analysis.py의 VDOT_TABLE 참조)
            v = analysis.get('vdot', 36)
            # 간이 lookup
            easy_paces = {35: "6:34~7:17", 36: "6:25~7:07", 37: "6:16~6:57", 38: "6:07~6:47"}
            tempo_paces = {35: "5:47", 36: "5:39", 37: "5:31", 38: "5:23"}
            easy_p = easy_paces.get(v, "6:16~6:57")
            tempo_p = tempo_paces.get(v, "5:31")
            if "템포" in workout or "Long" in workout:
                lines.append(f"  📊 Tempo: {tempo_p}/km | Easy: {easy_p}/km")
            else:
                lines.append(f"  📊 Easy: {easy_p}/km (대화 가능 속도)")

    # 컨디션 요약 (garmin_health.json)
    condition = load_condition()
    if condition:
        lines.append("")
        lines.append(condition)

    # 피로 관리 팁 (월요일 = 능동적 휴식 대체 가능일)
    if DOW == 0:  # 월요일
        lines.append("")
        lines.append("💡 피로 누적 시 월요일 수영 → 완전 휴식 대체 가능 (주 1회 한정)")

    return "\n".join(lines)


def format_tomorrow_coaching():
    """내일 운동에 대한 코칭 코멘트 생성"""
    tomorrow = NOW + timedelta(days=1)
    tomorrow_workout, tomorrow_detail = get_schedule_for_date(tomorrow)

    if "휴식" in tomorrow_workout:
        return None

    analysis = load_last_analysis()
    v = analysis.get('vdot', 36)
    easy_paces = {35: "6:34~7:17", 36: "6:25~7:07", 37: "6:16~6:57", 38: "6:07~6:47"}
    tempo_paces = {35: "5:47", 36: "5:39", 37: "5:31", 38: "5:23"}

    lines = []
    dow_name = DOW_NAMES[tomorrow.weekday()]
    lines.append(f"📋 내일({dow_name}) 코칭")
    lines.append(f"  {get_emoji(tomorrow_workout)} {tomorrow_workout}")

    if "브릭" in tomorrow_workout or "미니브릭" in tomorrow_workout:
        easy_p = easy_paces.get(v, "6:16~6:57")
        lines.append(f"  자전거 → 러닝 전환을 최대한 빠르게")
        lines.append(f"  러닝: 처음 2분 다리 무거워도 정상 (전환기)")
        lines.append(f"  러닝 페이스: {easy_p}/km (Easy)")
        if "수영" in tomorrow_workout:
            lines.append(f"  수영: 개인교습 커리큘럼 따라가기")

    elif "러닝" in tomorrow_workout:
        easy_p = easy_paces.get(v, "6:16~6:57")
        tempo_p = tempo_paces.get(v, "5:31")

        if "템포" in tomorrow_workout:
            lines.append(f"  워밍업 2km Easy({easy_p})")
            lines.append(f"  → 템포 3km @ {tempo_p}/km")
            lines.append(f"  → 쿨다운 2km Easy")
            lines.append(f"  ⚠️ 워밍업/쿨다운은 반드시 느리게")
        elif "Long" in tomorrow_workout or "long" in tomorrow_workout:
            lines.append(f"  Easy {easy_p}/km — 대화 가능 속도")
            lines.append(f"  거리 채우기가 목표, 페이스 ❌")
        elif "코어" in tomorrow_workout:
            lines.append(f"  러닝 Easy {easy_p}/km + 코어 15분")
            lines.append(f"  코어: 플랭크/사이드/버드독 각 30초×3")
        else:
            lines.append(f"  Easy {easy_p}/km — 절대 빨리 뛰지 말 것")

    elif "수영" in tomorrow_workout:
        if "수업" in tomorrow_workout:
            lines.append(f"  수업 커리큘럼 따라가기")
            lines.append(f"  장비 사용 시 나중에 알려주세요 (강도 보정)")
        elif "개인교습" in tomorrow_workout:
            lines.append(f"  코치 지시 따라가기")
            lines.append(f"  장비/드릴 내용 공유해주시면 반영합니다")
        else:
            lines.append(f"  맨몸 추천 (벤치마크 측정용)")

    elif "자전거" in tomorrow_workout:
        lines.append(f"  에어로 포지션 최대한 유지")
        if "가볍게" in tomorrow_workout:
            lines.append(f"  HR Zone 1-2, 회복 목적")

    return "\n".join(lines)


def format_recovery_scenario(missed_workout):
    """운동 못한 날 복구 시나리오 제안"""
    lines = []
    lines.append("📊 복구 시나리오")

    today = NOW.date()
    monday = today - timedelta(days=today.weekday())
    days_passed = today.weekday() + 1
    remaining = 7 - days_passed

    # 이번 주 실적
    run_count = 0
    swim_count = 0
    bike_count = 0
    run_km = 0.0

    for d in range(days_passed):
        dt = monday + timedelta(days=d)
        key = dt.strftime('%Y-%m-%d')
        entry = WORKOUT_LOG.get(key)
        if entry and entry.get('done'):
            wtype = entry.get('metrics', {}).get('type', '')
            if wtype == 'run':
                run_count += 1
                run_km += entry.get('metrics', {}).get('distance_km', 0)
            elif wtype == 'swim':
                swim_count += 1
            elif wtype == 'bike':
                bike_count += 1

    run_need = max(0, 3 - run_count)

    lines.append(f"  현재: 러닝 {run_count}/3 | 수영 {swim_count}/4 | 자전거 {bike_count}/1")

    if remaining <= 0:
        lines.append(f"  🔴 이번 주 마감 — 다음 주 볼륨 보충 필요")
        return "\n".join(lines)

    # 러닝 미달 시 복구 방법
    if '러닝' in missed_workout or '브릭' in missed_workout:
        if run_need > remaining:
            lines.append(f"  🔴 러닝 {run_need}회 필요한데 남은 {remaining}일 — 주간 목표 미달 예상")
            lines.append(f"  → 다음 주 초반 러닝 1회 추가로 보충")
        elif run_need > 0:
            lines.append(f"  ✅ 복구 가능 — 남은 {remaining}일 내 러닝 {run_need}회")
            # 구체적 복구 계획
            tomorrow = NOW + timedelta(days=1)
            for d in range(1, remaining + 1):
                future = NOW + timedelta(days=d)
                fw, fd = get_schedule_for_date(future)
                if '러닝' in fw or '브릭' in fw:
                    dow = DOW_NAMES[future.weekday()]
                    lines.append(f"  → {dow}({future.strftime('%m/%d')}) {fw} — 여기서 만회")
                    break
    elif '수영' in missed_workout:
        lines.append(f"  💡 수영 스킵 — 러닝 빈도 확보가 우선이므로 영향 적음")
    else:
        lines.append(f"  💡 오늘 못 해도 주간 목표에 큰 영향 없음")

    # 야근/음주 대응
    lines.append("")
    if DOW in (3, 4):  # 목/금 — 회식 가능성
        lines.append("  💡 야근/회식이었다면:")
        lines.append("    → 내일 Easy로 전환하거나 수영 대체 가능")
        lines.append("    → 음주 다음날: 무리하지 말 것 (탈수 + 수면질 저하)")

    return "\n".join(lines)


def format_evening():
    workout, detail = get_today_workout()
    if workout is None:
        return None
    if "휴식" in workout:
        # 휴식일이어도 내일 코칭은 보내기
        coaching = format_tomorrow_coaching()
        if coaching:
            lines = [f"🏁 D-{DAYS_LEFT} | 😴 오늘은 휴식"]
            lines.append("")
            lines.append(coaching)
            return "\n".join(lines)
        return None

    lines = []

    # 오늘 운동 완료했으면 칭찬 메시지
    today_done = is_done(TODAY)
    if today_done:
        actual = get_actual(TODAY)
        lines.append(f"🏁 D-{DAYS_LEFT} | ✅ 오늘 운동 완료!")
        lines.append("")
        lines.append(f"{get_emoji(workout)} {workout}")
        if actual:
            lines.append(f"  → {actual}")
    else:
        # 미완료 → 리마인드 + 복구 시나리오
        lines.append(f"🏁 D-{DAYS_LEFT} | ⚠️ 오늘 운동 기록이 없습니다!")
        lines.append("")
        lines.append(f"{get_emoji(workout)} {workout}")
        if detail:
            lines.append(f"  → {detail}")
        lines.append("")

        # 복구 시나리오 생성
        recovery = format_recovery_scenario(workout)
        if recovery:
            lines.append(recovery)

    # 내일 코칭 (항상 추가)
    coaching = format_tomorrow_coaching()
    if coaching:
        lines.append("")
        lines.append(coaching)

    # 저녁 운동 가능성 안내
    if not today_done and "휴식" not in workout:
        lines.append("")
        lines.append("💡 저녁에 운동하면 자동 감지되어 업데이트됩니다")

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
