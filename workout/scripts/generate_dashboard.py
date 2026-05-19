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
    for p in [Path(__file__).parents[1], Path(__file__).parents[3]]:
        if (p / '.env').exists():
            load_dotenv(p / '.env')
            break
except ImportError:
    pass

def days_until(d):
    target = datetime.strptime(d, '%Y-%m-%d').replace(tzinfo=KST)
    return (target.date() - datetime.now(KST).date()).days

def type_emoji(t):
    return {'swim':'🏊','run':'🏃','bike':'🚴','brick':'🏊→🚴→🏃','strength':'💪'}.get(t,'🏋')

def main():
    log = json.loads(LOG_FILE.read_text(encoding='utf-8'))
    health = json.loads(HEALTH_FILE.read_text(encoding='utf-8')) if HEALTH_FILE.exists() else {}

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

    # 이번주 실적
    week_entries = [e for e in entries if e['date'] >= '2026-05-18']
    week_swim = sum(m.get('distance_m',0) or 0 for e in week_entries for m in e['metrics'] if m.get('type')=='swim')
    week_bike = sum((m.get('distance_m',0) or 0)/1000 for e in week_entries for m in e['metrics'] if m.get('type')=='bike')
    week_run  = sum((m.get('distance_m',0) or 0)/1000 for e in week_entries for m in e['metrics'] if m.get('type')=='run')
    week_tl   = sum(e['total_tl'] for e in week_entries)

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

<div class="grid">
"""
    for rdate, rname, goal in races:
        d = days_until(rdate)
        col = '#ff6c6c' if d<7 else '#ffd56c' if d<21 else '#7c6fff'
        html += f'<div class="card"><div class="val" style="color:{col}">D-{d}</div><div class="label">{rname}</div><div class="sub-val">{goal}</div></div>\n'

    html += f"""<div class="card"><div class="val" style="color:#6affa0">{week_swim//100/10:.1f}km</div><div class="label">이번주 수영</div></div>
<div class="card"><div class="val" style="color:#ffa06a">{week_bike:.0f}km</div><div class="label">이번주 자전거</div></div>
<div class="card"><div class="val" style="color:#6ab4ff">{week_run:.0f}km</div><div class="label">이번주 러닝</div></div>
<div class="card"><div class="val" style="color:#ff6aff">{int(week_tl)}</div><div class="label">이번주 누적부하</div></div>
</div>

<div class="section">훈련 부하 트렌드 (최근 60일)</div>
<div class="chart-wrap">
  <div class="chart-inner">
    <canvas id="tlChart"></canvas>
  </div>
</div>

<script>
const isMobile = window.innerWidth < 640;
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
    plugins: {{
      legend: {{ labels: {{ color: '#999', font: {{ size: isMobile ? 11 : 10 }}, boxWidth: 12, padding: 8 }} }},
      tooltip: {{ mode: 'index', intersect: false }},
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

    html += f"""
<div class="section">훈련 기록 (3/16~, 전체 {len(all_dates)}일)</div>
<table><thead><tr>
<th>날짜</th><th>운동</th><th>부하 / 7일누적</th><th>수면</th><th>RHR</th><th>HRV</th>
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

        is_race_day = any(dk==r[0] for r in races)
        is_today_day = dk==today
        rc = ' class="race-row"' if is_race_day else (' class="today-row"' if is_today_day else (' class="rest-row"' if is_rest else ''))
        html += f'<tr{rc}><td>{date_s}</td><td>{badges}</td><td style="white-space:nowrap">{tl_s}</td><td>{sleep_s}</td><td>{rhr_s}</td><td>{hrv_s}</td></tr>\n'

    html += "</tbody></table>"

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
