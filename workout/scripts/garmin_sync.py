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
import base64
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

RACE_DAY = datetime(2026, 5, 10, tzinfo=KST)
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
def _restore_tokens_from_env():
    """GARMIN_TOKENS 환경변수(base64 tar.gz)에서 토큰 파일 복원 (GitHub Actions용)

    로컬에서 생성:
        tar -czf - -C data garmin_tokens | base64 | gh secret set GARMIN_TOKENS
    """
    import subprocess
    import tempfile

    tokens_b64 = os.environ.get('GARMIN_TOKENS', '')
    if not tokens_b64:
        return False
    try:
        tar_bytes = base64.b64decode(tokens_b64)
        os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as tmp:
            tmp.write(tar_bytes)
            tmp_path = tmp.name
        subprocess.run(
            ['tar', '-xzf', tmp_path, '-C', os.path.join(BASE_DIR, 'data')],
            check=True,
        )
        os.unlink(tmp_path)
        print("[OK] GARMIN_TOKENS 환경변수에서 토큰 복원 완료 (tar.gz)")
        return True
    except Exception as e:
        print(f"[WARN] GARMIN_TOKENS 복원 실패: {e}")
        return False


def _update_tokens_secret():
    """갱신된 토큰을 GitHub Secret에 자동 업데이트 (GitHub Actions 환경에서만)"""
    import subprocess
    if not os.environ.get('GITHUB_ACTIONS'):
        return  # 로컬 실행 시 skip
    try:
        gh_token = os.environ.get('GH_TOKEN', '')
        if not gh_token:
            print("[SKIP] GH_TOKEN 없음 — Secret 자동 업데이트 불가")
            return
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run(
            ['tar', '-czf', tmp_path, '-C', os.path.join(BASE_DIR, 'data'), 'garmin_tokens'],
            check=True,
        )
        with open(tmp_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        os.unlink(tmp_path)
        result = subprocess.run(
            ['gh', 'secret', 'set', 'GARMIN_TOKENS',
             '-R', os.environ.get('GITHUB_REPOSITORY', 'sywoolab/sy-workspace'),
             '--body', b64],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, 'GH_TOKEN': gh_token},
        )
        if result.returncode == 0:
            print("[OK] GARMIN_TOKENS Secret 자동 갱신 완료")
        else:
            print(f"[WARN] Secret 갱신 실패: {result.stderr[:200]}")
    except Exception as e:
        print(f"[WARN] Secret 자동 갱신 중 오류: {e}")


SYNC_STATE_FILE = os.path.join(BASE_DIR, 'data', 'sync_state.json')


def _is_rate_limited(err):
    """429 계열 에러인지 판별"""
    s = str(err)
    return '429' in s or 'Too Many Requests' in s or 'Rate limit' in s


def _load_sync_state():
    """동기화 상태 파일 로드 (마지막 성공일, 연속 실패 횟수)"""
    try:
        with open(SYNC_STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_sync_state(state):
    os.makedirs(os.path.dirname(SYNC_STATE_FILE), exist_ok=True)
    with open(SYNC_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def login_garmin():
    """가민 커넥트 로그인 (토큰 캐시 → 비밀번호, 429 시 지수 백오프 재시도)

    재시도 전략: 30초 → 60초 → 120초 (최대 3회)
    전부 실패 시 연속 실패 카운트 기록. 알림은 sync()에서 판단.
    """
    import time

    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        print("[ERROR] GARMIN_EMAIL / GARMIN_PASSWORD 환경변수 필요")
        sys.exit(1)

    _restore_tokens_from_env()

    api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    os.makedirs(TOKEN_DIR, exist_ok=True)

    # 시도 순서: [(방법, 설명), ...]
    attempts = [
        ('cache', '토큰 캐시'),
        ('password', '비밀번호'),
    ]
    max_retries = 3
    base_delay = 30  # 초

    for i, (method, desc) in enumerate(attempts):
        # cache→password 전환 시 쿨다운 (이전 방식에서 429였으면 잠시 대기)
        if i > 0:
            print(f"[INFO] {desc} 전환 전 {base_delay}초 쿨다운")
            time.sleep(base_delay)

        for retry in range(max_retries):
            try:
                if method == 'cache':
                    api.login(TOKEN_DIR)
                else:
                    api.login()
                    api.garth.dump(TOKEN_DIR)
                    _update_tokens_secret()
                print(f"[OK] 가민 {desc} 로그인 성공" + (f" (재시도 {retry}회)" if retry else ""))
                return api
            except Exception as e:
                if _is_rate_limited(e) and retry < max_retries - 1:
                    delay = base_delay * (2 ** retry)
                    print(f"[RATE_LIMIT] {desc} 시도 {retry+1}/{max_retries} 실패 — {delay}초 대기")
                    time.sleep(delay)
                    continue
                elif _is_rate_limited(e):
                    print(f"[RATE_LIMIT] {desc} {max_retries}회 재시도 모두 실패")
                    break  # 다음 method로
                else:
                    print(f"[WARN] {desc} 로그인 실패: {e}")
                    break  # 다음 method로

    # 모든 시도 실패
    return None


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


def fetch_laps(api, activity_id):
    """활동의 랩/스플릿 데이터 가져오기"""
    try:
        splits = api.get_activity_splits(activity_id)
        laps = splits.get('lapDTOs', [])
        return laps
    except Exception:
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

    # 활동 시작 시각 → 날짜 (KST) + 시작 시간(HH:MM)
    start_local = activity.get('startTimeLocal', '')
    if start_local:
        date_key = start_local[:10]  # 'YYYY-MM-DD'
    else:
        date_key = TODAY

    # 시작 시각 (HH:MM) 추출
    start_time = None
    if start_local and len(start_local) >= 16:
        start_time = start_local[11:16]  # 'HH:MM'

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
        'start_time': start_time,
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


def parse_laps(laps, wtype):
    """랩 데이터를 간결한 형태로 파싱"""
    result = []
    for lap in laps:
        dist = lap.get('distance', 0)
        dur = lap.get('duration', 0)
        if dist <= 0 or dur <= 0:
            continue

        entry = {
            'distance': round(dist),
            'duration': round(dur),
            'avg_hr': round(lap.get('averageHR', 0)) if lap.get('averageHR') else None,
            'max_hr': round(lap.get('maxHR', 0)) if lap.get('maxHR') else None,
        }

        if wtype == 'run':
            pace = dur / (dist / 1000) if dist > 0 else 0
            entry['pace_sec'] = round(pace)
            entry['pace_str'] = seconds_to_pace(pace)
        elif wtype == 'bike':
            speed = (dist / 1000) / (dur / 3600) if dur > 0 else 0
            entry['speed_kmh'] = round(speed, 1)

        result.append(entry)
    return result


def analyze_splits(laps, wtype, vdot=37):
    """구간별 페이스 분석 → 훈련 방식 자동 판정 + 피드백"""
    if not laps or len(laps) < 2:
        return []

    import statistics
    feedback = []

    if wtype == 'run':
        paces = [l['pace_sec'] for l in laps if l.get('pace_sec')]
        hrs = [l['avg_hr'] for l in laps if l.get('avg_hr')]
        if len(paces) < 2:
            return []

        avg_pace = sum(paces) / len(paces)
        best = min(paces)
        worst = max(paces)

        # 1. 훈련 방식 자동 판정
        # 빌드업: 구간마다 점진적으로 빨라짐 (연속 3개 이상 가속)
        accel_count = sum(1 for i in range(1, len(paces)) if paces[i] < paces[i-1] - 3)
        decel_count = sum(1 for i in range(1, len(paces)) if paces[i] > paces[i-1] + 3)
        pace_range = worst - best

        if accel_count >= len(paces) * 0.6 and pace_range > 30:
            run_type = "빌드업"
            feedback.append(f"🔥 빌드업 러닝 감지 — {seconds_to_pace(worst)} → {seconds_to_pace(best)}/km")
            feedback.append(f"  워밍업 → 점진 가속, 유산소+역치 복합 자극 효과")
            # 빌드업은 편차가 큰 게 정상 → 편차 경고 하지 않음
        elif pace_range < 15 and len(paces) >= 3:
            run_type = "이븐"
            stdev = statistics.stdev(paces) if len(paces) >= 3 else 0
            feedback.append(f"✅ 이븐 페이스 — 편차 {stdev:.0f}초, 일관적")
        else:
            # 전반/후반 비교
            first_half = paces[:len(paces)//2]
            second_half = paces[len(paces)//2:]
            first_avg = sum(first_half) / len(first_half)
            second_avg = sum(second_half) / len(second_half)
            diff = first_avg - second_avg

            if diff > 10:
                run_type = "네거티브"
                feedback.append(f"✅ 네거티브 스플릿 — 후반 {diff:.0f}초/km 빠름 (이상적)")
            elif diff < -10:
                run_type = "포지티브"
                feedback.append(f"⚠️ 포지티브 스플릿 — 후반 {abs(diff):.0f}초/km 느려짐 (초반 오버페이스)")
            else:
                run_type = "이븐"
                feedback.append(f"👍 이븐 스플릿 — 페이스 안정적")

            # 페이스 편차 (빌드업이 아닌 경우만)
            if len(paces) >= 3:
                stdev = statistics.stdev(paces)
                if stdev > 20:
                    feedback.append(f"⚠️ 페이스 편차 {stdev:.0f}초 — 일정 페이스 연습 필요")

        # 2. 존별 구간 분포
        vdot_zones = {
            35: {'easy': 394, 'tempo': 347},
            36: {'easy': 385, 'tempo': 339},
            37: {'easy': 376, 'tempo': 331},
            38: {'easy': 367, 'tempo': 323},
            39: {'easy': 359, 'tempo': 316},
        }
        z = vdot_zones.get(vdot, vdot_zones[37])

        easy_laps = [i+1 for i, p in enumerate(paces) if p >= z['easy']]
        moderate_laps = [i+1 for i, p in enumerate(paces) if z['tempo'] + 10 <= p < z['easy']]
        tempo_laps = [i+1 for i, p in enumerate(paces) if p < z['tempo'] + 10]

        zone_parts = []
        if easy_laps:
            zone_parts.append(f"Easy {len(easy_laps)}개")
        if moderate_laps:
            zone_parts.append(f"Moderate {len(moderate_laps)}개")
        if tempo_laps:
            zone_parts.append(f"Tempo+ {len(tempo_laps)}개")
        feedback.append(f"  존 분포: {' / '.join(zone_parts)}")

        # 3. 심박 추이 (있으면)
        if hrs and len(hrs) >= 2:
            hr_first = hrs[0]
            hr_last = hrs[-1]
            hr_drift = hr_last - hr_first
            if hr_drift > 20:
                feedback.append(f"⚠️ 심박 드리프트 +{hr_drift}bpm (Lap1 {hr_first} → 마지막 {hr_last}) — 피로 누적")
            elif hr_drift > 10:
                feedback.append(f"💡 심박 상승 +{hr_drift}bpm — 빌드업/가속에 의한 정상 반응")

        # 4. 구간 페이스 요약
        lap_strs = [f"{seconds_to_pace(p)}" for p in paces]
        feedback.append(f"  구간: {' → '.join(lap_strs)}")

    elif wtype == 'bike':
        speeds = [l['speed_kmh'] for l in laps if l.get('speed_kmh')]
        if len(speeds) >= 2:
            feedback.append(f"  구간 평속: {min(speeds):.0f}~{max(speeds):.0f}km/h")

    return feedback


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

    # laps 데이터가 있으면 metrics에 포함 (빌드업 판정 등에 사용)
    if parsed.get('laps'):
        metrics['laps'] = parsed['laps']

    # 수영 장비 사용 추정: 페이스/Swolf가 최근 평균 대비 확연히 좋으면 장비 가능성
    if wtype == 'swim':
        equipment_guess = detect_swim_equipment(parsed, workout_log_data)
        if equipment_guess:
            metrics['swim_equipment'] = 'fins'
            note = (note + " | 장비 추정").strip(' | ')

    entry = {
        'planned': '',  # 나중에 스케줄과 매칭
        'done': True,
        'actual': actual,
        'metrics': metrics,
        'training_zone': zone,
        'note': note,
        'garmin_id': parsed.get('garmin_id'),
    }

    # 운동 시작 시각 저장
    if parsed.get('start_time'):
        entry['start_time'] = parsed['start_time']

    return entry


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


def _count_types_from_entry(entry):
    """하나의 workout_log 엔트리에서 모든 운동 종목을 집계.
    all_metrics가 있으면 각 종목별로 카운트, 없으면 metrics.type 하나만."""
    counts = {'run': 0, 'swim': 0, 'bike': 0, 'brick': 0}
    run_km = 0.0
    all_m = entry.get('all_metrics', [])
    if all_m:
        seen_types = set()
        for m in all_m:
            t = m.get('type', '')
            if t in counts and t not in seen_types:
                counts[t] += 1
                seen_types.add(t)
            if t == 'run':
                run_km += m.get('distance_km', 0)
    else:
        t = entry.get('metrics', {}).get('type', '')
        if t in counts:
            counts[t] += 1
        if t == 'run':
            run_km += entry.get('metrics', {}).get('distance_km', 0)
    return counts, run_km


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
        counts, km = _count_types_from_entry(entry)
        run_count += counts['run']
        swim_count += counts['swim']
        bike_count += counts['bike']
        total_run_km += km

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
# 운동 피드백 생성 (종합 판정 + 주간 경과)
# ============================================================
VDOT_ZONES = {
    35: {'easy_lo': 394, 'easy_hi': 437, 'tempo': 347},
    36: {'easy_lo': 385, 'easy_hi': 427, 'tempo': 339},
    37: {'easy_lo': 376, 'easy_hi': 417, 'tempo': 331},
    38: {'easy_lo': 367, 'easy_hi': 407, 'tempo': 323},
    39: {'easy_lo': 359, 'easy_hi': 398, 'tempo': 316},
}

# Phase별 주간 목표
PHASE_WEEKLY_TARGETS = {
    1: {'swim': 4, 'run': 3, 'run_km': (20, 23), 'bike': 1, 'desc': '베이스 — Easy 90% / Tempo 10%'},
    2: {'swim': 3, 'run': 3, 'run_km': (21, 25), 'bike': 2, 'desc': '빌드 — Easy 80% / Tempo 15% / Interval 5%'},
    3: {'swim': 2, 'run': 2, 'run_km': (10, 10), 'bike': 1, 'desc': '테이퍼 — 볼륨 감소, 강도 유지'},
}


def generate_workout_feedback(parsed, schedule_data, workout_log=None):
    """종합 운동 판정: 계획 준수 + 강도 적절성 + 볼륨 + 추이 + 주간 경과"""
    if workout_log is None:
        workout_log = {}

    wtype = parsed['type']
    vdot = schedule_data.get('current_vdot', 37)
    z = VDOT_ZONES.get(vdot, VDOT_ZONES[37])
    date_str = parsed.get('date', TODAY)

    lines = []
    score = 0  # 100점 만점
    max_score = 0

    # ─── 1. 계획 준수 ───
    max_score += 25
    try:
        import sys as _sys
        _scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from workout_alert import get_schedule_for_date, get_phase, WEEK_NAMES, PHASE_GOALS
        dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=KST)
        planned, detail = get_schedule_for_date(dt)
        phase_num, phase_name = get_phase(dt)

        type_name = {'run': '러닝', 'swim': '수영', 'bike': '자전거', 'brick': '브릭'}
        actual_name = type_name.get(wtype, wtype)

        if wtype == 'run' and '러닝' in planned:
            lines.append(f"✅ 계획 준수 — {planned} 예정 → {actual_name} 완료")
            score += 25
        elif wtype == 'swim' and '수영' in planned:
            lines.append(f"✅ 계획 준수 — {planned} 예정 → {actual_name} 완료")
            score += 25
        elif wtype == 'bike' and ('자전거' in planned or '브릭' in planned):
            lines.append(f"✅ 계획 준수 — {planned} 예정 → {actual_name} 완료")
            score += 25
        elif '휴식' in planned:
            lines.append(f"💡 휴식일인데 운동 — 컨디션 괜찮으면 OK")
            score += 15
        else:
            lines.append(f"🔄 계획 변경 — {planned} 예정 → {actual_name} 수행")
            score += 15
    except Exception:
        planned = ''
        phase_num = 1

    # ─── 2. 강도 적절성 ───
    max_score += 25
    laps = parsed.get('laps', [])
    is_buildup = False
    if laps and len(laps) >= 3:
        lap_paces = [l['pace_sec'] for l in laps if l.get('pace_sec')]
        if lap_paces:
            accel = sum(1 for i in range(1, len(lap_paces)) if lap_paces[i] < lap_paces[i-1] - 3)
            is_buildup = accel >= len(lap_paces) * 0.6 and (max(lap_paces) - min(lap_paces)) > 30

    if wtype == 'run':
        pace = parsed.get('pace_sec', 0)
        # Easy Day 판정: Phase 1에서 Easy 비율 90%, 주 3회 중 2회는 Easy
        is_easy_day = 'Easy' in (detail or '') or 'Long' in (detail or '') or '+' in (planned or '')
        is_tempo_day = '템포' in (planned or '') or 'Tempo' in (detail or '')

        if is_buildup:
            lines.append(f"👍 강도 — 빌드업 (유산소+역치 복합 자극)")
            score += 20
        elif is_easy_day and pace >= z['easy_lo']:
            lines.append(f"✅ 강도 — Easy Day에 Easy 페이스 ({seconds_to_pace(pace)}/km) 정확")
            score += 25
        elif is_easy_day and pace < z['easy_lo']:
            lines.append(f"⚠️ 강도 — Easy Day인데 {seconds_to_pace(pace)}/km (6:16+ 권장)")
            score += 10
        elif is_tempo_day and z['tempo'] - 5 <= pace <= z['tempo'] + 10:
            lines.append(f"✅ 강도 — Tempo Day에 Tempo 페이스 ({seconds_to_pace(pace)}/km) 정확")
            score += 25
        elif pace >= z['easy_lo']:
            lines.append(f"✅ 강도 — Easy 존 ({seconds_to_pace(pace)}/km)")
            score += 22
        elif pace >= z['tempo'] + 10:
            lines.append(f"⚠️ 강도 — Dead Zone ({seconds_to_pace(pace)}/km), Easy 또는 Tempo로 명확히")
            score += 10
        else:
            lines.append(f"✅ 강도 — Tempo+ ({seconds_to_pace(pace)}/km)")
            score += 20
    elif wtype == 'swim':
        lines.append(f"✅ 강도 — 수영 (코치/수업 기반)")
        score += 22
    elif wtype == 'bike':
        lines.append(f"✅ 강도 — 자전거")
        score += 22

    # ─── 3. 볼륨 ───
    max_score += 25
    if wtype == 'run':
        dist = parsed.get('distance_km', 0)
        # 목표 거리 (계획에서 추출)
        import re
        target_dist = 6  # 기본
        if detail:
            m = re.search(r'(\d+)[~\-](\d+)km', detail)
            if m:
                target_dist = (int(m.group(1)) + int(m.group(2))) / 2
            else:
                m2 = re.search(r'(\d+)km', detail)
                if m2:
                    target_dist = int(m2.group(1))
        pct = round(dist / target_dist * 100) if target_dist > 0 else 0
        if pct >= 90:
            lines.append(f"✅ 볼륨 — {dist}km / 목표 {target_dist:.0f}km ({pct}%)")
            score += 25
        elif pct >= 70:
            lines.append(f"👍 볼륨 — {dist}km / 목표 {target_dist:.0f}km ({pct}%)")
            score += 18
        else:
            lines.append(f"⚠️ 볼륨 — {dist}km / 목표 {target_dist:.0f}km ({pct}%) 부족")
            score += 10
    elif wtype == 'swim':
        dist = parsed.get('distance_m', 0)
        if dist >= 1500:
            lines.append(f"✅ 볼륨 — {dist}m (대회 거리!)")
            score += 25
        elif dist >= 1000:
            lines.append(f"👍 볼륨 — {dist}m")
            score += 20
        elif dist >= 500:
            lines.append(f"👌 볼륨 — {dist}m (기본)")
            score += 15
        else:
            lines.append(f"⚠️ 볼륨 — {dist}m (1km+ 권장)")
            score += 8
    elif wtype == 'bike':
        dur = parsed.get('duration_sec', 0) / 60
        if dur >= 60:
            lines.append(f"✅ 볼륨 — {dur:.0f}분")
            score += 25
        elif dur >= 30:
            lines.append(f"👍 볼륨 — {dur:.0f}분")
            score += 18
        else:
            lines.append(f"⚠️ 볼륨 — {dur:.0f}분 (30분+ 권장)")
            score += 10

    # ─── 4. 추이 (직전 같은 종목 대비) ───
    max_score += 25
    prev_entries = []
    for dk in sorted(workout_log.keys(), reverse=True):
        if dk >= date_str:
            continue
        entry = workout_log.get(dk, {})
        if entry.get('done') and entry.get('metrics', {}).get('type') == wtype:
            prev_entries.append((dk, entry))
            if len(prev_entries) >= 1:
                break

    if prev_entries:
        prev_date, prev = prev_entries[0]
        pm = prev.get('metrics', {})
        if wtype == 'run':
            prev_pace_str = pm.get('pace_per_km', '')
            if prev_pace_str:
                pp = prev_pace_str.split(':')
                prev_pace = int(pp[0]) * 60 + int(pp[1]) if len(pp) == 2 else 0
                curr_pace = parsed.get('pace_sec', 0)
                diff = prev_pace - curr_pace
                prev_dist = pm.get('distance_km', 0)
                if is_buildup:
                    lines.append(f"✅ 추이 — 빌드업으로 최고 {seconds_to_pace(min(lap_paces))}/km 도달 (직전 {prev_date[-5:]} 평균 {prev_pace_str})")
                    score += 22
                elif diff > 5:
                    lines.append(f"✅ 추이 — {prev_date[-5:]} {prev_pace_str} → 오늘 {seconds_to_pace(curr_pace)}/km (향상)")
                    score += 25
                elif diff > -5:
                    lines.append(f"👍 추이 — {prev_date[-5:]} {prev_pace_str} → 오늘 {seconds_to_pace(curr_pace)}/km (유지)")
                    score += 20
                else:
                    lines.append(f"💡 추이 — {prev_date[-5:]} {prev_pace_str} → 오늘 {seconds_to_pace(curr_pace)}/km (느려짐, 컨디션 체크)")
                    score += 12
            else:
                score += 15
        elif wtype == 'swim':
            prev_swolf = pm.get('swolf')
            curr_swolf = parsed.get('swolf')
            if prev_swolf and curr_swolf:
                diff = prev_swolf - curr_swolf
                if diff > 0:
                    lines.append(f"✅ 추이 — Swolf {prev_swolf} → {curr_swolf} (향상)")
                    score += 25
                elif diff == 0:
                    lines.append(f"👍 추이 — Swolf {curr_swolf} (유지)")
                    score += 20
                else:
                    lines.append(f"💡 추이 — Swolf {prev_swolf} → {curr_swolf}")
                    score += 15
            else:
                score += 15
        else:
            score += 15
    else:
        lines.append(f"💡 추이 — 첫 기록 (비교 데이터 없음)")
        score += 15

    # ─── 종합 판정 ───
    pct_score = round(score / max_score * 100) if max_score > 0 else 0
    if pct_score >= 85:
        grade = "🟢 GREAT"
    elif pct_score >= 65:
        grade = "👍 GOOD"
    elif pct_score >= 45:
        grade = "👌 OK"
    else:
        grade = "⚠️ 부족"

    header = f"📊 오늘 운동 판정: {grade}"

    # ─── 구간 분석 (있으면) ───
    split_lines = []
    if laps:
        split_fb = analyze_splits(laps, wtype, vdot)
        if split_fb:
            split_lines = split_fb

    # ─── 조합 ───
    result = [header, ""]
    result.extend(lines)
    if split_lines:
        result.append("")
        result.extend(split_lines)

    return result


def generate_weekly_progress(workout_log, schedule_data):
    """이번 주 경과 피드백: 주간 목표 대비 진행률"""
    try:
        import sys as _sys
        _scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from workout_alert import get_phase, WEEK_NAMES
    except Exception:
        return []

    today = NOW.date()
    monday = today - timedelta(days=today.weekday())
    week_num = (monday - TRAIN_START.date()).days // 7
    days_passed = today.weekday() + 1  # 1=월 ~ 7=일
    remaining = 7 - days_passed

    phase_num, phase_name = get_phase(NOW)
    targets = PHASE_WEEKLY_TARGETS.get(phase_num, PHASE_WEEKLY_TARGETS[1])
    week_name = WEEK_NAMES.get(week_num, f"Week {week_num}")

    # 주간 실적 집계
    run_count = 0
    swim_count = 0
    bike_count = 0
    run_km = 0.0
    easy_runs = 0
    hard_runs = 0

    for d in range(days_passed):
        dt = monday + timedelta(days=d)
        key = dt.strftime('%Y-%m-%d')
        entry = workout_log.get(key)
        if not entry or not entry.get('done'):
            continue
        counts, km = _count_types_from_entry(entry)
        run_count += counts['run']
        swim_count += counts['swim']
        bike_count += counts['bike']
        run_km += km
        # 80/20 분류 (러닝만)
        if counts['run'] > 0:
            zone = entry.get('training_zone', 'moderate')
            if zone == 'easy':
                easy_runs += 1
            else:
                hard_runs += 1

    lines = []
    lines.append(f"📅 {week_name} 경과 ({days_passed}/7일)")
    lines.append(f"  목표: {targets['desc']}")

    # 종목별 진행률
    run_t = targets['run']
    swim_t = targets['swim']
    bike_t = targets['bike']
    km_lo, km_hi = targets['run_km']

    run_bar = "●" * run_count + "○" * max(0, run_t - run_count)
    swim_bar = "●" * swim_count + "○" * max(0, swim_t - swim_count)
    bike_bar = "●" * bike_count + "○" * max(0, bike_t - bike_count)

    run_icon = "✅" if run_count >= run_t else ("🟡" if run_count >= run_t - 1 else "🔴")
    swim_icon = "✅" if swim_count >= swim_t else "🟡"
    bike_icon = "✅" if bike_count >= bike_t else "🟡"

    lines.append(f"  러닝 {run_bar} {run_count}/{run_t}회 ({run_km:.1f}km/{km_lo}~{km_hi}km) {run_icon}")
    lines.append(f"  수영 {swim_bar} {swim_count}/{swim_t}회 {swim_icon}")
    lines.append(f"  자전거 {bike_bar} {bike_count}/{bike_t}회 {bike_icon}")

    # 80/20 체크
    if run_count >= 2:
        total = easy_runs + hard_runs
        easy_pct = round(easy_runs / total * 100) if total > 0 else 0
        if easy_pct >= 60:
            lines.append(f"  80/20: Easy {easy_runs}회 / 고강도 {hard_runs}회 ✅")
        else:
            lines.append(f"  80/20: Easy {easy_runs}회 / 고강도 {hard_runs}회 ⚠️ Easy 비율 부족")

    # 남은 일정 가이드
    if remaining > 0:
        todos = []
        run_need = max(0, run_t - run_count)
        if run_need > 0:
            todos.append(f"러닝 {run_need}회")
        if bike_count < bike_t:
            todos.append(f"자전거 {bike_t - bike_count}회")
        if todos:
            lines.append(f"  📌 남은 {remaining}일: {', '.join(todos)} 필요")
        else:
            lines.append(f"  ✅ 이번 주 핵심 목표 달성!")

    return lines


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
            counts, km = _count_types_from_entry(entry)
            run_count += counts['run']
            swim_count += counts['swim']
            bike_count += counts['bike']
            run_km += km
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
# 온트랙 판정
# ============================================================
def format_on_track(workout_log, schedule_data, plan_adjustments):
    """전체 훈련이 목표 달성 궤도에 있는지 종합 판정"""
    lines = []
    last_analysis = schedule_data.get('last_analysis', {})
    est = last_analysis.get('estimated_finish', '?')
    status = last_analysis.get('status', '')
    vdot = schedule_data.get('current_vdot', 37)
    brick_count = schedule_data.get('brick_count', 0)
    ow_count = schedule_data.get('ow_count', 0)

    # 이번 주 러닝 현황
    today = NOW.date()
    monday = today - timedelta(days=today.weekday())
    days_passed = today.weekday()
    remaining = 6 - days_passed

    run_count = 0
    run_km = 0.0
    for d in range(days_passed + 1):
        dt = monday + timedelta(days=d)
        key = dt.strftime('%Y-%m-%d')
        entry = workout_log.get(key)
        if entry and entry.get('done'):
            counts, km = _count_types_from_entry(entry)
            run_count += counts['run']
            run_km += km

    run_target = 3
    run_deficit = max(0, run_target - run_count)

    # 종합 판정
    issues = []
    if run_deficit > remaining:
        issues.append(f"러닝 {run_count}/{run_target}회 — 이번 주 목표 달성 불가")
    elif run_deficit > 0 and days_passed >= 3:
        issues.append(f"러닝 {run_count}/{run_target}회 — 남은 {remaining}일 내 {run_deficit}회 필요")
    if vdot < 39 and DAYS_LEFT < 30:
        issues.append(f"VDOT {vdot} → 목표 39 (D-{DAYS_LEFT}, 시간 촉박)")
    if brick_count == 0 and DAYS_LEFT < 45:
        issues.append(f"브릭 0회 — 빨리 시작 필요")
    if ow_count == 0 and DAYS_LEFT < 30:
        issues.append(f"OW 0회 — 대회 전 최소 2~3회 필요")

    # 판정 결과
    if status == 'green' and not issues:
        verdict = "🟢 ON TRACK"
        comment = "현재 페이스 유지하면 목표 달성 가능"
    elif status == 'red' or len(issues) >= 3:
        verdict = "🔴 OFF TRACK"
        comment = "스케줄 강화 필요"
    else:
        verdict = "🟡 CAUTION"
        comment = "개선 포인트 있음"

    lines.append(f"{'─' * 20}")
    lines.append(f"{verdict} | D-{DAYS_LEFT} | 예상 {est}")
    lines.append(f"  {comment}")

    # 핵심 지표
    vdot_icon = "✅" if vdot >= 39 else ("⚠️" if vdot >= 37 else "🔴")
    brick_icon = "✅" if brick_count >= 6 else ("⚠️" if brick_count >= 3 else "🔴")
    ow_icon = "✅" if ow_count >= 3 else ("⚠️" if ow_count >= 1 else "🔴")
    lines.append(f"  {vdot_icon} VDOT {vdot}→39 | {brick_icon} 브릭 {brick_count}/6 | {ow_icon} OW {ow_count}/3")

    # 이번 주 핵심 할 일
    if run_deficit > 0:
        lines.append(f"  📌 이번 주: 러닝 {run_deficit}회 더 채우기")
    elif run_count >= run_target:
        lines.append(f"  ✅ 이번 주 러닝 목표 달성!")

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

        # 운동 종합 판정
        feedback = generate_workout_feedback(p, schedule_data, workout_log)
        if feedback:
            lines.append("")
            for fb in feedback:
                lines.append(f"  {fb}")
        lines.append("")

    # 주간 경과 피드백
    try:
        weekly = generate_weekly_progress(workout_log, schedule_data)
        if weekly:
            lines.extend(weekly)
            lines.append("")
    except Exception as e:
        print(f"  [WARN] 주간 경과 포매팅 실패: {e}")

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

    # 온트랙 판정
    on_track = format_on_track(workout_log, schedule_data, plan_adjustments)
    lines.append(on_track)

    return "\n".join(lines)


# ============================================================
# 브릭 자동 감지
# ============================================================
def _detect_brick(entry, parsed_activities):
    """같은 날 자전거→러닝이 30분 이내면 브릭 태깅.
    parsed_activities: 해당 날짜에 파싱된 활동 리스트 (start_time 포함)
    """
    all_m = entry.get('all_metrics', [])

    # 해당 날짜의 파싱된 활동에서 start_time 가져오기
    bike_times = []
    run_times = []

    for p in parsed_activities:
        st = p.get('start_time')
        dur = p.get('duration_sec', 0)
        if not st:
            continue
        try:
            t_start = datetime.strptime(st, '%H:%M')
        except (ValueError, TypeError):
            continue

        if p['type'] == 'bike':
            t_end = t_start + timedelta(seconds=dur)
            bike_times.append((t_start, t_end))
        elif p['type'] == 'run':
            run_times.append(t_start)

    # start_time이 없으면 all_metrics의 순서로 대략 추정 (타입 순서만 체크)
    if not bike_times or not run_times:
        # all_metrics 순서 기반 fallback: 자전거→러닝 순서면 브릭 가능성
        types_seq = [m.get('type') for m in all_m]
        has_bike = 'bike' in types_seq
        has_run = 'run' in types_seq
        if has_bike and has_run:
            bike_idx = types_seq.index('bike')
            run_indices = [i for i, t in enumerate(types_seq) if t == 'run']
            for ri in run_indices:
                if ri > bike_idx:
                    # 순서상 자전거→러닝이지만 시간 확인 불가 → 플래그만 표시
                    entry['is_brick'] = True
                    if 'note' in entry:
                        if '브릭' not in entry.get('note', '') and '브릭' not in entry.get('planned', ''):
                            entry['note'] = entry['note'] + ' | 브릭(순서 추정)'
                    return
        return

    # 시간 기반 브릭 판정: 자전거 종료 → 러닝 시작이 30분 이내
    for bike_start, bike_end in bike_times:
        for run_start in run_times:
            gap_min = (run_start - bike_end).total_seconds() / 60
            if 0 <= gap_min <= 30:
                entry['is_brick'] = True
                gap_str = f"{int(gap_min)}분"
                if '브릭' not in entry.get('note', '') and '브릭' not in entry.get('planned', ''):
                    entry['note'] = (entry.get('note', '') + f' | 브릭(T2 {gap_str})').strip(' | ')
                return


# ============================================================
# 메인 동기화 로직
# ============================================================
def sync():
    print(f"[{NOW.strftime('%Y-%m-%d %H:%M')}] 가민 동기화 시작")

    # 동기화 상태 로드
    sync_state = _load_sync_state()

    # 1. 가민 로그인 (재시도 포함)
    api = login_garmin()

    if api is None:
        # 로그인 실패 — 연속 실패 카운트 증가
        fail_count = sync_state.get('consecutive_failures', 0) + 1
        sync_state['consecutive_failures'] = fail_count
        sync_state['last_failure'] = NOW.strftime('%Y-%m-%d %H:%M')
        _save_sync_state(sync_state)

        # 하루 전체 실패(4회 연속) 시에만 알림 — 불필요한 알림 방지
        if fail_count >= 4:
            msg = (f"❌ 가민 동기화 {fail_count}회 연속 실패\n"
                   f"마지막 성공: {sync_state.get('last_success', '없음')}\n"
                   f"로컬 수동 sync 필요할 수 있습니다.")
            send_telegram(msg)
            # 알림 후 카운터 리셋 (다음 4회 실패 때 다시 알림)
            sync_state['consecutive_failures'] = 0
            _save_sync_state(sync_state)

        print(f"  로그인 실패 (연속 {fail_count}회)")
        return False

    # 로그인 성공 — 실패 카운터 리셋
    sync_state['consecutive_failures'] = 0

    # 2. 기존 데이터 로드
    workout_log = load_json(LOG_FILE)
    schedule_data = load_json(SCHEDULE_FILE)

    # 기존 garmin_id 목록 (중복 방지) — 단일 garmin_id + garmin_ids 리스트 + all_metrics 모두 체크
    existing_ids = set()
    for entry in workout_log.values():
        gid = entry.get('garmin_id')
        if gid:
            existing_ids.add(gid)
        for gid in entry.get('garmin_ids', []):
            existing_ids.add(gid)
        for am in entry.get('all_metrics', []):
            amid = am.get('garmin_id')
            if amid:
                existing_ids.add(amid)

    # 3. 조회 범위 결정: 마지막 성공일 ~ 오늘 (놓친 날짜 소급)
    last_success = sync_state.get('last_success_date')
    if last_success and last_success < YESTERDAY:
        start_date = last_success
        print(f"  소급 동기화: {start_date} ~ {TODAY} (마지막 성공: {last_success})")
    else:
        start_date = YESTERDAY

    activities = fetch_activities(api, start_date, TODAY)

    # 4. 새 활동 필터링 & 파싱
    new_activities = []
    for act in activities:
        act_id = act.get('activityId')
        if act_id in existing_ids:
            continue

        parsed = parse_activity(act)
        if parsed['type'] in ('run', 'swim', 'bike', 'brick', 'strength'):
            # 10분 미만 활동 필터 (가민 자동 감지 잡음 제거)
            duration = parsed.get('duration_sec', 0) or 0
            if duration < 600 and parsed['type'] != 'brick':
                print(f"  [SKIP] {parsed['type']} {duration}초 — 10분 미만 무시")
                continue
            # 랩/스플릿 데이터 추가
            if parsed['type'] in ('run', 'bike'):
                laps = fetch_laps(api, act_id)
                parsed['laps'] = parse_laps(laps, parsed['type'])
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
            new_gid = entry.get('garmin_id')

            # 같은 날 이미 기록이 있으면 → 기존 것에 추가 (멀티 스포츠)
            if date_key in workout_log:
                existing = workout_log[date_key]

                # garmin_ids 리스트 관리 (기존 단일 garmin_id → 리스트 마이그레이션)
                if 'garmin_ids' not in existing:
                    old_gid = existing.get('garmin_id')
                    existing['garmin_ids'] = [old_gid] if old_gid else []

                # 이미 이 garmin_id가 기록되어 있으면 skip (중복 머징 방지)
                if new_gid and new_gid in existing['garmin_ids']:
                    print(f"  [SKIP] garmin_id {new_gid} 이미 {date_key}에 존재 — 머징 생략")
                    continue

                # 새 garmin_id 추가
                if new_gid:
                    existing['garmin_ids'].append(new_gid)
                existing['garmin_id'] = new_gid
                existing['done'] = True

                # 기존 기록의 actual에 추가
                existing['actual'] = existing.get('actual', '') + ' + ' + entry['actual']

                # all_metrics: 모든 운동의 metrics를 배열로 보존 (garmin_id 포함)
                if 'all_metrics' not in existing:
                    first_m = dict(existing['metrics'])
                    first_m['garmin_id'] = existing['garmin_ids'][0] if existing['garmin_ids'] else None
                    if existing.get('start_time'):
                        first_m['start_time'] = existing['start_time']
                    existing['all_metrics'] = [first_m]
                new_m = dict(entry['metrics'])
                new_m['garmin_id'] = new_gid
                if entry.get('start_time'):
                    new_m['start_time'] = entry['start_time']
                existing['all_metrics'].append(new_m)

                # 주 운동(러닝 우선) 기준으로 단일 metrics 유지 (호환성)
                if entry['metrics']['type'] == 'run' or existing['metrics']['type'] not in ('run',):
                    existing['metrics'] = entry['metrics']
                    existing['training_zone'] = entry['training_zone']
                if entry.get('note'):
                    existing['note'] = (existing.get('note', '') + ' | ' + entry['note']).strip(' | ')

                # start_time 보존 (가장 이른 시각)
                if entry.get('start_time'):
                    if not existing.get('start_time') or entry['start_time'] < existing['start_time']:
                        existing['start_time'] = entry['start_time']
            else:
                # 스케줄에서 planned 가져오기
                try:
                    planned = get_planned_workout(date_key, schedule_data)
                    entry['planned'] = planned
                except Exception:
                    entry['planned'] = ''
                # garmin_ids 리스트 초기화
                entry['garmin_ids'] = [new_gid] if new_gid else []
                workout_log[date_key] = entry

        # 브릭 자동 감지: 같은 날 자전거→러닝 30분 이내
        affected_dates = set(p['date'] for p in new_activities)
        for date_key in affected_dates:
            entry = workout_log.get(date_key)
            if not entry or not entry.get('all_metrics') or len(entry['all_metrics']) < 2:
                continue
            date_activities = [p for p in new_activities if p['date'] == date_key]
            _detect_brick(entry, date_activities)

        save_json(LOG_FILE, workout_log)
        print(f"  workout_log.json 업데이트 완료")

        # 7. 계획 조정 점검
        plan_adjustments = check_plan_adherence(workout_log, schedule_data)

        # 8. 텔레그램 알림
        msg = format_workout_message(new_activities, health, plan_adjustments, schedule_data, workout_log)
        ok = send_telegram(msg)
        print(f"  텔레그램 전송: {'성공' if ok else '실패'}")

        # 동기화 성공 기록
        sync_state['last_success'] = NOW.strftime('%Y-%m-%d %H:%M')
        sync_state['last_success_date'] = TODAY
        _save_sync_state(sync_state)

        return True  # 변경 있음
    else:
        # 새 활동 없어도 로그인 성공 = sync 정상
        sync_state['last_success'] = NOW.strftime('%Y-%m-%d %H:%M')
        sync_state['last_success_date'] = TODAY
        _save_sync_state(sync_state)

        print("  새 활동 없음 — 조용히 종료")
        return False  # 변경 없음


def resend_today():
    """오늘 기록된 운동을 다시 알림 전송 (가민 로그인 불필요, 저장된 데이터 사용)"""
    print(f"[{NOW.strftime('%Y-%m-%d %H:%M')}] 오늘 운동 재전송")

    workout_log = load_json(LOG_FILE)
    schedule_data = load_json(SCHEDULE_FILE)
    health_history = load_json(HEALTH_FILE)
    health = health_history.get(TODAY, {})

    # 오늘 또는 가장 최근 운동 기록 찾기
    target_date = TODAY
    today_entry = workout_log.get(TODAY)
    if not today_entry or not today_entry.get('done'):
        # 오늘 기록 없으면 가장 최근 완료 기록
        recent_dates = sorted(
            [d for d, e in workout_log.items() if e.get('done')],
            reverse=True
        )
        if not recent_dates:
            print("  운동 기록 없음")
            return
        target_date = recent_dates[0]
        today_entry = workout_log[target_date]
        print(f"  오늘 기록 없음 → 최근 기록 사용: {target_date}")
    if not health:
        health = health_history.get(target_date, {})

    import re

    def _build_parsed(m, note=''):
        """metrics dict에서 parsed activity 하나를 생성"""
        wtype = m.get('type', '')
        p = {
            'type': wtype,
            'date': target_date,
            'duration_sec': int(m.get('duration_min', 0) * 60) if m.get('duration_min') else 0,
            'moving_sec': int(m.get('moving_min', 0) * 60) if m.get('moving_min') else 0,
            'avg_hr': m.get('avg_hr'),
            'max_hr': m.get('max_hr'),
            'aerobic_te': None,
            'anaerobic_te': None,
            'training_load': None,
        }

        # TE/부하 파싱 from note
        ae_match = re.search(r'유산소 ([\d.]+)', note)
        an_match = re.search(r'무산소 ([\d.]+)', note)
        load_match = re.search(r'부하 (\d+)', note)
        if ae_match:
            p['aerobic_te'] = float(ae_match.group(1))
        if an_match:
            p['anaerobic_te'] = float(an_match.group(1))
        if load_match:
            p['training_load'] = int(load_match.group(1))

        if wtype == 'swim':
            pace_str = m.get('pace_per_100m', '0:00')
            parts = pace_str.split(':')
            pace_sec = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 0
            p.update({
                'distance_m': m.get('distance_m', 0),
                'pace_per_100m': pace_str,
                'pace_sec_100m': pace_sec,
                'swolf': m.get('swolf'),
                'total_strokes': m.get('strokes'),
            })
            if not p['duration_sec']:
                p['duration_sec'] = int(pace_sec * m.get('distance_m', 0) / 100)
        elif wtype == 'run':
            pace_str = m.get('pace_per_km', '0:00')
            parts = pace_str.split(':')
            pace_sec = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 0
            p.update({
                'distance_km': m.get('distance_km', 0),
                'pace_per_km': pace_str,
                'pace_sec': pace_sec,
            })
            if not p['duration_sec']:
                p['duration_sec'] = int(pace_sec * m.get('distance_km', 0))
        elif wtype == 'bike':
            p.update({
                'distance_km': m.get('distance_km'),
                'avg_speed_kmh': m.get('avg_speed_kmh'),
                'avg_power': m.get('avg_power'),
            })
            if not p['duration_sec']:
                p['duration_sec'] = int(m.get('duration_min', 0) * 60)
        return p

    note = today_entry.get('note', '')
    actual = today_entry.get('actual', '')
    parsed_list = []

    # 복수 운동 판별: actual에 "+"가 있으면 metrics가 리스트이거나 개별 type으로 분리
    metrics_raw = today_entry.get('metrics', {})
    if '+' in actual and isinstance(metrics_raw, list):
        # metrics가 리스트인 경우: 각각 parsed activity 생성
        for m in metrics_raw:
            parsed_list.append(_build_parsed(m, note))
    elif '+' in actual and isinstance(metrics_raw, dict):
        # metrics가 단일 dict이지만 actual에 "+"가 있는 경우
        # actual 문자열을 파싱하여 각 운동별 parsed activity를 생성
        parts = [p.strip() for p in actual.split('+')]
        type_keywords = {
            '러닝': 'run', '달리기': 'run',
            '수영': 'swim',
            '자전거': 'bike', '바이크': 'bike', '사이클': 'bike',
            '근력': 'strength',
        }
        for part in parts:
            sub_type = metrics_raw.get('type', '')
            for kw, t in type_keywords.items():
                if kw in part:
                    sub_type = t
                    break

            sub_metrics = dict(metrics_raw)
            sub_metrics['type'] = sub_type

            # actual 파트에서 거리/페이스 추출
            dist_km = re.search(r'([\d.]+)\s*km', part)
            dist_m = re.search(r'([\d.]+)\s*m(?!in)', part)
            pace_km = re.search(r'@\s*([\d]+:[\d]+)/km', part)
            pace_100m = re.search(r'@\s*([\d]+:[\d]+)/100m', part)
            speed_kmh = re.search(r'([\d.]+)\s*km/h', part)

            if sub_type == 'run' and dist_km:
                sub_metrics['distance_km'] = float(dist_km.group(1))
                if pace_km:
                    sub_metrics['pace_per_km'] = pace_km.group(1)
            elif sub_type == 'swim' and dist_m:
                sub_metrics['distance_m'] = float(dist_m.group(1))
                if pace_100m:
                    sub_metrics['pace_per_100m'] = pace_100m.group(1)
            elif sub_type == 'bike' and dist_km:
                sub_metrics['distance_km'] = float(dist_km.group(1))
                if speed_kmh:
                    sub_metrics['avg_speed_kmh'] = float(speed_kmh.group(1))

            parsed_list.append(_build_parsed(sub_metrics, note))
    else:
        # 단일 운동
        parsed_list.append(_build_parsed(metrics_raw, note))

    plan_adj = check_plan_adherence(workout_log, schedule_data)
    msg = format_workout_message(parsed_list, health, plan_adj, schedule_data, workout_log)
    ok = send_telegram(msg)
    print(f"  텔레그램 전송: {'성공' if ok else '실패'} ({len(parsed_list)}건)")


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'sync'
    try:
        if mode == 'resend':
            resend_today()
        else:
            changed = sync()
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        error_msg = f"❌ 가민 동기화 오류\n{traceback.format_exc()}"
        print(error_msg)
        send_telegram(error_msg[:500])
        sys.exit(1)
