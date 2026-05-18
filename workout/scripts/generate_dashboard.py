#!/usr/bin/env python3
"""훈련 대시보드 HTML 생성 (자동 업데이트용)"""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

BASE = Path(__file__).resolve().parent.parent
LOG_FILE = BASE / 'workout_log.json'
HEALTH_FILE = BASE / 'data' / 'garmin_health.json'
SCHED_FILE = BASE / 'workout_schedule.json'
OUT_FILE = BASE / 'data' / 'training_report.html'

from pathlib import Path
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
    sched = json.loads(SCHED_FILE.read_text(encoding='utf-8')) if SCHED_FILE.exists() else {}

    races = [
        ("2026-06-07", "한강 쉬엄쉬엄 (1+20+10)", "T1 연습"),
        ("2026-06-21", "한강리버크로스 2km OW", "OW 적응"),
        ("2026-06-28", "대가야 스탠다드", "🎯 sub-2:42"),
        ("2026-08-27", "거제 스탠다드", "🎯 sub-2:35"),
    ]

    entries = []
    for dk in sorted(log.keys()):
        if dk < '2026-03-16': continue
        e = log[dk]
        actual = e.get('actual', '') or ''
        mets = e.get('all_metrics', [])
        total_tl = sum((m.get('training_load') or 0) for m in mets)
        h = health.get(dk) or {}
        hrv_d = h.get('hrv') or {}
        rhr = h.get('resting_hr')
        sleep_min = (h.get('sleep') or {}).get('duration_min')
        entries.append({
            'date': dk, 'actual': actual, 'total_tl': total_tl,
            'metrics': mets, 'rhr': rhr,
            'hrv_last': hrv_d.get('last_night'),
            'hrv_weekly': hrv_d.get('weekly_avg'),
            'sleep_min': sleep_min,
            'planned': e.get('planned',''),
        })

    now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')
    today = datetime.now(KST).strftime('%Y-%m-%d')

    # 이번주 실적 요약
    week_entries = [e for e in entries if e['date'] >= '2026-05-18']
    week_swim = sum(m.get('distance_m',0) or 0 for e in week_entries for m in e['metrics'] if m.get('type')=='swim')
    week_bike = sum((m.get('distance_m',0) or 0)/1000 for e in week_entries for m in e['metrics'] if m.get('type')=='bike')
    week_run = sum((m.get('distance_m',0) or 0)/1000 for e in week_entries for m in e['metrics'] if m.get('type')=='run')
    week_tl = sum(e['total_tl'] for e in week_entries)

    html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>철인3종 훈련 대시보드</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:#0f0f13;color:#e0e0e0;padding:16px;max-width:900px;margin:0 auto}}
h1{{color:#fff;font-size:18px;margin-bottom:4px}}
.sub{{color:#666;font-size:12px;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:18px}}
.card{{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:10px;padding:12px 14px}}
.card .val{{font-size:26px;font-weight:700;color:#7c6fff}}
.card .label{{font-size:12px;color:#888;margin-top:2px}}
.card .sub-val{{font-size:12px;color:#aaa;margin-top:4px}}
.section{{font-size:14px;font-weight:600;color:#fff;margin:16px 0 8px;border-left:3px solid #7c6fff;padding-left:8px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#1a1a2e;color:#888;padding:7px 8px;text-align:left;position:sticky;top:0}}
td{{padding:6px 8px;border-bottom:1px solid #1e1e2e;vertical-align:middle}}
tr:hover{{background:#15152a}}
.badge{{display:inline-block;font-size:11px;padding:2px 6px;border-radius:8px;margin:1px;white-space:nowrap}}
.swim{{background:#0d2a45;color:#6ab4ff}}.bike{{background:#2d1e0d;color:#ffa06a}}
.run{{background:#0d2a1e;color:#6affa0}}.brick{{background:#2d0d2d;color:#ff6aff}}
.rest{{color:#444}}
.g{{color:#6affa0}}.y{{color:#ffd56c}}.r{{color:#ff6c6c}}.dim{{color:#555}}
.race-row td{{background:#1a1a2e!important;color:#ffd56c;font-weight:600}}
.today-row td{{background:#1a2a1a!important}}
.tl-cell{{white-space:nowrap}}
.tl{{display:inline-block;height:5px;border-radius:2px;margin-left:4px;vertical-align:middle}}
</style></head><body>
<h1>🏊‍♂️ 철인3종 훈련 대시보드</h1>
<div class="sub">마지막 업데이트: {now_str}</div>

<div class="grid">
"""
    for rdate, rname, goal in races:
        d = days_until(rdate)
        if d < 0: continue
        col = '#ff6c6c' if d < 7 else '#ffd56c' if d < 21 else '#7c6fff'
        html += f'<div class="card"><div class="val" style="color:{col}">D-{d}</div><div class="label">{rname}</div><div class="sub-val">{goal}</div></div>\n'

    # 이번주 실적
    html += f"""<div class="card"><div class="val" style="color:#6affa0">{week_swim//100/10:.1f}km</div><div class="label">이번주 수영</div></div>
<div class="card"><div class="val" style="color:#ffa06a">{week_bike:.0f}km</div><div class="label">이번주 자전거</div></div>
<div class="card"><div class="val" style="color:#6ab4ff">{week_run:.0f}km</div><div class="label">이번주 러닝</div></div>
<div class="card"><div class="val" style="color:#ff6aff">{int(week_tl)}</div><div class="label">이번주 누적부하</div></div>
"""
    html += """</div>
<div class="section">훈련 기록 (최근 60일)</div>
<table><thead><tr><th>날짜</th><th>운동</th><th>부하</th><th>수면</th><th>RHR</th><th>HRV</th></tr></thead><tbody>
"""
    for e in sorted(entries, key=lambda x:x['date'], reverse=True)[:60]:
        dk = e['date']
        actual = e['actual']
        tl = e['total_tl']
        rhr = e['rhr']
        hrv_l = e['hrv_last']
        hrv_w = e['hrv_weekly']
        sleep_min = e['sleep_min']

        dt = datetime.strptime(dk, '%Y-%m-%d')
        dow = ['월','화','수','목','금','토','일'][dt.weekday()]
        date_s = f"{dk[5:]} ({dow})"

        badges = ''
        for m in e['metrics']:
            t = m.get('type','')
            dist = m.get('distance_m') or 0
            pace = m.get('avg_pace','') or ''
            spd = m.get('avg_speed') or 0
            if t=='swim': info=f"{int(dist)}m {pace}"
            elif t=='bike': info=f"{dist/1000:.0f}km {spd*3.6:.1f}km/h" if spd else f"{dist/1000:.0f}km"
            elif t=='run': info=f"{dist/1000:.1f}km {pace}"
            else: info=f"{dist/1000:.1f}km" if dist else ''
            badges+=f'<span class="badge {t}">{type_emoji(t)} {info.strip()}</span>'
        if not badges: badges='<span class="rest">휴식</span>'

        tl_pct = min(100, tl/600*100)
        tc = '#ff6c6c' if tl>400 else '#ffd56c' if tl>200 else '#7c6fff'
        tl_s = f'<span style="color:{tc}">{int(tl)}</span><span class="tl" style="width:{tl_pct*0.5:.0f}px;background:{tc}"></span>' if tl else '-'

        if sleep_min:
            sh,sm=sleep_min//60,sleep_min%60
            sc='g' if sleep_min>=420 else 'y' if sleep_min>=360 else 'r'
            sleep_s=f'<span class="{sc}">{sh}h{sm:02d}m</span>'
        else: sleep_s='-'

        rhr_s=f'<span class="{"g" if (rhr or 99)<45 else "y" if (rhr or 99)<50 else "r"}">{rhr}</span>' if rhr else '-'

        def hc(v): return 'g' if (v or 0)>=60 else 'y' if (v or 0)>=45 else 'r' if v else 'dim'
        hrv_s=f'<span class="{hc(hrv_l)}">{hrv_l}</span>/<span class="{hc(hrv_w)}">{hrv_w}</span>' if hrv_l and hrv_w else (f'<span class="{hc(hrv_l)}">{hrv_l}</span>' if hrv_l else '-')

        is_race = any(dk==r[0] for r in races)
        is_today = dk==today
        rc = ' class="race-row"' if is_race else (' class="today-row"' if is_today else '')
        html += f'<tr{rc}><td>{date_s}</td><td>{badges}</td><td class="tl-cell">{tl_s}</td><td>{sleep_s}</td><td>{rhr_s}</td><td>{hrv_s}</td></tr>\n'

    html += f"""</tbody></table>
<div style="color:#333;font-size:11px;margin-top:14px;text-align:center">
sy-workspace / workout_log.json · {now_str}</div>
</body></html>"""

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(html, encoding='utf-8')
    print(f'[OK] {OUT_FILE} ({len(html)//1024}KB, {len(entries)} entries)')

if __name__ == '__main__':
    main()
