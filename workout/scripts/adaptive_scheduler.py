"""
적응형 운동 스케줄 조정 모듈
- garmin_sync.py에서 운동 감지 후 호출
- 일일/주간/Phase 전환 조정 → overrides 생성
- workout_schedule.json의 overrides 필드에 기록

설계서: workout/data/adaptive_algorithm_design.md
상위 규칙: WORKOUT_MASTER.md > WORKOUT_ALGORITHM.md > ~/CLAUDE.md (L0)
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

# L0 §"환경변수 부트스트랩": 부모 경로 거슬러 올라가며 .env 탐색
try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / '.env').exists():
            load_dotenv(_p / '.env')
            break
except ImportError:
    pass

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.strftime('%Y-%m-%d')

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LOG_FILE = os.path.join(BASE_DIR, 'workout_log.json')
SCHEDULE_FILE = os.path.join(BASE_DIR, 'workout_schedule.json')
HEALTH_FILE = os.path.join(BASE_DIR, 'data', 'garmin_health.json')
QUEUE_FILE = os.path.join(BASE_DIR, 'data', 'improvement_queue.json')

RACE_DAY = datetime(2026, 5, 10, tzinfo=KST)
TRAIN_START = datetime(2026, 3, 16, tzinfo=KST)

# Phase 경계일
PHASE1_END = datetime(2026, 4, 5, tzinfo=KST).date()
PHASE2_END = datetime(2026, 4, 26, tzinfo=KST).date()
PHASE3_END = datetime(2026, 5, 10, tzinfo=KST).date()

# 텔레그램 (운동 봇 fallback 체인)
BOT_TOKEN = (os.environ.get('BOT_TOKEN')
             or os.environ.get('TRAINING_BOT_TOKEN')
             or os.environ.get('TELEGRAM_BOT_TOKEN', ''))
CHAT_ID = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')

DOW_NAMES = ['월', '화', '수', '목', '금', '토', '일']

# ============================================================
# VDOT Table (Jack Daniels) — workout_analysis.py와 동일
# ============================================================
VDOT_TABLE = [
    # (vdot, 10k_sec/km, easy_low, easy_high, tempo, interval)
    (30, 414, 448, 496, 396, 362),
    (31, 403, 436, 483, 385, 352),
    (32, 393, 425, 471, 375, 342),
    (33, 383, 414, 459, 365, 333),
    (34, 374, 404, 448, 356, 324),
    (35, 365, 394, 437, 347, 316),
    (36, 356, 385, 427, 339, 308),
    (37, 348, 376, 417, 331, 300),
    (38, 340, 367, 407, 323, 293),
    (39, 333, 359, 398, 316, 286),
    (40, 326, 351, 389, 309, 280),
    (41, 319, 344, 381, 302, 274),
    (42, 312, 337, 373, 296, 268),
    (43, 306, 330, 365, 290, 262),
    (44, 300, 323, 358, 284, 257),
    (45, 294, 317, 351, 278, 252),
]

# ============================================================
# Phase별 기본 스케줄 (WORKOUT_MASTER.md 기반)
# 요일: 0=월 ~ 6=일
# ============================================================
BASE_SCHEDULE = {
    1: {  # Phase 1: 베이스
        0: {"workout": "수영 수업", "detail": "", "type": "swim"},
        1: {"workout": "러닝 Easy", "detail": "5~6km @6:00+", "type": "run", "base_km": 6},
        2: {"workout": "수영 수업", "detail": "", "type": "swim"},
        3: {"workout": "러닝 + 코어", "detail": "6~7km + 코어 15분", "type": "run", "base_km": 7},
        4: {"workout": "수영 수업", "detail": "", "type": "swim"},
        5: {"workout": "브릭 → 수영", "detail": "자전거 60분 → 러닝 5km → 수영", "type": "brick", "base_km": 5},
        6: {"workout": "완전 휴식", "detail": "", "type": "rest"},
    },
    2: {  # Phase 2: 빌드
        0: {"workout": "수영 수업", "detail": "", "type": "swim"},
        1: {"workout": "러닝 템포", "detail": "7km: 2up→3@5:10→2dn", "type": "run", "base_km": 7},
        2: {"workout": "수영 수업", "detail": "", "type": "swim"},
        3: {"workout": "러닝 Easy", "detail": "7~8km Easy", "type": "run", "base_km": 8},
        4: {"workout": "수영 수업", "detail": "", "type": "swim"},
        5: {"workout": "브릭 → 수영", "detail": "자전거 75~90분 → 러닝 5km → 수영", "type": "brick", "base_km": 5},
        6: {"workout": "완전 휴식", "detail": "", "type": "rest"},
    },
    3: {  # Phase 3: 테이퍼
        0: {"workout": "수영 수업", "detail": "가볍게", "type": "swim"},
        1: {"workout": "러닝", "detail": "6km 레이스 페이스", "type": "run", "base_km": 6},
        2: {"workout": "수영 수업", "detail": "가볍게", "type": "swim"},
        3: {"workout": "러닝 Easy", "detail": "4km + 스트라이드", "type": "run", "base_km": 4},
        4: {"workout": "수영 가볍게", "detail": "1km", "type": "swim"},
        5: {"workout": "수영 개인교습", "detail": "사이팅 연습", "type": "swim"},
        6: {"workout": "완전 휴식", "detail": "", "type": "rest"},
    },
}

# Phase별 주간 목표
PHASE_TARGETS = {
    1: {"swim": 4, "run": 3, "run_km": 20, "bike": 1, "weekly_load": 300},
    2: {"swim": 3, "run": 3, "run_km": 21, "bike": 2, "weekly_load": 380},
    3: {"swim": 2, "run": 2, "run_km": 10, "bike": 1, "weekly_load": 200},
}

# Override 우선순위 (낮을수록 높은 우선순위)
RULE_PRIORITY = {
    'C3': 1, 'A3-high': 2, 'B2': 3, 'A1': 4,
    'A3-medium': 5, 'B1': 6, 'A4': 7, 'B3': 8,
}


# ============================================================
# 유틸리티
# ============================================================
def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def pace_to_seconds(pace_str):
    """'5:33' → 333"""
    if not pace_str:
        return None
    parts = pace_str.split(':')
    if len(parts) == 2:
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            return None
    return None


def seconds_to_pace(secs):
    """333 → '5:33'"""
    if secs is None or secs <= 0:
        return '?'
    m = int(secs) // 60
    s = int(secs) % 60
    return f"{m}:{s:02d}"


def get_phase(dt):
    d = dt.date() if hasattr(dt, 'date') else dt
    if d <= PHASE1_END:
        return 1, "Phase 1: 베이스"
    elif d <= PHASE2_END:
        return 2, "Phase 2: 빌드"
    elif d <= PHASE3_END:
        return 3, "Phase 3: 테이퍼"
    return 0, "대회 완료"


def get_phase_end_date(phase):
    if phase == 1:
        return PHASE1_END
    elif phase == 2:
        return PHASE2_END
    elif phase == 3:
        return PHASE3_END
    return NOW.date()


def get_week_monday(dt):
    d = dt.date() if hasattr(dt, 'date') else dt
    wk = (d - TRAIN_START.date()).days // 7
    return (TRAIN_START + timedelta(days=wk * 7)).date()


def get_base_schedule(date_obj):
    """날짜에 해당하는 기본 스케줄 반환"""
    if hasattr(date_obj, 'date'):
        date_obj = date_obj.date()
    phase, _ = get_phase(date_obj)
    if phase == 0:
        return {"workout": "완전 휴식", "detail": "", "type": "rest", "base_km": 0}
    dow = date_obj.weekday()
    phase_sched = BASE_SCHEDULE.get(phase, BASE_SCHEDULE[1])
    return dict(phase_sched.get(dow, {"workout": "휴식", "detail": "", "type": "rest", "base_km": 0}))


def get_vdot_paces(vdot):
    """VDOT에 해당하는 각 존별 페이스(초/km) 반환"""
    for v, race, easy_lo, easy_hi, tempo, interval in VDOT_TABLE:
        if v == vdot:
            return {
                "race_10k": race, "easy_low": easy_lo, "easy_high": easy_hi,
                "tempo": tempo, "interval": interval,
            }
    # 범위 밖 → 기본값
    if vdot > 45:
        return {"race_10k": 294, "easy_low": 317, "easy_high": 351, "tempo": 278, "interval": 252}
    return {"race_10k": 414, "easy_low": 448, "easy_high": 496, "tempo": 396, "interval": 362}


def predict_10k_time(vdot):
    """VDOT로 10km 레이스 타임 예측 (분)"""
    paces = get_vdot_paces(vdot)
    return (paces['race_10k'] * 10) / 60


def is_recovery_period():
    """복귀 2주 이내인지 판정"""
    return NOW.date() <= (TRAIN_START.date() + timedelta(days=14))


def get_weekly_stats(log, week_monday):
    """주간 운동 통계 (간이 버전)"""
    stats = {
        'run': {'count': 0, 'total_km': 0.0},
        'swim': {'count': 0},
        'bike': {'count': 0},
        'total_load': 0,
    }
    for d in range(7):
        day = week_monday + timedelta(days=d)
        key = day.strftime('%Y-%m-%d')
        entry = log.get(key)
        if not entry or not entry.get('done'):
            continue

        all_metrics = entry.get('all_metrics')
        if all_metrics and isinstance(all_metrics, list):
            metrics_list = all_metrics
        else:
            metrics_list = [entry.get('metrics', {})]

        for m in metrics_list:
            wtype = m.get('type', '')
            if wtype == 'run':
                stats['run']['count'] += 1
                stats['run']['total_km'] += m.get('distance_km', 0)
            elif wtype == 'swim':
                stats['swim']['count'] += 1
            elif wtype == 'bike':
                stats['bike']['count'] += 1

            # 간이 부하 계산
            load = m.get('training_load', 0)
            if not load:
                duration = m.get('duration_min', m.get('moving_min', 0))
                if wtype == 'run':
                    pace = pace_to_seconds(m.get('pace_per_km'))
                    dist = m.get('distance_km', 0)
                    if pace and dist:
                        duration = (pace * dist) / 60
                load = round(duration * 1.2)
            stats['total_load'] += load

    stats['run']['total_km'] = round(stats['run']['total_km'], 1)
    return stats


def is_run_day(date_obj, log):
    """해당 날짜에 러닝을 했는지 확인"""
    key = date_obj.strftime('%Y-%m-%d')
    entry = log.get(key)
    if not entry or not entry.get('done'):
        return False
    all_metrics = entry.get('all_metrics')
    if all_metrics:
        return any(m.get('type') == 'run' for m in all_metrics)
    return entry.get('metrics', {}).get('type') == 'run'


def get_recent_notes(log, days=7):
    """최근 N일간의 노트 반환"""
    notes = []
    for d in range(days):
        dt = NOW.date() - timedelta(days=d)
        key = dt.strftime('%Y-%m-%d')
        entry = log.get(key, {})
        note = entry.get('note', '')
        if note:
            notes.append((key, note))
    return notes


def send_telegram(text):
    """텔레그램 메시지 전송"""
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[adaptive] 텔레그램 토큰/채팅ID 없음 — 메시지 출력만")
        print(text)
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
        return resp.ok
    except Exception as e:
        print(f"[adaptive] 텔레그램 전송 실패: {e}")
        return False


# ============================================================
# 규칙 A: 일일 조정
# ============================================================

def rule_a1_post_hard(today_entry, tomorrow_date, log):
    """A1: 고강도 후 회복 강제 — 내일 러닝이면 Easy로 전환"""
    if not today_entry or not today_entry.get('done'):
        return None

    zone = today_entry.get('training_zone', '')
    is_hard = zone in ('tempo', 'interval', 'repetition')

    # training_load 체크 (all_metrics의 합산)
    total_load = 0
    all_metrics = today_entry.get('all_metrics')
    if all_metrics:
        for m in all_metrics:
            total_load += m.get('training_load', 0)
    else:
        total_load = today_entry.get('metrics', {}).get('training_load', 0)

    # 주간 평균 부하
    week_monday = get_week_monday(NOW)
    weekly_stats = get_weekly_stats(log, week_monday)
    days_in_week = min(NOW.date().weekday() + 1, 7)
    week_avg_load = weekly_stats['total_load'] / max(days_in_week, 1)

    is_overload = total_load > week_avg_load * 1.5 if week_avg_load > 0 else False

    if not (is_hard or is_overload):
        return None

    tomorrow_base = get_base_schedule(tomorrow_date)
    if tomorrow_base['type'] not in ('run', 'brick'):
        return None

    # 일요일 완전 휴식 불가침
    if tomorrow_date.weekday() == 6:
        return None

    base_km = tomorrow_base.get('base_km', 6)
    easy_km = max(3, int(base_km * 0.5))

    reason_parts = []
    if is_hard:
        reason_parts.append(f"어제 {zone}")
    if is_overload:
        reason_parts.append(f"부하 {total_load}")

    return {
        "date": tomorrow_date.strftime('%Y-%m-%d'),
        "workout": "러닝 Easy",
        "detail": f"{easy_km}km @6:00+/km",
        "reason": f"{', '.join(reason_parts)} 후 회복",
        "source": "adaptive_A1",
        "auto": True,
        "rule": "A1",
        "created_at": NOW.isoformat(),
    }


def rule_a2_missed_workout(today_date_str, log):
    """A2: 러닝 누락 시 내일 수영→러닝 대체"""
    entry = log.get(today_date_str, {})

    # planned가 있는데 done이 False인 경우만
    if entry.get('done', False):
        return None
    planned = entry.get('planned', '')
    if not planned:
        return None
    if '러닝' not in planned and 'run' not in planned.lower() and '브릭' not in planned:
        return None  # 누락된 게 러닝/브릭이 아니면 패스

    today_date = datetime.strptime(today_date_str, '%Y-%m-%d').date()
    tomorrow = today_date + timedelta(days=1)

    # 일요일 완전 휴식 불가침
    if tomorrow.weekday() == 6:
        return None

    tomorrow_base = get_base_schedule(tomorrow)
    if tomorrow_base['type'] == 'swim':
        # 연속 러닝 체크: 어제 러닝했거나 모레도 러닝 예정이면 복귀 2주 이내 금지
        yesterday = today_date - timedelta(days=1)
        day_after = tomorrow + timedelta(days=1)
        if is_recovery_period():
            if is_run_day(yesterday, log):
                return None  # 복귀 2주 내 연속 러닝 금지
            day_after_base = get_base_schedule(day_after)
            if day_after_base['type'] in ('run', 'brick'):
                return None  # 복귀 2주 내 연속 러닝 금지

        return {
            "date": tomorrow.strftime('%Y-%m-%d'),
            "workout": "러닝 Easy",
            "detail": "6km @6:00+/km (수영 대체하여 보충)",
            "reason": f"{today_date_str} 러닝 누락 → 수영 대체하여 보충",
            "source": "adaptive_A2",
            "auto": True,
            "rule": "A2",
            "created_at": NOW.isoformat(),
        }

    return None  # 내일이 이미 러닝이면 자연 보충


def rule_a3_condition_check(target_date_str, health_data, workout_log=None):
    """A3: 컨디션 불량 시 오늘/내일 전환. 이미 운동 완료면 내일 회복 권고."""
    health = health_data.get(target_date_str)
    if not health:
        # 가장 최근 데이터 사용
        dates = sorted(health_data.keys(), reverse=True)
        if dates:
            health = health_data[dates[0]]
    if not health:
        return None

    high_count = 0
    medium_count = 0
    reasons = []

    # Body Battery
    bb = health.get('body_battery', {})
    bb_max = bb.get('max')
    if bb_max is not None:
        if bb_max < 40:
            high_count += 1
            reasons.append(f"BB {bb_max}")
        elif bb_max < 60:
            medium_count += 1
            reasons.append(f"BB {bb_max}")

    # HRV
    hrv = health.get('hrv', {})
    hrv_status = hrv.get('status', '')
    if hrv_status in ('LOW', 'POOR'):
        high_count += 1
        reasons.append(f"HRV {hrv_status}")
    elif hrv.get('last_night') and hrv.get('weekly_avg'):
        if hrv['last_night'] < hrv['weekly_avg'] * 0.75:
            medium_count += 1
            reasons.append(f"HRV {hrv['last_night']}ms (avg {hrv['weekly_avg']})")

    # Training Readiness
    tr = health.get('training_readiness', {})
    tr_score = tr.get('score')
    if tr_score is not None:
        if tr_score < 30:
            high_count += 1
            reasons.append(f"TR {tr_score}")
        elif tr_score < 50:
            medium_count += 1
            reasons.append(f"TR {tr_score}")

    # 수면
    sleep = health.get('sleep', {})
    sleep_score = sleep.get('score')
    sleep_min = sleep.get('duration_min', 999)
    if sleep_score is not None and sleep_score < 50:
        high_count += 1
        reasons.append(f"수면 {sleep_score}")
    elif sleep_min < 360:
        medium_count += 1
        reasons.append(f"수면 {sleep_min // 60}h{sleep_min % 60}m")

    # 안정시 심박
    rhr = health.get('resting_hr')
    if rhr and rhr > 55:
        medium_count += 1
        reasons.append(f"RHR {rhr}")

    reason_str = ", ".join(reasons)

    # 오늘 이미 운동 완료했는지 + 운동 강도/종목 확인
    already_done = False
    today_zone = 'rest'
    today_type = ''
    if workout_log:
        entry = workout_log.get(target_date_str, {})
        already_done = entry.get('done', False)
        today_zone = entry.get('training_zone') or 'rest'
        today_type = entry.get('metrics', {}).get('type', '')

    # 근력 운동(strength)은 심폐 부하 아님 → 컨디션 판정에서 "운동 안 한 것"과 동일 취급
    if today_type == 'strength':
        already_done = False

    # 종합 판정: 단일 지표가 아닌 다수 지표 종합
    # 적색: high 2개 이상, 또는 high 1개 + medium 2개 이상
    # 황색: high 1개 (단독), 또는 medium 2개 이상
    # 주의: medium 1개 (경고만)
    is_red = (high_count >= 2) or (high_count >= 1 and medium_count >= 2)
    is_yellow = (not is_red) and (high_count >= 1 or medium_count >= 2)
    is_caution = (not is_red and not is_yellow) and medium_count >= 1

    # easy 운동은 회복에 방해 안 됨 → 이미 easy로 운동한 경우 내일 override 불필요
    easy_done = already_done and today_zone in ('easy', 'rest')

    if is_red:
        if already_done:
            if easy_done:
                # easy 운동 완료 → 경고만 (내일 override 불필요)
                return {"warning_only": True, "reason": f"⚠️ 컨디션 적색({reason_str}) — Easy 운동은 OK, 내일 고강도 금지"}
            tomorrow = (datetime.strptime(target_date_str, '%Y-%m-%d') + timedelta(days=1))
            if tomorrow.weekday() == 6:
                return {"warning_only": True, "reason": f"⚠️ 컨디션 적색({reason_str})에서 고강도 운동함 — 일요일 충분히 쉬세요"}
            return {
                "date": tomorrow.strftime('%Y-%m-%d'),
                "workout": "Easy 또는 완전 휴식",
                "detail": f"오늘({target_date_str}) 컨디션 불량에서 고강도 운동 → 회복 필요",
                "reason": f"컨디션 적색 사후 회복: {reason_str}",
                "source": "adaptive_A3",
                "auto": True,
                "rule": "A3-high",
                "created_at": NOW.isoformat(),
            }
        return {
            "date": target_date_str,
            "workout": "Easy 또는 완전 휴식",
            "detail": "컨디션 불량 — 고강도 금지",
            "reason": f"컨디션 적색: {reason_str}",
            "source": "adaptive_A3",
            "auto": True,
            "rule": "A3-high",
            "created_at": NOW.isoformat(),
        }
    elif is_yellow:
        if already_done:
            if easy_done:
                return {"warning_only": True, "reason": f"💡 컨디션 황색({reason_str}) — Easy 운동은 OK"}
            return {"warning_only": True, "reason": f"💡 컨디션 황색({reason_str})에서 운동함 — 내일 Easy 권장"}
        return {
            "date": target_date_str,
            "workout": "Easy only",
            "detail": "템포/인터벌 금지, Easy 강도까지만",
            "reason": f"컨디션 황색: {reason_str}",
            "source": "adaptive_A3",
            "auto": True,
            "rule": "A3-medium",
            "created_at": NOW.isoformat(),
        }

    if is_caution:
        return {"warning_only": True, "reason": f"컨디션 주의: {reason_str}"}
    return None


def rule_a4_outperformance(today_entry, schedule, log=None):
    """A4: 예상보다 좋은 성과 시 다음 고강도 세션 목표 상향"""
    if not today_entry or not today_entry.get('done'):
        return None

    zone = today_entry.get('training_zone', '')
    if zone not in ('tempo', 'interval'):
        return None

    # 주간 부하 확인: total_load > target * 1.2이면 발동 안 함
    if log is not None:
        phase, _ = get_phase(NOW)
        week_monday = get_week_monday(NOW)
        weekly_stats = get_weekly_stats(log, week_monday)
        target_load = PHASE_TARGETS.get(phase, {}).get('weekly_load', 300)
        if weekly_stats['total_load'] > target_load * 1.2:
            return None

    # all_metrics에서 러닝 metrics 찾기
    all_metrics = today_entry.get('all_metrics')
    if all_metrics and isinstance(all_metrics, list):
        metrics = next((m for m in all_metrics if m.get('type') == 'run'), {})
    else:
        metrics = today_entry.get('metrics', {})
    pace_sec = pace_to_seconds(metrics.get('pace_per_km'))
    if not pace_sec:
        return None

    current_vdot = schedule.get('current_vdot', 36)
    vdot_paces = get_vdot_paces(current_vdot)
    target_pace = vdot_paces['tempo'] if zone == 'tempo' else vdot_paces['interval']

    improvement = target_pace - pace_sec  # 양수 = 목표보다 빠름
    avg_hr = metrics.get('avg_hr', 0)

    # 페이스 5초+ 빠르고, HR이 합리적 범위 (Zone 3-4 이내)
    if improvement >= 5 and avg_hr < 175:
        # 다음 화요일 찾기
        today = datetime.strptime(TODAY, '%Y-%m-%d').date()
        days_ahead = (1 - today.weekday()) % 7  # 다음 화요일
        if days_ahead == 0:
            days_ahead = 7
        next_tempo_day = today + timedelta(days=days_ahead)

        new_target = target_pace - 3  # 3초 보수적 상향

        return {
            "date": next_tempo_day.strftime('%Y-%m-%d'),
            "workout": "러닝 템포 (상향)",
            "detail": f"3km @{seconds_to_pace(new_target)} (기존 대비 -3초)",
            "reason": f"이전 {zone} {seconds_to_pace(pace_sec)}/km (목표 {seconds_to_pace(target_pace)} 대비 -{improvement}초)",
            "source": "adaptive_A4",
            "auto": True,
            "rule": "A4",
            "created_at": NOW.isoformat(),
        }
    return None


# ============================================================
# 규칙 B: 주간 조정
# ============================================================

def rule_b1_run_frequency(log, week_monday):
    """B1: 수요일 이후 러닝 빈도 부족 시 수영→러닝 대체"""
    today_dow = NOW.date().weekday()

    # 수요일 이후만 판단
    if today_dow < 2:
        return []

    stats = get_weekly_stats(log, week_monday)
    run_count = stats['run']['count']
    run_target = 3
    deficit = run_target - run_count

    if deficit <= 0:
        return []

    overrides = []
    swim_replaced = 0

    # 이미 러닝인 날짜와 새로 배치한 날짜를 추적
    tracked_dates = set()
    for dow in range(7):
        day_date = week_monday + timedelta(days=dow)
        base = get_base_schedule(day_date)
        if base['type'] in ('run', 'brick') or is_run_day(day_date, log):
            tracked_dates.add(day_date)

    for dow in range(today_dow + 1, 6):  # 일요일(6) 제외
        if deficit <= 0:
            break

        day_date = week_monday + timedelta(days=dow)
        base = get_base_schedule(day_date)

        # 이미 러닝이거나 브릭이면 스킵
        if base['type'] in ('run', 'brick'):
            continue

        # 수영만 러닝으로 대체 가능
        if base['type'] != 'swim':
            continue

        # 연속 러닝 규칙 체크 (기존 + 새로 배치한 날짜 포함)
        prev_day = day_date - timedelta(days=1)
        next_day = day_date + timedelta(days=1)
        if prev_day in tracked_dates or next_day in tracked_dates:
            if is_recovery_period():
                continue  # 복귀 2주 내 연속 러닝 금지

        ov = {
            "date": day_date.strftime('%Y-%m-%d'),
            "workout": "러닝 Easy",
            "detail": "6km @6:00+/km (수영 대체)",
            "reason": f"주간 러닝 {run_count}/{run_target}회 — 빈도 보충",
            "source": "adaptive_B1",
            "auto": True,
            "rule": "B1",
            "created_at": NOW.isoformat(),
        }
        tracked_dates.add(day_date)  # 새로 배치한 날짜도 추적
        swim_replaced += 1
        if swim_replaced >= 2:
            ov['warning'] = "수영 2회 대체 — 수영 볼륨 부족 가능"
        overrides.append(ov)
        deficit -= 1

    return overrides


def rule_b2_overload(log, week_monday, phase):
    """B2: 주간 볼륨 120% 초과 시 남은 일 Easy"""
    stats = get_weekly_stats(log, week_monday)
    target = PHASE_TARGETS.get(phase, {}).get('weekly_load', 300)

    if stats['total_load'] <= target * 1.2:
        return []

    overrides = []
    today_dow = NOW.date().weekday()

    for d in range(today_dow + 1, 6):  # 일요일(6) 제외
        day_date = week_monday + timedelta(days=d)
        base = get_base_schedule(day_date)
        if base['type'] in ('run', 'brick'):
            overrides.append({
                "date": day_date.strftime('%Y-%m-%d'),
                "workout": "Easy 또는 수영",
                "detail": "볼륨 초과 — 고강도 금지",
                "reason": f"주간 부하 {stats['total_load']}/{target} ({round(stats['total_load'] / target * 100)}%)",
                "source": "adaptive_B2",
                "auto": True,
                "rule": "B2",
                "created_at": NOW.isoformat(),
            })

    return overrides


def rule_b3_underload(log, week_monday, phase):
    """B3: 일요일 기준 주간 볼륨 80% 미만 시 다음 주 보충"""
    stats = get_weekly_stats(log, week_monday)
    target = PHASE_TARGETS.get(phase, {}).get('weekly_load', 300)

    if stats['total_load'] >= target * 0.8:
        return []

    deficit_pct = round((1 - stats['total_load'] / target) * 100) if target > 0 else 0
    next_tuesday = week_monday + timedelta(days=8)  # 다음 주 화요일

    return [{
        "date": next_tuesday.strftime('%Y-%m-%d'),
        "workout": "러닝 Easy (보충)",
        "detail": "기본 거리 + 2km 추가 @6:00+",
        "reason": f"지난 주 부하 {stats['total_load']}/{target} ({100 - deficit_pct}%) — Easy 보충",
        "source": "adaptive_B3",
        "auto": True,
        "rule": "B3",
        "created_at": NOW.isoformat(),
    }]


# ============================================================
# 규칙 C: Phase 전환 / 부상 감지
# ============================================================

def rule_c1_phase_transition(log, schedule, current_phase):
    """C1: Phase 전환 조건 평가 — 보고만, override 없음"""
    vdot = schedule.get('current_vdot', 35)

    benchmarks = {}
    if current_phase == 1:
        # Phase 1→2: 러닝 주 3회 안정적 + 10km 논스톱 + 자전거 60분 완주
        week_monday = get_week_monday(NOW)
        stats = get_weekly_stats(log, week_monday)
        benchmarks = {
            "러닝 주 3회": stats['run']['count'] >= 3,
            "10km 논스톱": _has_10k_nonstop(log),
            "자전거 60분": _has_bike_session(log, 55),
        }
    elif current_phase == 2:
        # Phase 2→3: 템포 3km @5:15 + 10km 55분 + 브릭런 5km
        benchmarks = {
            "템포 3km @5:15": _has_tempo_pace(log, 315),
            "10km 55분": predict_10k_time(vdot) <= 56,
            "브릭런 5km": _has_brick_run(log, 4.5),
        }

    all_met = all(benchmarks.values()) if benchmarks else False

    return {
        "phase": current_phase,
        "benchmarks": benchmarks,
        "all_met": all_met,
        "action": "phase_advance" if all_met else "report_to_user",
    }


def _has_10k_nonstop(log):
    """10km 이상 논스톱 러닝 기록이 있는지"""
    for entry in log.values():
        if not entry.get('done'):
            continue
        all_metrics = entry.get('all_metrics')
        if all_metrics and isinstance(all_metrics, list):
            metrics_list = all_metrics
        else:
            metrics_list = [entry.get('metrics', {})]
        for m in metrics_list:
            if m.get('type') == 'run' and m.get('distance_km', 0) >= 9.5:
                return True
    return False


def _has_bike_session(log, min_minutes):
    """자전거 N분 이상 기록이 있는지"""
    for entry in log.values():
        if not entry.get('done'):
            continue
        all_metrics = entry.get('all_metrics', [entry.get('metrics', {})])
        for m in all_metrics:
            if m.get('type') == 'bike' and m.get('duration_min', 0) >= min_minutes:
                return True
    return False


def _has_tempo_pace(log, max_pace_sec):
    """템포 구간에서 목표 페이스 이하 달성 기록이 있는지"""
    for entry in log.values():
        if not entry.get('done'):
            continue
        if entry.get('training_zone') in ('tempo', 'interval'):
            all_metrics = entry.get('all_metrics')
            if all_metrics and isinstance(all_metrics, list):
                metrics_list = all_metrics
            else:
                metrics_list = [entry.get('metrics', {})]
            for m in metrics_list:
                if m.get('type') == 'run':
                    pace = pace_to_seconds(m.get('pace_per_km'))
                    if pace and pace <= max_pace_sec:
                        return True
    return False


def _has_brick_run(log, min_km):
    """브릭 러닝 N km 이상 기록이 있는지"""
    for entry in log.values():
        if not entry.get('done'):
            continue
        if entry.get('is_brick'):
            all_metrics = entry.get('all_metrics')
            if all_metrics and isinstance(all_metrics, list):
                metrics_list = all_metrics
            else:
                metrics_list = [entry.get('metrics', {})]
            for m in metrics_list:
                if m.get('type') == 'run' and m.get('distance_km', 0) >= min_km:
                    return True
    return False


def rule_c2_vdot_stagnation(schedule):
    """C2: VDOT 3주 정체 감지 — 보고만"""
    history = schedule.get('vdot_history', [])
    if len(history) < 3:
        return None

    recent_3 = [h.get('vdot', 0) if isinstance(h, dict) else h for h in history[-3:]]
    vdot_range = max(recent_3) - min(recent_3)

    if vdot_range <= 1:
        vdot_val = recent_3[-1]
        est_finish = round(predict_10k_time(vdot_val), 1)
        return {
            "type": "vdot_stagnation",
            "vdot": vdot_val,
            "weeks": 3,
            "action": "report_to_user",
            "message": f"VDOT {vdot_val}에서 3주 정체",
            "options": [
                "Phase 1주 연장",
                "인터벌 세션 1회 추가 (주 1회 → 화/목 중 택1)",
                f"목표 재설정: 현 VDOT 기준 10km 예상 {est_finish}분",
            ],
        }
    return None


def rule_c3_injury_detection(log, health_data):
    """C3: 부상 징후 감지 — 러닝 볼륨 50% 감소"""
    injury_keywords = ['통증', '부상', 'pain', '아프', '쑤시', '찌릿']
    recent_notes = get_recent_notes(log, days=7)

    injury_mentions = []
    for date, note in recent_notes:
        for kw in injury_keywords:
            if kw in note:
                injury_mentions.append((date, note))
                break

    # RHR 급등 체크
    today_health = health_data.get(TODAY, {})
    rhr = today_health.get('resting_hr', 0)

    if len(injury_mentions) == 0 and rhr < 54:
        return []

    severity = "medium"
    if len(injury_mentions) >= 2:
        severity = "high"
    if rhr >= 54:  # 평소 45 * 1.2 = 54
        severity = "high"

    if severity != "high":
        return []

    overrides = []
    for d in range(1, 8):
        day_date = NOW.date() + timedelta(days=d)
        if day_date.weekday() == 6:  # 일요일 불가침
            continue
        base = get_base_schedule(day_date)
        if base['type'] in ('run', 'brick'):
            base_km = base.get('base_km', 6)
            easy_km = max(3, int(base_km * 0.5))
            reason_text = injury_mentions[-1][1] if injury_mentions else f"RHR {rhr}"
            overrides.append({
                "date": day_date.strftime('%Y-%m-%d'),
                "workout": "러닝 Easy (감량)",
                "detail": f"{easy_km}km — 부상 방지",
                "reason": f"부상 징후 감지: {reason_text}",
                "source": "adaptive_C3",
                "auto": True,
                "rule": "C3",
                "created_at": NOW.isoformat(),
            })

    return overrides


# ============================================================
# Override 관리
# ============================================================

def resolve_conflicts(overrides_list):
    """동일 날짜에 여러 override → 우선순위 최고만 적용"""
    by_date = {}
    for ov in overrides_list:
        if ov.get('warning_only'):
            continue  # 경고만인 항목은 override 아님
        date = ov.get('date')
        if not date:
            continue
        if date not in by_date:
            by_date[date] = ov
        else:
            existing_priority = RULE_PRIORITY.get(by_date[date].get('rule', ''), 99)
            new_priority = RULE_PRIORITY.get(ov.get('rule', ''), 99)
            if new_priority < existing_priority:
                by_date[date] = ov  # 더 높은 우선순위로 교체
    return by_date


def cleanup_overrides(schedule):
    """과거 날짜의 override 정리 (7일 이전 삭제)"""
    overrides = schedule.get('overrides', {})
    cutoff = (NOW - timedelta(days=7)).strftime('%Y-%m-%d')
    cleaned = {k: v for k, v in overrides.items() if k >= cutoff}
    schedule['overrides'] = cleaned


# ============================================================
# 텔레그램 알림 포맷
# ============================================================

def format_override_notification(new_overrides, existing_overrides):
    """새로운 override에 대한 텔레그램 메시지 생성"""
    if not new_overrides:
        return ""

    lines = ["🔄 스케줄 자동 조정", ""]

    for date_str in sorted(new_overrides.keys()):
        ov = new_overrides[date_str]
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        dow = DOW_NAMES[dt.weekday()]
        base = get_base_schedule(dt)

        lines.append(f"📅 {dt.strftime('%m/%d')} ({dow}) 변경")
        lines.append(f"  기존: {base['workout']}")
        lines.append(f"  변경: {ov.get('workout', '?')}")
        if ov.get('detail'):
            lines.append(f"  상세: {ov['detail']}")
        lines.append(f"  사유: {ov.get('reason', '?')} [{ov.get('rule', '?')}]")
        if ov.get('warning'):
            lines.append(f"  ⚠️ {ov['warning']}")
        lines.append("")

    return "\n".join(lines).strip()


def format_condition_warning(reason):
    """컨디션 경고 메시지 (override 미생성 수준)"""
    return f"⚠️ {reason} — 오늘 운동 시 강도 조절 권장"


def format_phase_report(result):
    """Phase 전환 평가 보고"""
    lines = [f"📊 Phase {result['phase']} 전환 평가", ""]
    for name, met in result.get('benchmarks', {}).items():
        icon = "✅" if met else "❌"
        lines.append(f"  {icon} {name}")
    lines.append("")
    if result.get('all_met'):
        lines.append("🟢 모든 벤치마크 충족 — Phase 전환 준비 완료")
    else:
        lines.append("🟡 일부 미충족 — 사용자 판단 필요")
    return "\n".join(lines)


def format_stagnation_report(result):
    """VDOT 정체 보고"""
    lines = [f"📊 {result['message']}", ""]
    lines.append("다음 옵션 중 선택:")
    for i, opt in enumerate(result.get('options', []), 1):
        lines.append(f"  ({i}) {opt}")
    return "\n".join(lines)


# ============================================================
# 메인 함수들 (외부 호출용)
# ============================================================

def adjust_daily(workout_log, schedule_data, health_data):
    """일일 조정 → overrides dict 반환"""
    global NOW, TODAY
    NOW = datetime.now(KST)
    TODAY = NOW.strftime('%Y-%m-%d')

    overrides = []
    warnings = []
    phase, _ = get_phase(NOW)

    if phase == 0:
        return {}, []

    today_entry = workout_log.get(TODAY)
    tomorrow = NOW.date() + timedelta(days=1)

    # A1: 고강도 후 회복
    if today_entry and today_entry.get('done'):
        ov = rule_a1_post_hard(today_entry, tomorrow, workout_log)
        if ov:
            overrides.append(ov)

    # A2: 운동 누락 재배치
    ov = rule_a2_missed_workout(TODAY, workout_log)
    if ov:
        overrides.append(ov)

    # A3: 컨디션 체크 (운동 완료 여부도 확인)
    ov = rule_a3_condition_check(TODAY, health_data, workout_log)
    if ov:
        if ov.get('warning_only'):
            warnings.append(ov['reason'])
        else:
            overrides.append(ov)

    # A4: 성과 상향
    if today_entry and today_entry.get('done'):
        ov = rule_a4_outperformance(today_entry, schedule_data, workout_log)
        if ov:
            overrides.append(ov)

    return resolve_conflicts(overrides), warnings


def adjust_weekly(workout_log, schedule_data):
    """주간 조정 → overrides dict 반환"""
    global NOW, TODAY
    NOW = datetime.now(KST)
    TODAY = NOW.strftime('%Y-%m-%d')

    overrides = []
    phase, _ = get_phase(NOW)
    dow = NOW.date().weekday()
    week_monday = get_week_monday(NOW)

    if phase == 0:
        return {}

    # B1: 러닝 빈도 보충 (수요일 이후)
    if dow >= 2:
        ovs = rule_b1_run_frequency(workout_log, week_monday)
        overrides.extend(ovs)

    # B2: 주간 과부하
    ovs = rule_b2_overload(workout_log, week_monday, phase)
    overrides.extend(ovs)

    # B3: 볼륨 부족 (일요일만)
    if dow == 6:
        ovs = rule_b3_underload(workout_log, week_monday, phase)
        overrides.extend(ovs)

    return resolve_conflicts(overrides)


def detect_phase_transition(workout_log, schedule_data):
    """Phase 전환 + VDOT 정체 감지 → 보고 문자열"""
    phase, _ = get_phase(NOW)
    reports = []

    if phase == 0:
        return ""

    # C1: Phase 전환 (종료 1주 전)
    phase_end = get_phase_end_date(phase)
    days_to_end = (phase_end - NOW.date()).days
    if 5 <= days_to_end <= 7:
        result = rule_c1_phase_transition(workout_log, schedule_data, phase)
        reports.append(format_phase_report(result))

    # C2: VDOT 정체 (일요일만)
    if NOW.date().weekday() == 6:
        stagnation = rule_c2_vdot_stagnation(schedule_data)
        if stagnation:
            reports.append(format_stagnation_report(stagnation))

    return "\n\n".join(reports)


def run(workout_log, schedule_data, health_data):
    """
    통합 실행: 일일 + 주간 + Phase 조정
    반환: (overrides dict, report 문자열)
    """
    global NOW, TODAY
    NOW = datetime.now(KST)
    TODAY = NOW.strftime('%Y-%m-%d')

    phase, _ = get_phase(NOW)
    if phase == 0:
        return {}, ""

    all_overrides = {}
    report_parts = []
    warnings = []

    # === A: 일일 조정 ===
    daily_overrides, daily_warnings = adjust_daily(workout_log, schedule_data, health_data)
    all_overrides.update(daily_overrides)
    warnings.extend(daily_warnings)

    # === B: 주간 조정 ===
    weekly_overrides = adjust_weekly(workout_log, schedule_data)
    # 주간 override는 일일보다 낮은 우선순위로 병합
    for date, ov in weekly_overrides.items():
        if date not in all_overrides:
            all_overrides[date] = ov
        else:
            # 기존(일일)의 우선순위가 더 높으면 유지
            existing_pri = RULE_PRIORITY.get(all_overrides[date].get('rule', ''), 99)
            new_pri = RULE_PRIORITY.get(ov.get('rule', ''), 99)
            if new_pri < existing_pri:
                all_overrides[date] = ov

    # === C: Phase/부상 ===
    # C3: 부상 감지 (매일)
    injury_ovs = rule_c3_injury_detection(workout_log, health_data)
    if injury_ovs:
        injury_resolved = resolve_conflicts(injury_ovs)
        for date, ov in injury_resolved.items():
            # C3은 최고 우선순위
            all_overrides[date] = ov

    # Phase 전환/VDOT 정체 보고
    phase_report = detect_phase_transition(workout_log, schedule_data)
    if phase_report:
        report_parts.append(phase_report)

    # === 사용자 수동 override 보존 ===
    existing_overrides = schedule_data.get('overrides', {})
    user_overrides = {k: v for k, v in existing_overrides.items()
                      if not v.get('auto', True)}
    for date, ov in user_overrides.items():
        all_overrides[date] = ov  # 사용자 override 항상 우선

    # === Override 알림 메시지 ===
    new_overrides = {k: v for k, v in all_overrides.items()
                     if k not in existing_overrides or existing_overrides.get(k) != v}
    if new_overrides:
        notification = format_override_notification(new_overrides, existing_overrides)
        if notification:
            report_parts.insert(0, notification)

    # 경고 메시지
    for w in warnings:
        report_parts.append(format_condition_warning(w))

    report = "\n\n".join(report_parts)

    return all_overrides, report


def _detect_improvement_items(workout_log, schedule_data, health_data):
    """코드만으로 자동 판단 불가한 패턴을 감지하여 improvement_queue에 축적"""
    items = []
    phase, _ = get_phase(NOW)
    week_monday = get_week_monday(NOW)

    # 1. 80/20 위반 3주 연속 → 구조 변경 필요할 수 있음
    violation_weeks = 0
    for w_offset in range(3):
        w_start = week_monday - timedelta(weeks=w_offset)
        w_end = w_start + timedelta(days=6)
        hard_count = 0
        total_count = 0
        for d_offset in range(7):
            d = (w_start + timedelta(days=d_offset)).strftime('%Y-%m-%d')
            entry = workout_log.get(d)
            if entry and entry.get('done') and entry.get('training_zone'):
                total_count += 1
                if entry['training_zone'] not in ('easy', 'rest'):
                    hard_count += 1
        if total_count >= 3 and hard_count / total_count > 0.3:
            violation_weeks += 1
    if violation_weeks >= 3:
        items.append({
            "type": "structure",
            "priority": "high",
            "message": "80/20 강도 배분 3주 연속 위반 — 스케줄 구조 재검토 필요",
            "detected_at": TODAY,
        })

    # 2. VDOT 3주 정체 → 훈련 자극 변경 필요
    vdot_history = schedule_data.get('vdot_history', [])
    if len(vdot_history) >= 3:
        recent_3 = [v['vdot'] if isinstance(v, dict) else v for v in vdot_history[-3:]]
        if max(recent_3) - min(recent_3) <= 1:
            items.append({
                "type": "plateau",
                "priority": "medium",
                "message": f"VDOT {recent_3[-1]} — 3주간 정체. 인터벌/템포 비율 조정 검토",
                "detected_at": TODAY,
            })

    # 3. 수면 품질 지속 저하 → 훈련 볼륨 재검토
    sleep_scores = []
    for d_offset in range(7):
        d = (NOW.date() - timedelta(days=d_offset)).strftime('%Y-%m-%d')
        h = health_data.get(d, {})
        score = h.get('sleep', {}).get('score')
        if score:
            sleep_scores.append(score)
    if len(sleep_scores) >= 5 and sum(sleep_scores) / len(sleep_scores) < 60:
        items.append({
            "type": "recovery",
            "priority": "high",
            "message": f"주간 평균 수면 점수 {sum(sleep_scores)//len(sleep_scores)}점 — 볼륨 감량 검토 필요",
            "detected_at": TODAY,
        })

    # 4. 같은 규칙이 주 3회 이상 트리거 → 근본 원인 검토
    overrides = schedule_data.get('overrides', {})
    rule_counts = {}
    for d_offset in range(7):
        d = (NOW.date() - timedelta(days=d_offset)).strftime('%Y-%m-%d')
        ov = overrides.get(d)
        if ov and ov.get('auto'):
            rule = ov.get('rule', '')
            rule_counts[rule] = rule_counts.get(rule, 0) + 1
    for rule, count in rule_counts.items():
        if count >= 3:
            items.append({
                "type": "recurring_override",
                "priority": "medium",
                "message": f"규칙 {rule}이 주 {count}회 트리거 — 근본 원인 검토 필요",
                "detected_at": TODAY,
            })

    return items


def _update_improvement_queue(new_items):
    """improvement_queue.json에 새 항목 축적 (중복 방지, 30일 보관)"""
    queue = load_json(QUEUE_FILE)
    if not isinstance(queue, dict):
        queue = {"items": [], "resolved": []}
    if "items" not in queue:
        queue["items"] = []
    if "resolved" not in queue:
        queue["resolved"] = []

    existing_types = {item['type'] for item in queue['items']}

    added = 0
    for item in new_items:
        if item['type'] not in existing_types:
            queue['items'].append(item)
            added += 1
        else:
            # 같은 type이면 message만 최신으로 갱신
            for existing in queue['items']:
                if existing['type'] == item['type']:
                    existing['message'] = item['message']
                    existing['detected_at'] = item['detected_at']
                    break

    # 30일 이전 항목 정리
    cutoff = (NOW - timedelta(days=30)).strftime('%Y-%m-%d')
    queue['items'] = [i for i in queue['items'] if i.get('detected_at', '') >= cutoff]
    queue['resolved'] = [i for i in queue['resolved'] if i.get('resolved_at', '') >= cutoff]

    if added > 0:
        save_json(QUEUE_FILE, queue)
        print(f"[adaptive] improvement_queue: {added}건 추가 (총 {len(queue['items'])}건)")

    return added, queue


def adjust_schedule():
    """
    적응형 스케줄 조정 메인 — garmin_sync.py에서 호출
    workout_schedule.json의 overrides를 업데이트하고 텔레그램 알림
    """
    global NOW, TODAY
    NOW = datetime.now(KST)
    TODAY = NOW.strftime('%Y-%m-%d')

    workout_log = load_json(LOG_FILE)
    schedule_data = load_json(SCHEDULE_FILE)
    health_data = load_json(HEALTH_FILE)

    overrides, report = run(workout_log, schedule_data, health_data)

    if overrides:
        # 기존 auto override 중 아직 유효한 것 보존 (전체 교체 방지)
        existing = schedule_data.get('overrides', {})
        merged = dict(existing)
        merged.update(overrides)  # 새 override가 우선
        schedule_data['overrides'] = merged
        cleanup_overrides(schedule_data)
        save_json(SCHEDULE_FILE, schedule_data)
        print(f"[adaptive] overrides 업데이트: {len(schedule_data['overrides'])}건")

    # VDOT 히스토리 축적
    current_vdot = schedule_data.get('current_vdot')
    if current_vdot and NOW.date().weekday() == 6:  # 일요일마다 기록
        if 'vdot_history' not in schedule_data:
            schedule_data['vdot_history'] = []
        last_entry = schedule_data['vdot_history'][-1] if schedule_data['vdot_history'] else {}
        if last_entry.get('date') != TODAY:
            schedule_data['vdot_history'].append({'date': TODAY, 'vdot': current_vdot})
            # 최근 12주만 보관
            schedule_data['vdot_history'] = schedule_data['vdot_history'][-12:]
            save_json(SCHEDULE_FILE, schedule_data)

    # improvement_queue 축적
    improvement_items = _detect_improvement_items(workout_log, schedule_data, health_data)
    added, queue = _update_improvement_queue(improvement_items)

    # 텔레그램 보고
    report_parts = []
    if report:
        report_parts.append(report)

    # improvement_queue에 high priority 항목이 있으면 알림
    high_items = [i for i in queue.get('items', []) if i.get('priority') == 'high']
    if high_items:
        lines = ["🔍 검토 필요 사항:"]
        for item in high_items:
            lines.append(f"  ⚠️ {item['message']}")
        report_parts.append("\n".join(lines))

    if report_parts:
        send_telegram("\n\n".join(report_parts))
        print(f"[adaptive] 보고 전송 완료")

    return overrides, report


# ============================================================
# CLI 진입점 (아침 컨디션 체크 등)
# ============================================================
if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'full'

    if mode == '--weekly-review':
        # 일요일 주간 종합 리뷰
        workout_log = load_json(LOG_FILE)
        schedule_data = load_json(SCHEDULE_FILE)
        health_data = load_json(HEALTH_FILE)
        queue = load_json(QUEUE_FILE)

        # 주간 통계 (get_weekly_stats 재사용)
        week_monday = get_week_monday(NOW)
        stats = get_weekly_stats(workout_log, week_monday)
        swim_count = stats['swim']['count']
        run_count = stats['run']['count']
        bike_count = stats['bike']['count']
        total_run_km = stats['run']['total_km']

        phase, phase_name = get_phase(NOW)
        targets = PHASE_TARGETS.get(phase, {})
        vdot = schedule_data.get('current_vdot', '?')

        lines = [
            f"📊 주간 리뷰 ({week_monday.strftime('%m/%d')}~{(week_monday + timedelta(days=6)).strftime('%m/%d')})",
            f"Phase {phase} ({phase_name}) | VDOT {vdot} | D-{(RACE_DAY.date() - NOW.date()).days}",
            "",
            f"🏊 수영 {swim_count}/{targets.get('swim', '?')}회",
            f"🏃 러닝 {run_count}/{targets.get('run', '?')}회 ({total_run_km:.1f}/{targets.get('run_km', '?')}km)",
            f"🚴 자전거 {bike_count}/{targets.get('bike', '?')}회",
        ]

        # VDOT 추이
        vdot_history = schedule_data.get('vdot_history', [])
        if len(vdot_history) >= 2:
            prev = vdot_history[-2]['vdot']
            curr = vdot_history[-1]['vdot']
            diff = curr - prev
            arrow = "↑" if diff > 0 else "↓" if diff < 0 else "→"
            lines.append(f"\nVDOT 추이: {prev} {arrow} {curr}")

        # improvement_queue 요약
        items = queue.get('items', [])
        if items:
            lines.append(f"\n🔍 검토 필요 ({len(items)}건):")
            for item in items:
                icon = "🔴" if item.get('priority') == 'high' else "🟡"
                lines.append(f"  {icon} {item['message']}")
            lines.append("\n→ 다음 대화에서 검토하겠습니다")
        else:
            lines.append("\n✅ 검토 필요 사항 없음 — 알고리즘 정상 작동 중")

        send_telegram("\n".join(lines))
        print("[adaptive] 주간 리뷰 전송 완료")

    elif mode == '--morning':
        # 아침 컨디션 체크만 (A3 규칙)
        health_data = load_json(HEALTH_FILE)
        schedule_data = load_json(SCHEDULE_FILE)
        workout_log = load_json(LOG_FILE)

        result = rule_a3_condition_check(TODAY, health_data)
        if result and not result.get('warning_only'):
            # override 적용
            existing = schedule_data.get('overrides', {})
            # 사용자 수동 override가 있으면 건드리지 않음
            if TODAY not in existing or existing[TODAY].get('auto', True):
                existing[TODAY] = result
                schedule_data['overrides'] = existing
                cleanup_overrides(schedule_data)
                save_json(SCHEDULE_FILE, schedule_data)

            # 알림
            base = get_base_schedule(NOW)
            health = health_data.get(TODAY, {})
            bb = health.get('body_battery', {}).get('max', '?')
            hrv_val = health.get('hrv', {}).get('last_night', '?')
            hrv_avg = health.get('hrv', {}).get('weekly_avg', '?')
            sleep_min = health.get('sleep', {}).get('duration_min', 0)
            sleep_h = sleep_min // 60 if sleep_min else '?'
            sleep_m = sleep_min % 60 if sleep_min else ''
            tr = health.get('training_readiness', {}).get('score', '?')

            msg_lines = [
                "🌅 오늘의 컨디션",
                "",
                f"BB {bb} | HRV {hrv_val}ms (avg {hrv_avg}) | 수면 {sleep_h}h{sleep_m}m | TR {tr}",
                f"⚠️ {result['reason']}",
                "",
                f"📅 오늘 스케줄 변경",
                f"  기존: {base['workout']}",
                f"  변경: {result['workout']} [{result['rule']}]",
            ]
            send_telegram("\n".join(msg_lines))
        elif result and result.get('warning_only'):
            # 경고만
            send_telegram(format_condition_warning(result['reason']))
        else:
            print("[adaptive] 컨디션 정상 — 조정 없음")

    else:
        # 전체 실행
        overrides, report = adjust_schedule()
        if not overrides and not report:
            print("[adaptive] 조정 없음")
