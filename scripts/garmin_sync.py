"""
가민 커넥트 자동 동기화
- 가민 커넥트에서 운동 기록 + 건강 데이터 자동 수집
- 새 운동 감지 시 workout_log.json 업데이트 + 텔레그램 알림
- 건강 데이터 (Body Battery, 수면, HRV, 스트레스, Training Status) 모니터링
- 운동 계획 자동 조정 (스케줄 vs 실적 비교)

스케줄: 08:00, 12:00, 16:00, 20:00 KST (GitHub Actions)
"""

import os
import sys
import json
import traceback
from datetime import datetime, timezone, timedelta
from garminconnect import Garmin

# dotenv는 로컬 실행용 (GitHub Actions에서는 환경변수 직접 주입)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))
except ImportError:
    pass

import requests

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.strftime('%Y-%m-%d')
YESTERDAY = (NOW - timedelta(days=1)).strftime('%Y-%m-%d')

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LOG_FILE = os.path.join(BASE_DIR, 'workout_log.json')
SCHEDULE_FILE = os.path.join(BASE_DIR, 'workout_schedule.json')
HEALTH_FILE = os.path.join(BASE_DIR, 'data', 'garmin_health.json')
TOKEN_DIR = os.path.join(BASE_DIR, 'data', 'garmin_tokens')

GARMIN_EMAIL = os.environ.get('GARMIN_EMAIL', '')
GARMIN_PASSWORD = os.environ.get('GARMIN_PASSWORD', '')
BOT_TOKEN = os.environ.get('BOT_TOKEN', os.environ.get('TELEGRAM_BOT_TOKEN', ''))
CHAT_ID = os.environ.get('CHAT_ID', os.environ.get('TELEGRAM_CHAT_ID', ''))

RACE_DAY = datetime(2026, 5, 9, tzinfo=KST)
TRAIN_START = datetime(2026, 3, 16, tzinfo=KST)
DAYS_LEFT = (RACE_DAY.date() - NOW.date()).days

# 가민 활동 종류 → 내부 타입 매핑
ACTIVITY_TYPE_MAP = {
    'running': 'run',
    'treadmill_running': 'run',
    'trail_running': 'run',
    'track_running': 'run',
    'lap_swimming': 'swim',
    'open_water_swimming': 'swim',
    'cycling': 'bike',
    'indoor_cycling': 'bike',
    'virtual_ride': 'bike',
    'mountain_biking': 'bike',
    'strength_training': 'strength',
    'multi_sport': 'brick',
    'triathlon': 'brick',
    'transition': 'brick',
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


def seconds_to_pace(secs):
    if secs is None or secs <= 0:
        return '?'
    m = int(secs) // 60
    s = int(secs) % 60
    return f"{m}:{s:02d}"


def seconds_to_hhmm(secs):
    h = int(secs) // 3600
    m = (int(secs) % 3600) // 60
    s = int(secs) % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[SKIP] 텔레그램 토큰/챗ID 없음")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=30)
    return resp.json().get('ok', False)


# ============================================================
# 가민 로그인
# ============================================================
def login_garmin():
    """가민 커넥트 로그인 (토큰 캐시 활용)"""
    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        print("[ERROR] GARMIN_EMAIL / GARMIN_PASSWORD 환경변수 필요")
        sys.exit(1)

    api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)

    # 토큰 캐시 시도
    os.makedirs(TOKEN_DIR, exist_ok=True)
    try:
        api.login(TOKEN_DIR)
        print("[OK] 가민 토큰 캐시로 로그인")
    except Exception:
        try:
            api.login()
            api.garth.dump(TOKEN_DIR)
            print("[OK] 가민 신규 로그인 + 토큰 저장")
        except Exception as e:
            print(f"[ERROR] 가민 로그인 실패: {e}")
            sys.exit(1)

    return api


# ============================================================
# 활동 데이터 수집
# ============================================================
def fetch_activities(api, start_date, end_date):
    """가민에서 활동 목록 가져오기"""
    try:
        activities = api.get_activities_by_date(start_date, end_date)
        print(f"[OK] 활동 {len(activities)}건 조회 ({start_date} ~ {end_date})")
        return activities
    except Exception as e:
        print(f"[ERROR] 활동 조회 실패: {e}")
        return []


def parse_activity(activity):
    """가민 활동 데이터 → workout_log.json 형식으로 변환"""
    # 활동 종류 판별
    activity_type_key = activity.get('activityType', {}).get('typeKey', '').lower()
    parent_type = activity.get('activityType', {}).get('parentTypeId', 0)
    wtype = ACTIVITY_TYPE_MAP.get(activity_type_key, '')

    # parentTypeId로 보조 판별
    if not wtype:
        parent_map = {1: 'run', 2: 'bike', 5: 'swim', 4: 'strength'}
        wtype = parent_map.get(parent_type, 'other')

    # 활동 시작 시각 → 날짜 (KST)
    start_local = activity.get('startTimeLocal', '')
    if start_local:
        date_key = start_local[:10]  # 'YYYY-MM-DD'
    else:
        date_key = TODAY

    # 기본 메트릭스
    duration_sec = activity.get('duration', 0) or 0
    moving_sec = activity.get('movingDuration', 0) or 0
    distance_m = activity.get('distance', 0) or 0
    avg_hr = activity.get('averageHR', None)
    max_hr = activity.get('maxHR', None)
    calories = activity.get('calories', 0)
    avg_speed = activity.get('averageSpeed', None)  # m/s

    # Training Effect
    aerobic_te = activity.get('aerobicTrainingEffect', None)
    anaerobic_te = activity.get('anaerobicTrainingEffect', None)
    training_load = activity.get('activityTrainingLoad', None)

    result = {
        'garmin_id': activity.get('activityId'),
        'date': date_key,
        'type': wtype,
        'activity_name': activity.get('activityName', ''),
        'duration_sec': round(duration_sec),
        'moving_sec': round(moving_sec),
        'calories': calories,
        'avg_hr': round(avg_hr) if avg_hr else None,
        'max_hr': round(max_hr) if max_hr else None,
        'aerobic_te': aerobic_te,
        'anaerobic_te': anaerobic_te,
        'training_load': round(training_load) if training_load else None,
    }

    if wtype == 'run':
        distance_km = round(distance_m / 1000, 2)
        pace_sec = (duration_sec / (distance_m / 1000)) if distance_m > 0 else 0
        cadence = activity.get('averageRunningCadenceInStepsPerMinute', None)
        stride = activity.get('avgStrideLength', None)
        vo = activity.get('vO2MaxValue', None)

        result.update({
            'distance_km': distance_km,
            'pace_per_km': seconds_to_pace(pace_sec),
            'pace_sec': round(pace_sec),
            'cadence': round(cadence) if cadence else None,
            'stride_m': round(stride / 100, 2) if stride else None,
            'vo2max': vo,
        })

    elif wtype == 'swim':
        distance_m_val = round(distance_m)
        pace_per_100m = (duration_sec / (distance_m / 100)) if distance_m > 0 else 0
        moving_pace = (moving_sec / (distance_m / 100)) if distance_m > 0 and moving_sec > 0 else pace_per_100m
        avg_swolf = activity.get('averageSwolf', None)
        total_strokes = activity.get('strokes', None)
        avg_stroke_rate = activity.get('avgStrokeRate', None)
        pool_length = activity.get('poolLength', None)

        result.update({
            'distance_m': distance_m_val,
            'pace_per_100m': seconds_to_pace(moving_pace if moving_sec > 0 else pace_per_100m),
            'pace_sec_100m': round(moving_pace if moving_sec > 0 else pace_per_100m),
            'swolf': round(avg_swolf) if avg_swolf else None,
            'total_strokes': round(total_strokes) if total_strokes else None,
            'avg_stroke_rate': round(avg_stroke_rate) if avg_stroke_rate else None,
            'pool_length': pool_length,
        })

    elif wtype == 'bike':
        distance_km = round(distance_m / 1000, 2)
        avg_speed_kmh = round(avg_speed * 3.6, 1) if avg_speed else None
        avg_power = activity.get('avgPower', None)
        max_power = activity.get('maxPower', None)
        norm_power = activity.get('normPower', None)

        result.update({
            'distance_km': distance_km,
            'avg_speed_kmh': avg_speed_kmh,
            'avg_power': avg_power,
            'max_power': max_power,
            'norm_power': norm_power,
        })

    return result


def classify_zone(parsed, vdot=37):
    """훈련 존 판정 (러닝만, 나머지는 HR 기반)"""
    wtype = parsed['type']
    if wtype == 'run':
        pace = parsed.get('pace_sec', 0)
        if not pace:
            return 'moderate'
        # VDOT 기반 존 경계 (간이 lookup)
        zones = {
            35: {'easy': 394, 'tempo': 347},
            36: {'easy': 385, 'tempo': 339},
            37: {'easy': 376, 'tempo': 331},
            38: {'easy': 367, 'tempo': 323},
        }
        z = zones.get(vdot, zones[37])
        if pace >= z['easy']:
            return 'easy'
        elif pace >= z['tempo'] + 10:
            return 'moderate'
        elif pace >= z['tempo'] - 5:
            return 'tempo'
        else:
            return 'interval'
    else:
        # HR 기반 간이 판정 (최대심박 184 기준)
        hr = parsed.get('avg_hr', 0)
        if not hr:
            return 'moderate'
        if hr < 130:
            return 'easy'
        elif hr < 150:
            return 'moderate'
        elif hr < 165:
            return 'tempo'
        else:
            return 'interval'


def detect_swim_equipment(parsed, workout_log):
    """수영 데이터가 최근 맨몸 평균 대비 확연히 좋으면 장비 사용 추정"""
    pace_100m = parsed.get('pace_sec_100m', 0)
    swolf = parsed.get('swolf', 0)
    if not pace_100m:
        return False

    # 최근 수영 기록에서 맨몸(swim_equipment 없는) 데이터 수집
    bare_paces = []
    bare_swolfs = []
    for entry in sorted(workout_log.values(), key=lambda e: e.get('garmin_id', 0), reverse=True):
        if not entry.get('done'):
            continue
        m = entry.get('metrics', {})
        if m.get('type') != 'swim':
            continue
        if m.get('swim_equipment') and m['swim_equipment'] != 'none':
            continue  # 이미 장비로 판정된 건 제외
        p = m.get('pace_per_100m')
        if p:
            parts = p.split(':')
            if len(parts) == 2:
                bare_paces.append(int(parts[0]) * 60 + int(parts[1]))
        s = m.get('swolf')
        if s:
            bare_swolfs.append(s)
        if len(bare_paces) >= 5:
            break

    if len(bare_paces) < 2:
        return False  # 비교 데이터 부족

    avg_pace = sum(bare_paces) / len(bare_paces)
    avg_swolf = sum(bare_swolfs) / len(bare_swolfs) if bare_swolfs else 99

    # 페이스가 맨몸 평균보다 10초/100m 이상 빠르거나, Swolf가 5 이상 낮으면 장비 추정
    pace_diff = avg_pace - pace_100m
    swolf_diff = avg_swolf - swolf if swolf else 0

    return pace_diff >= 10 or swolf_diff >= 5


def to_workout_log_entry(parsed, schedule_file_data, workout_log_data=None):
    """파싱된 활동 → workout_log.json 엔트리 형식"""
    if workout_log_data is None:
        workout_log_data = {}
    vdot = schedule_file_data.get('current_vdot', 37)
    zone = classify_zone(parsed, vdot)
    wtype = parsed['type']

    # 실제 운동 내용 요약
    if wtype == 'run':
        actual = f"러닝 {parsed['distance_km']}km @{parsed['pace_per_km']}"
    elif wtype == 'swim':
        dur_min = round(parsed['duration_sec'] / 60)
        actual = f"수영 {parsed['distance_m']}m {dur_min}분 @{parsed['pace_per_100m']}/100m"
    elif wtype == 'bike':
        dur_min = round(parsed['duration_sec'] / 60)
        if parsed.get('distance_km'):
            actual = f"자전거 {parsed['distance_km']}km {dur_min}분"
        else:
            actual = f"자전거 {dur_min}분"
    else:
        dur_min = round(parsed['duration_sec'] / 60)
        actual = f"{parsed.get('activity_name', wtype)} {dur_min}분"

    metrics = {'type': wtype}
    if wtype == 'run':
        metrics.update({
            'distance_km': parsed['distance_km'],
            'pace_per_km': parsed['pace_per_km'],
            'avg_hr': parsed['avg_hr'],
            'max_hr': parsed['max_hr'],
            'cadence': parsed.get('cadence'),
        })
    elif wtype == 'swim':
        metrics.update({
            'distance_m': parsed['distance_m'],
            'duration_min': round(parsed['duration_sec'] / 60, 1),
            'moving_min': round(parsed['moving_sec'] / 60, 1),
            'pace_per_100m': parsed['pace_per_100m'],
            'avg_hr': parsed['avg_hr'],
            'max_hr': parsed['max_hr'],
            'swolf': parsed.get('swolf'),
            'strokes': parsed.get('total_strokes'),
            'avg_spm': parsed.get('avg_stroke_rate'),
        })
    elif wtype == 'bike':
        metrics.update({
            'distance_km': parsed.get('distance_km'),
            'duration_min': round(parsed['duration_sec'] / 60, 1),
            'avg_speed_kmh': parsed.get('avg_speed_kmh'),
            'avg_hr': parsed['avg_hr'],
            'max_hr': parsed['max_hr'],
            'avg_power': parsed.get('avg_power'),
        })

    # 훈련 효과 기록
    te_parts = []
    if parsed.get('aerobic_te'):
        te_parts.append(f"유산소 {round(parsed['aerobic_te'], 1)}")
    if parsed.get('anaerobic_te'):
        te_parts.append(f"무산소 {round(parsed['anaerobic_te'], 1)}")
    te_note = " / ".join(te_parts)
    note = te_note
    if parsed.get('training_load'):
        note += f" | 부하 {parsed['training_load']}"

    # 수영 장비 사용 추정: 페이스/Swolf가 최근 평균 대비 확연히 좋으면 장비 가능성
    if wtype == 'swim':
        equipment_guess = detect_swim_equipment(parsed, workout_log_data)
        if equipment_guess:
            metrics['swim_equipment'] = 'fins'
            note = (note + " | 장비 추정").strip(' | ')

    return {
        'planned': '',  # 나중에 스케줄과 매칭
        'done': True,
        'actual': actual,
        'metrics': metrics,
        'training_zone': zone,
        'note': note,
        'garmin_id': parsed.get('garmin_id'),
    }


# ============================================================
# 건강 데이터 수집
# ============================================================
def fetch_health_data(api, date_str):
    """일별 건강 데이터 수집"""
    health = {'date': date_str}

    # Daily Stats (steps, stress, body battery 등)
    try:
        stats = api.get_stats(date_str)
        health['steps'] = stats.get('totalSteps', 0)
        health['stress'] = {
            'avg': stats.get('averageStressLevel', None),
            'max': stats.get('maxStressLevel', None),
            'rest': stats.get('restStressPercentage', None),
            'low': stats.get('lowStressPercentage', None),
            'medium': stats.get('mediumStressPercentage', None),
            'high': stats.get('highStressPercentage', None),
        }
        health['body_battery'] = {
            'max': stats.get('bodyBatteryHighestValue', None),
            'min': stats.get('bodyBatteryLowestValue', None),
            'start': stats.get('bodyBatteryMostRecentValue', None),
        }
        health['resting_hr'] = stats.get('restingHeartRate', None)
    except Exception as e:
        print(f"  [WARN] daily stats 조회 실패: {e}")

    # 수면 데이터
    try:
        sleep = api.get_sleep_data(date_str)
        if sleep and sleep.get('dailySleepDTO'):
            s = sleep['dailySleepDTO']
            sleep_sec = s.get('sleepTimeSeconds', 0) or 0
            health['sleep'] = {
                'duration_min': round(sleep_sec / 60),
                'score': s.get('sleepScores', {}).get('overall', {}).get('value', None),
                'deep_min': round((s.get('deepSleepSeconds', 0) or 0) / 60),
                'light_min': round((s.get('lightSleepSeconds', 0) or 0) / 60),
                'rem_min': round((s.get('remSleepSeconds', 0) or 0) / 60),
                'awake_min': round((s.get('awakeSleepSeconds', 0) or 0) / 60),
            }
    except Exception as e:
        print(f"  [WARN] 수면 데이터 조회 실패: {e}")

    # HRV
    try:
        hrv = api.get_hrv_data(date_str)
        if hrv and hrv.get('hrvSummary'):
            h = hrv['hrvSummary']
            health['hrv'] = {
                'weekly_avg': h.get('weeklyAvg', None),
                'last_night': h.get('lastNightAvg', None),
                'status': h.get('status', None),  # BALANCED, LOW, etc.
            }
    except Exception as e:
        print(f"  [WARN] HRV 조회 실패: {e}")

    # Training Readiness
    try:
        tr = api.get_training_readiness(date_str)
        if tr:
            # API 응답 형태에 따라 처리
            if isinstance(tr, list) and len(tr) > 0:
                tr = tr[0]
            if isinstance(tr, dict):
                health['training_readiness'] = {
                    'score': tr.get('score', tr.get('trainingReadinessScore', None)),
                    'level': tr.get('level', tr.get('trainingReadinessLevel', None)),
                }
    except Exception as e:
        print(f"  [WARN] Training Readiness 조회 실패: {e}")

    # Training Status
    try:
        ts = api.get_training_status(date_str)
        if ts:
            if isinstance(ts, list) and len(ts) > 0:
                ts = ts[0]
            if isinstance(ts, dict):
                health['training_status'] = {
                    'status': ts.get('trainingStatusMessage', ts.get('status', None)),
                    'load': ts.get('weeklyTrainingLoad', ts.get('trainingLoad', None)),
                    'vo2max_run': ts.get('vo2MaxRun', ts.get('runVo2Max', None)),
                }
    except Exception as e:
        print(f"  [WARN] Training Status 조회 실패: {e}")

    return health


# ============================================================
# 스케줄 매칭 & 계획 조정
# ============================================================
def get_planned_workout(date_str, schedule_data):
    """해당 날짜의 계획된 운동 반환"""
    # workout_alert.py의 스케줄 로직 참조
    from workout_alert import get_schedule_for_date
    dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=KST)
    workout, detail = get_schedule_for_date(dt)
    return workout


def check_plan_adherence(workout_log, schedule_data):
    """계획 대비 실제 실행 상태 점검 → 조정 제안"""
    adjustments = []

    # 이번 주 월요일 계산
    today = NOW.date()
    monday = today - timedelta(days=today.weekday())

    run_count = 0
    swim_count = 0
    bike_count = 0
    total_run_km = 0.0
    days_passed = today.weekday()  # 0=월 ~ 6=일

    for d in range(days_passed + 1):
        dt = monday + timedelta(days=d)
        key = dt.strftime('%Y-%m-%d')
        entry = workout_log.get(key)
        if not entry or not entry.get('done'):
            continue
        wtype = entry.get('metrics', {}).get('type', '')
        if wtype == 'run':
            run_count += 1
            total_run_km += entry.get('metrics', {}).get('distance_km', 0)
        elif wtype == 'swim':
            swim_count += 1
        elif wtype == 'bike':
            bike_count += 1

    remaining_days = 6 - days_passed

    # 러닝 빈도 체크 (수요일 이후)
    if days_passed >= 2 and run_count < 1:
        adjustments.append(f"🔴 러닝 {run_count}회 — {remaining_days}일 내 최소 3회 필요")
    elif days_passed >= 3 and run_count < 2:
        adjustments.append(f"🟡 러닝 {run_count}회 — 남은 {remaining_days}일 내 추가 필요")

    # 자전거 체크 (금요일 이후)
    if days_passed >= 4 and bike_count == 0:
        adjustments.append(f"⚠️ 자전거 0회 — 주말에 반드시 포함 권장")

    return adjustments


# ============================================================
# 운동 피드백 생성
# ============================================================
def generate_workout_feedback(parsed, schedule_data):
    """개별 운동에 대한 피드백 (의미 판정 + 구체적 코멘트)"""
    wtype = parsed['type']
    vdot = schedule_data.get('current_vdot', 37)
    zone = classify_zone(parsed, vdot)
    feedback = []

    # VDOT 기반 페이스 존 경계
    vdot_zones = {
        35: {'easy_lo': 394, 'easy_hi': 437, 'tempo': 347},
        36: {'easy_lo': 385, 'easy_hi': 427, 'tempo': 339},
        37: {'easy_lo': 376, 'easy_hi': 417, 'tempo': 331},
        38: {'easy_lo': 367, 'easy_hi': 407, 'tempo': 323},
        39: {'easy_lo': 359, 'easy_hi': 398, 'tempo': 316},
    }
    z = vdot_zones.get(vdot, vdot_zones[37])

    if wtype == 'run':
        dist = parsed.get('distance_km', 0)
        pace = parsed.get('pace_sec', 0)
        hr = parsed.get('avg_hr', 0)

        # 거리 평가
        if dist >= 10:
            feedback.append("✅ 10km+ Long Run — 대회 거리 달성, 자신감 ↑")
        elif dist >= 7:
            feedback.append("✅ 7km+ — 충분한 거리, 좋습니다")
        elif dist >= 5:
            feedback.append("👍 5km+ — 기본 볼륨 충족")
        else:
            feedback.append("⚠️ 5km 미만 — 짧은 런, 다음엔 5km 이상 목표")

        # 페이스 평가
        if pace >= z['easy_lo']:
            feedback.append(f"✅ Easy 존 정확히 준수 ({seconds_to_pace(pace)}/km)")
        elif pace >= z['tempo'] + 10:
            feedback.append(f"⚠️ Moderate 존 (Dead Zone) — {seconds_to_pace(pace)}/km")
            feedback.append(f"  → Easy({seconds_to_pace(z['easy_lo'])}~) 또는 Tempo({seconds_to_pace(z['tempo'])})로 명확히")
        elif pace >= z['tempo'] - 5:
            feedback.append(f"✅ Tempo 존 — {seconds_to_pace(pace)}/km, 역치 훈련 효과 ○")
        else:
            feedback.append(f"🔥 고강도 — {seconds_to_pace(pace)}/km, 회복일 필요")

        # 심박 vs 페이스 매칭
        if hr and pace >= z['easy_lo'] and hr > 155:
            feedback.append(f"⚠️ Easy 페이스인데 HR {hr} 높음 — 피로 누적 or 컨디션 체크")

    elif wtype == 'swim':
        dist = parsed.get('distance_m', 0)
        pace_100m = parsed.get('pace_sec_100m', 0)

        if dist >= 1500:
            feedback.append("✅ 1.5km+ — 대회 거리 달성!")
        elif dist >= 1000:
            feedback.append("👍 1km+ — 좋은 볼륨")
        elif dist >= 500:
            feedback.append("👌 기본 유지 수준")
        else:
            feedback.append("⚠️ 500m 미만 — 볼륨 부족, 1km 이상 권장")

        # Swolf
        swolf = parsed.get('swolf')
        if swolf:
            if swolf <= 35:
                feedback.append(f"✅ Swolf {swolf} — 효율 우수")
            elif swolf <= 40:
                feedback.append(f"👍 Swolf {swolf} — 양호")
            else:
                feedback.append(f"⚠️ Swolf {swolf} — 스트로크 효율 개선 필요")

        # 장비 사용 가능성 판정 (평소 대비 페이스/Swolf가 확연히 좋은 경우)
        # 수업(수/금/토)에서 오리발·패들 등 사용 가능성 있음
        if pace_100m and pace_100m > 0:
            # 간이 판정: workout_log에서 최근 맨몸 평균과 비교는 detect_swim_equipment에서 처리
            # 여기서는 결과만 표시
            date_dt = datetime.strptime(parsed.get('date', TODAY), '%Y-%m-%d')
            is_lesson_day = date_dt.weekday() in (2, 4, 5)  # 수/금/토
            if is_lesson_day and pace_100m < 110:  # 1:50 미만이면 장비 가능성
                feedback.append(f"💡 수업일 + 빠른 페이스 — 장비 사용 가능성 있음 (맨몸 환산 보정 적용)")

    elif wtype == 'bike':
        dur_min = parsed.get('duration_sec', 0) / 60
        speed = parsed.get('avg_speed_kmh', 0)

        if dur_min >= 75:
            feedback.append("✅ 75분+ — 대회 수준 볼륨")
        elif dur_min >= 60:
            feedback.append("👍 60분+ — 기본 볼륨 충족")
        elif dur_min >= 30:
            feedback.append("👌 감각 유지 수준")
        else:
            feedback.append("⚠️ 30분 미만 — 다음엔 더 길게")

        if speed and speed >= 30:
            feedback.append(f"✅ 평속 {speed}km/h — 대회 목표 속도 이상")

    # 훈련 효과 평가
    te = parsed.get('aerobic_te', 0) or 0
    if te >= 3.0:
        feedback.append(f"🔥 유산소 TE {round(te, 1)} — 체력 향상에 크게 기여")
    elif te >= 2.0:
        feedback.append(f"👍 유산소 TE {round(te, 1)} — 유지/소폭 향상")
    elif te > 0:
        feedback.append(f"💡 유산소 TE {round(te, 1)} — 가벼운 자극 (회복 운동)")

    return feedback


# ============================================================
# 금주 스케줄 포매팅
# ============================================================
def format_week_schedule(workout_log):
    """이번 주 스케줄 + 완료 현황"""
    import sys as _sys
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if _scripts_dir not in _sys.path:
        _sys.path.insert(0, _scripts_dir)
    from workout_alert import get_schedule_for_date, get_emoji, DOW_NAMES, WEEK_NAMES, PHASE_GOALS, get_phase

    today = NOW.date()
    monday = today - timedelta(days=today.weekday())
    week_num = (monday - TRAIN_START.date()).days // 7

    lines = []
    week_name = WEEK_NAMES.get(week_num, f"Week {week_num}")
    phase_num, phase_name = get_phase(NOW)
    lines.append(f"📅 {week_name} ({phase_name})")

    # 주간 목표
    goals = PHASE_GOALS.get(phase_num, {})
    if goals.get('min'):
        lines.append(f"  최소: {goals['min']}")

    # 주간 통계
    run_count = 0
    swim_count = 0
    bike_count = 0
    run_km = 0.0

    for d in range(7):
        dt = monday + timedelta(days=d)
        date_str = dt.strftime('%Y-%m-%d')
        dow_name = DOW_NAMES[dt.weekday()]
        date_disp = dt.strftime('%m/%d')

        workout, detail = get_schedule_for_date(datetime(dt.year, dt.month, dt.day, tzinfo=KST))
        emoji = get_emoji(workout)

        entry = workout_log.get(date_str)
        is_today = (dt == today)

        if entry and entry.get('done'):
            status = "✅"
            actual = entry.get('actual', '')
            wtype = entry.get('metrics', {}).get('type', '')
            if wtype == 'run':
                run_count += 1
                run_km += entry.get('metrics', {}).get('distance_km', 0)
            elif wtype == 'swim':
                swim_count += 1
            elif wtype == 'bike':
                bike_count += 1
            line = f"  {dow_name}({date_disp}) {emoji} {actual} {status}"
        elif dt < today:
            if "휴식" in workout:
                status = "😴"
            else:
                status = "❌"
            line = f"  {dow_name}({date_disp}) {emoji} {workout} {status}"
        elif is_today:
            line = f"  {dow_name}({date_disp}) {emoji} {workout} 👈"
        else:
            line = f"  {dow_name}({date_disp}) {emoji} {workout}"

        lines.append(line)

    # 주간 진척 요약
    targets = {1: (4, 3, 20), 2: (3, 3, 21), 3: (2, 2, 10)}
    swim_t, run_t, km_t = targets.get(phase_num, (4, 3, 20))

    swim_bar = "●" * swim_count + "○" * max(0, swim_t - swim_count)
    run_bar = "●" * run_count + "○" * max(0, run_t - run_count)

    lines.append("")
    lines.append(f"  수영 {swim_bar} {swim_count}/{swim_t} | 러닝 {run_bar} {run_count}/{run_t} ({run_km:.0f}km/{km_t}km) | 자전거 {bike_count}회")

    return "\n".join(lines)


# ============================================================
# 텔레그램 메시지 포매팅
# ============================================================
def format_workout_message(parsed_activities, health, plan_adjustments, schedule_data, workout_log):
    """새 운동 감지 시 텔레그램 메시지"""
    lines = []
    lines.append("🏋️ 가민 운동 자동 감지")
    lines.append("")

    type_emoji = {'run': '🏃', 'swim': '🏊', 'bike': '🚴', 'brick': '🔥', 'strength': '💪'}
    type_name = {'run': '러닝', 'swim': '수영', 'bike': '자전거', 'brick': '브릭', 'strength': '근력'}

    for p in parsed_activities:
        wtype = p['type']
        emoji = type_emoji.get(wtype, '🏋️')
        name = type_name.get(wtype, p.get('activity_name', '운동'))

        lines.append(f"{emoji} {name}")

        if wtype == 'run':
            lines.append(f"  {p['distance_km']}km | {p['pace_per_km']}/km | {seconds_to_hhmm(p['duration_sec'])}")
            if p['avg_hr']:
                lines.append(f"  HR {p['avg_hr']}/{p['max_hr']}")
            if p.get('cadence'):
                lines.append(f"  케이던스 {p['cadence']}spm | 보폭 {p.get('stride_m', '?')}m")

        elif wtype == 'swim':
            dur_str = seconds_to_hhmm(p['duration_sec'])
            lines.append(f"  {p['distance_m']}m | {p['pace_per_100m']}/100m | {dur_str}")
            if p['avg_hr']:
                lines.append(f"  HR {p['avg_hr']}/{p['max_hr']}")
            if p.get('swolf'):
                lines.append(f"  Swolf {p['swolf']} | 스트로크 {p.get('total_strokes', '?')}")

        elif wtype == 'bike':
            dur_str = seconds_to_hhmm(p['duration_sec'])
            if p.get('distance_km'):
                lines.append(f"  {p['distance_km']}km | {p.get('avg_speed_kmh', '?')}km/h | {dur_str}")
            else:
                lines.append(f"  {dur_str}")
            if p['avg_hr']:
                lines.append(f"  HR {p['avg_hr']}/{p['max_hr']}")
            if p.get('avg_power'):
                lines.append(f"  파워 {p['avg_power']}W (NP {p.get('norm_power', '?')}W)")

        # 훈련 효과
        te_parts = []
        if p.get('aerobic_te'):
            te_parts.append(f"유산소 {round(p['aerobic_te'], 1)}")
        if p.get('anaerobic_te'):
            te_parts.append(f"무산소 {round(p['anaerobic_te'], 1)}")
        if te_parts:
            lines.append(f"  TE: {' / '.join(te_parts)}")

        # 운동 피드백 (핵심 추가)
        feedback = generate_workout_feedback(p, schedule_data)
        if feedback:
            lines.append("")
            lines.append("  💬 피드백")
            for fb in feedback:
                lines.append(f"    {fb}")
        lines.append("")

    # 금주 스케줄 + 진척 현황
    try:
        week_schedule = format_week_schedule(workout_log)
        lines.append(week_schedule)
        lines.append("")
    except Exception as e:
        print(f"  [WARN] 주간 스케줄 포매팅 실패: {e}")

    # 컨디션 섹션 (간결하게)
    lines.append("📊 컨디션")
    condition_parts = []
    if health.get('body_battery'):
        bb = health['body_battery']
        icon = "🟢" if (bb.get('max') or 0) >= 60 else ("🟡" if (bb.get('max') or 0) >= 40 else "🔴")
        condition_parts.append(f"BB {bb.get('min', '?')}~{bb.get('max', '?')} {icon}")
    if health.get('sleep'):
        sl = health['sleep']
        h, m = sl['duration_min'] // 60, sl['duration_min'] % 60
        score = sl.get('score', '?')
        icon = "🟢" if (score and score != '?' and score >= 70) else "🟡"
        condition_parts.append(f"수면 {h}h{m}m({score}) {icon}")
    if health.get('hrv'):
        hrv = health['hrv']
        status = hrv.get('status', '?')
        icon = "🟢" if status == 'BALANCED' else "🟡"
        condition_parts.append(f"HRV {hrv.get('last_night', '?')}ms[{status}] {icon}")
    if health.get('training_readiness'):
        tr = health['training_readiness']
        s = tr.get('score')
        icon = "🟢" if (s and s >= 60) else ("🟡" if (s and s >= 40) else "🔴")
        condition_parts.append(f"Readiness {s}({tr.get('level', '?')}) {icon}")
    lines.append(f"  {' | '.join(condition_parts)}")
    lines.append("")

    # 계획 조정
    if plan_adjustments:
        lines.append("🔄 계획 조정")
        for adj in plan_adjustments:
            lines.append(f"  {adj}")
        lines.append("")

    # D-day
    last_analysis = schedule_data.get('last_analysis', {})
    est = last_analysis.get('estimated_finish', '?')
    status_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(
        last_analysis.get('status', ''), '⚪')
    lines.append(f"🏁 D-{DAYS_LEFT} | 목표 2:50 | 예상 {est} {status_icon}")

    return "\n".join(lines)


# ============================================================
# 메인 동기화 로직
# ============================================================
def sync():
    print(f"[{NOW.strftime('%Y-%m-%d %H:%M')}] 가민 동기화 시작")

    # 1. 가민 로그인
    api = login_garmin()

    # 2. 기존 데이터 로드
    workout_log = load_json(LOG_FILE)
    schedule_data = load_json(SCHEDULE_FILE)

    # 기존 garmin_id 목록 (중복 방지)
    existing_ids = set()
    for entry in workout_log.values():
        gid = entry.get('garmin_id')
        if gid:
            existing_ids.add(gid)

    # 3. 최근 2일간 활동 조회
    activities = fetch_activities(api, YESTERDAY, TODAY)

    # 4. 새 활동 필터링 & 파싱
    new_activities = []
    for act in activities:
        act_id = act.get('activityId')
        if act_id in existing_ids:
            continue

        parsed = parse_activity(act)
        if parsed['type'] in ('run', 'swim', 'bike', 'brick', 'strength'):
            new_activities.append(parsed)

    print(f"  새 활동: {len(new_activities)}건")

    # 5. 건강 데이터 수집 (항상)
    health = fetch_health_data(api, TODAY)
    print(f"  건강 데이터 수집 완료")

    # 건강 데이터 저장 (rolling 14일)
    health_history = load_json(HEALTH_FILE)
    if not isinstance(health_history, dict):
        health_history = {}
    health_history[TODAY] = health
    # 14일 이전 데이터 정리
    cutoff = (NOW - timedelta(days=14)).strftime('%Y-%m-%d')
    health_history = {k: v for k, v in health_history.items() if k >= cutoff}
    save_json(HEALTH_FILE, health_history)

    # 6. 새 활동이 있으면 workout_log 업데이트
    if new_activities:
        for parsed in new_activities:
            date_key = parsed['date']
            entry = to_workout_log_entry(parsed, schedule_data, workout_log)

            # 같은 날 이미 기록이 있으면 → 기존 것에 추가 (멀티 스포츠)
            if date_key in workout_log:
                existing = workout_log[date_key]
                # 기존 기록의 actual에 추가
                existing['actual'] = existing.get('actual', '') + ' + ' + entry['actual']
                existing['garmin_id'] = entry['garmin_id']
                existing['done'] = True
                # 메트릭스는 마지막 운동 기준 (또는 주 운동)
                # 러닝이 있으면 러닝 우선
                if entry['metrics']['type'] == 'run' or existing['metrics']['type'] not in ('run',):
                    existing['metrics'] = entry['metrics']
                    existing['training_zone'] = entry['training_zone']
                if entry['note']:
                    existing['note'] = (existing.get('note', '') + ' | ' + entry['note']).strip(' | ')
            else:
                # 스케줄에서 planned 가져오기
                try:
                    planned = get_planned_workout(date_key, schedule_data)
                    entry['planned'] = planned
                except Exception:
                    entry['planned'] = ''
                workout_log[date_key] = entry

        save_json(LOG_FILE, workout_log)
        print(f"  workout_log.json 업데이트 완료")

        # 7. 계획 조정 점검
        plan_adjustments = check_plan_adherence(workout_log, schedule_data)

        # 8. 텔레그램 알림
        msg = format_workout_message(new_activities, health, plan_adjustments, schedule_data, workout_log)
        ok = send_telegram(msg)
        print(f"  텔레그램 전송: {'성공' if ok else '실패'}")

        return True  # 변경 있음
    else:
        print("  새 활동 없음 — 조용히 종료")
        return False  # 변경 없음


def resend_today():
    """오늘 기록된 운동을 다시 알림 전송 (테스트/재전송용)"""
    print(f"[{NOW.strftime('%Y-%m-%d %H:%M')}] 오늘 운동 재전송")

    api = login_garmin()
    workout_log = load_json(LOG_FILE)
    schedule_data = load_json(SCHEDULE_FILE)
    health = fetch_health_data(api, TODAY)

    # 오늘 기록에서 parsed activity 재구성
    today_entry = workout_log.get(TODAY)
    if not today_entry or not today_entry.get('done'):
        print("  오늘 운동 기록 없음")
        return

    m = today_entry.get('metrics', {})
    wtype = m.get('type', '')

    parsed = {
        'type': wtype,
        'date': TODAY,
        'duration_sec': int(m.get('duration_min', 0) * 60) if m.get('duration_min') else 0,
        'moving_sec': int(m.get('moving_min', 0) * 60) if m.get('moving_min') else 0,
        'avg_hr': m.get('avg_hr'),
        'max_hr': m.get('max_hr'),
        'aerobic_te': None,
        'anaerobic_te': None,
        'training_load': None,
    }

    # TE/부하 파싱 from note
    note = today_entry.get('note', '')
    import re
    ae_match = re.search(r'유산소 ([\d.]+)', note)
    an_match = re.search(r'무산소 ([\d.]+)', note)
    load_match = re.search(r'부하 (\d+)', note)
    if ae_match:
        parsed['aerobic_te'] = float(ae_match.group(1))
    if an_match:
        parsed['anaerobic_te'] = float(an_match.group(1))
    if load_match:
        parsed['training_load'] = int(load_match.group(1))

    if wtype == 'swim':
        pace_str = m.get('pace_per_100m', '0:00')
        parts = pace_str.split(':')
        pace_sec = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 0
        parsed.update({
            'distance_m': m.get('distance_m', 0),
            'pace_per_100m': pace_str,
            'pace_sec_100m': pace_sec,
            'swolf': m.get('swolf'),
            'total_strokes': m.get('strokes'),
        })
        if not parsed['duration_sec']:
            parsed['duration_sec'] = int(pace_sec * m.get('distance_m', 0) / 100)
    elif wtype == 'run':
        pace_str = m.get('pace_per_km', '0:00')
        parts = pace_str.split(':')
        pace_sec = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 0
        parsed.update({
            'distance_km': m.get('distance_km', 0),
            'pace_per_km': pace_str,
            'pace_sec': pace_sec,
        })
        if not parsed['duration_sec']:
            parsed['duration_sec'] = int(pace_sec * m.get('distance_km', 0))
    elif wtype == 'bike':
        parsed.update({
            'distance_km': m.get('distance_km'),
            'avg_speed_kmh': m.get('avg_speed_kmh'),
            'avg_power': m.get('avg_power'),
        })
        if not parsed['duration_sec']:
            parsed['duration_sec'] = int(m.get('duration_min', 0) * 60)

    plan_adj = check_plan_adherence(workout_log, schedule_data)
    msg = format_workout_message([parsed], health, plan_adj, schedule_data, workout_log)
    ok = send_telegram(msg)
    print(f"  텔레그램 전송: {'성공' if ok else '실패'}")


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'sync'
    try:
        if mode == 'resend':
            resend_today()
        else:
            changed = sync()
        sys.exit(0)
    except Exception as e:
        error_msg = f"❌ 가민 동기화 오류\n{traceback.format_exc()}"
        print(error_msg)
        send_telegram(error_msg[:500])
        sys.exit(1)
