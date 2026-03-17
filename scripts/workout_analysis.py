"""
운동 기록 분석 + 텔레그램 전송
- workout_log.json 기반으로 진척도 분석
- Phase 벤치마크 대비 갭 분석
- 스케줄 자동 조정 (workout_schedule.json)
- 2:50 목표 예상 완주시간 재계산
- 결과를 텔레그램으로 전송
"""

import os
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
RACE_DAY = datetime(2026, 5, 9, tzinfo=KST)
TRAIN_START = datetime(2026, 3, 16, tzinfo=KST)
DAYS_LEFT = (RACE_DAY.date() - NOW.date()).days

# 목표 스플릿 (분)
TARGET_SWIM_MIN = 33.0      # 1.5km OW
TARGET_T1_MIN = 2.5
TARGET_BIKE_MIN = 75.0      # 40km
TARGET_T2_MIN = 1.5
TARGET_RUN_MIN = 58.0       # 10km
TARGET_TOTAL_MIN = 170.0    # 2:50

# OW 보정 (초/100m)
OW_CORRECTION_SEC = 15

# 브릭 러닝 보정 (초/km)
BRICK_CORRECTION_SEC = 30


def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_week_number(dt):
    delta = (dt.date() - TRAIN_START.date()).days
    return delta // 7


def get_week_monday(dt):
    """해당 날짜가 속한 주의 월요일"""
    wk = get_week_number(dt)
    return TRAIN_START + timedelta(days=wk * 7)


def get_phase(dt):
    d = dt.date() if hasattr(dt, 'date') else dt
    if d <= datetime(2026, 4, 5, tzinfo=KST).date():
        return 1, "Phase 1: 베이스"
    elif d <= datetime(2026, 4, 26, tzinfo=KST).date():
        return 2, "Phase 2: 빌드"
    elif d <= datetime(2026, 5, 9, tzinfo=KST).date():
        return 3, "Phase 3: 테이퍼"
    return 0, "대회 완료"


# Phase별 주간 목표
PHASE_TARGETS = {
    1: {"swim": 4, "run": 3, "run_km": 20, "bike": 1},
    2: {"swim": 3, "run": 3, "run_km": 21, "bike": 2},
    3: {"swim": 2, "run": 2, "run_km": 10, "bike": 1},
}

# Phase별 벤치마크 (종료 시점 기준)
PHASE_BENCHMARKS = {
    1: {
        "swim_pace": "1:55",   # /100m
        "run_10k_pace": "6:00",  # /km (완주만 하면 OK)
        "run_freq": 3,
        "bike_min": 60,
    },
    2: {
        "swim_pace": "1:50",
        "run_10k_pace": "5:24",
        "run_freq": 3,
        "bike_min": 75,
    },
    3: {
        "swim_pace": "1:50",
        "run_10k_pace": "5:10",
        "run_freq": 2,
        "bike_min": 30,
    },
}


def pace_to_seconds(pace_str):
    """'5:33' → 333초"""
    if not pace_str:
        return None
    parts = pace_str.split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return None


def seconds_to_pace(secs):
    """333초 → '5:33'"""
    if secs is None:
        return None
    m = int(secs) // 60
    s = int(secs) % 60
    return f"{m}:{s:02d}"


def minutes_to_hhmm(mins):
    """170.5 → '2:50'"""
    h = int(mins) // 60
    m = int(mins) % 60
    return f"{h}:{m:02d}"


def analyze_week(log, dt=None):
    """이번 주 운동 집계"""
    if dt is None:
        dt = NOW
    mon = get_week_monday(dt)

    swim_count = 0
    run_count = 0
    bike_count = 0
    run_total_km = 0.0
    swim_paces = []
    run_paces = []

    for d in range(7):
        day = mon + timedelta(days=d)
        key = day.strftime('%Y-%m-%d')
        entry = log.get(key)
        if not entry or not entry.get('done'):
            continue

        metrics = entry.get('metrics', {})
        wtype = metrics.get('type', '')

        if wtype == 'swim':
            swim_count += 1
            pace = metrics.get('pace_per_100m')
            if pace:
                swim_paces.append(pace_to_seconds(pace))
        elif wtype == 'run':
            run_count += 1
            dist = metrics.get('distance_km', 0)
            run_total_km += dist
            pace = metrics.get('pace_per_km')
            if pace:
                run_paces.append(pace_to_seconds(pace))
        elif wtype == 'bike':
            bike_count += 1
        elif wtype == 'brick':
            bike_count += 1
            # 브릭의 러닝 부분도 카운트
            run_km = metrics.get('run_km', 0)
            if run_km >= 5:  # 풀 브릭만 러닝 1회로 카운트
                run_count += 1
                run_total_km += run_km

    return {
        "swim": {"count": swim_count, "paces": swim_paces},
        "run": {"count": run_count, "total_km": round(run_total_km, 1), "paces": run_paces},
        "bike": {"count": bike_count},
    }


def get_latest_metrics(log, workout_type, n=3):
    """최근 n회 특정 종목 메트릭 가져오기"""
    entries = []
    for date_key in sorted(log.keys(), reverse=True):
        entry = log[date_key]
        if not entry.get('done'):
            continue
        metrics = entry.get('metrics', {})
        if metrics.get('type') == workout_type:
            entries.append((date_key, metrics))
            if len(entries) >= n:
                break
    return entries


def estimate_finish_time(log):
    """현재 데이터 기반 예상 완주시간 계산"""
    # 수영: 최근 풀 페이스 + OW 보정
    swim_entries = get_latest_metrics(log, 'swim', 3)
    if swim_entries:
        avg_pace_sec = sum(pace_to_seconds(e[1].get('pace_per_100m', '2:00'))
                          for e in swim_entries if e[1].get('pace_per_100m')) / len(swim_entries)
        ow_pace_sec = avg_pace_sec + OW_CORRECTION_SEC
        est_swim = (ow_pace_sec * 15) / 60  # 1500m = 15 × 100m
    else:
        est_swim = TARGET_SWIM_MIN  # 데이터 없으면 목표값 사용

    # 자전거: 최근 평균속도
    bike_entries = get_latest_metrics(log, 'bike', 3)
    if bike_entries:
        speeds = [e[1].get('avg_speed_kmh', 32) for e in bike_entries]
        avg_speed = sum(speeds) / len(speeds)
        est_bike = (40 / avg_speed) * 60
    else:
        est_bike = TARGET_BIKE_MIN  # 기본값

    # 러닝: 최근 단독 페이스 + 브릭 보정
    run_entries = get_latest_metrics(log, 'run', 3)
    if run_entries:
        paces = [pace_to_seconds(e[1].get('pace_per_km', '5:48'))
                 for e in run_entries if e[1].get('pace_per_km')]
        if paces:
            avg_pace_sec = sum(paces) / len(paces)
            brick_pace_sec = avg_pace_sec + BRICK_CORRECTION_SEC
            est_run = (brick_pace_sec * 10) / 60
        else:
            est_run = TARGET_RUN_MIN
    else:
        est_run = TARGET_RUN_MIN

    total = est_swim + TARGET_T1_MIN + est_bike + TARGET_T2_MIN + est_run

    return {
        "swim": round(est_swim, 1),
        "bike": round(est_bike, 1),
        "run": round(est_run, 1),
        "t1": TARGET_T1_MIN,
        "t2": TARGET_T2_MIN,
        "total": round(total, 1),
    }


def check_adjustments(log, week_stats, phase):
    """스케줄 자동 조정 판단"""
    adjustments = []
    targets = PHASE_TARGETS.get(phase, {})

    # 목요일(DOW=3) 이후인데 러닝 3회 미달
    if DOW >= 3 and week_stats['run']['count'] < targets.get('run', 3):
        remaining_days = 6 - DOW  # 남은 요일 수
        deficit = targets.get('run', 3) - week_stats['run']['count']
        if deficit > 0 and remaining_days > 0:
            adjustments.append({
                "type": "run_frequency",
                "severity": "high" if deficit >= 2 else "medium",
                "message": f"러닝 {week_stats['run']['count']}/{targets.get('run', 3)}회 — "
                           f"남은 {remaining_days}일 내 {deficit}회 추가 필요",
            })

    # 러닝 페이스 확인 (최근 러닝 vs 벤치마크)
    benchmarks = PHASE_BENCHMARKS.get(phase, {})
    target_pace = pace_to_seconds(benchmarks.get('run_10k_pace'))
    if target_pace and week_stats['run']['paces']:
        latest_pace = week_stats['run']['paces'][-1]
        if latest_pace and latest_pace > target_pace + 30:
            adjustments.append({
                "type": "run_pace_behind",
                "severity": "low",
                "message": f"러닝 페이스 {seconds_to_pace(latest_pace)} "
                           f"(목표 {benchmarks.get('run_10k_pace')}, +{latest_pace - target_pace}초/km)",
            })

    # 주간 러닝 볼륨
    target_km = targets.get('run_km', 20)
    if DOW >= 4 and week_stats['run']['total_km'] < target_km * 0.5:
        adjustments.append({
            "type": "run_volume_low",
            "severity": "medium",
            "message": f"주간 러닝 {week_stats['run']['total_km']}km "
                       f"(목표 {target_km}km의 {int(week_stats['run']['total_km'] / target_km * 100)}%)",
        })

    return adjustments


def get_today_entry(log):
    """오늘의 운동 로그"""
    return log.get(TODAY)


def format_today_workout(entry):
    """오늘 운동 메시지 포매팅"""
    if not entry or not entry.get('done'):
        return None

    metrics = entry.get('metrics', {})
    wtype = metrics.get('type', '?')

    type_emoji = {
        'swim': '🏊', 'run': '🏃', 'bike': '🚴', 'brick': '🔥'
    }
    type_name = {
        'swim': '수영', 'run': '러닝', 'bike': '자전거', 'brick': '브릭'
    }

    emoji = type_emoji.get(wtype, '🏋️')
    name = type_name.get(wtype, wtype)

    lines = [f"{emoji} {name}"]

    if wtype == 'swim':
        dist = metrics.get('distance_m', 0)
        pace = metrics.get('pace_per_100m', '')
        dur = metrics.get('duration_min', 0)
        hr = metrics.get('avg_hr', '')
        maxhr = metrics.get('max_hr', '')
        swolf = metrics.get('swolf', '')
        lines.append(f"  {dist}m | {dur:.0f}분 | {pace}/100m")
        if hr:
            lines.append(f"  HR {hr}/{maxhr} | Swolf {swolf}")

    elif wtype == 'run':
        dist = metrics.get('distance_km', 0)
        pace = metrics.get('pace_per_km', '')
        hr = metrics.get('avg_hr', '')
        maxhr = metrics.get('max_hr', '')
        lines.append(f"  {dist}km | {pace}/km")
        if hr:
            lines.append(f"  HR {hr}/{maxhr}")

    elif wtype == 'bike':
        dur = metrics.get('duration_min', 0)
        dist = metrics.get('distance_km', 0)
        speed = metrics.get('avg_speed_kmh', 0)
        hr = metrics.get('avg_hr', '')
        if dist:
            lines.append(f"  {dist}km | {dur:.0f}분 | {speed}km/h")
        else:
            lines.append(f"  {dur:.0f}분")
        if hr:
            lines.append(f"  HR {hr}")

    note = entry.get('note', '')
    if note:
        lines.append(f"  📝 {note}")

    return "\n".join(lines)


def format_analysis_message(log):
    """전체 분석 메시지 생성"""
    phase, phase_name = get_phase(NOW)
    if phase == 0:
        return None

    week_stats = analyze_week(log)
    estimate = estimate_finish_time(log)
    adjustments = check_adjustments(log, week_stats, phase)
    today_entry = get_today_entry(log)
    targets = PHASE_TARGETS.get(phase, {})
    benchmarks = PHASE_BENCHMARKS.get(phase, {})

    lines = []

    # 헤더
    lines.append("🏋️ 운동 기록 업데이트")
    lines.append("")

    # 오늘의 운동
    if today_entry and today_entry.get('done'):
        today_msg = format_today_workout(today_entry)
        if today_msg:
            lines.append("📊 오늘의 운동")
            lines.append(today_msg)
            lines.append("")

    # Phase 벤치마크 진척
    lines.append(f"📈 {phase_name} 벤치마크")

    # 수영
    swim_latest = get_latest_metrics(log, 'swim', 1)
    if swim_latest:
        cur_pace = swim_latest[0][1].get('pace_per_100m', '?')
        tgt_pace = benchmarks.get('swim_pace', '?')
        cur_sec = pace_to_seconds(cur_pace)
        tgt_sec = pace_to_seconds(tgt_pace)
        if cur_sec and tgt_sec:
            diff = cur_sec - tgt_sec
            symbol = "✅" if diff <= 0 else f"⚠️ +{diff}초"
            lines.append(f"  수영: {cur_pace}/100m (목표 {tgt_pace}) {symbol}")
        else:
            lines.append(f"  수영: {cur_pace}/100m (목표 {tgt_pace})")
    else:
        lines.append(f"  수영: 데이터 없음 (목표 {benchmarks.get('swim_pace', '?')})")

    # 러닝
    run_latest = get_latest_metrics(log, 'run', 1)
    if run_latest:
        cur_pace = run_latest[0][1].get('pace_per_km', '?')
        tgt_pace = benchmarks.get('run_10k_pace', '?')
        cur_sec = pace_to_seconds(cur_pace)
        tgt_sec = pace_to_seconds(tgt_pace)
        if cur_sec and tgt_sec:
            diff = cur_sec - tgt_sec
            symbol = "✅" if diff <= 0 else f"⚠️ +{diff}초"
            lines.append(f"  러닝: {cur_pace}/km (목표 {tgt_pace}) {symbol}")
        else:
            lines.append(f"  러닝: {cur_pace}/km (목표 {tgt_pace})")
    else:
        lines.append(f"  러닝: 데이터 없음 (목표 {benchmarks.get('run_10k_pace', '?')})")

    # 자전거
    bike_latest = get_latest_metrics(log, 'bike', 1)
    if bike_latest:
        speed = bike_latest[0][1].get('avg_speed_kmh', '?')
        lines.append(f"  자전거: {speed}km/h")
    else:
        lines.append("  자전거: 데이터 없음")
    lines.append("")

    # 주간 현황
    lines.append("📅 이번 주 현황")
    run_target = targets.get('run', 3)
    swim_target = targets.get('swim', 4)
    bike_target = targets.get('bike', 1)
    run_km_target = targets.get('run_km', 20)

    swim_bar = "●" * week_stats['swim']['count'] + "○" * max(0, swim_target - week_stats['swim']['count'])
    run_bar = "●" * week_stats['run']['count'] + "○" * max(0, run_target - week_stats['run']['count'])
    bike_bar = "●" * week_stats['bike']['count'] + "○" * max(0, bike_target - week_stats['bike']['count'])

    lines.append(f"  수영 {swim_bar} {week_stats['swim']['count']}/{swim_target}")
    lines.append(f"  러닝 {run_bar} {week_stats['run']['count']}/{run_target} ({week_stats['run']['total_km']}km/{run_km_target}km)")
    lines.append(f"  자전거 {bike_bar} {week_stats['bike']['count']}/{bike_target}")
    lines.append("")

    # 스케줄 조정
    if adjustments:
        lines.append("🔄 스케줄 조정")
        for adj in adjustments:
            severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(adj['severity'], "⚪")
            lines.append(f"  {severity_icon} {adj['message']}")
        lines.append("")
    else:
        lines.append("🔄 변경 없음 — 계획대로 진행")
        lines.append("")

    # 예상 완주시간
    total = estimate['total']
    if total <= TARGET_TOTAL_MIN:
        status_emoji = "🟢"
        status_text = "목표 달성 가능"
    elif total <= 180:
        status_emoji = "🟡"
        status_text = "주의 — 개선 필요"
    else:
        status_emoji = "🔴"
        status_text = "경고 — 스케줄 강화 필요"

    est_str = minutes_to_hhmm(total)
    lines.append(f"🏁 D-{DAYS_LEFT} | 목표 2:50 | 예상 {est_str} {status_emoji}")
    lines.append(f"  ({status_text})")
    lines.append(f"  수영 {estimate['swim']:.0f}분 + T1 + 자전거 {estimate['bike']:.0f}분 + T2 + 러닝 {estimate['run']:.0f}분")

    return "\n".join(lines)


def update_schedule_json(log, analysis_msg):
    """workout_schedule.json 업데이트"""
    schedule = load_json(SCHEDULE_FILE)
    phase, phase_name = get_phase(NOW)
    week_stats = analyze_week(log)
    estimate = estimate_finish_time(log)
    adjustments = check_adjustments(log, week_stats, phase)
    targets = PHASE_TARGETS.get(phase, {})

    total = estimate['total']
    if total <= TARGET_TOTAL_MIN:
        status = "green"
        status_text = "목표 달성 가능"
    elif total <= 180:
        status = "yellow"
        status_text = "주의 — 개선 필요"
    else:
        status = "red"
        status_text = "경고 — 스케줄 강화 필요"

    schedule['last_analysis'] = {
        "date": TODAY,
        "estimated_finish": minutes_to_hhmm(total),
        "status": status,
        "status_text": status_text,
        "phase": phase,
        "phase_name": phase_name,
        "weekly_summary": {
            "swim": {"count": week_stats['swim']['count'], "target": targets.get('swim', 4)},
            "run": {
                "count": week_stats['run']['count'],
                "target": targets.get('run', 3),
                "total_km": week_stats['run']['total_km'],
            },
            "bike": {"count": week_stats['bike']['count'], "target": targets.get('bike', 1)},
        },
        "adjustments": [a['message'] for a in adjustments],
    }

    save_json(SCHEDULE_FILE, schedule)


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

    # 분석 메시지 생성
    msg = format_analysis_message(log)
    if not msg:
        print("  분석 메시지 없음 (대회 완료)")
        return

    print(f"\n--- 메시지 미리보기 ---\n{msg}\n")

    # workout_schedule.json 업데이트
    update_schedule_json(log, msg)
    print("  workout_schedule.json 업데이트 완료")

    # 텔레그램 전송
    ok = send_telegram(msg)
    print(f"  텔레그램 전송: {'성공' if ok else '실패'}")


if __name__ == '__main__':
    main()
