#!/usr/bin/env python3
"""훈련 대시보드 HTML 생성 (자동 업데이트용)"""
import json, re as _re
from pathlib import Path
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

BASE = Path(__file__).resolve().parent.parent
LOG_FILE = BASE / 'workout_log.json'
HEALTH_FILE = BASE / 'data' / 'garmin_health.json'
SCHED_FILE = BASE / 'workout_schedule.json'
OUT_FILE = BASE / 'data' / 'training_report.html'

import sys
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / '.env').exists():
            load_dotenv(_p / '.env')
            break
except ImportError:
    pass

try:
    from workout_analysis import estimate_finish_time, update_vdot
    _ANALYSIS_AVAILABLE = True
except Exception:
    _ANALYSIS_AVAILABLE = False


def _find_recent_race(log, within_days=120):
    """최근 N일 내 레이스(수영+자전거+러닝 복합 활동) 탐색.
    반환: (date_key, swim_m, t1_min, bike_m, t2_min, run_m, swim_pace_sec, bike_spd_kmh) 또는 None
    """
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    today = datetime.now(KST).date()
    cutoff = (today - timedelta(days=within_days)).strftime('%Y-%m-%d')

    for dk in sorted(log.keys(), reverse=True):
        if dk < cutoff:
            break
        entry = log[dk]
        if not entry.get('done'):
            continue
        all_m = entry.get('all_metrics', [])
        types = {m.get('type') for m in all_m}
        if not ({'swim', 'bike', 'run'} <= types):
            continue
        # 레이스 활동 확인 (수영 OW + 자전거 HR 높음)
        swim_m_list = [m for m in all_m if m.get('type') == 'swim' and (m.get('distance_m') or 0) > 500]
        bike_m_list = [m for m in all_m if m.get('type') == 'bike']
        run_m_list  = [m for m in all_m if m.get('type') == 'run']
        if not (swim_m_list and bike_m_list and run_m_list):
            continue

        swim_m = swim_m_list[0]
        bike_m_obj = bike_m_list[0]
        run_m_obj  = run_m_list[0]

        # start_time으로 T1/T2 계산
        def parse_hm(s):
            try:
                h, m_ = s.split(':')
                return int(h) * 60 + int(m_)
            except Exception:
                return None

        sw_start = parse_hm(swim_m.get('start_time', ''))
        bk_start = parse_hm(bike_m_obj.get('start_time', ''))
        rn_start = parse_hm(run_m_obj.get('start_time', ''))

        sw_dur = swim_m.get('duration_min', 30)
        bk_dur = bike_m_obj.get('duration_min', 70)

        t1 = (bk_start - (sw_start + sw_dur)) if (sw_start and bk_start) else 4.5
        t2 = (rn_start - (bk_start + bk_dur)) if (bk_start and rn_start) else 2.5
        t1 = max(1.0, min(t1, 15.0))
        t2 = max(0.5, min(t2, 10.0))

        # 표준 거리 환산
        swim_pace_sec = None
        p = swim_m.get('pace_per_100m', '')
        if p:
            try:
                pm, ps = p.split(':')
                swim_pace_sec = int(pm) * 60 + int(ps)
            except Exception:
                pass
        swim_1500 = (swim_pace_sec * 15 / 60) if swim_pace_sec else sw_dur * 1500 / max(swim_m.get('distance_m', 1500), 1)

        bike_spd = bike_m_obj.get('avg_speed_kmh', 0)
        bike_40  = (40 / bike_spd * 60) if bike_spd else bk_dur

        run_dist_km = run_m_obj.get('distance_km', 0)
        run_dur     = run_m_obj.get('duration_min') or (run_dist_km * 340 / 60 if run_dist_km else 57)
        run_10 = (run_dur / run_dist_km * 10) if run_dist_km else run_dur

        total = swim_1500 + t1 + bike_40 + t2 + run_10
        return {
            'date': dk,
            'swim': round(swim_1500, 1),
            't1':   round(t1, 1),
            'bike': round(bike_40, 1),
            't2':   round(t2, 1),
            'run_brick': round(run_10, 1),
            'total': round(total, 1),
            'avg_swim_pace_sec': swim_pace_sec or 134,
            'avg_bike_speed_kmh': round(bike_spd, 1) if bike_spd else 33.0,
            'vdot': None,
            'race_actual': {
                'swim_actual_min': round(sw_dur, 1),
                'swim_actual_m':   swim_m.get('distance_m', 0),
                'bike_actual_min': round(bk_dur, 1),
                'bike_actual_km':  bike_m_obj.get('distance_km', 0),
                'run_actual_min':  round(run_dur, 1),
                'run_actual_km':   run_dist_km,
            },
        }
    return None


def _compute_estimate(log, sched):
    """현재 체력 기반 예상 완주 분할 계산.
    우선순위: workout_analysis 동적 알고리즘 > last_analysis 폴백
    (레이스 실측값은 과거 고정값이므로 1순위에서 제외 — 훈련에 따라 동적으로 업데이트됨)
    """
    # 1순위: workout_analysis 동적 알고리즘 (VDOT + 최근 수영/바이크 데이터)
    if _ANALYSIS_AVAILABLE:
        try:
            est = estimate_finish_time(log)
            vdot = update_vdot(log)
            return est, vdot
        except Exception:
            pass

    # 3순위: last_analysis 폴백
    la = sched.get('last_analysis', {})
    splits = la.get('splits', {})
    if splits:
        est = {
            'swim': splits.get('swim_min', 0),
            't1': splits.get('t1_min', 4.5),
            'bike': splits.get('bike_min', 0),
            't2': splits.get('t2_min', 2.5),
            'run_brick': splits.get('run_min', 0),
            'total': splits.get('total_min', 0),
            'avg_swim_pace_sec': la.get('sport_paces', {}).get('swim_pace_100m', 0),
            'avg_bike_speed_kmh': la.get('sport_paces', {}).get('bike_speed_kmh', 0),
        }
        return est, la.get('vdot', 36)
    return None, sched.get('current_vdot', 36)

def days_until(d):
    target = datetime.strptime(d, '%Y-%m-%d').replace(tzinfo=KST)
    return (target.date() - datetime.now(KST).date()).days

def type_emoji(t):
    return {'swim':'🏊','run':'🏃','bike':'🚴','brick':'🏊→🚴→🏃','strength':'💪'}.get(t,'🏋')

def main():
    log = json.loads(LOG_FILE.read_text(encoding='utf-8'))
    health = json.loads(HEALTH_FILE.read_text(encoding='utf-8')) if HEALTH_FILE.exists() else {}
    sched_data = json.loads(SCHED_FILE.read_text(encoding='utf-8')) if SCHED_FILE.exists() else {}

    # 현재 체력 기반 예상 완주 계산 (페이지 상단 표시용)
    est, cur_vdot = _compute_estimate(log, sched_data)

    # ── 훈련 진단 (최근 14일) ──
    def _training_diagnosis(log, today_str):
        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        today_dt = datetime.strptime(today_str, '%Y-%m-%d').replace(tzinfo=KST)
        cutoff = (today_dt - timedelta(days=14)).strftime('%Y-%m-%d')

        swim_cnt = bike_cnt = run_cnt = brick_cnt = 0
        bike_km_total = run_km_total = 0
        last_run_date = None

        for dk in sorted(log.keys(), reverse=True):
            if dk < cutoff: break
            e = log[dk]
            types = {m.get('type') for m in e.get('all_metrics', [])}
            for m in e.get('all_metrics', []):
                t = m.get('type')
                if t == 'swim': swim_cnt += 1
                elif t == 'bike':
                    bike_cnt += 1
                    bike_km_total += m.get('distance_km') or (m.get('distance_m') or 0)/1000
                elif t == 'run':
                    run_cnt += 1
                    run_km_total += m.get('distance_km') or m.get('distance_m', 0)/1000
                    if last_run_date is None: last_run_date = dk
            if {'bike', 'run'} <= types: brick_cnt += 1

        run_gap_days = (today_dt - datetime.strptime(last_run_date, '%Y-%m-%d').replace(tzinfo=KST)).days if last_run_date else 99

        items = []
        # 러닝
        if run_gap_days >= 14:
            items.append({'sport': '🏃 러닝', 'status': 'bad',   'msg': f'{run_gap_days}일째 런 없음 — VDOT 정체·하락 위험'})
        elif run_cnt < 2:
            items.append({'sport': '🏃 러닝', 'status': 'warn',  'msg': f'주 {run_cnt/2:.1f}회 평균 — 목표(주 3회) 미달'})
        else:
            items.append({'sport': '🏃 러닝', 'status': 'good',  'msg': f'14일 {run_cnt}회 {run_km_total:.0f}km'})
        # 자전거
        if bike_km_total >= 100:
            items.append({'sport': '🚴 자전거', 'status': 'good', 'msg': f'14일 {bike_km_total:.0f}km — 볼륨 충분'})
        elif bike_km_total >= 50:
            items.append({'sport': '🚴 자전거', 'status': 'warn', 'msg': f'14일 {bike_km_total:.0f}km — 장거리 1회 추가 권장'})
        else:
            items.append({'sport': '🚴 자전거', 'status': 'bad',  'msg': f'14일 {bike_km_total:.0f}km — 라이딩 부족'})
        # 수영
        if swim_cnt >= 4:
            items.append({'sport': '🏊 수영', 'status': 'good',  'msg': f'14일 {swim_cnt}회 — 빈도 양호'})
        elif swim_cnt >= 2:
            items.append({'sport': '🏊 수영', 'status': 'warn',  'msg': f'14일 {swim_cnt}회 — 주 3회 목표 미달'})
        else:
            items.append({'sport': '🏊 수영', 'status': 'bad',   'msg': f'14일 {swim_cnt}회 — 빈도 부족'})
        # 브릭
        if brick_cnt >= 2:
            items.append({'sport': '🔗 브릭',  'status': 'good',  'msg': f'14일 {brick_cnt}회 — 전환 감각 유지'})
        elif brick_cnt == 1:
            items.append({'sport': '🔗 브릭',  'status': 'warn',  'msg': '14일 1회 — 주 1회 이상 권장'})
        else:
            items.append({'sport': '🔗 브릭',  'status': 'bad',   'msg': '14일 0회 — 자전거→런 전환 감각 저하 중'})

        bad_count = sum(1 for i in items if i['status'] == 'bad')
        warn_count = sum(1 for i in items if i['status'] == 'warn')
        if bad_count >= 2:
            verdict = ('red', '개선 필요', '핵심 종목 공백 발생. 이번 주 러닝·브릭 우선 복구 필요.')
        elif bad_count == 1 or warn_count >= 2:
            verdict = ('yellow', '부분 보완 필요', '자전거 기반은 좋음. 러닝·브릭 비중 높여야 대가야 목표 달성 가능.')
        else:
            verdict = ('green', '순항 중', '전 종목 균형 잡힘. 현재 페이스 유지.')

        return items, verdict

    # diag_items 는 today 문자열 정의 후 계산 (아래)

    # ── 목표별 처방 계산 ──
    # race_targets: (대회날짜, 이름, 목표분)
    race_targets = [
        ("2026-06-28", "대가야", 162),   # sub-2:42
        ("2026-08-27", "거제",  155),    # sub-2:35
    ]

    def _prescription(est, target_total_min):
        """현재 분할 → 목표 달성 처방 반환."""
        if not est:
            return None
        T12 = est.get('t1', 4.5) + est.get('t2', 2.5)
        sport_budget = target_total_min - T12
        cur_swim = est.get('swim', 33)
        cur_bike = est.get('bike', 75)
        cur_run  = est.get('run_brick', 61)
        cur_pure = cur_swim + cur_bike + cur_run
        gap = cur_pure - sport_budget
        if gap <= 0:
            return {'gap': gap, 'achieved': True}

        # 종목별 단축 목표 — swim 22% / bike 36% / run 42%
        # 수영은 39일 내 최대 2분 개선이 현실적 상한 (풀 pace 5초/100m 개선)
        need_swim = min(round(gap * 0.22, 1), 2.0)
        need_bike = round(gap * 0.36, 1)
        need_run  = round(gap - need_swim - need_bike, 1)

        tgt_swim = cur_swim - need_swim
        tgt_bike = cur_bike - need_bike
        tgt_run  = cur_run  - need_run

        # 목표 pace/speed 역산
        tgt_swim_pace = round(tgt_swim * 60 / 15)   # sec/100m  (1.5km = 15×100m)
        tgt_bike_spd  = round(40 / tgt_bike * 60, 1) # km/h
        # VDOT 역산: predict_10k_time(v)*brick_factor = tgt_run → 근사 탐색
        est_vdot = est.get('vdot', cur_vdot)
        tgt_vdot = est_vdot
        if _ANALYSIS_AVAILABLE:
            try:
                from workout_analysis import predict_10k_time
                brick = 1.06 * 0.97
                for v in range(est_vdot, est_vdot + 10):
                    if predict_10k_time(v) * brick <= tgt_run:
                        tgt_vdot = v
                        break
            except Exception:
                pass

        cur_swim_pace_s = f'{est.get("avg_swim_pace_sec",125)//60}:{est.get("avg_swim_pace_sec",125)%60:02d}'
        tgt_swim_pace_s = f'{tgt_swim_pace//60}:{tgt_swim_pace%60:02d}'
        cur_bike_spd    = est.get('avg_bike_speed_kmh', 32)

        # 종목별 핵심 훈련 처방
        swim_rx  = f'강습 주3 + 개인강습 지속, OW 경험 축적 → pace {cur_swim_pace_s}→{tgt_swim_pace_s}/100m'
        bike_rx  = f'장거리 라이딩 주1 (70km+) + 브릭 2회 → {cur_bike_spd:.0f}→{tgt_bike_spd}km/h'
        vdot_paces_str = ''
        if _ANALYSIS_AVAILABLE:
            try:
                from workout_analysis import get_vdot_paces, seconds_to_pace
                p = get_vdot_paces(tgt_vdot)
                vdot_paces_str = f'템포 {seconds_to_pace(p["tempo"])}/km'
            except Exception:
                pass
        run_rx = f'템포런 주1 ({vdot_paces_str}) → VDOT {est_vdot}→{tgt_vdot}'

        return {
            'gap': round(gap, 1),
            'achieved': False,
            'items': [
                {'sport': '🏊 수영',   'cur_t': cur_swim, 'tgt_t': round(tgt_swim,1), 'save': need_swim, 'rx': swim_rx},
                {'sport': '🚴 자전거', 'cur_t': cur_bike, 'tgt_t': round(tgt_bike,1), 'save': need_bike, 'rx': bike_rx},
                {'sport': '🏃 러닝',   'cur_t': cur_run,  'tgt_t': round(tgt_run,1),  'save': need_run,  'rx': run_rx},
            ],
        }

    races = [
        ("2026-06-07", "한강 쉬엄쉬엄 (1+20+10)", "T1 연습"),
        ("2026-06-21", "한강리버크로스 2km OW", "OW 적응"),
        ("2026-06-28", "대가야 스탠다드", "🎯 sub-2:42"),
        ("2026-08-27", "거제 스탠다드", "🎯 sub-2:35"),
    ]

    now = datetime.now(KST)
    today = now.strftime('%Y-%m-%d')
    start = datetime(2026, 3, 16, tzinfo=KST)

    # ── 날짜 연속 생성 (하루도 빠짐없이 3/16~오늘) ──
    all_dates = []
    cur = start
    while cur.strftime('%Y-%m-%d') <= today:
        all_dates.append(cur.strftime('%Y-%m-%d'))
        cur += timedelta(days=1)

    # ── 각 날짜 entry 구성 ──
    entries = []
    for dk in all_dates:
        e = log.get(dk) or {}
        actual = e.get('actual', '') or ''
        mets = e.get('all_metrics', [])
        total_tl = sum((m.get('training_load') or 0) for m in mets)
        h = health.get(dk) or {}
        hrv_d = h.get('hrv') or {}
        entries.append({
            'date': dk,
            'actual': actual,
            'total_tl': total_tl,
            'metrics': mets,
            'rhr': h.get('resting_hr'),
            'hrv_last': hrv_d.get('last_night'),
            'hrv_weekly': hrv_d.get('weekly_avg'),
            'sleep_min': (h.get('sleep') or {}).get('duration_min'),
            'log_metrics': e.get('metrics') or {},
            'is_rest': not actual.strip(),
        })

    # ── rolling 7일 TL 계산 ──
    tl_by_date = {e['date']: e['total_tl'] for e in entries}
    for i, e in enumerate(entries):
        window = [tl_by_date.get(all_dates[j], 0) for j in range(max(0, i-6), i+1)]
        e['tl_7d'] = int(sum(window))

    # 최근 7일 실적 (롤링 7일)
    from datetime import date as _date
    _today = _date.today()
    _week_start = str(_today - timedelta(days=6))
    week_entries = [e for e in entries if e['date'] >= _week_start]
    week_swim = sum(m.get('distance_m',0) or 0 for e in week_entries for m in e['metrics'] if m.get('type')=='swim')
    week_bike = sum((m.get('distance_m',0) or 0)/1000 for e in week_entries for m in e['metrics'] if m.get('type')=='bike')
    week_run  = sum((m.get('distance_m',0) or 0)/1000 for e in week_entries for m in e['metrics'] if m.get('type')=='run')
    week_tl   = sum(e['total_tl'] for e in week_entries)

    # ── 날짜 → 가민 활동 ID 매핑 (클릭 시 가민 커넥트로 이동) ──
    garmin_id_map = {}
    for dk in all_dates:
        mets = (log.get(dk) or {}).get('all_metrics', [])
        if mets:
            # TL이 가장 큰 활동 (대회처럼 여러 활동인 날은 메인 활동)
            best = max(mets, key=lambda m: m.get('training_load') or 0)
            gid = best.get('garmin_id')
            if gid:
                garmin_id_map[dk[5:]] = gid  # key: MM-DD

    # ── 그래프 데이터 (최근 60일) ──
    chart_entries = entries[-60:]
    chart_labels = [e['date'][5:] for e in chart_entries]  # MM-DD
    chart_tl7    = [e['tl_7d'] for e in chart_entries]
    # 종목별 TL 분리 (스택 바)
    def type_tl(e, t):
        return sum((m.get('training_load') or 0) for m in e['metrics'] if m.get('type') == t)
    chart_swim = [type_tl(e, 'swim') for e in chart_entries]
    chart_bike = [type_tl(e, 'bike') for e in chart_entries]
    chart_run  = [type_tl(e, 'run')  for e in chart_entries]
    chart_other= [max(0, e['total_tl'] - chart_swim[i] - chart_bike[i] - chart_run[i])
                  for i, e in enumerate(chart_entries)]
    # 대회 날짜에 vertical line 표시
    chart_race_dates = {r[0][5:] for r in races}

    now_str = now.strftime('%Y-%m-%d %H:%M KST')

    # today 문자열 정의 후 진단 계산
    diag_items, diag_verdict = _training_diagnosis(log, today)

    html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>철인3종 훈련 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:#0f0f13;color:#e0e0e0;padding:14px;max-width:960px;margin:0 auto}}
h1{{color:#fff;font-size:17px;margin-bottom:3px}}
.sub{{color:#555;font-size:11px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:9px;margin-bottom:16px}}
.card{{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:10px;padding:10px 13px}}
.card .val{{font-size:24px;font-weight:700;color:#7c6fff}}
.card .label{{font-size:11px;color:#888;margin-top:2px}}
.card .sub-val{{font-size:11px;color:#aaa;margin-top:3px}}
.section{{font-size:13px;font-weight:600;color:#fff;margin:14px 0 8px;border-left:3px solid #7c6fff;padding-left:8px}}
.chart-wrap{{background:#13131f;border:1px solid #2a2a4a;border-radius:10px;padding:12px;margin-bottom:16px;overflow-x:auto;-webkit-overflow-scrolling:touch}}
.chart-inner{{min-width:600px;height:180px}}
@media(max-width:640px){{.chart-inner{{height:200px}}}}
table{{width:100%;border-collapse:collapse;font-size:11.5px}}
th{{background:#1a1a2e;color:#777;padding:7px 7px;text-align:left;position:sticky;top:0;z-index:1}}
td{{padding:5px 7px;border-bottom:1px solid #1a1a28;vertical-align:middle}}
tr:hover{{background:#15152a}}
.badge{{display:inline-block;font-size:10.5px;padding:2px 5px;border-radius:7px;margin:1px;white-space:nowrap}}
.swim{{background:#0d2a45;color:#6ab4ff}}.bike{{background:#2d1e0d;color:#ffa06a}}
.run{{background:#0d2a1e;color:#6affa0}}.brick{{background:#2d0d2d;color:#ff6aff}}
.rest-row td{{color:#3a3a3a}}
.rest-badge{{color:#333;font-size:10px}}
.race-row td{{background:#1a1a2e!important;color:#ffd56c;font-weight:600}}
.today-row td{{background:#0d2a0d!important}}
.g{{color:#6affa0}}.y{{color:#ffd56c}}.r{{color:#ff6c6c}}.dim{{color:#444}}
.tl-bar-wrap{{display:inline-flex;align-items:center;gap:4px}}
.tl-bar{{width:36px;height:5px;background:#222;border-radius:2px;display:inline-block}}
.tl-fill{{height:100%;border-radius:2px}}
.tl7{{font-size:10px;color:#888}}
</style></head><body>
<h1>🏊‍♂️ 철인3종 훈련 대시보드</h1>
<div class="sub">업데이트: {now_str}</div>
"""

    # ── 코치 요약 카드 (최상단) ──
    vc = {'red': '#ff6c6c', 'yellow': '#ffd56c', 'green': '#6affa0'}.get(diag_verdict[0], '#888')
    vi = {'red': '❌', 'yellow': '⚠️', 'green': '✅'}.get(diag_verdict[0], '📊')

    # 다음 대회 + 예상 gap
    next_race_name, next_race_gap_str = '', ''
    if est:
        total_m_for_gap = est.get('total', 0)
        for rdate, rname, rgoal in races:
            if days_until(rdate) > 0 and 'sub-' in rgoal:
                th_, tm_ = rgoal.replace('🎯 sub-', '').split(':')
                target_min = int(th_)*60 + int(tm_)
                gap_min = round(total_m_for_gap - target_min)
                next_race_name = rname
                d_left = days_until(rdate)
                if gap_min > 0:
                    next_race_gap_str = f'D-{d_left} {rgoal} — 현재 {int(total_m_for_gap)//60}:{int(total_m_for_gap)%60:02d} 예상, <span style="color:#ff6c6c">-{gap_min}분 필요</span>'
                else:
                    next_race_gap_str = f'D-{d_left} {rgoal} — <span style="color:#6affa0">달성 가능 (+{abs(gap_min)}분 여유)</span>'
                break

    # 오늘 할 일
    today_plan = (sched_data.get('overrides', {}).get(today) or {}).get('workout', '스케줄 미등록')

    # 핵심 액션 — 러닝 공백 우선, 그 다음 bad/warn 순
    key_action = ''
    for item in diag_items:
        if '러닝' in item['sport'] and item['status'] in ('bad', 'warn'):
            key_action = item['msg']
            break
    if not key_action:
        for item in diag_items:
            if item['status'] == 'bad':
                key_action = item['msg']
                break
    if not key_action:
        for item in diag_items:
            if item['status'] == 'warn':
                key_action = item['msg']
                break

    html += f"""
<div style="background:#13131f;border:1px solid {vc}44;border-radius:12px;padding:14px 16px;margin-bottom:16px">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
    <span style="font-size:15px;font-weight:700;color:{vc}">{vi} {diag_verdict[1]}</span>
    <span style="font-size:11px;color:#555">— {diag_verdict[2]}</span>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px">
    <div><span style="color:#555">📅 오늘</span><br><span style="color:#ccc">{today_plan}</span></div>
    <div><span style="color:#555">🎯 다음 대회</span><br><span style="color:#ccc">{next_race_gap_str}</span></div>
    <div style="grid-column:1/-1"><span style="color:#555">🔥 핵심 과제</span><br><span style="color:{vc}">{key_action}</span></div>
  </div>
</div>

<div class="grid">
"""
    for rdate, rname, goal in races:
        d = days_until(rdate)
        col = '#ff6c6c' if d<7 else '#ffd56c' if d<21 else '#7c6fff'
        html += f'<div class="card"><div class="val" style="color:{col}">D-{d}</div><div class="label">{rname}</div><div class="sub-val">{goal}</div></div>\n'

    html += f"""<div class="card"><div class="val" style="color:#6affa0">{week_swim//100/10:.1f}km</div><div class="label">7일 수영</div></div>
<div class="card"><div class="val" style="color:#ffa06a">{week_bike:.0f}km</div><div class="label">7일 자전거</div></div>
<div class="card"><div class="val" style="color:#6ab4ff">{week_run:.0f}km</div><div class="label">7일 러닝</div></div>
<div class="card"><div class="val" style="color:#ff6aff">{int(week_tl)}</div><div class="label">7일 누적부하</div></div>
</div>
"""

    # ── 현재 체력 기반 예상 완주 vs 목표 섹션 ──
    if est:
        swim_m = est.get('swim', 0)
        t1_m = est.get('t1', 4.5)
        bike_m = est.get('bike', 0)
        t2_m = est.get('t2', 2.5)
        run_m = est.get('run_brick', 0)
        total_m = est.get('total', 0)
        pure_m = swim_m + bike_m + run_m
        swim_pace = est.get('avg_swim_pace_sec', 0)
        bike_spd = est.get('avg_bike_speed_kmh', 0)

        def _fmt_min(m):
            """총 시간 H:MM"""
            h, mn = divmod(int(m), 60)
            return f"{h}:{mn:02d}"

        def _fmt_mmss(m):
            """분할 시간 MM:SS"""
            total_sec = round(m * 60)
            mm, ss = divmod(total_sec, 60)
            return f"{mm}:{ss:02d}"

        def _fmt_pace(sec):
            if not sec: return '—'
            return f"{int(sec)//60}:{int(sec)%60:02d}"

        def _run_pace_from_min(run_min, dist_km=10.0):
            """러닝 예상 페이스 (분/km) → MM:SS/km"""
            if not run_min or not dist_km: return '—'
            pace_sec = run_min * 60 / dist_km
            return f"{int(pace_sec)//60}:{int(pace_sec)%60:02d}/km"

        # 가장 가까운 목표 대회 gap 계산
        # 대가야 목표: 2:42 = 162분, 거제 목표: 2:35 = 155분
        next_target_race = None
        next_target_min = None
        next_target_label = None
        for rdate, rname, rgoal in races:
            if days_until(rdate) > 0 and 'sub-' in rgoal:
                t_str = rgoal.replace('🎯 sub-', '')
                th, tm_str = t_str.split(':')
                target_total = int(th)*60 + int(tm_str)
                next_target_race = rname
                next_target_min = target_total
                next_target_label = rgoal
                break

        gap_html = ''
        if next_target_min and total_m:
            gap = total_m - next_target_min
            if gap > 0:
                gap_color = '#ff6c6c' if gap > 20 else '#ffd56c'
                gap_html = f'<div style="margin-top:6px;font-size:12px;color:{gap_color}">목표 {next_target_label} 까지 <b>-{gap:.0f}분</b> 필요</div>'
            else:
                gap_html = f'<div style="margin-top:6px;font-size:12px;color:#6affa0">✅ 목표 {next_target_label} 달성 가능 (+{abs(gap):.0f}분 여유)</div>'

        swim_pace_s = f'{_fmt_pace(swim_pace)}/100m' if swim_pace else '—'
        bike_spd_s  = f'{bike_spd:.1f}km/h' if bike_spd else '—'
        run_pace_s  = _run_pace_from_min(run_m)

        race_info = est.get('race_actual') if est else None
    race_date_label = f'대구 {est["date"][5:]} 실적 기반' if race_info else '알고리즘 추정'
    html += f"""
<div class="section">🏁 현재 체력 기반 예상 완주 <span style="font-size:11px;color:#555;font-weight:400">({race_date_label})</span></div>
<div style="background:#13131f;border:1px solid #2a2a4a;border-radius:10px;padding:14px 16px;margin-bottom:16px">
  <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
    <span style="font-size:32px;font-weight:700;color:#7c6fff">{_fmt_min(total_m)}</span>
    <span style="font-size:14px;color:#888">바꿈터 포함 전체</span>
    <span style="font-size:20px;font-weight:600;color:#6affa0;margin-left:8px">{_fmt_min(pure_m)}</span>
    <span style="font-size:13px;color:#888">순수 운동합계 (바꿈터 제외)</span>
  </div>
  {gap_html}
  <div style="margin-top:12px;display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:8px">
    <div style="background:#1a1a2e;border-radius:8px;padding:8px 12px">
      <div style="font-size:20px;font-weight:700;color:#6ab4ff">{_fmt_mmss(swim_m)}</div>
      <div style="font-size:11px;color:#777;margin-top:2px">🏊 수영 1.5km</div>
      <div style="font-size:11px;color:#6ab4ff;margin-top:1px">{swim_pace_s}</div>
    </div>
    <div style="background:#1a1a2e;border-radius:8px;padding:8px 12px">
      <div style="font-size:20px;font-weight:700;color:#ffa06a">{_fmt_mmss(bike_m)}</div>
      <div style="font-size:11px;color:#777;margin-top:2px">🚴 자전거 40km</div>
      <div style="font-size:11px;color:#ffa06a;margin-top:1px">{bike_spd_s}</div>
    </div>
    <div style="background:#1a1a2e;border-radius:8px;padding:8px 12px">
      <div style="font-size:20px;font-weight:700;color:#6affa0">{_fmt_mmss(run_m)}</div>
      <div style="font-size:11px;color:#777;margin-top:2px">🏃 러닝 10km</div>
      <div style="font-size:11px;color:#6affa0;margin-top:1px">{run_pace_s}</div>
    </div>
    <div style="background:#1a1a2e;border-radius:8px;padding:8px 12px;opacity:0.5">
      <div style="font-size:14px;font-weight:500;color:#666">T1 {_fmt_mmss(t1_m)} / T2 {_fmt_mmss(t2_m)}</div>
      <div style="font-size:11px;color:#444;margin-top:2px">바꿈터 (별도)</div>
    </div>
  </div>
</div>
"""

    # ── 훈련 진단 섹션 HTML ──
    v_color = {'red': '#ff6c6c', 'yellow': '#ffd56c', 'green': '#6affa0'}.get(diag_verdict[0], '#888')
    v_icon  = {'red': '❌', 'yellow': '⚠️', 'green': '✅'}.get(diag_verdict[0], '📊')
    s_color = {'good': '#6affa0', 'warn': '#ffd56c', 'bad': '#ff6c6c'}
    s_icon  = {'good': '✅', 'warn': '⚠️', 'bad': '❌'}
    html += f'<div class="section">📊 훈련 진단 (최근 14일)</div>\n'
    html += f'<div style="background:#13131f;border:1px solid #2a2a4a;border-radius:10px;padding:12px 16px;margin-bottom:16px">\n'
    html += f'<div style="font-size:14px;font-weight:600;color:{v_color};margin-bottom:8px">{v_icon} {diag_verdict[1]}</div>\n'
    html += f'<div style="font-size:12px;color:#888;margin-bottom:10px">{diag_verdict[2]}</div>\n'
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px">\n'
    for item in diag_items:
        sc = s_color[item['status']]
        si = s_icon[item['status']]
        html += (f'<div style="background:#1a1a2e;border-radius:7px;padding:8px 10px">'
                 f'<div style="font-size:12px;font-weight:600;color:{sc}">{si} {item["sport"]}</div>'
                 f'<div style="font-size:11px;color:#777;margin-top:2px">{item["msg"]}</div>'
                 f'</div>\n')
    html += '</div>\n</div>\n'

    # ── 목표 달성 처방 섹션 ──
    for r_date, r_name, r_target_min in race_targets:
        d_left = days_until(r_date)
        if d_left <= 0:
            continue
        rx = _prescription(est, r_target_min)
        if not rx:
            continue
        th, tm_ = divmod(r_target_min, 60)
        target_str = f'{th}:{tm_:02d}'

        if rx.get('achieved'):
            html += f'<div style="background:#0d2a0d;border:1px solid #2a4a2a;border-radius:10px;padding:12px 16px;margin-bottom:12px;font-size:13px;color:#6affa0">✅ {r_name} 목표 sub-{target_str} 이미 달성 가능 (현재 예상 {_fmt_min(est["total"])})</div>\n'
            continue

        gap = rx['gap']
        html += f'<div class="section">🎯 {r_name} sub-{target_str} 달성 처방 (D-{d_left}, -{gap:.0f}분 필요)</div>\n'
        html += '<table><thead><tr><th>종목</th><th>현재</th><th>목표</th><th>단축</th><th>핵심 훈련</th></tr></thead><tbody>\n'
        total_save = 0
        for item in rx['items']:
            total_save += item['save']
            save_c = '#6affa0' if item['save'] > 0 else '#888'
            html += (f'<tr>'
                     f'<td style="font-weight:600">{item["sport"]}</td>'
                     f'<td style="color:#aaa">{item["cur_t"]:.0f}분</td>'
                     f'<td style="color:#ffd56c">{item["tgt_t"]:.0f}분</td>'
                     f'<td style="color:{save_c};font-weight:600">-{item["save"]:.1f}분</td>'
                     f'<td style="color:#888;font-size:10.5px">{item["rx"]}</td>'
                     f'</tr>\n')
        # 합계 행
        html += (f'<tr style="background:#1a1a2e;font-weight:600">'
                 f'<td>합계</td>'
                 f'<td style="color:#aaa">{(est["swim"]+est["bike"]+est["run_brick"]):.0f}분</td>'
                 f'<td style="color:#6affa0">{(est["swim"]+est["bike"]+est["run_brick"]-total_save):.0f}분</td>'
                 f'<td style="color:#6affa0">-{total_save:.1f}분</td>'
                 f'<td style="color:#6affa0;font-size:11px">바꿈터({est["t1"]+est["t2"]:.0f}분) 포함 전체 → sub-{target_str} 달성</td>'
                 f'</tr>\n')
        # T1/T2 단축 가능성 주석
        t1_actual = est.get('t1', 4.5)
        if t1_actual > 3.5:
            t12_save = round(t1_actual - 3.0, 1)
            html += f'<div style="font-size:11px;color:#6ab4ff;margin:6px 0 12px;padding-left:4px">💡 바꿈터 단축 추가 가능: 대구 T1 {t1_actual:.0f}분 → 목표 3분 이하 = -{t12_save:.0f}분 (연습으로 해결 가능)</div>\n'
        else:
            html += '<div style="margin-bottom:12px"></div>\n'
        html += '</tbody></table>\n'

    html += f"""
<div class="section">훈련 부하 트렌드 (최근 60일)</div>
<div class="chart-wrap">
  <div class="chart-inner">
    <canvas id="tlChart"></canvas>
  </div>
</div>

<script>
const isMobile = window.innerWidth < 640;
// 날짜(MM-DD) → 가민 활동 ID 매핑
const garminIdMap = {json.dumps(garmin_id_map, ensure_ascii=False)};
// 모바일: 최근 30일, PC: 60일
const allLabels = {json.dumps(chart_labels, ensure_ascii=False)};
const allTl7    = {json.dumps(chart_tl7)};
const allSwim   = {json.dumps(chart_swim)};
const allBike   = {json.dumps(chart_bike)};
const allRun    = {json.dumps(chart_run)};
const allOther  = {json.dumps(chart_other)};
const raceDates = {json.dumps(list(chart_race_dates))};

const slice = isMobile ? 30 : 60;
const labels   = allLabels.slice(-slice);
const tl7Data  = allTl7.slice(-slice);
const swimData = allSwim.slice(-slice);
const bikeData = allBike.slice(-slice);
const runData  = allRun.slice(-slice);
const otherData= allOther.slice(-slice);

// 모바일: chart-inner 너비를 막대 수에 비례해 늘려 가독성 확보
const minWidth = Math.max(600, labels.length * (isMobile ? 18 : 14));
document.querySelector('.chart-inner').style.minWidth = minWidth + 'px';

new Chart(document.getElementById('tlChart'), {{
  data: {{
    labels: labels,
    datasets: [
      {{
        type: 'bar', label: '🏊 수영', data: swimData,
        backgroundColor: '#6ab4ff66', borderColor: '#6ab4ff', borderWidth: 1,
        stack: 'tl', order: 2,
      }},
      {{
        type: 'bar', label: '🚴 자전거', data: bikeData,
        backgroundColor: '#ffa06a66', borderColor: '#ffa06a', borderWidth: 1,
        stack: 'tl', order: 2,
      }},
      {{
        type: 'bar', label: '🏃 러닝', data: runData,
        backgroundColor: '#6affa066', borderColor: '#6affa0', borderWidth: 1,
        stack: 'tl', order: 2,
      }},
      {{
        type: 'bar', label: '기타', data: otherData,
        backgroundColor: '#7c6fff44', borderColor: '#7c6fff', borderWidth: 1,
        stack: 'tl', order: 2,
      }},
      {{
        type: 'line', label: '∑7일 누적',
        data: tl7Data,
        borderColor: '#fff', backgroundColor: 'transparent',
        borderWidth: 1.5, pointRadius: 0, tension: 0.3, order: 1,
      }},
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    onClick: (evt, elements) => {{
      if (!elements.length) return;
      const label = labels[elements[0].index];  // MM-DD
      const gid = garminIdMap[label];
      if (gid) {{
        window.open('https://connect.garmin.com/modern/activity/' + gid, '_blank');
      }}
    }},
    onHover: (evt, elements) => {{
      evt.native.target.style.cursor = elements.length ? 'pointer' : 'default';
    }},
    plugins: {{
      legend: {{ labels: {{ color: '#999', font: {{ size: isMobile ? 11 : 10 }}, boxWidth: 12, padding: 8 }} }},
      tooltip: {{
        mode: 'index', intersect: false,
        callbacks: {{
          footer: (items) => {{
            const label = items[0]?.label;
            return garminIdMap[label] ? '👆 탭하면 가민 상세 보기' : '';
          }}
        }}
      }},
    }},
    scales: {{
      x: {{
        stacked: true,
        ticks: {{ color: '#666', maxTicksLimit: isMobile ? 8 : 12, font: {{ size: isMobile ? 11 : 10 }} }},
        grid: {{ color: '#1a1a2a' }},
      }},
      y: {{
        stacked: true,
        ticks: {{ color: '#666', font: {{ size: isMobile ? 11 : 10 }} }},
        grid: {{ color: '#1a1a2a' }},
        beginAtZero: true,
      }},
    }},
  }}
}});
</script>

<div class="section">📅 훈련 계획 (오늘~14일)</div>
"""

    # 다음 14일 스케줄 표 생성
    sched_data = json.loads(SCHED_FILE.read_text(encoding='utf-8')) if SCHED_FILE.exists() else {}
    overrides = sched_data.get('overrides', {})
    race_dates_set = {r[0] for r in races}

    html += '<table><thead><tr><th>날짜</th><th>계획</th></tr></thead><tbody>\n'
    for i in range(15):
        d = datetime.now(KST) + timedelta(days=i)
        dk = d.strftime('%Y-%m-%d')
        dow = ['월','화','수','목','금','토','일'][d.weekday()]
        date_s = f"{dk[5:]} ({dow})"
        plan = (overrides.get(dk) or {}).get('workout', '')
        if not plan and dk in race_dates_set:
            plan = '🏁 대회'
        if not plan:
            # 고정 강습일 (월/수/금)
            if d.weekday() in (0,2,4):
                plan = '🏊 수영 강습 06시'
        if not plan:
            plan = '—'

        is_today_r = dk == today
        is_race_r = dk in race_dates_set
        plan_color = '#ffd56c' if is_race_r else ('#6affa0' if is_today_r else '#ccc')
        bg = ' style="background:#1a1a2e"' if is_race_r else (' style="background:#0d2a0d"' if is_today_r else '')
        date_color = '#ffd56c' if is_race_r else ('#fff' if is_today_r else '#888')
        html += f'<tr{bg}><td style="color:{date_color};font-weight:{"600" if is_today_r or is_race_r else "400"};white-space:nowrap">{date_s}</td><td style="color:{plan_color}">{plan}</td></tr>\n'
    html += '</tbody></table>'

    # ── 종목별 주차 트렌드 계산 ──
    sched_full = json.loads(SCHED_FILE.read_text(encoding='utf-8')) if SCHED_FILE.exists() else {}
    history = sched_full.get('analysis_history', [])
    trend_rows = []
    for h in history:
        ws = h.get('week_start', '')
        if not ws:
            continue
        sp = h.get('swim_pace_100m', 0)
        bs = h.get('bike_speed_kmh', 0)
        rm = h.get('run_min_standalone', 0)
        pm = h.get('pure_sport_min', 0)
        trend_rows.append((ws, sp, bs, rm, pm))
    trend_rows.sort(key=lambda x: x[0])

    # ── 날짜별 준수 여부 ──
    def _plan_types(text):
        t = (text or '').lower()
        types = set()
        if any(k in t for k in ('러닝', 'run', '달리기')): types.add('run')
        if any(k in t for k in ('수영', 'swim', 'ow')): types.add('swim')
        if any(k in t for k in ('자전거', 'bike', '사이클')): types.add('bike')
        if any(k in t for k in ('브릭', 'brick')): types.update({'run', 'bike'})
        return types

    def _actual_types(e):
        if not e or not e.get('done'): return set()
        ts = set()
        for m in (e.get('all_metrics') or []):
            if m.get('type') in ('swim', 'run', 'bike'): ts.add(m['type'])
        if not ts:
            a = (e.get('actual') or '').lower()
            if any(k in a for k in ('러닝', 'run')): ts.add('run')
            if any(k in a for k in ('수영', 'swim', 'ow')): ts.add('swim')
            if any(k in a for k in ('자전거', 'bike')): ts.add('bike')
        return ts

    compliance_map = {}
    overrides = sched_full.get('overrides', {})
    today_dt2 = datetime.now(KST)
    for dk in all_dates:
        d = datetime.strptime(dk, '%Y-%m-%d').replace(tzinfo=KST)
        if d.date() >= today_dt2.date():
            compliance_map[dk] = 'future'
            continue
        entry = log.get(dk) or {}
        planned_text = entry.get('planned') or (overrides.get(dk) or {}).get('workout', '')
        is_rest_p = any(k in (planned_text or '').lower() for k in ('휴식', 'rest'))
        plan_types = _plan_types(planned_text)
        act_types = _actual_types(entry)
        if is_rest_p and not act_types:
            compliance_map[dk] = 'rest'
        elif is_rest_p and act_types:
            compliance_map[dk] = 'extra'
        elif not plan_types:
            compliance_map[dk] = 'none'
        elif plan_types & act_types:
            compliance_map[dk] = 'ok'
        elif not act_types:
            compliance_map[dk] = 'miss'
        else:
            compliance_map[dk] = 'partial'

    html += f"""
<div class="section">훈련 기록 (3/16~, 전체 {len(all_dates)}일)</div>
<table><thead><tr>
<th>날짜</th><th>계획</th><th>운동 (준수)</th><th>부하 / 7일누적</th><th>수면</th><th>RHR</th><th>HRV</th>
</tr></thead><tbody>
"""

    for e in reversed(entries):
        dk = e['date']
        actual = e['actual']
        tl = e['total_tl']
        tl7 = e['tl_7d']
        rhr = e['rhr']
        hrv_l = e['hrv_last']
        hrv_w = e['hrv_weekly']
        sleep_min = e['sleep_min']
        is_rest = e['is_rest']

        dt = datetime.strptime(dk, '%Y-%m-%d')
        dow = ['월','화','수','목','금','토','일'][dt.weekday()]
        date_s = f"{dk[5:]} ({dow})"

        # 뱃지
        if is_rest:
            badges = '<span class="rest-badge">—</span>'
        else:
            single_m = e.get('log_metrics') or {}
            badges = ''
            for m in e['metrics']:
                t = m.get('type','')
                dist = m.get('distance_m') or 0
                pace = m.get('avg_pace','') or ''
                spd  = m.get('avg_speed') or 0
                if not dist:
                    patterns = {'bike': r'자전거\s+([\d.]+)km', 'run': r'러닝\s+([\d.]+)km',
                                'swim': r'수영\s+(\d+)m|OW\s+(\d+)m'}
                    pat = patterns.get(t, '')
                    if pat:
                        for chunk in actual.split('+'):
                            m2 = _re.search(pat, chunk.strip())
                            if m2:
                                v = next(g for g in m2.groups() if g)
                                dist = float(v) * (1000 if t in ('bike','run') else 1)
                                break
                if not spd and t=='bike' and single_m.get('avg_speed_kmh'):
                    spd = single_m['avg_speed_kmh'] / 3.6
                if t=='swim':   info = f"{int(dist)}m {pace}".strip()
                elif t=='bike': info = f"{dist/1000:.0f}km {spd*3.6:.1f}km/h".strip() if spd else f"{dist/1000:.0f}km"
                elif t=='run':  info = f"{dist/1000:.1f}km {pace}".strip()
                else:           info = f"{dist/1000:.1f}km" if dist else ''
                badges += f'<span class="badge {t}">{type_emoji(t)} {info}</span>'
            if not badges:
                badges = f'<span class="rest-badge">{actual[:60]}</span>'

        # 부하 + 7일 누적
        if tl:
            tl_pct = min(100, tl/600*100)
            tc = '#ff6c6c' if tl>400 else '#ffd56c' if tl>200 else '#7c6fff'
            # 7일 누적 색상
            t7c = '#ff6c6c' if tl7>900 else '#ffd56c' if tl7>500 else '#6affa0'
            tl_s = (f'<div class="tl-bar-wrap">'
                    f'<span style="color:{tc}">{int(tl)}</span>'
                    f'<div class="tl-bar"><div class="tl-fill" style="width:{tl_pct:.0f}%;background:{tc}"></div></div>'
                    f'</div>'
                    f'<div class="tl7" style="color:{t7c}">∑7d {tl7}</div>')
        else:
            t7c = '#ff6c6c' if tl7>900 else '#ffd56c' if tl7>500 else '#6affa0'
            tl_s = f'<div class="tl7" style="color:{t7c}">∑7d {tl7}</div>' if tl7 else '-'

        # 수면
        if sleep_min:
            sh,sm = sleep_min//60, sleep_min%60
            sc = 'g' if sleep_min>=420 else 'y' if sleep_min>=360 else 'r'
            sleep_s = f'<span class="{sc}">{sh}h{sm:02d}m</span>'
        else: sleep_s = '-'

        rhr_s = f'<span class="{"g" if (rhr or 99)<45 else "y" if (rhr or 99)<50 else "r"}">{rhr}</span>' if rhr else '-'

        def hc(v): return 'g' if (v or 0)>=60 else 'y' if (v or 0)>=45 else 'r' if v else 'dim'
        hrv_s = f'<span class="{hc(hrv_l)}">{hrv_l}</span>/<span class="{hc(hrv_w)}">{hrv_w}</span>' if (hrv_l and hrv_w) else (f'<span class="{hc(hrv_l)}">{hrv_l}</span>' if hrv_l else '-')

        # 계획 컬럼
        entry_log = log.get(dk) or {}
        planned_text = entry_log.get('planned') or (overrides.get(dk) or {}).get('workout', '')
        planned_short = (planned_text or '').replace('🏊 ','').replace('🏃 ','').replace('🚴 ','').replace('🏁 ','')
        planned_short = planned_short[:28] + ('…' if len(planned_short) > 28 else '')
        planned_s = f'<span style="color:#666;font-size:10.5px">{planned_short}</span>' if planned_short else '-'

        # 준수 마크
        comp = compliance_map.get(dk, 'none')
        comp_icon = {'ok': '✅', 'miss': '❌', 'partial': '⚠️', 'extra': '💪', 'rest': '😴', 'future': '', 'none': ''}.get(comp, '')
        badges_with_comp = f'{badges} {comp_icon}' if comp_icon else badges

        is_race_day = any(dk==r[0] for r in races)
        is_today_day = dk==today
        rc = ' class="race-row"' if is_race_day else (' class="today-row"' if is_today_day else (' class="rest-row"' if is_rest else ''))
        html += f'<tr{rc}><td>{date_s}</td><td>{planned_s}</td><td>{badges_with_comp}</td><td style="white-space:nowrap">{tl_s}</td><td>{sleep_s}</td><td>{rhr_s}</td><td>{hrv_s}</td></tr>\n'

    html += "</tbody></table>"

    # ── 종목별 실력 트렌드 섹션 ──
    if trend_rows:
        def fmt_pace(sec):
            if not sec: return '—'
            return f"{int(sec)//60}:{int(sec)%60:02d}"

        html += '<div class="section">📈 종목별 실력 트렌드 (주차별)</div>'
        html += '<table><thead><tr><th>주차</th><th>🏊 수영 pace/100m</th><th>🚴 자전거 km/h</th><th>🏃 러닝 10km 예측</th><th>순수 운동합계</th></tr></thead><tbody>'
        for i, (ws, sp, bs, rm, pm) in enumerate(trend_rows):
            d_ws = datetime.strptime(ws, '%Y-%m-%d')
            week_label = f"{ws[5:]} ~"
            sp_s = fmt_pace(sp) if sp else '—'
            bs_s = f'{bs:.1f}' if bs else '—'
            rm_s = f'{rm:.0f}분' if rm else '—'
            pm_h, pm_m = divmod(int(pm or 0), 60)
            pm_s = f'{pm_h}:{pm_m:02d}' if pm else '—'

            # 개선 화살표 vs 전주
            sp_trend = bs_trend = rm_trend = pm_trend = ''
            if i > 0:
                prev_sp = trend_rows[i-1][1]
                prev_bs = trend_rows[i-1][2]
                prev_rm = trend_rows[i-1][3]
                prev_pm = trend_rows[i-1][4]
                if sp and prev_sp and abs(sp - prev_sp) >= 2:
                    sp_trend = f' <span style="color:{"#6affa0" if sp < prev_sp else "#ff6c6c"}">{"▼" if sp < prev_sp else "▲"}</span>'
                if bs and prev_bs and abs(bs - prev_bs) >= 0.5:
                    bs_trend = f' <span style="color:{"#6affa0" if bs > prev_bs else "#ff6c6c"}">{"▲" if bs > prev_bs else "▼"}</span>'
                if rm and prev_rm and abs(rm - prev_rm) >= 0.5:
                    rm_trend = f' <span style="color:{"#6affa0" if rm < prev_rm else "#ff6c6c"}">{"▼" if rm < prev_rm else "▲"}</span>'
                if pm and prev_pm and abs(pm - prev_pm) >= 0.5:
                    pm_trend = f' <span style="color:{"#6affa0" if pm < prev_pm else "#ff6c6c"}">{"▼" if pm < prev_pm else "▲"}</span>'

            is_current = (i == len(trend_rows) - 1)
            row_style = ' style="background:#0d2a0d"' if is_current else ''
            html += f'<tr{row_style}><td style="color:#888;white-space:nowrap">{week_label}</td><td>{sp_s}{sp_trend}</td><td>{bs_s}{bs_trend}</td><td>{rm_s}{rm_trend}</td><td>{pm_s}{pm_trend}</td></tr>\n'
        html += '</tbody></table>'

    # 대회 일정 섹션
    all_races_ext = [
        ("2026-06-07", "한강 쉬엄쉬엄", "수영 1+자전거 20+러닝 10km", "T1 수트 탈의 연습 + 자전거 풀 push"),
        ("2026-06-21", "한강리버크로스스위밍", "OW 2km 도강", "OW 패닉 대응 실전 / 사이팅 거리 감"),
        ("2026-06-28", "고령 대가야 스탠다드", "수영 1.5+자전거 40+러닝 10km", "🎯 목표 sub-2:42 (5/10 -8:39)"),
        ("2026-08-27", "거제 스탠다드", "수영 1.5+자전거 40+러닝 10km", "🎯 목표 sub-2:35 (가을 시즌)"),
    ]
    html += '<div class="section">주요 대회 일정</div><table><thead><tr><th>D-day</th><th>대회</th><th>거리</th><th>목표/포인트</th></tr></thead><tbody>'
    for rdate, rname, rdist, rgoal in all_races_ext:
        d = days_until(rdate)
        if d < 0: dstr, dcol = f'완료 ({rdate[5:]})', '#444'
        elif d == 0: dstr, dcol = '🏁 오늘!', '#ff6c6c'
        else:
            dcol = '#ff6c6c' if d<7 else '#ffd56c' if d<21 else '#7c6fff'
            dstr = f'D-{d} ({rdate[5:]})'
        html += f'<tr><td style="color:{dcol};font-weight:600;white-space:nowrap">{dstr}</td><td>{rname}</td><td style="color:#888;font-size:10.5px">{rdist}</td><td style="color:#6affa0;font-size:10.5px">{rgoal}</td></tr>\n'
    html += '</tbody></table>'

    # ── 완주 대회 아카이브 ──
    race_records_file = BASE / 'data' / 'race_records.json'
    if race_records_file.exists():
        race_records = json.loads(race_records_file.read_text(encoding='utf-8'))
        if race_records:
            html += '<div class="section">🏅 완주 대회 아카이브</div>\n'
            for rec in race_records:
                rdate = rec.get('date', '')
                rname = rec.get('name', '')
                rtype = rec.get('type', '')
                official = rec.get('official_time', '')
                std_ext  = rec.get('standard_extrapolated', '')
                sp = rec.get('splits', {})
                swim_s = sp.get('swim', {})
                bike_s = sp.get('bike', {})
                run_s  = sp.get('run', {})
                t1 = sp.get('t1', '')
                t2 = sp.get('t2', '')
                notes = rec.get('notes', [])

                dt_r = datetime.strptime(rdate, '%Y-%m-%d')
                dow_r = ['월','화','수','목','금','토','일'][dt_r.weekday()]
                date_label = f"{rdate} ({dow_r})"

                html += f'<div style="background:#13131f;border:1px solid #2a2a4a;border-radius:10px;padding:14px 16px;margin-bottom:14px">\n'
                html += f'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:10px">\n'
                html += f'  <span style="font-size:16px;font-weight:700;color:#ffd56c">{rname}</span>\n'
                html += f'  <span style="font-size:12px;color:#666">{date_label} · {rtype}</span>\n'
                html += f'  <span style="font-size:22px;font-weight:700;color:#7c6fff;margin-left:auto">{official}</span>\n'
                if std_ext:
                    html += f'  <span style="font-size:11px;color:#555">(표준거리 환산 {std_ext})</span>\n'
                html += '</div>\n'

                # 분할 기록
                html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:6px;margin-bottom:10px">\n'
                for label, color, val, sub in [
                    ('🏊 수영', '#6ab4ff',
                     swim_s.get('time','—'),
                     f"{swim_s.get('distance_m','')}m · {swim_s.get('pace_per_100m','')}/100m"),
                    ('T1', '#555', t1, '바꿈터'),
                    ('🚴 자전거', '#ffa06a',
                     bike_s.get('time','—'),
                     f"{bike_s.get('distance_km','')}km · {bike_s.get('speed_kmh','')}km/h"),
                    ('T2', '#555', t2, '바꿈터'),
                    ('🏃 러닝', '#6affa0',
                     run_s.get('time','—'),
                     f"{run_s.get('distance_km','')}km · {run_s.get('pace_per_km','')}/km"),
                ]:
                    opacity = '0.4' if label in ('T1','T2') else '1'
                    html += (f'<div style="background:#1a1a2e;border-radius:7px;padding:7px 10px;opacity:{opacity}">'
                             f'<div style="font-size:16px;font-weight:600;color:{color}">{val}</div>'
                             f'<div style="font-size:10px;color:#666;margin-top:1px">{label}</div>'
                             f'<div style="font-size:10px;color:#444">{sub}</div>'
                             f'</div>\n')
                html += '</div>\n'

                # 시사점 노트
                if notes:
                    html += '<div style="border-top:1px solid #1a1a2e;padding-top:8px">\n'
                    html += '<div style="font-size:11px;color:#666;margin-bottom:5px">📝 시사점</div>\n'
                    for note in notes:
                        html += f'<div style="font-size:11.5px;color:#aaa;margin-bottom:3px;padding-left:8px">• {note}</div>\n'
                    html += '</div>\n'

                html += '</div>\n'

    html += f'\n<div style="color:#2a2a3a;font-size:10px;margin-top:12px;text-align:center">sy-workspace · {now_str}</div>\n</body></html>'

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(html, encoding='utf-8')
    n_rest = sum(1 for e in entries if e['is_rest'])
    print(f'[OK] {OUT_FILE} ({len(html)//1024}KB, {len(all_dates)}일, 운동 {len(all_dates)-n_rest}일 / 휴식 {n_rest}일)')

    # OneDrive 백업 (Mac 로컬 실행 시만)
    import os as _os, shutil as _shutil
    if not _os.environ.get('GITHUB_ACTIONS'):
        onedrive = Path.home() / 'Library' / 'CloudStorage' / 'OneDrive-개인' / '바탕 화면' / 'workspace' / 'workout_backup'
        if onedrive.parent.exists():
            try:
                onedrive.mkdir(parents=True, exist_ok=True)
                _shutil.copy2(OUT_FILE, onedrive / 'training_report.html')
                print(f'[OK] OneDrive 백업: {onedrive}/training_report.html')
            except Exception as e:
                print(f'[WARN] OneDrive 백업 실패: {e}')

if __name__ == '__main__':
    main()
