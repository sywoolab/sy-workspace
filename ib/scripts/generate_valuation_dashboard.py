#!/usr/bin/env python3
"""
IB 밸류에이션 대시보드 HTML 생성
- ib/data/valuation_*.md 파일 스캔 → HTML 대시보드 생성
- 다크테마, 아코디언 방식 기업 상세 펼치기
- 업사이드(UP/DOWN) 컬러 표시

출력: ib/data/valuation_dashboard.html
"""

import os
import re
import sys
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

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'


# ════════════════════════════════════════════════════════════
# Markdown 파싱
# ════════════════════════════════════════════════════════════

def parse_valuation_md(md_path: Path) -> dict:
    """valuation_*.md 파일에서 핵심 지표를 추출한다."""
    text = md_path.read_text(encoding='utf-8')
    result = {
        'file_path': md_path,
        'file_name': md_path.name,
        'corp_name': None,
        'analysis_date': None,
        'ltm_ebit': None,
        'net_debt': None,
        'ev_lo': None,
        'ev_hi': None,
        'current_mktcap': None,
        'upside_pct': None,
        'upside_dir': None,
        'raw_md': text,
    }

    # 기업명: "# 밸류에이션 초안: (기업명)" 줄
    m = re.search(r'^#\s*밸류에이션 초안:\s*(.+)$', text, re.MULTILINE)
    if m:
        result['corp_name'] = m.group(1).strip()

    # 분석 날짜: 파일명 valuation_XXX_YYYYMMDD.md
    m = re.search(r'valuation_.+_(\d{8})\.md$', md_path.name)
    if m:
        raw_date = m.group(1)
        try:
            result['analysis_date'] = datetime.strptime(raw_date, '%Y%m%d').strftime('%Y-%m-%d')
        except ValueError:
            result['analysis_date'] = raw_date

    # LTM EBIT: "| 영업이익(EBIT) |" 줄에서 LTM 컬럼 (3번째 |)
    # 테이블 형식: | 항목 | FY2024값 | LTM값 |
    m = re.search(r'\|\s*영업이익\(EBIT\)\s*\|[^|]*\|\s*([0-9,\-—]+)\s*\|', text)
    if m:
        val_str = m.group(1).replace(',', '').replace('—', '').strip()
        try:
            result['ltm_ebit'] = float(val_str)
        except ValueError:
            pass

    # 순차입금: "**순차입금**: (숫자)억원" 패턴
    m = re.search(r'\*\*순차입금\*\*:\s*([-0-9,]+)억원', text)
    if m:
        val_str = m.group(1).replace(',', '').strip()
        try:
            result['net_debt'] = float(val_str)
        except ValueError:
            pass

    # EV/EBIT 섹션의 Equity Value 행 파싱
    # "| **Equity Value** | **하단** | **상단** |" 형태
    ev_ebit_section = re.search(
        r'###\s*EV/EBIT.*?(?=###|\Z)',
        text,
        re.DOTALL,
    )
    if ev_ebit_section:
        section_text = ev_ebit_section.group(0)
        m_eq = re.search(
            r'\|\s*\*\*Equity Value\*\*\s*\|\s*\*\*([0-9,\-—]+)\*\*\s*\|\s*\*\*([0-9,\-—]+)\*\*\s*\|',
            section_text,
        )
        if m_eq:
            lo_str = m_eq.group(1).replace(',', '').replace('—', '').strip()
            hi_str = m_eq.group(2).replace(',', '').replace('—', '').strip()
            try:
                result['ev_lo'] = float(lo_str)
            except ValueError:
                pass
            try:
                result['ev_hi'] = float(hi_str)
            except ValueError:
                pass

    # 현재 시총: "**시가총액**: (숫자)억원" 패턴
    m = re.search(r'\*\*시가총액\*\*:\s*([0-9,]+)억원', text)
    if m:
        val_str = m.group(1).replace(',', '').strip()
        try:
            result['current_mktcap'] = float(val_str)
        except ValueError:
            pass

    # 업사이드: "UP/DOWN (숫자)%" 패턴
    m = re.search(r'\b(UP|DOWN)\s+([\d.]+)%', text)
    if m:
        result['upside_dir'] = m.group(1)
        try:
            result['upside_pct'] = float(m.group(2))
        except ValueError:
            pass

    return result


def scan_valuation_files() -> list:
    """DATA_DIR에서 valuation_*.md 파일 모두 스캔, 분석일 내림차순 정렬."""
    if not DATA_DIR.exists():
        return []
    files = sorted(DATA_DIR.glob('valuation_*.md'), reverse=True)
    results = []
    for f in files:
        try:
            parsed = parse_valuation_md(f)
            results.append(parsed)
        except Exception as e:
            print(f'  [WARN] 파싱 실패: {f.name} — {e}')
    return results


# ════════════════════════════════════════════════════════════
# Markdown → HTML 간단 변환
# ════════════════════════════════════════════════════════════

def md_to_html(md_text: str) -> str:
    """Markdown을 기본 HTML로 변환한다.
    지원: # h1/h2/h3, **bold**, | table |, > blockquote, - 목록
    """
    lines = md_text.split('\n')
    html_parts = []
    in_table = False
    in_list = False
    table_header_done = False

    def close_table():
        nonlocal in_table, table_header_done
        if in_table:
            html_parts.append('</tbody></table>')
            in_table = False
            table_header_done = False

    def close_list():
        nonlocal in_list
        if in_list:
            html_parts.append('</ul>')
            in_list = False

    def process_inline(text: str) -> str:
        """인라인 마크다운 (**bold**, *italic*) 변환."""
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        return text

    for line in lines:
        stripped = line.strip()

        # 빈 줄
        if not stripped:
            close_table()
            close_list()
            html_parts.append('<br>')
            continue

        # 제목
        if stripped.startswith('### '):
            close_table(); close_list()
            html_parts.append(f'<h3>{process_inline(stripped[4:])}</h3>')
            continue
        if stripped.startswith('## '):
            close_table(); close_list()
            html_parts.append(f'<h2>{process_inline(stripped[3:])}</h2>')
            continue
        if stripped.startswith('# '):
            close_table(); close_list()
            html_parts.append(f'<h2>{process_inline(stripped[2:])}</h2>')
            continue

        # 수평선
        if stripped.startswith('---'):
            close_table(); close_list()
            html_parts.append('<hr>')
            continue

        # 인용
        if stripped.startswith('> '):
            close_table(); close_list()
            html_parts.append(f'<blockquote>{process_inline(stripped[2:])}</blockquote>')
            continue

        # 목록
        if stripped.startswith('- ') or stripped.startswith('* '):
            close_table()
            if not in_list:
                html_parts.append('<ul>')
                in_list = True
            html_parts.append(f'<li>{process_inline(stripped[2:])}</li>')
            continue

        # 테이블
        if stripped.startswith('|'):
            close_list()
            cells = [c.strip() for c in stripped.split('|')[1:-1]]
            # 구분선 행 (------) 스킵
            if all(re.match(r'^[-: ]+$', c) for c in cells):
                continue
            if not in_table:
                html_parts.append('<table><thead><tr>')
                for c in cells:
                    html_parts.append(f'<th>{process_inline(c)}</th>')
                html_parts.append('</tr></thead><tbody>')
                in_table = True
                table_header_done = True
            else:
                html_parts.append('<tr>')
                for c in cells:
                    html_parts.append(f'<td>{process_inline(c)}</td>')
                html_parts.append('</tr>')
            continue

        # 일반 단락
        close_table(); close_list()
        html_parts.append(f'<p>{process_inline(stripped)}</p>')

    close_table()
    close_list()
    return '\n'.join(html_parts)


# ════════════════════════════════════════════════════════════
# HTML 생성
# ════════════════════════════════════════════════════════════

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: #0f0f13;
    color: #e0e0e0;
    font-family: 'Segoe UI', 'Noto Sans KR', sans-serif;
    font-size: 14px;
    line-height: 1.6;
    padding: 24px;
}
h1 {
    font-size: 22px;
    font-weight: 700;
    color: #a0a8ff;
    margin-bottom: 4px;
}
.subtitle {
    color: #888;
    font-size: 12px;
    margin-bottom: 28px;
}
h2 {
    font-size: 16px;
    font-weight: 600;
    color: #c4c4f0;
    border-left: 3px solid #7c6fff;
    padding-left: 10px;
    margin: 20px 0 12px 0;
}
h3 {
    font-size: 14px;
    font-weight: 600;
    color: #b0b0e0;
    margin: 14px 0 8px 0;
}
hr { border: none; border-top: 1px solid #2a2a4a; margin: 12px 0; }
blockquote {
    border-left: 3px solid #7c6fff;
    padding-left: 10px;
    color: #999;
    font-size: 12px;
    margin: 8px 0;
}
ul { padding-left: 20px; margin: 6px 0; }
li { margin: 3px 0; }
p { margin: 6px 0; }
br { display: block; margin: 2px 0; content: ""; }

/* 요약 테이블 */
.summary-table-wrap {
    overflow-x: auto;
    margin-bottom: 28px;
}
.summary-table {
    width: 100%;
    border-collapse: collapse;
    min-width: 760px;
}
.summary-table th {
    background: #1a1a2e;
    color: #9090c0;
    font-weight: 600;
    padding: 10px 12px;
    text-align: left;
    border-bottom: 2px solid #2a2a4a;
    white-space: nowrap;
}
.summary-table td {
    padding: 9px 12px;
    border-bottom: 1px solid #1e1e32;
    white-space: nowrap;
}
.summary-table tr.row-clickable:hover td {
    background: #1e1e38;
    cursor: pointer;
}
.summary-table .no-data { color: #555; }
.up   { color: #6affa0; font-weight: 600; }
.down { color: #ff6c6c; font-weight: 600; }

/* 아코디언 상세 카드 */
.detail-row td {
    padding: 0 !important;
    border-bottom: 2px solid #2a2a4a !important;
}
.detail-inner {
    display: none;
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 4px;
    padding: 20px 24px;
    margin: 4px 0 8px 0;
}
.detail-inner.open { display: block; }

/* 상세 내부 테이블 */
.detail-inner table {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
}
.detail-inner th, .detail-inner td {
    padding: 7px 10px;
    border-bottom: 1px solid #2a2a4a;
    text-align: left;
}
.detail-inner th {
    background: #13132a;
    color: #9090c0;
    font-weight: 600;
    white-space: nowrap;
}
.detail-inner td { color: #d0d0e8; }
.detail-inner strong { color: #e0e0ff; }
.detail-inner em { font-style: italic; color: #aaa; }

/* 빈 상태 */
.empty-state {
    text-align: center;
    color: #555;
    padding: 48px;
    background: #1a1a2e;
    border-radius: 6px;
}
"""

JS = """
function toggleDetail(idx) {
    var el = document.getElementById('detail-' + idx);
    if (!el) return;
    var isOpen = el.classList.contains('open');
    // 모두 닫기
    var all = document.querySelectorAll('.detail-inner');
    for (var i = 0; i < all.length; i++) {
        all[i].classList.remove('open');
    }
    // 클릭한 것 토글
    if (!isOpen) {
        el.classList.add('open');
    }
}
"""


def fmt_num(val, suffix='억') -> str:
    """숫자를 천 단위 콤마 포맷으로 변환. None이면 '—' 반환."""
    if val is None:
        return '—'
    try:
        return f'{val:,.0f}{suffix}'
    except Exception:
        return '—'


def upside_html(direction: str, pct: float) -> str:
    """업사이드 표시 HTML 반환."""
    if direction is None or pct is None:
        return '<span class="no-data">—</span>'
    css = 'up' if direction == 'UP' else 'down'
    return f'<span class="{css}">{direction} {pct:.1f}%</span>'


def build_summary_rows(records: list) -> str:
    """요약 테이블 행 HTML 생성."""
    rows = []
    for i, rec in enumerate(records):
        corp = rec['corp_name'] or rec['file_name']
        date = rec['analysis_date'] or '—'
        ebit = fmt_num(rec['ltm_ebit'])
        nd = fmt_num(rec['net_debt'])
        ev_lo = fmt_num(rec['ev_lo'])
        ev_hi = fmt_num(rec['ev_hi'])
        mktcap = fmt_num(rec['current_mktcap'])
        upside = upside_html(rec['upside_dir'], rec['upside_pct'])

        row = (
            f'<tr class="row-clickable" onclick="toggleDetail({i})">'
            f'<td>{corp}</td>'
            f'<td>{date}</td>'
            f'<td>{ebit}</td>'
            f'<td>{nd}</td>'
            f'<td>{ev_lo}</td>'
            f'<td>{ev_hi}</td>'
            f'<td>{mktcap}</td>'
            f'<td>{upside}</td>'
            f'</tr>'
        )
        rows.append(row)

        # 아코디언 상세 행
        detail_html = md_to_html(rec['raw_md'])
        detail_row = (
            f'<tr class="detail-row">'
            f'<td colspan="8">'
            f'<div class="detail-inner" id="detail-{i}">'
            f'{detail_html}'
            f'</div>'
            f'</td>'
            f'</tr>'
        )
        rows.append(detail_row)

    return '\n'.join(rows)


def generate_html(records: list) -> str:
    """전체 대시보드 HTML 문자열 반환."""
    now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')

    if not records:
        body_content = '<div class="empty-state"><p>분석 파일이 없습니다.</p><p style="font-size:12px;margin-top:8px;">python3 valuation_auto.py &lt;기업명&gt; 실행 후 재시도하세요.</p></div>'
    else:
        summary_rows = build_summary_rows(records)
        body_content = f"""
<div class="summary-table-wrap">
  <table class="summary-table">
    <thead>
      <tr>
        <th>기업명</th>
        <th>분석일</th>
        <th>LTM EBIT</th>
        <th>순차입금</th>
        <th>EV 하단</th>
        <th>EV 상단</th>
        <th>현재 시총</th>
        <th>업사이드</th>
      </tr>
    </thead>
    <tbody>
      {summary_rows}
    </tbody>
  </table>
</div>
"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IB 밸류에이션 대시보드</title>
  <style>
{CSS}
  </style>
</head>
<body>
  <h1>IB 밸류에이션 대시보드</h1>
  <p class="subtitle">업데이트: {now_str} &nbsp;|&nbsp; 분석 기업 {len(records)}개 &nbsp;|&nbsp; 행 클릭 시 상세 보기</p>

  <h2>분석 기업 요약</h2>
  {body_content}

  <script>
{JS}
  </script>
</body>
</html>"""
    return html


# ════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════

def main():
    print(f'[{datetime.now(KST).strftime("%Y-%m-%d %H:%M")}] IB 밸류에이션 대시보드 생성 시작')

    # 파일 스캔
    records = scan_valuation_files()
    print(f'  valuation 파일 {len(records)}개 발견')
    for rec in records:
        corp = rec['corp_name'] or rec['file_name']
        print(f'    - {corp} ({rec["analysis_date"] or "날짜 없음"})')

    # HTML 생성
    html = generate_html(records)

    # 저장
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_file = DATA_DIR / 'valuation_dashboard.html'
    out_file.write_text(html, encoding='utf-8')
    print(f'[OK] {out_file}')


if __name__ == '__main__':
    main()
