#!/usr/bin/env python3
"""
IB 딜 소싱 신호 대시보드 HTML 생성
- deal_signals.json 읽어 HTML 대시보드 생성
- 출력: ib/data/deal_dashboard.html
"""

import os
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

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

BASE_DIR       = Path(__file__).resolve().parent.parent
DATA_DIR       = BASE_DIR / 'data'
SIGNAL_FILE    = DATA_DIR / 'deal_signals.json'
WATCHLIST_FILE = BASE_DIR / 'watchlist.json'
OUT_FILE       = DATA_DIR / 'deal_dashboard.html'

# 카테고리별 색상
CATEGORY_COLOR = {
    'M&A':    '#ffd56c',
    '자금조달': '#6ab4ff',
    '구조조정': '#ff6c6c',
    '자산매각': '#ff9f43',
    '자산인수': '#26de81',
    'EB/지분':  '#fd9644',
    '지분변동': '#a55eea',
    '자사주':   '#45aaf2',
    'IB이벤트': '#7c6fff',
    '경영변화': '#778ca3',
}

CATEGORY_EMOJI = {
    'M&A':    '🏢',
    '구조조정': '⚠️',
    '자산매각': '💰',
    '자산인수': '🛒',
    'EB/지분':  '🔄',
    '지분변동': '📊',
    '자금조달': '💵',
    '자사주':   '🔵',
    'IB이벤트': '📌',
    '경영변화': '🔀',
}


def load_watchlist():
    """워치리스트 기업명 집합 로드"""
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding='utf-8'))
        companies = data if isinstance(data, list) else data.get('companies', [])
        names = {c.get('name', '') for c in companies}
        # alias도 포함
        for c in companies:
            for alias in c.get('alias', []):
                names.add(alias)
        return names
    except Exception:
        return set()


def load_signals():
    """deal_signals.json 로드 — list of run_entries"""
    if not SIGNAL_FILE.exists():
        return []
    try:
        return json.loads(SIGNAL_FILE.read_text(encoding='utf-8'))
    except Exception:
        return []


def compute_summary(runs, watchlist_names):
    """최신 run 기준 요약 카드 데이터 산출"""
    if not runs:
        return {
            'total_companies': 0,
            'ma_companies': 0,
            'financing_companies': 0,
            'watchlist_hit': 0,
            'latest_run': '-',
        }

    latest = runs[-1]
    top_signals = latest.get('top_signals', [])

    total_companies  = len(top_signals)
    ma_companies     = sum(1 for s in top_signals if 'M&A' in s.get('categories', []))
    fin_companies    = sum(1 for s in top_signals if '자금조달' in s.get('categories', []))
    watchlist_hit    = sum(1 for s in top_signals if s.get('corp_name', '') in watchlist_names)

    return {
        'total_companies': total_companies,
        'ma_companies':    ma_companies,
        'financing_companies': fin_companies,
        'watchlist_hit':   watchlist_hit,
        'latest_run':      latest.get('run_date', '-'),
    }


def compute_weekly_trend(runs):
    """최근 8주 주간 신호 트렌드 (labels, data)"""
    recent = runs[-8:] if len(runs) >= 8 else runs
    labels = []
    data   = []
    for entry in recent:
        run_date = entry.get('run_date', '')
        # "2026-05-22 09:32 KST" → "05/22"
        try:
            dt_str = run_date.split(' ')[0]
            dt = datetime.strptime(dt_str, '%Y-%m-%d')
            label = dt.strftime('%m/%d')
        except Exception:
            label = run_date[:5] if run_date else '?'
        labels.append(label)
        data.append(len(entry.get('top_signals', [])))
    return labels, data


def score_color(score, max_score):
    """점수 비율에 따라 row 배경 강조 색상 반환"""
    if max_score == 0:
        return 'rgba(124, 111, 255, 0.0)'
    ratio = score / max_score
    if ratio >= 0.8:
        return 'rgba(124, 111, 255, 0.18)'
    elif ratio >= 0.5:
        return 'rgba(124, 111, 255, 0.09)'
    elif ratio >= 0.3:
        return 'rgba(124, 111, 255, 0.04)'
    return ''


def build_category_badges(categories):
    """카테고리 리스트 → HTML 배지 문자열"""
    badges = []
    for cat in sorted(categories):
        color = CATEGORY_COLOR.get(cat, '#7c6fff')
        emoji = CATEGORY_EMOJI.get(cat, '📌')
        badges.append(
            f'<span class="cat-badge" style="background:{color}22;color:{color};border:1px solid {color}66;">'
            f'{emoji} {cat}</span>'
        )
    return ' '.join(badges)


def build_table_rows(top_signals, watchlist_names):
    """TOP 신호 기업 테이블 행 생성"""
    if not top_signals:
        return '<tr><td colspan="5" style="text-align:center;color:#666;">데이터 없음</td></tr>'

    max_score = top_signals[0].get('total_score', 1) if top_signals else 1
    rows = []

    for i, sig in enumerate(top_signals, 1):
        corp_name  = sig.get('corp_name', '')
        corp_code  = sig.get('corp_code', '')
        score      = sig.get('total_score', 0)
        categories = sig.get('categories', [])
        top_report = sig.get('top_report', '').strip()

        on_watchlist = corp_name in watchlist_names
        star_html    = ' <span style="color:#ffd56c;" title="워치리스트">⭐</span>' if on_watchlist else ''

        # DART 기업 검색 링크
        dart_corp_url = f'https://dart.fss.or.kr/corp/searchCorpInfo.do?textCrpCik={corp_code}' if corp_code else 'https://dart.fss.or.kr'

        # 공시 링크 (top_report는 공시명이므로 검색 URL 사용)
        if top_report:
            report_html = f'<a href="https://dart.fss.or.kr/dsab007/main.do?autoSearch=true&textCrpCik={corp_code}" target="_blank" title="{top_report}">{top_report[:25]}{"…" if len(top_report) > 25 else ""}</a>'
        else:
            report_html = '-'

        cat_badges = build_category_badges(categories)
        bg_color   = score_color(score, max_score)
        bg_style   = f'background-color:{bg_color};' if bg_color else ''

        rows.append(f'''
        <tr style="{bg_style}">
            <td class="rank-col">{i}</td>
            <td class="name-col">
                <a href="{dart_corp_url}" target="_blank" class="corp-link">{corp_name}</a>{star_html}
            </td>
            <td class="score-col">
                <span class="score-badge">{score}</span>
            </td>
            <td class="cat-col">{cat_badges}</td>
            <td class="report-col">{report_html}</td>
        </tr>''')

    return '\n'.join(rows)


def build_html(runs, watchlist_names):
    """전체 HTML 생성"""
    now_str  = datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')
    summary  = compute_summary(runs, watchlist_names)
    wk_labels, wk_data = compute_weekly_trend(runs)

    latest_top = runs[-1].get('top_signals', []) if runs else []
    table_rows = build_table_rows(latest_top, watchlist_names)

    labels_js = json.dumps(wk_labels, ensure_ascii=False)
    data_js   = json.dumps(wk_data)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IB 딜 소싱 신호 대시보드</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: #0f0f13;
      color: #e0e0e0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Apple SD Gothic Neo', sans-serif;
      font-size: 14px;
      line-height: 1.6;
      padding: 0 0 48px 0;
    }}

    /* ── 헤더 ── */
    .header {{
      background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
      border-bottom: 1px solid #2a2a4a;
      padding: 28px 32px 20px;
    }}
    .header h1 {{
      font-size: 22px;
      font-weight: 700;
      color: #e0e0e0;
      letter-spacing: -0.3px;
    }}
    .header h1 span {{
      color: #7c6fff;
    }}
    .header .meta {{
      margin-top: 6px;
      font-size: 12px;
      color: #666;
    }}
    .header .meta strong {{
      color: #999;
    }}

    /* ── 콘텐츠 래퍼 ── */
    .content {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px 24px 0;
    }}

    /* ── 섹션 헤더 ── */
    .section-header {{
      border-left: 3px solid #7c6fff;
      padding-left: 12px;
      margin-bottom: 16px;
      font-size: 15px;
      font-weight: 600;
      color: #c0b8ff;
    }}

    /* ── 요약 카드 grid ── */
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 14px;
      margin-bottom: 32px;
    }}
    @media (max-width: 768px) {{
      .summary-grid {{ grid-template-columns: repeat(2, 1fr); }}
    }}

    .summary-card {{
      background: #1a1a2e;
      border: 1px solid #2a2a4a;
      border-radius: 10px;
      padding: 18px 16px;
      position: relative;
      overflow: hidden;
    }}
    .summary-card::before {{
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 3px;
      background: var(--accent, #7c6fff);
    }}
    .summary-card .label {{
      font-size: 11px;
      color: #666;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 8px;
    }}
    .summary-card .value {{
      font-size: 32px;
      font-weight: 700;
      color: var(--accent, #7c6fff);
    }}
    .summary-card .sub {{
      font-size: 11px;
      color: #555;
      margin-top: 4px;
    }}

    /* ── 차트 섹션 ── */
    .chart-section {{
      background: #1a1a2e;
      border: 1px solid #2a2a4a;
      border-radius: 10px;
      padding: 20px 24px;
      margin-bottom: 32px;
    }}
    .chart-section .section-header {{
      margin-bottom: 16px;
    }}
    .chart-wrap {{
      height: 220px;
      position: relative;
    }}

    /* ── 테이블 섹션 ── */
    .table-section {{
      background: #1a1a2e;
      border: 1px solid #2a2a4a;
      border-radius: 10px;
      padding: 20px 24px;
      margin-bottom: 32px;
      overflow-x: auto;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    thead th {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #666;
      border-bottom: 1px solid #2a2a4a;
      padding: 8px 12px;
      text-align: left;
      white-space: nowrap;
    }}
    tbody tr {{
      border-bottom: 1px solid #1e1e30;
      transition: background 0.15s;
    }}
    tbody tr:hover {{
      background: rgba(124, 111, 255, 0.07) !important;
    }}
    tbody td {{
      padding: 10px 12px;
      vertical-align: middle;
    }}

    .rank-col   {{ width: 48px; text-align: center; color: #555; font-size: 12px; }}
    .name-col   {{ min-width: 120px; }}
    .score-col  {{ width: 80px; text-align: center; }}
    .cat-col    {{ min-width: 180px; }}
    .report-col {{ font-size: 12px; color: #888; }}

    .corp-link {{
      color: #c0b8ff;
      text-decoration: none;
      font-weight: 600;
    }}
    .corp-link:hover {{ color: #7c6fff; text-decoration: underline; }}

    .score-badge {{
      display: inline-block;
      background: #2a2a4a;
      color: #7c6fff;
      border: 1px solid #3a3a6a;
      border-radius: 4px;
      padding: 2px 8px;
      font-size: 13px;
      font-weight: 700;
      font-family: monospace;
    }}

    .cat-badge {{
      display: inline-block;
      border-radius: 4px;
      padding: 2px 7px;
      font-size: 11px;
      font-weight: 600;
      margin: 1px 2px 1px 0;
      white-space: nowrap;
    }}

    .report-col a {{
      color: #6ab4ff;
      text-decoration: none;
    }}
    .report-col a:hover {{ text-decoration: underline; }}

    /* ── 푸터 ── */
    .footer {{
      text-align: center;
      color: #3a3a5a;
      font-size: 11px;
      padding-top: 16px;
    }}
  </style>
</head>
<body>

  <!-- 헤더 -->
  <div class="header">
    <h1>IB 딜 소싱 <span>신호 대시보드</span></h1>
    <div class="meta">
      생성: <strong>{now_str}</strong> &nbsp;|&nbsp;
      마지막 실행: <strong>{summary['latest_run']}</strong>
    </div>
  </div>

  <div class="content">

    <!-- 요약 카드 -->
    <div class="section-header">요약</div>
    <div class="summary-grid">
      <div class="summary-card" style="--accent:#7c6fff;">
        <div class="label">이번 주 신호 기업</div>
        <div class="value">{summary['total_companies']}</div>
        <div class="sub">최신 탐지 기준</div>
      </div>
      <div class="summary-card" style="--accent:#ffd56c;">
        <div class="label">M&amp;A 신호</div>
        <div class="value">{summary['ma_companies']}</div>
        <div class="sub">경영권·합병·공개매수</div>
      </div>
      <div class="summary-card" style="--accent:#6ab4ff;">
        <div class="label">자금조달 신호</div>
        <div class="value">{summary['financing_companies']}</div>
        <div class="sub">CB·BW·유증</div>
      </div>
      <div class="summary-card" style="--accent:#ffd56c;">
        <div class="label">워치리스트 히트 ⭐</div>
        <div class="value">{summary['watchlist_hit']}</div>
        <div class="sub">기존 관심 기업</div>
      </div>
    </div>

    <!-- 주간 트렌드 차트 -->
    <div class="chart-section">
      <div class="section-header">주간 신호 트렌드 (최근 {len(wk_labels)}회 탐지)</div>
      <div class="chart-wrap">
        <canvas id="trendChart"></canvas>
      </div>
    </div>

    <!-- TOP 신호 기업 테이블 -->
    <div class="table-section">
      <div class="section-header">TOP 신호 기업 (최신 탐지 기준)</div>
      <table>
        <thead>
          <tr>
            <th class="rank-col">#</th>
            <th class="name-col">기업명</th>
            <th class="score-col">점수</th>
            <th class="cat-col">카테고리</th>
            <th class="report-col">주요 공시</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </div>

    <div class="footer">
      ⭐ = 워치리스트 기업 &nbsp;|&nbsp; 기업명 클릭 → DART 기업 정보 &nbsp;|&nbsp; 점수 기준: M&amp;A 10점 ~ 약신호 3점
    </div>

  </div><!-- /content -->

  <script>
    // 주간 트렌드 차트
    (function() {{
      const ctx = document.getElementById('trendChart').getContext('2d');
      new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: {labels_js},
          datasets: [{{
            label: '신호 감지 기업 수',
            data: {data_js},
            backgroundColor: 'rgba(124, 111, 255, 0.55)',
            borderColor: 'rgba(124, 111, 255, 1)',
            borderWidth: 1,
            borderRadius: 4,
            borderSkipped: false,
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{
              backgroundColor: '#1a1a2e',
              borderColor: '#2a2a4a',
              borderWidth: 1,
              titleColor: '#c0b8ff',
              bodyColor: '#e0e0e0',
              callbacks: {{
                label: function(ctx) {{
                  return ' ' + ctx.parsed.y + '개사';
                }}
              }}
            }}
          }},
          scales: {{
            x: {{
              grid: {{ color: 'rgba(42,42,74,0.5)', drawBorder: false }},
              ticks: {{ color: '#666', font: {{ size: 11 }} }}
            }},
            y: {{
              beginAtZero: true,
              grid: {{ color: 'rgba(42,42,74,0.5)', drawBorder: false }},
              ticks: {{
                color: '#666',
                font: {{ size: 11 }},
                stepSize: 5,
              }}
            }}
          }}
        }}
      }});
    }})();
  </script>

</body>
</html>"""
    return html


def main():
    runs           = load_signals()
    watchlist_names = load_watchlist()

    html = build_html(runs, watchlist_names)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(html, encoding='utf-8')
    print(f'[OK] {OUT_FILE}')


if __name__ == '__main__':
    main()
