"""
운동 기록 분석 + 텔레그램 전송
- WORKOUT_ALGORITHM.md 기반
- VDOT 기반 러닝 예측, 80/20 강도 체크, 누적 부하, 브릭 적응
- Banister Fitness-Fatigue 모델 참조
- Bosquet 테이퍼 효과 반영
"""

import os
import json
import math
import requests
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
DOW = NOW.weekday()

# L0 §"봇 토큰 fallback 체인" (운동 봇)
BOT_TOKEN = (os.environ.get('BOT_TOKEN')
             or os.environ.get('TRAINING_BOT_TOKEN')
             or os.environ.get('TELEGRAM_BOT_TOKEN', ''))
CHAT_ID = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LOG_FILE = os.path.join(BASE_DIR, 'workout_log.json')
SCHEDULE_FILE = os.path.join(BASE_DIR, 'workout_schedule.json')

RACE_DAY = datetime(2026, 5, 10, tzinfo=KST)
TRAIN_START = datetime(2026, 3, 16, tzinfo=KST)
DAYS_LEFT = (RACE_DAY.date() - NOW.date()).days

# ============================================================
# VDOT Lookup Table (Jack Daniels)
# pace_sec = seconds per km
# ============================================================
VDOT_TABLE = [
    # (vdot, 10k_sec_per_km, easy_low, easy_high, tempo, interval)
    (30, 414, 448, 496, 396, 362),  # 6:54, E 7:28-8:16, T 6:36, I 6:02
    (31, 403, 436, 483, 385, 352),
    (32, 393, 425, 471, 375, 342),
    (33, 383, 414, 459, 365, 333),
    (34, 374, 404, 448, 356, 324),
    (35, 365, 394, 437, 347, 316),  # 6:05, E 6:34-7:17, T 5:47, I 5:16
    (36, 356, 385, 427, 339, 308),  # 5:56, E 6:25-7:07, T 5:39, I 5:08
    (37, 348, 376, 417, 331, 300),  # 5:48, E 6:16-6:57, T 5:31, I 5:00
    (38, 340, 367, 407, 323, 293),  # 5:40, E 6:07-6:47, T 5:23, I 4:53
    (39, 333, 359, 398, 316, 286),
    (40, 326, 351, 389, 309, 280),  # 5:26, E 5:51-6:29, T 5:09, I 4:40
    (41, 319, 344, 381, 302, 274),
    (42, 312, 337, 373, 296, 268),
    (43, 306, 330, 365, 290, 262),
    (44, 300, 323, 358, 284, 257),
    (45, 294, 317, 351, 278, 252),  # 4:54, E 5:17-5:51, T 4:38, I 4:12
]


def pace_to_seconds(pace_str):
    """'5:33' → 333"""
    if not pace_str:
        return None
    parts = pace_str.split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return None


def seconds_to_pace(secs):
    """333 → '5:33'"""
    if secs is None:
        return '?'
    m = int(secs) // 60
    s = int(secs) % 60
    return f"{m}:{s:02d}"


def minutes_to_hhmm(mins):
    """170.5 → '2:50'"""
    h = int(mins) // 60
    m = int(mins) % 60
    return f"{h}:{m:02d}"


def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# VDOT 추정
# ============================================================
def estimate_vdot(pace_sec_per_km, distance_km):
    """러닝 페이스와 거리로 VDOT 추정 (보수적)"""
    # VDOT는 올아웃 레이스 기준. 훈련 페이스는 과대평가 → 보수적 보정
    # 짧은 거리일수록 보정 크게
    if distance_km < 5:
        pace_sec_per_km += 20
    elif distance_km < 8:
        pace_sec_per_km += 12
    elif distance_km < 10:
        pace_sec_per_km += 7
    # 10km+ = 보정 없음 (레이스에 가까운 거리)

    # 복귀 초기 (데이터 부족)는 추가 보수적 보정
    # → update_vdot에서 가중평균으로 처리

    best_vdot = 30
    for vdot, race_pace, *_ in VDOT_TABLE:
        if pace_sec_per_km <= race_pace:
            best_vdot = vdot
    return best_vdot


def get_vdot_paces(vdot):
    """VDOT에 해당하는 각 존별 페이스 반환"""
    for v, race, easy_lo, easy_hi, tempo, interval in VDOT_TABLE:
        if v == vdot:
            return {
                "race_10k": race,
                "easy_low": easy_lo,
                "easy_high": easy_hi,
                "tempo": tempo,
                "interval": interval,
            }
    # 범위 밖이면 가장 가까운 값
    return {
        "race_10k": 365, "easy_low": 394, "easy_high": 437,
        "tempo": 347, "interval": 316,
    }


def predict_10k_time(vdot):
    """VDOT로 10km 레이스 타임 예측 (분)"""
    paces = get_vdot_paces(vdot)
    return (paces['race_10k'] * 10) / 60


# ============================================================
# 훈련 존 판정
# ============================================================
def classify_training_zone(pace_sec, vdot):
    """실제 페이스가 어떤 훈련 존인지 판정"""
    paces = get_vdot_paces(vdot)
    if pace_sec >= paces['easy_low']:
        return 'easy'
    elif pace_sec >= paces['tempo'] + 10:
        return 'moderate'  # Dead zone — 가장 비효율적
    elif pace_sec >= paces['tempo'] - 5:
        return 'tempo'
    elif pace_sec >= paces['interval'] - 5:
        return 'interval'
    else:
        return 'repetition'


# ============================================================
# 누적 부하
# ============================================================
INTENSITY_MULTIPLIER = {'easy': 1.0, 'moderate': 1.2, 'tempo': 1.5, 'interval': 1.8, 'repetition': 2.0}
TYPE_MULTIPLIER = {'run': 1.3, 'swim': 1.0, 'bike': 0.8, 'brick': 1.4}

# 수영 장비별 부하 보정계수
SWIM_EQUIPMENT_MULTIPLIER = {
    'none': 1.0,       # 맨몸 = 기준
    'fins': 0.7,       # 오리발: 하체 부담 감소
    'paddles': 1.2,    # 패들: 상체 부하 증가
    'pull_buoy': 0.8,  # 풀부이: 하체 비활성
    'fins_paddles': 0.9,  # 오리발+패들
    'kickboard': 0.9,  # 킥보드: 하체 드릴
}

# 수영 장비별 페이스 보정 (초/100m, 맨몸 환산 시 더하기)
SWIM_EQUIPMENT_PACE_CORRECTION = {
    'none': 0,
    'fins': 12,        # 오리발 착용 시 맨몸보다 ~12초 빠름
    'paddles': 6,      # 패들 착용 시 ~6초 빠름
    'pull_buoy': 4,    # 풀부이 ~4초 빠름
    'fins_paddles': 15, # 오리발+패들 ~15초 빠름
    'kickboard': -20,  # 킥보드는 오히려 느림
}


def get_swim_equipment(entry):
    """수영 장비 타입 반환"""
    metrics = entry.get('metrics', {})
    return metrics.get('swim_equipment', 'none')


def get_bare_swim_pace(entry):
    """장비 보정 후 맨몸 환산 페이스 반환"""
    metrics = entry.get('metrics', {})
    pace = pace_to_seconds(metrics.get('pace_per_100m'))
    if pace is None:
        return None
    equipment = get_swim_equipment(entry)
    correction = SWIM_EQUIPMENT_PACE_CORRECTION.get(equipment, 0)
    return pace + correction


def calc_training_load(entry):
    """일일 훈련 부하 계산
    - 가민 training_load(EPOC 기반)가 있으면 그대로 사용
    - 없을 때만 시간×계수 폴백 계산
    """
    metrics = entry.get('metrics', {})

    # 가민 EPOC 기반 부하가 있으면 우선 사용
    garmin_load = metrics.get('training_load')
    if garmin_load:
        return round(garmin_load)

    # 폴백: 시간 × 강도 × 종목 × 장비 계수
    wtype = metrics.get('type', 'run')
    duration = metrics.get('duration_min', 0)
    if wtype == 'swim':
        duration = metrics.get('moving_min', duration)
    if wtype == 'run':
        pace = pace_to_seconds(metrics.get('pace_per_km'))
        dist = metrics.get('distance_km', 0)
        if pace and dist:
            duration = (pace * dist) / 60
    zone = entry.get('training_zone', 'moderate')
    intensity = INTENSITY_MULTIPLIER.get(zone, 1.2)
    type_mult = TYPE_MULTIPLIER.get(wtype, 1.0)
    equip_mult = 1.0
    if wtype == 'swim':
        equipment = get_swim_equipment(entry)
        equip_mult = SWIM_EQUIPMENT_MULTIPLIER.get(equipment, 1.0)
    return round(duration * intensity * type_mult * equip_mult)


def get_week_monday(dt):
    wk = (dt.date() - TRAIN_START.date()).days // 7
    return TRAIN_START + timedelta(days=wk * 7)


def get_phase(dt):
    d = dt.date() if hasattr(dt, 'date') else dt
    if d <= datetime(2026, 4, 5, tzinfo=KST).date():
        return 1, "Phase 1: 베이스"
    elif d <= datetime(2026, 4, 26, tzinfo=KST).date():
        return 2, "Phase 2: 빌드"
    elif d <= datetime(2026, 5, 10, tzinfo=KST).date():
        return 3, "Phase 3: 테이퍼"
    return 0, "대회 완료"


# Phase별 주간 목표
PHASE_TARGETS = {
    1: {"swim": 4, "run": 3, "run_km": 20, "bike": 1, "weekly_load": 300},
    2: {"swim": 3, "run": 3, "run_km": 21, "bike": 2, "weekly_load": 380},
    3: {"swim": 2, "run": 2, "run_km": 10, "bike": 1, "weekly_load": 200},
}


# ============================================================
# 분석
# ============================================================
def analyze_week(log, dt=None):
    if dt is None:
        dt = NOW
    mon = get_week_monday(dt)

    stats = {
        'swim': {'count': 0, 'paces': []},
        'run': {'count': 0, 'total_km': 0.0, 'paces': [], 'zones': []},
        'bike': {'count': 0},
        'brick': {'count': 0},
        'total_load': 0,
        'easy_minutes': 0,
        'hard_minutes': 0,
    }

    for d in range(7):
        day = mon + timedelta(days=d)
        key = day.strftime('%Y-%m-%d')
        entry = log.get(key)
        if not entry or not entry.get('done'):
            continue

        # all_metrics가 있으면 각 운동별로 개별 집계, 없으면 기존 metrics 하나만 사용
        all_metrics = entry.get('all_metrics', None)
        if all_metrics and isinstance(all_metrics, list) and len(all_metrics) > 0:
            metrics_list = all_metrics
        else:
            metrics_list = [entry.get('metrics', {})]

        # 같은 날짜의 같은 종목 연속 활동은 1세션으로 카운트 (예: 수영 중 시계 끊김)
        day_types_counted = set()

        for m_item in metrics_list:
            # all_metrics 항목으로 임시 entry 구성하여 부하 계산
            tmp_entry = dict(entry)
            tmp_entry['metrics'] = m_item
            wtype = m_item.get('type', '')
            zone = m_item.get('training_zone', entry.get('training_zone', 'moderate'))
            load = calc_training_load(tmp_entry)
            stats['total_load'] += load

            duration = m_item.get('duration_min', 0)
            # 수영: moving_min 우선
            if wtype == 'swim':
                duration = m_item.get('moving_min', duration)
            if wtype == 'run':
                dist = m_item.get('distance_km', 0)
                pace = pace_to_seconds(m_item.get('pace_per_km'))
                if pace and dist:
                    duration = (pace * dist) / 60

            if zone in ('easy',):
                stats['easy_minutes'] += duration
            elif zone in ('tempo', 'interval', 'repetition'):
                stats['hard_minutes'] += duration
            else:
                # moderate(Dead Zone)는 고강도로 100% 분류
                stats['hard_minutes'] += duration

            if wtype == 'swim':
                if wtype not in day_types_counted:
                    stats['swim']['count'] += 1
                    day_types_counted.add(wtype)
                pace = m_item.get('pace_per_100m')
                if pace:
                    stats['swim']['paces'].append(pace_to_seconds(pace))
            elif wtype == 'run':
                if wtype not in day_types_counted:
                    stats['run']['count'] += 1
                    day_types_counted.add(wtype)
                stats['run']['total_km'] += m_item.get('distance_km', 0)
                pace = m_item.get('pace_per_km')
                if pace:
                    stats['run']['paces'].append(pace_to_seconds(pace))
                stats['run']['zones'].append(zone)
            elif wtype == 'bike':
                if wtype not in day_types_counted:
                    stats['bike']['count'] += 1
                    day_types_counted.add(wtype)
            elif wtype == 'brick':
                stats['brick']['count'] += 1

    total_min = stats['easy_minutes'] + stats['hard_minutes']
    stats['easy_pct'] = round(stats['easy_minutes'] / total_min * 100) if total_min > 0 else 0
    stats['hard_pct'] = 100 - stats['easy_pct'] if total_min > 0 else 0
    stats['run']['total_km'] = round(stats['run']['total_km'], 1)

    return stats


def get_latest_metrics(log, workout_type, n=3):
    entries = []
    for date_key in sorted(log.keys(), reverse=True):
        entry = log[date_key]
        if not entry.get('done'):
            continue
        metrics = entry.get('metrics', {})
        if metrics.get('type') == workout_type:
            entries.append((date_key, entry))
            if len(entries) >= n:
                break
    return entries


def count_bricks(log):
    """누적 브릭 훈련 횟수 (이중 카운트 방지)"""
    count = 0
    for entry in log.values():
        if not entry.get('done'):
            continue
        metrics = entry.get('metrics', {})
        if metrics.get('type') == 'brick':
            count += 1
            continue  # type이 brick이면 note/planned 중복 검사 skip
        # 미니브릭 (러닝 후 자전거 또는 그 반대)도 카운트
        note = entry.get('note', '').lower()
        planned = entry.get('planned', '').lower() if isinstance(entry.get('planned'), str) else ''
        if '브릭' in note or 'brick' in note or '브릭' in planned or 'brick' in planned:
            count += 1
    return count


def count_ow(log):
    """오픈워터 경험 횟수"""
    count = 0
    for entry in log.values():
        if not entry.get('done'):
            continue
        note = (entry.get('note', '') + entry.get('actual', '')).lower()
        if '오픈워터' in note or 'open water' in note or 'ow수영' in note:
            count += 1
    return count


def estimate_finish_time(log):
    """개선된 예상 완주시간 (VDOT + 브릭감속 + 테이퍼 반영)"""
    schedule = load_json(SCHEDULE_FILE)
    current_vdot = schedule.get('current_vdot', 35)
    brick_count = count_bricks(log)
    ow_count = count_ow(log)

    # 수영 (장비 보정 → 맨몸 환산 페이스 사용)
    swim_entries = get_latest_metrics(log, 'swim', 5)
    if swim_entries:
        # 맨몸 환산 페이스로 변환
        bare_paces = [get_bare_swim_pace(e[1]) for e in swim_entries]
        bare_paces = [p for p in bare_paces if p is not None]
        avg_swim_pace = sum(bare_paces) / len(bare_paces) if bare_paces else 120
    else:
        avg_swim_pace = 117  # 1:57 기본값

    # OW 보정 (경험에 따라 차등)
    if ow_count == 0:
        ow_correction = 17
    elif ow_count <= 2:
        ow_correction = 12
    else:
        ow_correction = 8

    est_swim = ((avg_swim_pace + ow_correction) * 15) / 60

    # 자전거: 코스 영향 보정 — lap 데이터 있으면 평지 구간(상위 30%) 평속 - 5% 감속
    # lap에는 신호/턴/경사가 섞여있어 항속이 깎여 보이므로 상위 lap 사용
    bike_entries = get_latest_metrics(log, 'bike', 3)
    if bike_entries:
        speeds = []
        for _, e in bike_entries:
            m = e.get('metrics', {})
            laps = m.get('laps', [])
            lap_speeds = [l.get('speed_kmh', 0) for l in laps if l.get('speed_kmh', 0) > 0]
            if lap_speeds and len(lap_speeds) >= 4:
                # lap 평속 편차가 크면 (max-min > 10km/h) 코스 영향 → 가장 빠른 lap 1~2개를 항속으로 추정
                if max(lap_speeds) - min(lap_speeds) > 10:
                    sorted_speeds = sorted(lap_speeds, reverse=True)
                    top_n = min(2, len(sorted_speeds))  # 상위 1~2개
                    flat_speed = sum(sorted_speeds[:top_n]) / top_n
                    # 항속 - 후반 피로/턴어라운드 감속 7% → 대회 평균 평속
                    speeds.append(flat_speed * 0.93)
                else:
                    # 코스 균일 → 평균 평속 사용
                    speeds.append(m.get('avg_speed_kmh', 32))
            else:
                speeds.append(m.get('avg_speed_kmh', 32))
        # 너무 낮은 평속(20 미만) 제외 (브릭 등 특수 상황)
        speeds = [s for s in speeds if s >= 20]
        avg_speed = sum(speeds) / len(speeds) if speeds else 32
        est_bike = (40 / avg_speed) * 60
    else:
        est_bike = 75  # 사이클 선수 기본값

    # 러닝 (VDOT 기반)
    standalone_10k_min = predict_10k_time(current_vdot)

    # 브릭 감속률 (훈련 횟수에 따라)
    if brick_count <= 2:
        brick_slowdown = 0.08
    elif brick_count <= 5:
        brick_slowdown = 0.06
    else:
        brick_slowdown = 0.05

    # 테이퍼 효과 (대회 2주 전 테이퍼 가정)
    taper_effect = 0.03

    est_run_brick = standalone_10k_min * (1 + brick_slowdown) * (1 - taper_effect)

    # 트랜지션: 첫 대회 기준 T1 4.5분 + T2 2.5분 = 7분
    t1 = 4.5
    t2 = 2.5
    total = est_swim + t1 + est_bike + t2 + est_run_brick

    return {
        "swim": round(est_swim, 1),
        "t1": t1,
        "bike": round(est_bike, 1),
        "t2": t2,
        "run_standalone": round(standalone_10k_min, 1),
        "run_brick": round(est_run_brick, 1),
        "brick_slowdown_pct": round(brick_slowdown * 100),
        "taper_effect_pct": round(taper_effect * 100),
        "total": round(total, 1),
        "vdot": current_vdot,
        "brick_count": brick_count,
        "ow_count": ow_count,
        "ow_correction": ow_correction,
    }


def update_vdot(log):
    """최근 러닝 데이터로 VDOT 재추정 (훈련 공백 감쇠 + 거리 보정 반영)"""
    run_entries = get_latest_metrics(log, 'run', 5)
    if not run_entries:
        return 35  # 기본값

    vdots = []
    for date_key, entry in run_entries:
        metrics = entry.get('metrics', {})
        pace = pace_to_seconds(metrics.get('pace_per_km'))
        dist = metrics.get('distance_km', 0)
        if not pace or dist < 3:
            continue

        # 평균 페이스 기반 VDOT (빌드업 후반 페이스는 과대평가하므로 사용하지 않음)
        best_pace = pace

        v = estimate_vdot(best_pace, dist)

        # 짧은 거리 보정: 5~6km 러닝의 VDOT은 10km 대비 1~2 높게 나옴
        if dist < 7:
            v = max(35, v - 1)

        # 2주 이상 된 기록은 현재 체력을 반영하지 않으므로 제외
        try:
            from datetime import datetime as _dt
            days_ago = (NOW.date() - _dt.strptime(date_key, '%Y-%m-%d').date()).days
            if days_ago > 14:
                continue  # 오래된 기록 제외
            elif days_ago > 7:
                v = max(35, v - 1)  # 1~2주 기록: -1
        except Exception:
            pass

        vdots.append(v)

    if not vdots:
        return 35
    # 최근 기록 기반 평균
    return round(sum(vdots) / len(vdots))


def check_adjustments(log, week_stats, phase, vdot):
    """스케줄 조정 판단"""
    adjustments = []
    targets = PHASE_TARGETS.get(phase, {})
    paces = get_vdot_paces(vdot)

    # 러닝 빈도 (수요일 이후 체크)
    if DOW >= 2 and week_stats['run']['count'] < targets.get('run', 3):
        remaining = 6 - DOW
        deficit = targets.get('run', 3) - week_stats['run']['count']
        if deficit > 0 and remaining > 0:
            adjustments.append({
                "type": "run_frequency",
                "severity": "high" if deficit >= 2 else "medium",
                "message": f"🔴 러닝 {week_stats['run']['count']}/{targets.get('run', 3)}회 "
                           f"— {remaining}일 내 {deficit}회 추가 필요",
            })

    # 80/20 체크
    if week_stats['hard_pct'] > 22 and (week_stats['easy_minutes'] + week_stats['hard_minutes']) > 60:
        adjustments.append({
            "type": "intensity_too_high",
            "severity": "medium",
            "message": f"⚠️ 80/20 위반: Easy {week_stats['easy_pct']}% / 고강도 {week_stats['hard_pct']}% "
                       f"(목표: 80/20)",
        })

    # Easy 러닝이 너무 빠른지 체크
    easy_threshold = paces['easy_low']
    for zone, pace in zip(week_stats['run']['zones'], week_stats['run']['paces']):
        if zone == 'easy' and pace and pace < easy_threshold:
            adjustments.append({
                "type": "easy_too_fast",
                "severity": "low",
                "message": f"💡 Easy 런 {seconds_to_pace(pace)}/km → "
                           f"더 느리게 ({seconds_to_pace(paces['easy_low'])}~{seconds_to_pace(paces['easy_high'])})",
            })
            break

    # 주간 부하 체크
    target_load = targets.get('weekly_load', 300)
    if week_stats['total_load'] > target_load * 1.2:
        adjustments.append({
            "type": "overload",
            "severity": "high",
            "message": f"🔴 주간 부하 {week_stats['total_load']} > 목표 {target_load}의 120% — 다음 일 Easy 권장",
        })

    # 연속 러닝 규칙 (마스터: 복귀 2주 이내 연속 2일 금지, 이후 연속 2일 허용, 연속 3일 항상 금지)
    consecutive_run_adj = check_consecutive_running(log)
    adjustments.extend(consecutive_run_adj)

    # 생체 데이터 기반 컨디션 점검
    health_adj = check_health_adjustments()
    adjustments.extend(health_adj)

    return adjustments


def check_consecutive_running(log):
    """연속 러닝 규칙 점검 (WORKOUT_MASTER.md 105-108행)"""
    adjustments = []
    today = NOW.date()

    # 최근 3일 러닝 여부
    recent_runs = []
    for d in range(3):
        dt = today - timedelta(days=d)
        key = dt.strftime('%Y-%m-%d')
        entry = log.get(key, {})
        if entry.get('done'):
            wtype = entry.get('metrics', {}).get('type', '')
            # 미니브릭(2~3km)은 연속 러닝에 포함하지 않음
            dist = entry.get('metrics', {}).get('distance_km', 0)
            is_run = wtype == 'run' or (wtype == 'brick' and dist >= 5)
            recent_runs.append(is_run)
        else:
            recent_runs.append(False)

    # recent_runs[0]=오늘, [1]=어제, [2]=그저께

    # 연속 3일 러닝 금지
    if all(recent_runs):
        adjustments.append({
            "type": "consecutive_run_3days",
            "severity": "high",
            "message": "🔴 연속 3일 러닝 — 내일은 반드시 수영/자전거/휴식으로 대체",
        })
    # 연속 2일 체크
    elif recent_runs[0] and recent_runs[1]:
        # 복귀 2주 이내 (3/16~3/30)
        recovery_end = TRAIN_START.date() + timedelta(days=14)
        if today <= recovery_end:
            adjustments.append({
                "type": "consecutive_run_recovery",
                "severity": "high",
                "message": "🔴 복귀 2주 이내 연속 러닝 — 내일은 수영/자전거로 대체 필수",
            })
        else:
            # 3주차 이후: 허용하되 둘째 날은 Easy 필수
            today_entry = log.get(today.strftime('%Y-%m-%d'), {})
            pace = today_entry.get('metrics', {}).get('pace_per_km', '')
            if pace:
                parts = pace.split(':')
                pace_sec = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 999
                if pace_sec < 360:  # 6:00 미만이면 Easy 아님
                    adjustments.append({
                        "type": "consecutive_run_not_easy",
                        "severity": "medium",
                        "message": f"⚠️ 연속 러닝 둘째 날 — {pace}/km은 Easy 아님, 6:00+/km 필요",
                    })

    return adjustments


def check_health_adjustments():
    """garmin_health.json 기반 컨디션 점검 → 스케줄 조정 제안"""
    health_file = os.path.join(BASE_DIR, 'data', 'garmin_health.json')
    health_data = load_json(health_file)
    if not health_data:
        return []

    adjustments = []
    today_health = health_data.get(TODAY, {})
    if not today_health:
        # 가장 최근 데이터
        dates = sorted(health_data.keys(), reverse=True)
        if dates:
            today_health = health_data[dates[0]]

    if not today_health:
        return []

    # 1. Body Battery 점검: 아침 최대값이 낮으면 회복 부족
    bb = today_health.get('body_battery', {})
    bb_max = bb.get('max')
    if bb_max is not None and bb_max < 40:
        adjustments.append({
            "type": "low_body_battery",
            "severity": "high",
            "message": f"🔴 Body Battery 최대 {bb_max} — 회복 부족, 오늘 Easy 또는 휴식 권장",
        })
    elif bb_max is not None and bb_max < 60:
        adjustments.append({
            "type": "moderate_body_battery",
            "severity": "medium",
            "message": f"🟡 Body Battery 최대 {bb_max} — 고강도 운동 자제, Easy 권장",
        })

    # 2. 수면 점검
    sleep = today_health.get('sleep', {})
    sleep_score = sleep.get('score')
    sleep_min = sleep.get('duration_min', 0)
    if sleep_score is not None and sleep_score < 50:
        adjustments.append({
            "type": "poor_sleep",
            "severity": "high",
            "message": f"🔴 수면 점수 {sleep_score} — 수면 부족, 강도 낮추기 권장",
        })
    elif sleep_min > 0 and sleep_min < 360:  # 6시간 미만
        adjustments.append({
            "type": "short_sleep",
            "severity": "medium",
            "message": f"🟡 수면 {sleep_min // 60}h {sleep_min % 60}m — 6시간 미만, 컨디션 주의",
        })

    # 3. HRV 점검
    hrv = today_health.get('hrv', {})
    hrv_status = hrv.get('status', '')
    hrv_last = hrv.get('last_night')
    hrv_avg = hrv.get('weekly_avg')
    if hrv_status == 'LOW' or hrv_status == 'POOR':
        adjustments.append({
            "type": "low_hrv",
            "severity": "high",
            "message": f"🔴 HRV 상태 {hrv_status} (지난밤 {hrv_last}ms / 주간 {hrv_avg}ms) — 과훈련 위험, 볼륨 축소",
        })
    elif hrv_last and hrv_avg and hrv_last < hrv_avg * 0.75:
        adjustments.append({
            "type": "hrv_drop",
            "severity": "medium",
            "message": f"🟡 HRV 급감 {hrv_last}ms (주간평균 {hrv_avg}ms) — 피로 누적 주의",
        })

    # 4. Training Readiness 점검
    tr = today_health.get('training_readiness', {})
    tr_score = tr.get('score')
    tr_level = tr.get('level', '')
    if tr_score is not None and tr_score < 30:
        adjustments.append({
            "type": "low_readiness",
            "severity": "high",
            "message": f"🔴 Training Readiness {tr_score} ({tr_level}) — 몸이 준비 안 됨, 휴식 권장",
        })
    elif tr_score is not None and tr_score < 50:
        adjustments.append({
            "type": "moderate_readiness",
            "severity": "medium",
            "message": f"🟡 Training Readiness {tr_score} ({tr_level}) — Easy 강도까지만 권장",
        })

    # 5. 안정시 심박 점검 (평소 대비 높으면 피로/질병 신호)
    rhr = today_health.get('resting_hr')
    if rhr and rhr > 55:  # 사용자 평소 안정시 45bpm 기준
        adjustments.append({
            "type": "elevated_rhr",
            "severity": "medium",
            "message": f"🟡 안정시 심박 {rhr}bpm (평소 ~45) — 피로/스트레스 주의",
        })

    # 6. 스트레스 점검
    stress = today_health.get('stress', {})
    stress_avg = stress.get('avg')
    if stress_avg and stress_avg > 50:
        adjustments.append({
            "type": "high_stress",
            "severity": "medium",
            "message": f"🟡 스트레스 평균 {stress_avg} — 정신적 피로 주의, 운동으로 해소 or 휴식",
        })

    return adjustments


def format_single_activity(metrics):
    """단일 활동 포매팅 (시간순 표시용)"""
    wtype = metrics.get('type', '?')
    type_emoji = {'swim': '🏊', 'run': '🏃', 'bike': '🚴'}
    type_name = {'swim': '수영', 'run': '러닝', 'bike': '자전거'}

    start = metrics.get('start_time', '')
    prefix = f"  [{start}]" if start else " "
    header = f"{prefix} {type_emoji.get(wtype, '🏋️')} {type_name.get(wtype, wtype)}"
    lines = [header]

    if wtype == 'swim':
        dist = metrics.get('distance_m', 0)
        pace = metrics.get('pace_per_100m', '')
        dur = metrics.get('duration_min', 0)
        mov = metrics.get('moving_min')
        hr = metrics.get('avg_hr', '')
        maxhr = metrics.get('max_hr', '')
        swolf = metrics.get('swolf', '')
        time_str = f"{dur:.0f}분"
        if mov:
            time_str += f" (이동 {mov:.0f}분)"
        lines.append(f"    {dist}m | {time_str} | {pace}/100m")
        if hr:
            lines.append(f"    HR {hr}/{maxhr} | Swolf {swolf}")
    elif wtype == 'run':
        dist = metrics.get('distance_km', 0)
        pace = metrics.get('pace_per_km', '')
        hr = metrics.get('avg_hr', '')
        maxhr = metrics.get('max_hr', '')
        lines.append(f"    {dist}km | {pace}/km")
        if hr:
            lines.append(f"    HR {hr}/{maxhr}")
        # 랩 스플릿 (네거티브/빌드업 표시)
        laps = metrics.get('laps', [])
        if laps and len(laps) >= 3:
            lap_strs = [seconds_to_pace(l.get('pace_sec', 0)) for l in laps if l.get('distance', 0) >= 500]
            if lap_strs:
                lines.append(f"    스플릿: {' → '.join(lap_strs)}")
    elif wtype == 'bike':
        dur = metrics.get('duration_min', 0)
        dist = metrics.get('distance_km', 0)
        speed = metrics.get('avg_speed_kmh', 0)
        hr = metrics.get('avg_hr', '')
        maxhr = metrics.get('max_hr', '')
        if dist:
            lines.append(f"    {dist}km | {dur:.0f}분 | {speed}km/h")
        else:
            lines.append(f"    {dur:.0f}분")
        if hr:
            lines.append(f"    HR {hr}/{maxhr}")

    return "\n".join(lines)


def format_today_workout(entry):
    if not entry or not entry.get('done'):
        return None

    all_metrics = entry.get('all_metrics')
    lines = []

    # 복수 활동 (브릭 등): all_metrics를 시간순으로 표시
    if all_metrics and isinstance(all_metrics, list) and len(all_metrics) > 1:
        # start_time 기준 시간순 정렬
        sorted_metrics = sorted(all_metrics, key=lambda m: m.get('start_time', ''))
        is_brick = entry.get('is_brick', False)
        if is_brick:
            lines.append("🔥 브릭 트레이닝")
        else:
            lines.append("📊 복합 운동")
        for m in sorted_metrics:
            lines.append(format_single_activity(m))
    else:
        # 단일 활동
        metrics = entry.get('metrics', {})
        lines.append(format_single_activity(metrics))

    note = entry.get('note', '')
    if note:
        lines.append(f"  📝 {note}")
    return "\n".join(lines)


def format_analysis_message(log):
    phase, phase_name = get_phase(NOW)
    if phase == 0:
        return None

    # VDOT 업데이트
    current_vdot = update_vdot(log)
    schedule = load_json(SCHEDULE_FILE)
    schedule['current_vdot'] = current_vdot
    schedule['brick_count'] = count_bricks(log)
    schedule['ow_count'] = count_ow(log)

    week_stats = analyze_week(log)
    estimate = estimate_finish_time(log)
    adjustments = check_adjustments(log, week_stats, phase, current_vdot)
    today_entry = log.get(TODAY)
    targets = PHASE_TARGETS.get(phase, {})
    paces = get_vdot_paces(current_vdot)

    lines = []
    lines.append("🏋️ 운동 기록 업데이트")
    lines.append("")

    # 오늘의 운동
    if today_entry and today_entry.get('done'):
        today_msg = format_today_workout(today_entry)
        if today_msg:
            lines.append("📊 오늘의 운동")
            lines.append(today_msg)
            zone = today_entry.get('training_zone', '?')
            zone_kr = {'easy': 'Easy', 'moderate': '⚠️ Moderate (Dead Zone)',
                       'tempo': 'Tempo', 'interval': 'Interval'}.get(zone, zone)
            lines.append(f"  훈련 존: {zone_kr}")
            lines.append("")

    # Phase 진척
    lines.append(f"📈 {phase_name} (VDOT {current_vdot})")

    # 러닝 존 가이드
    lines.append(f"  Easy: {seconds_to_pace(paces['easy_low'])}~{seconds_to_pace(paces['easy_high'])}/km")
    lines.append(f"  Tempo: {seconds_to_pace(paces['tempo'])}/km")
    lines.append(f"  10km 예측: {predict_10k_time(current_vdot):.0f}분 (단독)")

    # 브릭 적응
    bc = estimate['brick_count']
    bp = estimate['brick_slowdown_pct']
    lines.append(f"  브릭 적응: {bc}회 (감속률 {bp}%)")
    lines.append("")

    # 주간 현황
    lines.append("📅 이번 주")
    run_target = targets.get('run', 3)
    swim_target = targets.get('swim', 4)
    bike_target = targets.get('bike', 1)
    run_km_target = targets.get('run_km', 20)

    swim_bar = "●" * week_stats['swim']['count'] + "○" * max(0, swim_target - week_stats['swim']['count'])
    run_bar = "●" * week_stats['run']['count'] + "○" * max(0, run_target - week_stats['run']['count'])
    bike_bar = "●" * week_stats['bike']['count'] + "○" * max(0, bike_target - week_stats['bike']['count'])

    lines.append(f"  수영 {swim_bar} {week_stats['swim']['count']}/{swim_target}")
    lines.append(f"  러닝 {run_bar} {week_stats['run']['count']}/{run_target} "
                 f"({week_stats['run']['total_km']}km/{run_km_target}km)")
    lines.append(f"  자전거 {bike_bar} {week_stats['bike']['count']}/{bike_target}")

    # 80/20
    total_min = week_stats['easy_minutes'] + week_stats['hard_minutes']
    if total_min > 0:
        e_icon = "✅" if week_stats['easy_pct'] >= 75 else "⚠️"
        lines.append(f"  80/20: Easy {week_stats['easy_pct']}% / 고강도 {week_stats['hard_pct']}% {e_icon}")

    # 부하
    tgt_load = targets.get('weekly_load', 300)
    lines.append(f"  부하: {week_stats['total_load']}/{tgt_load}")
    lines.append("")

    # 스케줄 조정
    if adjustments:
        lines.append("🔄 스케줄 조정")
        for adj in adjustments:
            lines.append(f"  {adj['message']}")
        lines.append("")
    else:
        lines.append("🔄 변경 없음 — 계획대로 진행")
        lines.append("")

    # 예상 완주시간
    total = estimate['total']
    if total <= 170:
        status_emoji = "🟢"
        status_text = "목표 달성 가능"
    elif total <= 180:
        status_emoji = "🟡"
        status_text = "주의 — 개선 필요"
    else:
        status_emoji = "🔴"
        status_text = "경고 — 스케줄 강화 필요"

    lines.append(f"🏁 D-{DAYS_LEFT} | 예상 완주 {minutes_to_hhmm(total)} {status_emoji}")
    lines.append(f"  ({status_text})")
    lines.append(f"  수영 {estimate['swim']:.0f} + T1 {estimate['t1']:.0f} "
                 f"+ 자전거 {estimate['bike']:.0f} + T2 {estimate['t2']:.0f} "
                 f"+ 러닝 {estimate['run_brick']:.0f}분")

    # schedule 업데이트
    total_status = "green" if total <= 170 else ("yellow" if total <= 180 else "red")
    schedule['last_analysis'] = {
        "date": TODAY,
        "estimated_finish": minutes_to_hhmm(total),
        "status": total_status,
        "status_text": status_text,
        "phase": phase,
        "phase_name": phase_name,
        "vdot": current_vdot,
        "weekly_summary": {
            "swim": {"count": week_stats['swim']['count'], "target": swim_target},
            "run": {"count": week_stats['run']['count'], "target": run_target,
                    "total_km": week_stats['run']['total_km']},
            "bike": {"count": week_stats['bike']['count'], "target": bike_target},
        },
        "intensity_split": {"easy_pct": week_stats['easy_pct'], "hard_pct": week_stats['hard_pct']},
        "training_load": {"current": week_stats['total_load'], "target": tgt_load},
        "adjustments": [a['message'] for a in adjustments],
    }
    save_json(SCHEDULE_FILE, schedule)

    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=30)
    return resp.json().get('ok', False)


def main():
    print(f"[{NOW}] 운동 분석 시작")

    log = load_json(LOG_FILE)
    if not log:
        print("  workout_log.json 비어있음")
        return

    msg = format_analysis_message(log)
    if not msg:
        print("  분석 메시지 없음")
        return

    print(f"\n--- 메시지 미리보기 ---\n{msg}\n")

    ok = send_telegram(msg)
    print(f"  텔레그램 전송: {'성공' if ok else '실패'}")


if __name__ == '__main__':
    main()
