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


def to_workout_log_entry(parsed, schedule_file_data):
    """파싱된 활동 → workout_log.json 엔트리 형식"""
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
# 텔레그램 메시지 포매팅
# ============================================================
def format_workout_message(parsed_activities, health, plan_adjustments, schedule_data):
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
            if p.get('vo2max'):
                lines.append(f"  VO2 Max: {p['vo2max']}")

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
        if p.get('training_load'):
            lines.append(f"  운동 부하: {p['training_load']}")

        zone = classify_zone(p, schedule_data.get('current_vdot', 37))
        zone_kr = {'easy': 'Easy', 'moderate': 'Moderate', 'tempo': 'Tempo', 'interval': 'Interval'}
        lines.append(f"  훈련 존: {zone_kr.get(zone, zone)}")
        lines.append("")

    # 컨디션 섹션
    lines.append("📊 컨디션")
    if health.get('body_battery'):
        bb = health['body_battery']
        lines.append(f"  Body Battery: {bb.get('min', '?')} ~ {bb.get('max', '?')}")
    if health.get('sleep'):
        sl = health['sleep']
        dur_h = sl['duration_min'] // 60
        dur_m = sl['duration_min'] % 60
        score = sl.get('score', '?')
        lines.append(f"  수면: {dur_h}h {dur_m}m (점수 {score})")
        lines.append(f"    깊은 {sl.get('deep_min', 0)}분 | REM {sl.get('rem_min', 0)}분 | 얕은 {sl.get('light_min', 0)}분")
    if health.get('hrv'):
        hrv = health['hrv']
        lines.append(f"  HRV: 지난밤 {hrv.get('last_night', '?')}ms (주간평균 {hrv.get('weekly_avg', '?')}ms) [{hrv.get('status', '?')}]")
    if health.get('training_readiness'):
        tr = health['training_readiness']
        lines.append(f"  Training Readiness: {tr.get('score', '?')} ({tr.get('level', '?')})")
    if health.get('training_status'):
        ts = health['training_status']
        lines.append(f"  Training Status: {ts.get('status', '?')}")
        if ts.get('vo2max_run'):
            lines.append(f"  VO2 Max (러닝): {ts['vo2max_run']}")
    if health.get('resting_hr'):
        lines.append(f"  안정시 심박: {health['resting_hr']}bpm")
    if health.get('stress'):
        st = health['stress']
        lines.append(f"  스트레스: 평균 {st.get('avg', '?')}")
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
            entry = to_workout_log_entry(parsed, schedule_data)

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
        msg = format_workout_message(new_activities, health, plan_adjustments, schedule_data)
        ok = send_telegram(msg)
        print(f"  텔레그램 전송: {'성공' if ok else '실패'}")

        return True  # 변경 있음
    else:
        print("  새 활동 없음 — 조용히 종료")
        return False  # 변경 없음


if __name__ == '__main__':
    try:
        changed = sync()
        sys.exit(0)
    except Exception as e:
        error_msg = f"❌ 가민 동기화 오류\n{traceback.format_exc()}"
        print(error_msg)
        send_telegram(error_msg[:500])
        sys.exit(1)
