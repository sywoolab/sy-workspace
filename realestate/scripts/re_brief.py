"""
MS 부동산 Dashboard — HTML 생성 스크립트
- 상단: 시장 스냅샷 카드 + 청약 Today + 매수 신호등
- 하단: Top 20 관심 단지 표 + 청약 리스트 + 추천 로직 + 뉴스
- 데이터: scored_all.csv (실거래가 로우데이터 기반), market_config.json (수동)
- GitHub Pages: docs/re.html
"""
import os, json, csv, re, requests, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / '.env').exists():
            load_dotenv(_p / '.env'); break
except ImportError:
    pass

KST      = timezone(timedelta(hours=9))
NOW      = datetime.now(KST)
BASE_DIR = str(Path(__file__).resolve().parent.parent)
DOCS_DIR = str(Path(__file__).resolve().parent.parent.parent / 'docs')
HEADERS  = {'User-Agent': 'Mozilla/5.0'}

BOT_TOKEN = os.environ.get('REALESTATE_BOT_TOKEN') or os.environ.get('BOT_TOKEN', '')
CHAT_ID   = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')
PAGES_URL = 'https://sywoolab.github.io/sy-workspace/re.html'

# ─────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────

def load_config():
    with open(f'{BASE_DIR}/data/market_config.json', encoding='utf-8') as f:
        return json.load(f)


BUDGET_TABS = [11.6, 12.1, 12.6, 13.1, 13.6]


def load_top20(max_price=11.6):
    """scored_all.csv → 총점_실거주 기준 Top 20 (매수상한 이내)"""
    rows = []
    try:
        with open(f'{BASE_DIR}/data/scored_all.csv', encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                try:
                    price = float(r.get('매매가', 0) or 0)
                    if price > max_price:
                        continue
                    score = float(r.get('총점_실거주', 0) or 0)
                    rows.append({**r, '_score': score, '_price': price})
                except (ValueError, TypeError):
                    continue
        rows.sort(key=lambda x: -x['_score'])
        return rows[:20]
    except Exception as e:
        print(f'  [scored_all] err: {e}')
        return []


def load_chungyak():
    try:
        with open(f'{BASE_DIR}/data/chungyak/registry.json', encoding='utf-8') as f:
            d = json.load(f)
        return d.get('listings', [])
    except Exception as e:
        print(f'  [chungyak] err: {e}')
        return []


def load_watchlist():
    try:
        with open(f'{BASE_DIR}/data/watchlist_summary.json', encoding='utf-8') as f:
            d = json.load(f)
        return d.get('complexes', d) if isinstance(d, dict) else d
    except Exception as e:
        print(f'  [watchlist] err: {e}')
        return []


def fetch_realestate_news(n=5):
    """부동산 정책·시장 뉴스"""
    query = '부동산+매매+아파트+정책+청약+분양'
    url   = f'https://news.google.com/rss/search?q={query}+when:12h&hl=ko&gl=KR&ceid=KR:ko'
    arts  = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        for item in ET.fromstring(r.content).findall('.//item'):
            title   = re.sub(r'<[^>]+>', '', item.findtext('title', '')).strip()
            link    = item.findtext('link', '').strip()
            source  = item.findtext('source', '').strip()
            pub_raw = item.findtext('pubDate', '').strip()
            pub     = ''
            if pub_raw:
                try:
                    from email.utils import parsedate_to_datetime
                    pub = parsedate_to_datetime(pub_raw).astimezone(KST).strftime('%m/%d %H:%M')
                except Exception:
                    pass
            if title and link:
                arts.append({'title': title, 'link': link, 'source': source, 'pub': pub})
    except Exception as e:
        print(f'  [news] err: {e}')
    return arts[:n]


# ─────────────────────────────────────────
# HTML 스타일
# ─────────────────────────────────────────

HTML_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
         background: #f0f4f8; color: #1a202c; font-size: 14px; line-height: 1.5; }
  .container { max-width: 900px; margin: 0 auto; padding: 16px; }

  /* 헤더 */
  .header { background: linear-gradient(135deg, #1a3c5e 0%, #2d6a4f 100%);
            color: #fff; border-radius: 12px; padding: 20px 24px; margin-bottom: 14px; }
  .header h1 { font-size: 20px; font-weight: 800; letter-spacing: -0.3px; }
  .header .meta { font-size: 12px; color: rgba(255,255,255,0.65); margin-top: 4px; }
  .badge { display: inline-block; border-radius: 6px; padding: 2px 10px;
           font-size: 12px; font-weight: 700; margin-left: 8px; }
  .badge-yellow { background: rgba(251,191,36,0.3); color: #fef08a; }
  .badge-green  { background: rgba(52,211,153,0.3); color: #a7f3d0; }
  .badge-red    { background: rgba(248,113,113,0.3); color: #fca5a5; }

  /* 카드 */
  .card { background: #fff; border-radius: 10px; padding: 16px 18px;
          margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.07); }
  .card-title { font-size: 11px; font-weight: 700; text-transform: uppercase;
                letter-spacing: 0.8px; color: #6b7280; margin-bottom: 12px; }

  /* 시장 스냅샷 그리드 */
  .snap-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .snap-item { background: #f8fafc; border-radius: 8px; padding: 11px 13px; }
  .snap-item .label { font-size: 10px; color: #9ca3af; font-weight: 600;
                      text-transform: uppercase; letter-spacing: 0.5px; }
  .snap-item .value { font-size: 20px; font-weight: 800; margin: 3px 0; color: #111827; }
  .snap-item .sub   { font-size: 11px; color: #6b7280; }

  /* 신호등 */
  .signal-card { display: flex; align-items: center; gap: 16px; }
  .signal-dot  { width: 48px; height: 48px; border-radius: 50%;
                 display: flex; align-items: center; justify-content: center;
                 font-size: 22px; flex-shrink: 0; }
  .signal-yellow { background: #fef9c3; }
  .signal-green  { background: #dcfce7; }
  .signal-red    { background: #fee2e2; }
  .signal-info h3 { font-size: 15px; font-weight: 700; color: #1a202c; }
  .signal-info p  { font-size: 12px; color: #6b7280; margin-top: 2px; }

  /* 청약 카드 그리드 */
  .chungyak-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .chungyak-item { background: #f8fafc; border-radius: 8px; padding: 12px 14px;
                   border-left: 3px solid #2d6a4f; }
  .chungyak-item .cname { font-weight: 700; font-size: 14px; color: #1a202c; }
  .chungyak-item .ctype { font-size: 11px; color: #9ca3af; margin-top: 2px; }
  .chungyak-item .cloc  { font-size: 12px; color: #374151; margin-top: 4px; }
  .chungyak-item a { font-size: 11px; color: #2563eb; text-decoration: none; margin-top: 6px; display: inline-block; }
  .chungyak-item a:hover { text-decoration: underline; }

  /* 테이블 */
  .tbl-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead th { font-size: 11px; color: #6b7280; font-weight: 700; text-align: center;
             padding: 6px 8px; border-bottom: 2px solid #e5e7eb; white-space: nowrap; }
  tbody td { padding: 7px 8px; border-bottom: 1px solid #f3f4f6; text-align: center;
             white-space: nowrap; }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: #f9fafb; }
  .td-name { text-align: left; font-weight: 600; color: #1a202c; }
  .td-rank { font-weight: 700; color: #6b7280; width: 30px; }
  .score-high { color: #dc2626; font-weight: 700; }
  .score-mid  { color: #d97706; font-weight: 600; }
  .score-low  { color: #6b7280; }
  .trend-up  { color: #dc2626; font-weight: 600; }
  .trend-dn  { color: #1d4ed8; font-weight: 600; }
  .trend-nt  { color: #9ca3af; }
  .tag { display: inline-block; background: #f3f4f6; border-radius: 4px;
         padding: 1px 5px; font-size: 10px; color: #374151; }
  .tag-gap  { background: #dcfce7; color: #166534; }
  .tag-live { background: #dbeafe; color: #1e40af; }

  /* 로직 섹션 */
  .logic-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  .logic-item { background: #f8fafc; border-radius: 8px; padding: 10px 12px; }
  .logic-item .var { font-size: 11px; font-weight: 800; color: #374151; }
  .logic-item .desc { font-size: 11px; color: #6b7280; margin-top: 2px; }
  .logic-item .wt { font-size: 10px; margin-top: 4px; }
  .wt-gap  { color: #166534; }
  .wt-live { color: #1e40af; }

  /* 뉴스 */
  .news-item { padding: 7px 0; border-bottom: 1px solid #f3f4f6; display: flex; gap: 10px; }
  .news-item:last-child { border-bottom: none; }
  .news-num  { font-size: 11px; font-weight: 700; color: #9ca3af; min-width: 16px; padding-top: 2px; }
  .news-body { flex: 1; }
  .news-title { font-size: 13px; color: #2563eb; text-decoration: none; display: block; }
  .news-title:hover { text-decoration: underline; }
  .news-meta  { font-size: 10px; color: #9ca3af; margin-top: 1px; }

  /* 예산 탭 */
  .tab-bar { display: flex; gap: 4px; margin-bottom: 12px; flex-wrap: wrap; border-bottom: 2px solid #e5e7eb; padding-bottom: 0; }
  .tab-btn { padding: 7px 16px; font-size: 12px; font-weight: 700; border: 2px solid #e5e7eb;
             border-bottom: none; border-radius: 6px 6px 0 0; background: #f3f4f6;
             color: #6b7280; cursor: pointer; transition: all 0.15s; margin-bottom: -2px; }
  .tab-btn:hover { background: #e5e7eb; color: #374151; }
  .tab-btn.active { background: #1a3c5e; color: #fff; border-color: #1a3c5e; }
  .tab-pane { display: none; }
  .tab-pane.active { display: block; }

  /* 푸터 */
  .footer { text-align: center; font-size: 11px; color: #9ca3af; padding: 16px 0 8px; }

  @media (max-width: 600px) {
    .snap-grid { grid-template-columns: repeat(2, 1fr); }
    .chungyak-grid { grid-template-columns: 1fr; }
    .logic-grid { grid-template-columns: repeat(2, 1fr); }
  }
</style>
"""

# ─────────────────────────────────────────
# HTML 섹션 빌더
# ─────────────────────────────────────────

def _score_cls(s):
    try:
        v = float(s)
        return 'score-high' if v >= 70 else ('score-mid' if v >= 55 else 'score-low')
    except Exception:
        return 'score-low'

def _trend_cls(t):
    try:
        v = float(t)
        return 'trend-up' if v > 0 else ('trend-dn' if v < 0 else 'trend-nt')
    except Exception:
        return 'trend-nt'

def _fmt(val, suffix='', dec=1):
    try:
        return f'{float(val):.{dec}f}{suffix}'
    except Exception:
        return '-'


def _build_rows_html(top20):
    """Top 20 리스트 → <tr> HTML 문자열"""
    rows_html = ''
    for i, r in enumerate(top20, 1):
        name      = r.get('단지명', '')
        gu        = r.get('구', '')
        area      = r.get('면적', '')
        price     = _fmt(r.get('매매가'), '억')
        jeonse_r  = _fmt(r.get('KB전세가율'), '%', 1)
        trend     = r.get('추세', '')
        trend_str = f'{float(trend):+.1f}%' if trend else '-'
        commute   = _fmt(r.get('통근가중'), '분', 0)
        score     = r.get('총점_실거주', '')
        gap_cash  = r.get('갭필요현금', '')
        live_cash = r.get('실거주필요현금', '')
        vintage   = r.get('준공', '')
        trades    = r.get('매매건수', '')

        gap_cash_f  = float(gap_cash)  if gap_cash  else 99
        live_cash_f = float(live_cash) if live_cash else 99
        if gap_cash_f <= 5.5:
            tag = '<span class="tag tag-gap">갭</span>'
        elif live_cash_f <= 6.0:
            tag = '<span class="tag tag-live">실거주</span>'
        else:
            tag = ''

        t_cls = _trend_cls(trend)
        s_cls = _score_cls(score)

        rows_html += f"""
        <tr>
          <td class="td-rank">{i}</td>
          <td class="td-name">{tag} <a href="https://land.naver.com/search/index.nhn?query={requests.utils.quote(name+' '+gu)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">{name}</a></td>
          <td>{gu}</td>
          <td>{area}</td>
          <td><b>{price}</b></td>
          <td class="{t_cls}">{trend_str}</td>
          <td>{jeonse_r}</td>
          <td>{commute}</td>
          <td>{_fmt(gap_cash,'억') if gap_cash else '-'}</td>
          <td>{_fmt(live_cash,'억') if live_cash else '-'}</td>
          <td>{vintage}</td>
          <td>{trades}</td>
          <td class="{s_cls}"><b>{_fmt(score,'',1)}</b></td>
        </tr>"""
    return rows_html


def build_html(cfg, top20_scenarios, chungyak, watchlist, news):
    now      = NOW
    _days_ko = ['월','화','수','목','금','토','일']
    date_str = f'{now.strftime("%Y년 %m월 %d일")} ({_days_ko[now.weekday()]})'
    date_tag = now.strftime('%y%m%d')

    # 신호등
    sig_color = cfg['strategy']['color']
    sig_cls   = {'yellow': 'signal-yellow', 'green': 'signal-green', 'red': 'signal-red'}.get(sig_color, 'signal-yellow')
    sig_emoji = {'yellow': '🟡', 'green': '🟢', 'red': '🔴'}.get(sig_color, '🟡')
    badge_cls = {'yellow': 'badge-yellow', 'green': 'badge-green', 'red': 'badge-red'}.get(sig_color, 'badge-yellow')

    # ── 시장 스냅샷 ──────────────────────────────
    rates = cfg['rates']
    budget = cfg['budget']
    snap_html = ''
    for item in [
        (rates['base_rate']['label'],      f'{rates["base_rate"]["value"]}%',   '한국은행'),
        (rates['mortgage_fixed']['label'], f'{rates["mortgage_fixed"]["value"]}%', rates['mortgage_fixed']['note']),
        (rates['mortgage_var']['label'],   f'{rates["mortgage_var"]["value"]}%',  rates['mortgage_var']['note']),
        (rates['jeonse_loan']['label'],    f'{rates["jeonse_loan"]["value"]}%',   rates['jeonse_loan']['note']),
        ('현금 가용',  f'{budget["cash_avail"]/1e8:.0f}억원',  '세전 연 3억+ 기준'),
        ('매수 상한',  f'{budget["max_purchase"]/1e8:.1f}억원', '취득세 포함 총비용'),
        ('서울 전세가율', f'{cfg["market_index"]["seoul_jeonse_rate"]}%', cfg['market_index']['updated']),
        ('목표 매수창', cfg['strategy']['target_window'], cfg['strategy']['trigger'][:16]),
    ]:
        snap_html += f"""
        <div class="snap-item">
          <div class="label">{item[0]}</div>
          <div class="value">{item[1]}</div>
          <div class="sub">{item[2]}</div>
        </div>"""

    # ── 청약 Today ───────────────────────────────
    chungyak_html = ''
    for c in chungyak[:6]:
        name  = c.get('name','')
        ctype = c.get('type','')
        loc   = c.get('location_summary','')[:30]
        url   = c.get('apply_url','#')
        status = c.get('status', {})
        sched  = c.get('schedule', {})
        sched_str = ''
        if sched:
            sched_str = sched[0]["start"][:10] if sched else ""
        chungyak_html += f"""
        <div class="chungyak-item">
          <div class="cname">{name}</div>
          <div class="ctype">{ctype}</div>
          <div class="cloc">📍 {loc}</div>
          {f'<div class="cloc">📅 {sched_str}</div>' if sched_str else ''}
          <a href="{url}" target="_blank" rel="noopener">신청 →</a>
        </div>"""
    if not chungyak_html:
        chungyak_html = '<p style="color:#9ca3af;font-size:13px">등록된 청약 없음</p>'

    # ── Top 20 예산 탭 HTML ────────────────────────
    _thead = """
          <thead>
            <tr>
              <th>#</th><th style="text-align:left">단지명</th><th>구</th><th>면적</th>
              <th>매매가</th><th>추세</th><th>전세가율</th><th>통근</th>
              <th>갭현금</th><th>실거주현금</th><th>준공</th><th>거래수</th><th>총점</th>
            </tr>
          </thead>"""

    tab_btns = ''
    tab_panes = ''
    for i, budget in enumerate(BUDGET_TABS):
        active_cls = ' active' if i == 0 else ''
        tab_btns += f'<button class="tab-btn{active_cls}" onclick="switchReTab(this,{i})">{budget:.1f}억</button>\n      '
        rows = top20_scenarios.get(budget, [])
        rows_html = _build_rows_html(rows)
        tab_panes += f"""
        <div class="tab-pane{active_cls}" id="re-tab-{i}">
          <div class="tbl-wrap">
            <table>{_thead}
              <tbody>{rows_html}
              </tbody>
            </table>
          </div>
        </div>"""

    # ── 추천 로직 설명 ────────────────────────────
    LOGIC = [
        ('S1', '가격 추세', '최근 거래가 상승률', '갭30/실25/전10'),
        ('S2', '가격대', '매수상한(11.6억) 이내 적합도', '갭15/실10/전5'),
        ('S3', '거래량', '실거래 건수 유동성', '갭10/실10/전10'),
        ('S4', '통근', '여의도×0.4+청계산×0.6', '갭15/실25/전30'),
        ('S5', '독립문', '친가(독립문역) 접근성', '갭5/실10/전15'),
        ('S6', '연식', '준공연도(신축일수록↑)', '갭5/실10/전10'),
        ('S7', '할인율', 'KB시세 대비 실거래가', '갭5/실10/전5'),
        ('S8', '전세가율', 'KB전세가율(높을수록↑)', '갭15/실0/전15'),
    ]
    logic_html = ''
    for var, name, desc, wts in LOGIC:
        gap_w, live_w, wait_w = wts.split('/')
        logic_html += f"""
        <div class="logic-item">
          <div class="var">{var} · {name}</div>
          <div class="desc">{desc}</div>
          <div class="wt">
            <span class="wt-gap">갭{gap_w[1:]}</span> &nbsp;
            <span class="wt-live">실{live_w[2:]}</span>
          </div>
        </div>"""

    # ── 뉴스 ─────────────────────────────────────
    news_html = ''
    for i, a in enumerate(news, 1):
        news_html += f"""
        <div class="news-item">
          <div class="news-num">{i}</div>
          <div class="news-body">
            <a class="news-title" href="{a['link']}" target="_blank" rel="noopener">{a['title']}</a>
            <div class="news-meta">{a.get('source','')}  {a.get('pub','')}</div>
          </div>
        </div>"""
    if not news_html:
        news_html = '<p style="color:#9ca3af;font-size:13px">뉴스 없음</p>'

    # ── 최종 조립 ─────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{date_tag} MS 부동산 Dashboard</title>
  <meta property="og:title" content="{date_tag} MS 부동산 Dashboard">
  <meta property="og:description" content="{date_str} 서울/분당 신혼부부 매수 의사결정 대시보드">
  {HTML_STYLE}
</head>
<body>
<div class="container">

  <div class="header">
    <h1>🏠 MS 부동산 Dashboard <span class="badge {badge_cls}">{cfg['strategy']['current_rec']}</span></h1>
    <div class="meta">{date_str} &nbsp;·&nbsp; {now.strftime('%H:%M')} KST &nbsp;·&nbsp; 실거래가 기준 · 매수자: 무주택 신혼부부 · 서울/분당</div>
  </div>

  <!-- 1. 시장 스냅샷 -->
  <div class="card">
    <div class="card-title">📊 시장 스냅샷 &nbsp;<span style="font-size:10px;color:#9ca3af">금리: 수동 업데이트 ({cfg['_meta']['updated']}) &nbsp;|&nbsp; 시장 상태</span></div>
    <div class="snap-grid">{snap_html}
    </div>
  </div>

  <!-- 2. 매수 신호등 -->
  <div class="card">
    <div class="card-title">🎯 매수 전략 현황</div>
    <div class="signal-card">
      <div class="signal-dot {sig_cls}">{sig_emoji}</div>
      <div class="signal-info">
        <h3>{cfg['strategy']['current_rec']}</h3>
        <p>목표 창: {cfg['strategy']['target_window']}</p>
        <p style="margin-top:2px">{cfg['strategy']['trigger']}</p>
      </div>
    </div>
  </div>

  <!-- 3. 청약 Today -->
  <div class="card">
    <div class="card-title">📋 청약·임대 등록 단지 ({len(chungyak)}개) &nbsp;<span style="font-size:10px;color:#9ca3af">소득 제한 없는 줍줍·임의공급 포함</span></div>
    <div class="chungyak-grid">{chungyak_html}
    </div>
  </div>

  <!-- 4. Top 20 관심 단지 (예산 탭) -->
  <div class="card">
    <div class="card-title">🏆 관심 단지 Top 20 &nbsp;<span style="font-size:10px;color:#9ca3af">실거주 총점 기준 · 탭별 매수상한 적용 · 데이터: scored_all.csv ({NOW.strftime('%Y-%m-%d')} 기준)</span></div>
    <div class="tab-bar">
      {tab_btns}
    </div>
    {tab_panes}
    <div style="font-size:11px;color:#9ca3af;margin-top:8px">
      단지명 클릭 → 네이버 부동산 검색 &nbsp;|&nbsp; 갭현금 ≤5.5억: 갭투자 적합 &nbsp;|&nbsp; 실거주현금 ≤6억: 실거주 가능
    </div>
  </div>

  <!-- 5. 추천 로직 -->
  <div class="card">
    <div class="card-title">⚙️ 추천 로직 — 스코어링 변수 (S1~S8)</div>
    <div class="logic-grid">{logic_html}
    </div>
    <div style="margin-top:12px;font-size:12px;color:#6b7280;background:#f8fafc;border-radius:8px;padding:10px 12px">
      <b>전략별 가중치</b>: 갭투자(S1:30, S4:15, S2:15, S8:15) · 실거주(S4:25, S1:25, S6:10, S5:10) · 전월세(S4:30, S8:15, S5:15, S1:10)<br>
      <b>매수자 기준</b>: 현금 6억 · 여의도 + 청계산입구 통근 각 60분 이내 · 매매가 11.6억 이하 · 부부 무주택 생애최초 (LTV 70%)
    </div>
  </div>

  <!-- 6. 부동산 뉴스 -->
  <div class="card">
    <div class="card-title">📰 부동산 뉴스 (최근 12시간)</div>
    {news_html}
  </div>

  <div class="footer">
    MS 부동산 Dashboard &nbsp;·&nbsp; 가격: 국토부 실거래가 API 로우데이터 기반 (추정치 포함 금지) &nbsp;·&nbsp; 생성: {now.strftime('%Y-%m-%d %H:%M')} KST
  </div>
</div>
<script>
function switchReTab(btn, idx) {{
  document.querySelectorAll('.tab-btn').forEach(function(b){{ b.classList.remove('active'); }});
  document.querySelectorAll('.tab-pane').forEach(function(p){{ p.classList.remove('active'); }});
  btn.classList.add('active');
  document.getElementById('re-tab-' + idx).classList.add('active');
}}
</script>
</body>
</html>"""


# ─────────────────────────────────────────
# 텔레그램 발송
# ─────────────────────────────────────────

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print('  REALESTATE_BOT_TOKEN / CHAT_ID 누락 — 발송 스킵')
        return
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    resp = requests.post(url, data={
        'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML',
        'disable_web_page_preview': 'false',
    }, timeout=30)
    if not resp.json().get('ok'):
        print(f'  전송 실패: {resp.text[:200]}')


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

def main():
    print(f'[{NOW}] MS 부동산 Dashboard 생성')
    cfg      = load_config()
    top20_scenarios = {b: load_top20(b) for b in BUDGET_TABS}
    chungyak = load_chungyak()
    watchlist = load_watchlist()
    news     = fetch_realestate_news(5)
    for b, rows in top20_scenarios.items():
        print(f'  단지 ({b:.1f}억): {len(rows)}개')
    print(f'  청약: {len(chungyak)}개 / 뉴스: {len(news)}건')

    html = build_html(cfg, top20_scenarios, chungyak, watchlist, news)

    os.makedirs(DOCS_DIR, exist_ok=True)
    out = os.path.join(DOCS_DIR, 're.html')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'  저장: {out}')

    # 텔레그램 (주 1회 실행 시에만)
    date_tag = NOW.strftime('%y%m%d')
    tg = (
        f'<b>🏠 {date_tag} MS 부동산 Dashboard</b>\n\n'
        f'<a href="{PAGES_URL}">{date_tag} | {PAGES_URL}</a>\n\n'
        f'📊 관심단지 {len(top20_scenarios[BUDGET_TABS[0]])}개(11.6억 기준) · 청약 {len(chungyak)}개\n'
        f'🎯 전략: {cfg["strategy"]["current_rec"]}'
    )
    send_telegram(tg)
    print(f'  텔레그램 전송: {PAGES_URL}')


if __name__ == '__main__':
    main()
