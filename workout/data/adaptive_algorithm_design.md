# 적응형 운동 스케줄 알고리즘 설계서

> 작성일: 2026-03-21
> 상위 규칙: WORKOUT_MASTER.md (L2) > WORKOUT_ALGORITHM.md (L2) > ~/CLAUDE.md (L0)
> 상태: 설계 완료, 구현 대기

---

## 1. 현황 분석

### 1.1 현재 시스템 구조

```
garmin_sync.py (4회/일 실행)
  → 가민 데이터 수집
  → workout_log.json 업데이트
  → workout_analysis.py 호출
      → VDOT 재계산
      → 주간 통계 분석
      → 스케줄 조정 "제안" (텔레그램 메시지)
      → workout_schedule.json 업데이트
```

### 1.2 문제점

| 문제 | 현재 | 필요 |
| ------ | ------ | ------ |
| 스케줄 고정 | WORKOUT_MASTER.md에 요일별 고정 배치 | 실적/컨디션에 따른 동적 조정 |
| 일일 조정 없음 | 고강도 후에도 다음 날 스케줄 불변 | 자동으로 회복일 전환 |
| 운동 누락 대응 없음 | 못 한 운동 → 그냥 소실 | 주간 내 재배치 or 보충 |
| 컨디션 반영 제한적 | garmin_health 데이터 수집만 | 오늘 컨디션 → 오늘 스케줄 실시간 전환 |
| overrides 미활용 | workout_schedule.json에 필드만 존재 | 구체적 override 생성/관리 로직 없음 |

### 1.3 기존 자산 (활용 가능)

- `workout_schedule.json`의 `overrides` 필드 (빈 객체로 존재)
- `check_adjustments()`: 조정 "제안"은 생성하지만 overrides에 기록하지 않음
- `check_health_adjustments()`: BB, HRV, 수면, Training Readiness 이미 파싱 중
- `check_consecutive_running()`: 연속 러닝 규칙 이미 구현
- `garmin_health.json`: BB, HRV, 수면, 스트레스, Training Readiness 데이터 축적

---

## 2. 아키텍처

### 2.1 새로운 모듈: `adaptive_scheduler.py`

기존 `workout_analysis.py`는 "분석 + 메시지 전송"에 집중한다.
적응형 스케줄 조정은 독립 모듈로 분리하여 단일 책임 원칙을 준수한다.

```
garmin_sync.py
  → workout_log.json 업데이트
  → adaptive_scheduler.py::adjust_schedule() 호출    ← 신규
      → 일일 조정 (A)
      → 주간 조정 (B)
      → Phase 전환 조정 (C)
      → overrides 기록
  → workout_analysis.py (분석 + 메시지)
      → overrides 반영된 스케줄 표시
```

### 2.2 호출 시점

| 트리거 | 함수 | 설명 |
| ------ | ------ | ------ |
| garmin_sync.py 실행 (4회/일) | `adjust_daily()` | 오늘 운동 감지 후 내일 스케줄 조정 |
| 매일 06:00 (별도 cron) | `adjust_morning()` | 오늘 garmin_health 기반 오늘 스케줄 전환 |
| 일요일 23:00 | `adjust_weekly()` | 주간 실적 평가 → 다음 주 초반 보충 |
| Phase 종료 1주 전 | `evaluate_phase()` | 벤치마크 달성 여부 → Phase 전환 판단 |

### 2.3 데이터 흐름

```
[입력]
  workout_log.json        ← 실제 운동 기록
  workout_schedule.json   ← 현재 VDOT, 분석 상태
  garmin_health.json      ← BB, HRV, 수면, 스트레스
  WORKOUT_MASTER.md       ← 요일별 기본 스케줄 (Phase별)

[처리]
  adaptive_scheduler.py
    → 기본 스케줄 로드 (Phase별 요일 매핑)
    → 조건 평가 (일일/주간/Phase)
    → override 생성

[출력]
  workout_schedule.json.overrides
    → {"2026-03-22": {"workout": "러닝 Easy", "detail": "6km", "reason": "어제 고강도 후 회복", "auto": true}}
```

---

## 3. 기본 스케줄 정의

WORKOUT_MASTER.md의 Phase별 스케줄을 코드로 정의한다. overrides가 없으면 이 기본값이 적용된다.

```python
# 요일: 0=월 ~ 6=일
BASE_SCHEDULE = {
    1: {  # Phase 1: 베이스
        0: {"workout": "수영 수업", "detail": "", "type": "swim"},
        1: {"workout": "러닝 Easy", "detail": "5~6km @6:00", "type": "run"},
        2: {"workout": "수영 수업", "detail": "", "type": "swim"},
        3: {"workout": "러닝 + 코어", "detail": "6~7km + 코어 15분", "type": "run"},
        4: {"workout": "수영 수업", "detail": "", "type": "swim"},
        5: {"workout": "브릭 → 수영", "detail": "자전거 60분 → 러닝 5km → 수영", "type": "brick"},
        6: {"workout": "완전 휴식", "detail": "", "type": "rest"},
    },
    2: {  # Phase 2: 빌드
        0: {"workout": "수영 수업", "detail": "", "type": "swim"},
        1: {"workout": "러닝 템포", "detail": "7km: 2up→3@5:10→2dn", "type": "run"},
        2: {"workout": "수영 수업", "detail": "", "type": "swim"},
        3: {"workout": "러닝 Easy", "detail": "7~8km Easy", "type": "run"},
        4: {"workout": "수영 수업", "detail": "", "type": "swim"},
        5: {"workout": "브릭 → 수영", "detail": "자전거 75~90분 → 러닝 5km → 수영", "type": "brick"},
        6: {"workout": "완전 휴식", "detail": "", "type": "rest"},
    },
    3: {  # Phase 3: 테이퍼 (주차별 별도 정의 필요 — WORKOUT_MASTER.md 참조)
        0: {"workout": "수영 수업", "detail": "가볍게", "type": "swim"},
        1: {"workout": "러닝", "detail": "6km 레이스 페이스", "type": "run"},
        2: {"workout": "수영 수업", "detail": "가볍게", "type": "swim"},
        3: {"workout": "러닝 Easy", "detail": "4km + 스트라이드", "type": "run"},
        4: {"workout": "수영 가볍게", "detail": "1km", "type": "swim"},
        5: {"workout": "수영 개인교습", "detail": "사이팅 연습", "type": "swim"},
        6: {"workout": "완전 휴식", "detail": "", "type": "rest"},
    },
}
```

---

## 4. 알고리즘 A: 일일 조정

### 4.1 트리거 조건과 조정 행동

garmin_sync.py에서 운동 감지 후, 또는 매일 아침 건강 데이터 수집 후 실행.

```
adjust_daily(today_date) → List[Override]
```

#### 규칙 A1: 고강도 후 회복 강제

```
조건: 오늘 training_zone in (tempo, interval, repetition) OR training_load > 주간평균 × 1.5
  AND 내일 기본 스케줄 = 러닝
결과: 내일 → 러닝 Easy (거리 50% 감소) 또는 수영으로 대체
근거: WORKOUT_ALGORITHM.md 주간 피로 관리 — "일일 최대 부하 < 주간평균 × 1.5"
```

**판정 로직:**
```python
def rule_a1_post_hard(today_entry, tomorrow_base):
    zone = today_entry.get('training_zone', '')
    load = today_entry.get('training_load', 0)
    week_avg_load = get_weekly_avg_load()

    is_hard = zone in ('tempo', 'interval', 'repetition')
    is_overload = load > week_avg_load * 1.5

    if (is_hard or is_overload) and tomorrow_base['type'] == 'run':
        return {
            "workout": "러닝 Easy",
            "detail": f"{max(3, int(tomorrow_base_km * 0.5))}km @6:00+",
            "reason": f"어제 {zone} (부하 {load}) 후 회복",
            "auto": True,
            "rule": "A1",
        }
    return None
```

#### 규칙 A2: 운동 누락 시 재배치

```
조건: 오늘 planned 운동이 있었으나 done=False (야근/음주/기타)
  AND 누락된 운동이 러닝
결과:
  - 내일 수영 → 러닝으로 대체 (러닝 우선순위, WORKOUT_MASTER §4)
  - 내일 러닝 → 유지 (자연스럽게 보충)
  - 내일 휴식(일) → 건드리지 않음 (일요일 완전 휴식 불가침)
  - 주간 러닝 목표 3회 달성이 어려우면 → 남은 일에 러닝 재배치 (규칙 B1로 위임)
```

**판정 로직:**
```python
def rule_a2_missed_workout(today_date, log):
    entry = log.get(today_date, {})
    if entry.get('done', False):
        return None  # 운동 완료 → 조정 불필요

    planned = entry.get('planned', '')
    if '러닝' not in planned and 'run' not in planned.lower():
        return None  # 누락된 게 러닝이 아니면 → 패스 (수영은 스킵 가능)

    tomorrow = next_day(today_date)
    tomorrow_dow = tomorrow.weekday()

    if tomorrow_dow == 6:  # 일요일
        return None  # 일요일 완전 휴식 불가침

    tomorrow_base = get_base_schedule(tomorrow)
    if tomorrow_base['type'] == 'swim':
        return {
            "workout": "러닝 Easy",
            "detail": f"{planned_detail_from_missed(entry)}",
            "reason": f"{today_date} 러닝 누락 → 수영 대체하여 보충",
            "auto": True,
            "rule": "A2",
        }
    return None  # 내일이 이미 러닝이면 자연 보충
```

#### 규칙 A3: 컨디션 불량 시 오늘 전환

매일 아침 06:00 실행. garmin_health.json의 당일 데이터 기반.

```
조건 (OR — 하나라도 해당 시):
  - Body Battery max < 40           → severity: high
  - Body Battery max < 60           → severity: medium
  - HRV status = LOW/POOR          → severity: high
  - HRV last_night < weekly_avg × 0.75 → severity: medium
  - Training Readiness score < 30   → severity: high
  - Training Readiness score < 50   → severity: medium
  - 수면 score < 50                 → severity: high
  - 수면 duration < 360분 (6시간)   → severity: medium
  - 안정시 심박 > 55bpm (평소 45)    → severity: medium

결과:
  - severity high 1개 이상: 오늘 → Easy or 완전 휴식
  - severity medium 2개 이상: 오늘 → Easy (고강도 금지)
  - severity medium 1개: 경고만 (텔레그램), 스케줄 유지
```

**판정 로직:**
```python
def rule_a3_condition_check(today_date):
    health = load_health(today_date)
    if not health:
        return None

    high_count = 0
    medium_count = 0
    reasons = []

    bb_max = health.get('body_battery', {}).get('max')
    if bb_max is not None:
        if bb_max < 40:
            high_count += 1
            reasons.append(f"BB {bb_max}")
        elif bb_max < 60:
            medium_count += 1
            reasons.append(f"BB {bb_max}")

    hrv = health.get('hrv', {})
    if hrv.get('status') in ('LOW', 'POOR'):
        high_count += 1
        reasons.append(f"HRV {hrv.get('status')}")
    elif hrv.get('last_night') and hrv.get('weekly_avg'):
        if hrv['last_night'] < hrv['weekly_avg'] * 0.75:
            medium_count += 1
            reasons.append(f"HRV {hrv['last_night']}ms (avg {hrv['weekly_avg']})")

    tr = health.get('training_readiness', {})
    tr_score = tr.get('score')
    if tr_score is not None:
        if tr_score < 30:
            high_count += 1
            reasons.append(f"TR {tr_score}")
        elif tr_score < 50:
            medium_count += 1
            reasons.append(f"TR {tr_score}")

    sleep = health.get('sleep', {})
    if sleep.get('score') is not None and sleep['score'] < 50:
        high_count += 1
        reasons.append(f"수면 {sleep['score']}")
    elif sleep.get('duration_min', 999) < 360:
        medium_count += 1
        reasons.append(f"수면 {sleep['duration_min']//60}h")

    rhr = health.get('resting_hr')
    if rhr and rhr > 55:
        medium_count += 1
        reasons.append(f"RHR {rhr}")

    reason_str = ", ".join(reasons)

    if high_count >= 1:
        return {
            "workout": "Easy 또는 완전 휴식",
            "detail": "컨디션 불량 — 고강도 금지",
            "reason": f"컨디션 적색: {reason_str}",
            "auto": True,
            "rule": "A3-high",
        }
    elif medium_count >= 2:
        return {
            "workout": "Easy only",
            "detail": "템포/인터벌 금지, Easy 강도까지만",
            "reason": f"컨디션 황색: {reason_str}",
            "auto": True,
            "rule": "A3-medium",
        }
    # medium 1개 → 경고만 (override 생성 안 함, 텔레그램 메시지만)
    return None
```

#### 규칙 A4: 예상보다 좋은 성과 시 상향

```
조건:
  - 오늘 러닝 템포/인터벌에서 목표 페이스 대비 5초/km 이상 빠르게 달성
  - AND HR이 예상 존 내 (즉, 페이스만 빠른 게 아니라 실제 체력 향상)
  - AND 주간 부하 < 120%

결과:
  - 다음 고강도 세션(화요일 템포)의 목표 페이스를 3초/km 상향
  - VDOT은 update_vdot()에서 자동 반영되므로 별도 조정 불필요
  - override로 기록: {"detail": "3km @5:07 (상향)", "reason": "이전 템포 목표 초과 달성"}
```

**판정 로직:**
```python
def rule_a4_outperformance(today_entry, schedule):
    zone = today_entry.get('training_zone', '')
    if zone not in ('tempo', 'interval'):
        return None

    metrics = today_entry.get('metrics', {})
    pace_sec = pace_to_seconds(metrics.get('pace_per_km'))
    if not pace_sec:
        return None

    vdot_paces = get_vdot_paces(schedule.get('current_vdot', 36))
    target_pace = vdot_paces['tempo'] if zone == 'tempo' else vdot_paces['interval']

    improvement = target_pace - pace_sec  # 양수 = 목표보다 빠름
    avg_hr = metrics.get('avg_hr', 0)

    # 페이스 5초+ 빠르고, HR이 합리적 범위(Zone 3-4, 155-175 가정)
    if improvement >= 5 and avg_hr < 175:
        # 다음 화요일 찾기
        next_tempo_day = find_next_weekday(today_entry['date'], 1)  # 화요일=1
        new_target = target_pace - 3  # 3초 상향 (보수적)
        return {
            "date": next_tempo_day,
            "workout": f"러닝 템포 (상향)",
            "detail": f"3km @{seconds_to_pace(new_target)} (기존 대비 -3초)",
            "reason": f"이전 템포 {seconds_to_pace(pace_sec)}/km (목표 {seconds_to_pace(target_pace)} 대비 -{improvement}초)",
            "auto": True,
            "rule": "A4",
        }
    return None
```

---

## 5. 알고리즘 B: 주간 조정

매주 수요일(중간 점검) + 일요일(주간 마감) 실행.

```
adjust_weekly(week_start_date) → List[Override]
```

#### 규칙 B1: 러닝 빈도 보충

```
조건: 수요일 기준 러닝 0~1회 (목표 3회)
결과: 남은 목/금/토에 러닝 배치
  - 목요일 수영 → 러닝 대체 (WORKOUT_MASTER §4: 수영 1회를 러닝으로 대체 허용)
  - 금요일 수영 → 러닝 대체 (주 2회 대체 시 수영 부족 경고 병행)
  - 토요일 브릭 → 유지 (러닝 포함)
우선순위: WORKOUT_MASTER §4 — "러닝 주 3회 확보 > 수영 횟수"
제약: 연속 러닝 규칙 (§1) 위반하지 않을 것
```

**판정 로직:**
```python
def rule_b1_run_frequency(log, week_start):
    stats = analyze_week(log, week_start)
    today_dow = get_today_dow()

    if today_dow < 2:  # 월/화 → 아직 판단 이름
        return []

    run_count = stats['run']['count']
    run_target = 3
    deficit = run_target - run_count
    if deficit <= 0:
        return []

    overrides = []
    remaining_days = list(range(today_dow + 1, 7))  # 남은 요일들
    available_for_run = []

    for dow in remaining_days:
        if dow == 6:  # 일요일 = 완전 휴식 불가침
            continue
        day_date = week_start + timedelta(days=dow)
        base = get_base_schedule_for_day(day_date)
        existing_override = get_override(day_date)

        # 이미 러닝이면 스킵
        if (existing_override or base)['type'] == 'run':
            continue
        # 브릭은 러닝 포함이므로 스킵
        if (existing_override or base)['type'] == 'brick':
            continue

        # 연속 러닝 규칙 체크
        prev_day = day_date - timedelta(days=1)
        if is_run_day(prev_day, log) and is_recovery_period():
            continue  # 복귀 2주 내 연속 러닝 금지

        available_for_run.append(dow)

    for dow in available_for_run[:deficit]:
        day_date = week_start + timedelta(days=dow)
        overrides.append({
            "date": day_date.strftime('%Y-%m-%d'),
            "workout": "러닝 Easy",
            "detail": "6km @6:00+ (수영 대체)",
            "reason": f"주간 러닝 {run_count}/{run_target}회 — 빈도 보충",
            "auto": True,
            "rule": "B1",
        })

    # 수영 대체 2회 이상이면 경고
    if len(overrides) >= 2:
        overrides[0]['warning'] = "수영 2회 대체 — 수영 볼륨 부족 가능"

    return overrides
```

#### 규칙 B2: 주간 볼륨 초과 (120%+)

```
조건: 주간 total_load > target_load × 1.2
결과: 남은 일 모두 Easy or 휴식으로 전환
근거: WORKOUT_ALGORITHM.md 주간 피로 관리 — 적색 "주간 총 부하 > 120%"
```

```python
def rule_b2_overload(log, week_start, phase):
    stats = analyze_week(log, week_start)
    target = PHASE_TARGETS[phase]['weekly_load']

    if stats['total_load'] <= target * 1.2:
        return []

    overrides = []
    today = get_today()
    for d in range(today.weekday() + 1, 7):
        if d == 6:  # 일요일 = 이미 휴식
            continue
        day_date = week_start + timedelta(days=d)
        base = get_base_schedule_for_day(day_date)
        if base['type'] in ('run', 'brick'):
            overrides.append({
                "date": day_date.strftime('%Y-%m-%d'),
                "workout": "Easy 또는 수영",
                "detail": "볼륨 초과 — 고강도 금지",
                "reason": f"주간 부하 {stats['total_load']}/{target} ({round(stats['total_load']/target*100)}%)",
                "auto": True,
                "rule": "B2",
            })
    return overrides
```

#### 규칙 B3: 주간 볼륨 부족 (80% 미만)

```
조건: 일요일 기준 주간 total_load < target_load × 0.8
결과: 다음 주 월/화에 보충 운동 배치
  - 월요일 수영 → 수영 + 러닝 Easy 3km (오후)
  - 화요일 러닝 거리 1~2km 추가
제약: 보충은 Easy 강도만. 볼륨 부족을 고강도로 만회하지 않음 (80/20 원칙)
```

```python
def rule_b3_underload(log, week_start, phase):
    stats = analyze_week(log, week_start)
    target = PHASE_TARGETS[phase]['weekly_load']

    if stats['total_load'] >= target * 0.8:
        return []

    deficit_pct = round((1 - stats['total_load'] / target) * 100)
    next_monday = week_start + timedelta(days=7)
    next_tuesday = next_monday + timedelta(days=1)

    return [
        {
            "date": next_tuesday.strftime('%Y-%m-%d'),
            "workout": "러닝 Easy (보충)",
            "detail": f"기본 거리 + 2km 추가 @6:00+",
            "reason": f"지난 주 부하 {stats['total_load']}/{target} ({100-deficit_pct}%) — Easy 보충",
            "auto": True,
            "rule": "B3",
        }
    ]
```

---

## 6. 알고리즘 C: Phase 전환 조정

#### 규칙 C1: Phase 진입 조건 충족 (VDOT 목표 도달)

```
조건: Phase 종료 1주 전 벤치마크 평가
  - Phase 1 → 2: 러닝 주 3회 안정적 + 10km 논스톱 + 자전거 60분 완주
  - Phase 2 → 3: 템포 3km @5:15 달성 + 10km 단독 55~56분 + 브릭 5km 완주
결과:
  - 충족 → Phase 전환 확정, 다음 주부터 새 Phase 스케줄 적용
  - 미충족 → 사용자에게 보고 (자동 판단 금지, WORKOUT_ALGORITHM.md "수동 판단" 항목)
```

```python
def rule_c1_phase_transition(log, schedule, current_phase):
    benchmarks = {
        1: {
            "run_frequency_stable": lambda s: s['run']['count'] >= 3,
            "long_run_10k": lambda log: has_10k_nonstop(log),
            "bike_60min": lambda log: has_bike_session(log, min_minutes=55),
        },
        2: {
            "tempo_5_15": lambda log: has_tempo_pace(log, max_pace_sec=315),
            "standalone_10k_55": lambda s: predict_10k_time(s.get('current_vdot', 35)) <= 56,
            "brick_5k": lambda log: has_brick_run(log, min_km=4.5),
        },
    }

    phase_checks = benchmarks.get(current_phase, {})
    results = {}
    for name, check_fn in phase_checks.items():
        try:
            # 함수 시그니처에 따라 log 또는 schedule 전달
            results[name] = check_fn(log) if 'log' in name else check_fn(schedule)
        except:
            results[name] = False

    all_met = all(results.values())

    return {
        "phase": current_phase,
        "benchmarks": results,
        "all_met": all_met,
        "action": "phase_advance" if all_met else "report_to_user",
    }
```

#### 규칙 C2: VDOT 정체

```
조건: VDOT가 3주 연속 동일 (±1 범위)
결과: 사용자에게 보고 + 제안
  - "VDOT {N}에서 3주 정체. 다음 옵션:
    (1) Phase 1주 연장
    (2) 인터벌 세션 1회 추가
    (3) 목표 시간 재설정 (2:50 → 2:55)"
  → 자동 적용 금지 (수동 판단 영역)
```

```python
def rule_c2_vdot_stagnation(schedule):
    history = schedule.get('vdot_history', [])
    if len(history) < 3:
        return None

    recent_3 = history[-3:]
    vdot_range = max(recent_3) - min(recent_3)

    if vdot_range <= 1:
        return {
            "type": "vdot_stagnation",
            "vdot": recent_3[-1],
            "weeks": 3,
            "action": "report_to_user",
            "message": f"VDOT {recent_3[-1]}에서 3주 정체",
            "options": [
                "Phase 1주 연장",
                "인터벌 세션 1회 추가 (주 1회 → 화/목 중 택1)",
                f"목표 재설정: 현 VDOT 기준 예상 완주 {predict_finish_with_vdot(recent_3[-1])}",
            ],
        }
    return None
```

#### 규칙 C3: 부상 징후 감지

```
조건 (OR):
  - 사용자가 workout_log note에 "통증", "부상", "pain" 키워드
  - 동일 부위 통증 2회 이상 기록
  - 안정시 심박 +15bpm 이상 (51 → 66+ for this user)
결과:
  - 즉시: 러닝 볼륨 50% 감소 (3회 → 2회, 거리 50%)
  - 관절/힘줄 통증: 해당 종목 3일 중단 (WORKOUT_MASTER §3)
  - 3일 이상 지속: 병원 권유 메시지
```

```python
def rule_c3_injury_detection(log, health):
    injury_keywords = ['통증', '부상', 'pain', '아프', '쑤시', '찌릿']
    recent_notes = get_recent_notes(log, days=7)

    injury_mentions = []
    for date, note in recent_notes:
        for kw in injury_keywords:
            if kw in note:
                injury_mentions.append((date, note))
                break

    if len(injury_mentions) == 0:
        # RHR 체크
        rhr = health.get('resting_hr', 0)
        if rhr < 60:  # 심각한 수준 아님
            return None

    severity = "medium"
    if len(injury_mentions) >= 2:
        severity = "high"

    # RHR 급등
    rhr = health.get('resting_hr', 0)
    if rhr >= 60:  # 평소 45 대비 +15
        severity = "high"

    overrides = []
    if severity == "high":
        # 향후 7일 러닝 볼륨 50% 감소
        for d in range(1, 8):
            day_date = today + timedelta(days=d)
            base = get_base_schedule_for_day(day_date)
            if base['type'] == 'run':
                overrides.append({
                    "date": day_date.strftime('%Y-%m-%d'),
                    "workout": "러닝 Easy (감량)",
                    "detail": f"{max(3, int(base_km * 0.5))}km — 부상 방지",
                    "reason": f"부상 징후 감지: {injury_mentions[-1][1] if injury_mentions else f'RHR {rhr}'}",
                    "auto": True,
                    "rule": "C3",
                })

    return overrides
```

---

## 7. Override 관리

### 7.1 데이터 구조

```json
{
  "overrides": {
    "2026-03-22": {
      "workout": "러닝 Easy",
      "detail": "6km @6:00+",
      "reason": "어제 고강도 후 회복",
      "auto": true,
      "rule": "A1",
      "created_at": "2026-03-21T20:00:00+09:00",
      "expires_at": null
    },
    "2026-03-25": {
      "workout": "러닝 Easy (보충)",
      "detail": "8km @6:00+ (수영 대체)",
      "reason": "주간 러닝 1/3회 — 빈도 보충",
      "auto": true,
      "rule": "B1",
      "created_at": "2026-03-23T23:00:00+09:00",
      "expires_at": null
    }
  }
}
```

### 7.2 Override 우선순위 (충돌 해소)

동일 날짜에 여러 규칙이 override를 생성할 때:

| 우선순위 | 규칙 | 이유 |
| ------ | ------ | ------ |
| 1 (최고) | C3 (부상) | WORKOUT_MASTER §4: 부상 방지 > 모든 것 |
| 2 | A3-high (컨디션 적색) | 건강 위험 방지 |
| 3 | B2 (주간 과부하) | 과훈련 방지 |
| 4 | A1 (고강도 후 회복) | 일일 회복 |
| 5 | A3-medium (컨디션 황색) | 주의 수준 |
| 6 | B1 (러닝 빈도 보충) | 빈도 확보 |
| 7 | A4 (성과 상향) | 성과 개선 (가장 낮음) |
| 8 | B3 (볼륨 보충) | 보충은 안전할 때만 |

**충돌 해소 로직:**
```python
def resolve_conflicts(overrides_list):
    """동일 날짜에 여러 override → 우선순위 최고만 적용"""
    PRIORITY = {'C3': 1, 'A3-high': 2, 'B2': 3, 'A1': 4,
                'A3-medium': 5, 'B1': 6, 'A4': 7, 'B3': 8}

    by_date = {}
    for ov in overrides_list:
        date = ov['date']
        if date not in by_date:
            by_date[date] = ov
        else:
            existing_priority = PRIORITY.get(by_date[date]['rule'], 99)
            new_priority = PRIORITY.get(ov['rule'], 99)
            if new_priority < existing_priority:
                by_date[date] = ov  # 더 높은 우선순위로 교체
    return by_date
```

### 7.3 Override 만료/정리

```python
def cleanup_overrides(schedule):
    """과거 날짜의 override 정리 (7일 이전 삭제)"""
    overrides = schedule.get('overrides', {})
    cutoff = (datetime.now(KST) - timedelta(days=7)).strftime('%Y-%m-%d')
    cleaned = {k: v for k, v in overrides.items() if k >= cutoff}
    schedule['overrides'] = cleaned
```

### 7.4 사용자 수동 override

사용자가 직접 override를 지정하면 `auto: false`로 기록. 자동 override보다 항상 우선.

```json
{
  "2026-03-25": {
    "workout": "완전 휴식",
    "detail": "",
    "reason": "개인 사유",
    "auto": false,
    "rule": "USER"
  }
}
```

---

## 8. 통합 실행 흐름

### 8.1 adjust_schedule() 메인 함수

```python
def adjust_schedule():
    """적응형 스케줄 조정 메인 — garmin_sync.py에서 호출"""
    log = load_json(LOG_FILE)
    schedule = load_json(SCHEDULE_FILE)
    health = load_health(TODAY)
    phase, phase_name = get_phase(NOW)

    if phase == 0:
        return  # 대회 완료

    all_overrides = []

    # === A: 일일 조정 ===
    today_entry = log.get(TODAY)

    # A1: 고강도 후 회복
    if today_entry and today_entry.get('done'):
        ov = rule_a1_post_hard(today_entry, get_tomorrow_base())
        if ov:
            ov['date'] = get_tomorrow_date()
            all_overrides.append(ov)

    # A2: 운동 누락 재배치
    ov = rule_a2_missed_workout(TODAY, log)
    if ov:
        ov['date'] = get_tomorrow_date()
        all_overrides.append(ov)

    # A3: 컨디션 체크 (오늘)
    ov = rule_a3_condition_check(TODAY)
    if ov:
        ov['date'] = TODAY
        all_overrides.append(ov)

    # A4: 성과 상향
    if today_entry and today_entry.get('done'):
        ov = rule_a4_outperformance(today_entry, schedule)
        if ov:
            all_overrides.append(ov)

    # === B: 주간 조정 (수/일만) ===
    dow = NOW.weekday()
    week_start = get_week_monday(NOW)

    if dow >= 2:  # 수요일 이후
        # B1: 러닝 빈도
        ovs = rule_b1_run_frequency(log, week_start)
        all_overrides.extend(ovs)

    # B2: 과부하
    ovs = rule_b2_overload(log, week_start, phase)
    all_overrides.extend(ovs)

    if dow == 6:  # 일요일
        # B3: 볼륨 부족 → 다음 주 보충
        ovs = rule_b3_underload(log, week_start, phase)
        all_overrides.extend(ovs)

    # === C: Phase 전환 (Phase 종료 1주 전) ===
    phase_end = get_phase_end_date(phase)
    days_to_phase_end = (phase_end - NOW.date()).days
    if 5 <= days_to_phase_end <= 7:
        result = rule_c1_phase_transition(log, schedule, phase)
        if not result['all_met']:
            # 사용자에게 보고 (override 아님)
            send_phase_report(result)

    # C2: VDOT 정체 (매주 점검)
    if dow == 6:
        stagnation = rule_c2_vdot_stagnation(schedule)
        if stagnation:
            send_stagnation_report(stagnation)

    # C3: 부상 감지 (매일)
    injury_ovs = rule_c3_injury_detection(log, health)
    if injury_ovs:
        all_overrides.extend(injury_ovs)

    # === 충돌 해소 + 기록 ===
    # 사용자 수동 override는 보존
    existing = schedule.get('overrides', {})
    user_overrides = {k: v for k, v in existing.items() if not v.get('auto', True)}

    resolved = resolve_conflicts(all_overrides)

    # 사용자 override 우선
    for date, ov in user_overrides.items():
        resolved[date] = ov

    schedule['overrides'] = resolved
    cleanup_overrides(schedule)
    save_json(SCHEDULE_FILE, schedule)

    # 변경 사항 텔레그램 알림
    new_overrides = {k: v for k, v in resolved.items()
                     if k not in existing or existing[k] != v}
    if new_overrides:
        send_override_notification(new_overrides)
```

### 8.2 garmin_sync.py 수정 포인트

기존 garmin_sync.py의 `main()` 함수 끝에 한 줄 추가:

```python
# 기존 코드 끝에 추가
from adaptive_scheduler import adjust_schedule
adjust_schedule()
```

### 8.3 아침 컨디션 체크 (별도 스케줄)

GitHub Actions에 06:00 KST cron 추가:

```yaml
# .github/workflows/morning_condition.yml
name: Morning Condition Check
on:
  schedule:
    - cron: '0 21 * * *'  # UTC 21:00 = KST 06:00
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python workout/scripts/adaptive_scheduler.py --morning
```

---

## 9. 텔레그램 알림 포맷

### 9.1 Override 알림

```
🔄 스케줄 자동 조정

📅 3/22 (토) 변경
  기존: 브릭 → 수영
  변경: 러닝 Easy 6km @6:00+
  사유: 어제 고강도(tempo, 부하 78) 후 회복 [A1]

📅 3/25 (화) 변경
  기존: 수영 수업
  변경: 러닝 Easy 6km (수영 대체)
  사유: 주간 러닝 1/3회 — 빈도 보충 [B1]
```

### 9.2 아침 컨디션 알림

```
🌅 오늘의 컨디션

BB 43 | HRV 28ms (avg 53) | 수면 5h44m | TR 4
⚠️ 컨디션 적색 — 오늘은 Easy or 휴식

📅 오늘 스케줄 변경
  기존: 러닝 + 코어 7km
  변경: Easy 또는 완전 휴식 [A3]
```

---

## 10. VDOT 히스토리 추적 (신규 필드)

workout_schedule.json에 주간 VDOT 기록을 추가하여 정체 판단에 활용:

```json
{
  "vdot_history": [
    {"week": "2026-03-16", "vdot": 36},
    {"week": "2026-03-23", "vdot": 38},
    {"week": "2026-03-30", "vdot": 39}
  ]
}
```

`update_vdot()` 호출 시 주 1회(일요일) vdot_history에 append.

---

## 11. 제약 사항 및 불가침 규칙

적응형 알고리즘이 절대 변경할 수 없는 사항:

| 불가침 | 근거 |
| ------ | ------ |
| 일요일 완전 휴식 | WORKOUT_MASTER §6 |
| 토요일 브릭 (부상 시 제외) | WORKOUT_MASTER §7: "절대 스킵 금지" |
| 연속 3일 러닝 | WORKOUT_MASTER §1 |
| 대회 주 스케줄 (5/4~5/10) | WORKOUT_MASTER Phase 3: 고정 |
| 80/20 강도 배분 | WORKOUT_ALGORITHM.md 핵심 원칙 |

---

## 12. 구현 순서 (제안)

| 단계 | 내용 | 난이도 | 우선순위 |
| ------ | ------ | ------ | ------ |
| 1 | BASE_SCHEDULE 정의 + override 읽기/쓰기 | 낮음 | P0 |
| 2 | A3 (컨디션 체크) — 이미 데이터 있음 | 낮음 | P0 |
| 3 | A1 (고강도 후 회복) | 낮음 | P0 |
| 4 | B1 (러닝 빈도 보충) | 중간 | P1 |
| 5 | A2 (운동 누락 재배치) | 중간 | P1 |
| 6 | Override 충돌 해소 + 정리 | 중간 | P1 |
| 7 | B2/B3 (주간 볼륨 조정) | 중간 | P2 |
| 8 | A4 (성과 상향) | 중간 | P2 |
| 9 | C1 (Phase 전환) | 높음 | P2 |
| 10 | C2 (VDOT 정체) + C3 (부상) | 높음 | P3 |
| 11 | 아침 컨디션 워크플로우 | 중간 | P3 |

---

## 부록: 현재 데이터 실증 (2026-03-21)

설계의 각 규칙이 현재 데이터에 적용되었을 때의 결과:

### 실제 데이터 (3/21)
- 운동: 수영 2275m + 자전거 32km + 러닝 5.22km @5:28 (브릭, 빌드업)
- Training Readiness: 4 (POOR)
- Body Battery max: 77 → min 5 (운동 후 고갈)
- HRV 지난밤: 63ms (주간 avg 53ms — 양호)
- 수면: 5h44m (6시간 미만)
- 80/20: Easy 23% / 고강도 77% (심각한 위반)
- 주간 부하: 351/300 (117%)

### 알고리즘 적용 결과

| 규칙 | 트리거 여부 | Override |
| ------ | ------ | ------ |
| A1 | YES — 오늘 tempo (브릭런 빌드업) | 3/22 → 완전 휴식 (일요일이므로 기본값과 동일) |
| A3-high | YES — TR 4 (POOR) | 오늘(3/21) → Easy (이미 운동 완료이므로 실효 없음. 아침 체크였으면 발동) |
| A3-medium | YES — 수면 5h44m | 오늘 → Easy 강도 경고 (이미 운동 완료) |
| B2 | NO — 117% < 120% | - |
| 80/20 | 경고 — Easy 23% | 텔레그램 경고: "Easy 비율 심각하게 낮음" |
| A4 | YES — 브릭런 후반 5:01/km, 목표 5:39 대비 -38초 | 다음 화요일 템포 페이스 상향 (단, A1과 충돌 시 A1 우선) |

**핵심 교훈**: 아침 06:00 컨디션 체크(A3)가 가장 중요. TR 4에서 수영+자전거+러닝 3종을 수행한 것은 과훈련 위험. 아침 체크가 있었으면 "오늘 Easy or 휴식" override가 발동하여 방지 가능했음.
