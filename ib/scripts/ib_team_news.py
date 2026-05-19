"""
IB 팀 채널 — 뉴스브리프
────────────────────────────────────────────
구조:
  1. 마켓 데이터 + 주가 + 뉴스 수집
  2. HTML 리포트 생성 → docs/ 저장 (GitHub Pages)
  3. 텔레그램: 링크 + 간단 요약만 발송

GitHub Pages URL:
  오전: https://sywoolab.github.io/sy-workspace/morning.html
  오후: https://sywoolab.github.io/sy-workspace/afternoon.html

스케줄: 07:30 KST / 15:00 KST
"""

import os
import json
import re
import hashlib
import requests
import xml.etree.ElementTree as ET
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

BOT_TOKEN = os.environ.get('IB_TEAM_BOT_TOKEN', '')
CHAT_ID   = os.environ.get('IB_TEAM_CHAT_ID', '')

BASE_DIR       = str(Path(__file__).resolve().parent.parent)   # ib/
REPO_ROOT      = str(Path(__file__).resolve().parent.parent.parent)  # repo root
WATCHLIST_FILE = os.path.join(BASE_DIR, 'watchlist_team.json')
SENT_FILE      = os.path.join(BASE_DIR, 'data', 'team_news_sent.json')
DOCS_DIR       = os.path.join(REPO_ROOT, 'docs')

PAGES_BASE     = 'https://sywoolab.github.io/sy-workspace'
HEADERS        = {'User-Agent': 'Mozilla/5.0'}

# ─────────────────────────────────────────
# 가중치 설정
# ─────────────────────────────────────────
SOURCE_WEIGHT = {
    '더벨': 3, '인베스트조선': 3, '딜사이트': 3, '연합인포맥스': 3,
    '매일경제': 2, '한국경제': 2, '서울경제': 2, '머니투데이': 2,
    '이데일리': 2, '조선비즈': 2, '뉴스핌': 2,
    '인포스탁': 2, '비즈니스워치': 2, 'ZDNet': 2,
    '조선일보': 1, '중앙일보': 1, '동아일보': 1,
    '파이낸셜뉴스': 1, '연합뉴스': 1, '뉴시스': 1, '뉴스1': 1,
    '아시아경제': 1, '디지털타임스': 1, 'IT조선': 1,
}

KEYWORD_BOOST = {
    '실적': 3, '잠정': 3, '영업이익': 3, '매출': 3,
    'IPO': 3, '상장': 3, '증자': 3, '유증': 3, '무증': 3,
    'M&A': 3, '인수': 3, '합병': 3, '분할': 3,
    'CB': 3, 'EB': 3, '전환사채': 3, '교환사채': 3,
    '자사주': 3, '블록딜': 3, '대주주': 3, '경영권': 3,
    '소송': 3, '제재': 3, '리콜': 3,
    '계약': 2, '수주': 2, '공급': 2, '특허': 2,
    '신제품': 2, '출시': 2, '진출': 2, '확장': 2,
    '투자유치': 2, '시리즈': 2,
    '광고': -2, '협찬': -2, '이벤트': -2, '경품': -2,
}


# ─────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────

def _n(val, default=0):
    """쉼표 포함 숫자 문자열 안전 변환"""
    try:
        return float(str(val or default).replace(',', ''))
    except (ValueError, TypeError):
        return float(default)


def _arrow_html(chg):
    """HTML용 상승/하락 색상 span"""
    if chg > 0:
        return f'<span class="up">▲</span>'
    elif chg < 0:
        return f'<span class="dn">▼</span>'
    return '<span class="nt">─</span>'


def _rate_html(rate):
    cls = 'up' if rate > 0 else ('dn' if rate < 0 else 'nt')
    sign = '+' if rate > 0 else ''
    return f'<span class="{cls}">{sign}{rate:.2f}%</span>'


# ─────────────────────────────────────────
# 시장 데이터 수집
# ─────────────────────────────────────────

def fetch_naver_index(code):
    url = f'https://polling.finance.naver.com/api/realtime/domestic/index/{code}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        d = r.json().get('datas', [{}])[0]
        return {'price': _n(d.get('closePrice') or d.get('price')),
                'change': _n(d.get('compareToPreviousClosePrice')),
                'rate':   _n(d.get('fluctuationsRatio'))}
    except Exception as e:
        print(f'  [index {code}] err: {e}')
        return None


def fetch_yahoo(symbol):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d'
    try:
        r    = requests.get(url, headers=HEADERS, timeout=10)
        meta = r.json()['chart']['result'][0]['meta']
        price = meta.get('regularMarketPrice', 0)
        prev  = meta.get('chartPreviousClose', price)
        chg   = price - prev
        return {'price': price, 'change': chg, 'rate': chg / prev * 100 if prev else 0}
    except Exception as e:
        print(f'  [yahoo {symbol}] err: {e}')
        return None


def fetch_stock_price(code):
    url = f'https://polling.finance.naver.com/api/realtime/domestic/stock/{code}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        d = r.json().get('datas', [{}])[0]
        return {'price': _n(d.get('closePrice') or d.get('price')),
                'change': _n(d.get('compareToPreviousClosePrice')),
                'rate':   _n(d.get('fluctuationsRatio'))}
    except Exception as e:
        print(f'  [stock {code}] err: {e}')
        return None


def collect_market_data():
    """지수·환율 데이터 리스트 (html_id, yahoo_sym 포함 — JS 실시간 업데이트용)"""
    items = []
    for code, label, html_id, ysym in [
        ('KOSPI',  'KOSPI',   'mkt-kospi',  '^KS11'),
        ('KOSDAQ', 'KOSDAQ',  'mkt-kosdaq', '^KQ11'),
    ]:
        d = fetch_naver_index(code)
        if d:
            items.append({'label': label, 'price_str': f'{d["price"]:,.2f}',
                          'change': d['change'], 'rate': d['rate'],
                          'html_id': html_id, 'yahoo_sym': ysym, 'invert': False})
    for sym, label, html_id, ysym in [
        ('%5EGSPC', 'S&P 500', 'mkt-sp500', '^GSPC'),
        ('%5EDJI',  'DOW',     'mkt-dow',   '^DJI'),
    ]:
        d = fetch_yahoo(sym)
        if d:
            items.append({'label': label, 'price_str': f'{d["price"]:,.2f}',
                          'change': d['change'], 'rate': d['rate'],
                          'html_id': html_id, 'yahoo_sym': ysym, 'invert': False})
    fx = fetch_yahoo('KRW=X')
    if fx:
        items.append({'label': 'USD/KRW', 'price_str': f'{fx["price"]:,.1f}',
                      'change': -fx['change'], 'rate': -fx['rate'],
                      'html_id': 'mkt-usdkrw', 'yahoo_sym': 'KRW=X', 'invert': True})
    return items


# ─────────────────────────────────────────
# 금리 데이터
# ─────────────────────────────────────────

def fetch_us_rates():
    """Treasury.gov XML — 미국 국채 수익률 (자동, 최근 2개월 fallback)"""
    from datetime import datetime as _dt
    ns_d = 'http://schemas.microsoft.com/ado/2007/08/dataservices'
    ns_m = 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata'
    now  = _dt.now()
    # 이번 달 → 이전 달 순서로 시도 (영업일 기준 당일 미게재 시 fallback)
    for delta in [0, -1]:
        import calendar
        if delta == 0:
            ym = now.strftime('%Y%m')
        else:
            m = now.month - 1 or 12
            y = now.year if now.month > 1 else now.year - 1
            ym = f'{y}{m:02d}'
        url = (f'https://home.treasury.gov/resource-center/data-chart-center/'
               f'interest-rates/pages/xml?data=daily_treasury_yield_curve'
               f'&field_tdr_date_value_month={ym}')
        try:
            r    = requests.get(url, headers=HEADERS, timeout=15)
            root = ET.fromstring(r.content)
            entries = root.findall('.//{http://www.w3.org/2005/Atom}entry')
            if not entries:
                continue
            props = entries[-1].find(f'.//{{{ns_m}}}properties')
            result = {}
            for key, tag in [('1y','BC_1YEAR'), ('3y','BC_3YEAR'),
                              ('5y','BC_5YEAR'), ('10y','BC_10YEAR')]:
                el = props.find(f'{{{ns_d}}}{tag}') if props is not None else None
                if el is not None and el.text:
                    result[key] = float(el.text)
            if result:
                print(f'  US Rates ({ym}): {result}')
                return result
        except Exception as e:
            print(f'  [treasury {ym}] err: {e}')
    return {}


def load_rates_config():
    """rates_config.json — 한국 금리 + SOFR/Fed 정적 관리"""
    config_path = os.path.join(BASE_DIR, 'data', 'rates_config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'  [rates_config] err: {e}')
        return {}


def collect_stock_data(companies):
    rows = []
    for comp in companies:
        if not comp.get('listed') or not comp.get('stock_code'):
            continue
        d = fetch_stock_price(comp['stock_code'])
        if d:
            mkt = comp.get('market', '')
            suffix = '.KQ' if mkt == 'KOSDAQ' else '.KS'
            rows.append({'name': comp['name'], 'code': comp['stock_code'],
                         'market': mkt,
                         'price': int(d['price']), 'change': d['change'],
                         'rate': d['rate'],
                         'yahoo_sym': f"{comp['stock_code']}{suffix}"})
    return rows


# ─────────────────────────────────────────
# 뉴스 수집
# ─────────────────────────────────────────

def load_watchlist():
    with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)['companies']


def load_sent():
    try:
        with open(SENT_FILE, 'r') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_sent(sent_set):
    os.makedirs(os.path.dirname(SENT_FILE), exist_ok=True)
    with open(SENT_FILE, 'w') as f:
        json.dump(sorted(sent_set)[-2000:], f)


def article_hash(title, link):
    return hashlib.md5(f'{title[:80]}{link[:80]}'.encode()).hexdigest()[:12]


def fetch_news(company_name, aliases=None):
    terms = [company_name] + (aliases or [])
    query = '+OR+'.join([f'%22{t}%22' for t in terms])
    url   = f'https://news.google.com/rss/search?q={query}+when:1d&hl=ko&gl=KR&ceid=KR:ko'
    out   = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        for item in ET.fromstring(resp.content).findall('.//item'):
            title  = re.sub(r'<[^>]+>', '', item.findtext('title', '')).strip()
            link   = item.findtext('link', '').strip()
            source = item.findtext('source', '').strip()
            if title and link:
                out.append({'title': title, 'link': link, 'source': source})
    except Exception as e:
        print(f'  뉴스 에러 ({company_name}): {e}')
    return out


def fetch_top_market_news(n=5):
    """IB 시장 주요 뉴스 — 기업 무관, 매체 가중치 상위 N건"""
    query = 'IPO+OR+상장+OR+M%26A+OR+인수+OR+채권+OR+기업공개+OR+사모펀드+OR+PE+OR+딜'
    url   = f'https://news.google.com/rss/search?q={query}+when:1d&hl=ko&gl=KR&ceid=KR:ko'
    arts  = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        for item in ET.fromstring(resp.content).findall('.//item'):
            title  = re.sub(r'<[^>]+>', '', item.findtext('title', '')).strip()
            link   = item.findtext('link', '').strip()
            source = item.findtext('source', '').strip()
            if title and link:
                score = next((w for k, w in SOURCE_WEIGHT.items() if k in source), 0)
                arts.append({'title': title, 'link': link, 'source': source, 'score': score})
    except Exception as e:
        print(f'  [top news] err: {e}')
    # 매체 점수 높은 순 → 상위 N건
    arts.sort(key=lambda x: -x['score'])
    return arts[:n]


def score_article(article, company):
    title, source, score = article['title'], article['source'], 0
    for k, w in SOURCE_WEIGHT.items():
        if k in source:
            score += w; break
    for k, w in KEYWORD_BOOST.items():
        if k in title:
            score += w
    for term in [company['name']] + company.get('alias', []):
        if term in title:
            score += 1; break
    return score


# ─────────────────────────────────────────
# HTML 리포트 생성
# ─────────────────────────────────────────

HTML_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
         background: #f4f6f9; color: #1a202c; font-size: 15px; line-height: 1.5; }
  .container { max-width: 680px; margin: 0 auto; padding: 16px; }

  /* 헤더 */
  .header { background: linear-gradient(135deg, #0f2044 0%, #1a3a6b 100%);
            color: #fff; border-radius: 12px; padding: 20px 24px; margin-bottom: 14px; }
  .header h1 { font-size: 19px; font-weight: 700; letter-spacing: -0.3px; }
  .header .meta { font-size: 13px; color: rgba(255,255,255,0.65); margin-top: 4px; }
  .session-badge { display: inline-block; background: rgba(255,255,255,0.18);
                   border-radius: 6px; padding: 2px 10px; font-size: 12px;
                   font-weight: 600; margin-left: 8px; letter-spacing: 0.3px; }

  /* 카드 */
  .card { background: #fff; border-radius: 10px; padding: 16px 18px;
          margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.07); }
  .card-title { font-size: 12px; font-weight: 700; text-transform: uppercase;
                letter-spacing: 0.8px; color: #6b7280; margin-bottom: 12px; }

  /* 마켓 그리드 */
  .market-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .market-item { background: #f8fafc; border-radius: 8px; padding: 11px 13px; }
  .market-item .label { font-size: 11px; color: #9ca3af; font-weight: 600;
                        text-transform: uppercase; letter-spacing: 0.5px; }
  .market-item .price { font-size: 18px; font-weight: 700; margin: 2px 0; color: #111827; }
  .market-item .chg { font-size: 12px; }

  /* 주가 테이블 */
  .stock-table { width: 100%; border-collapse: collapse; }
  .stock-table th { font-size: 11px; color: #9ca3af; font-weight: 600;
                    text-align: left; padding: 4px 0 8px; border-bottom: 1px solid #f3f4f6; }
  .stock-table th:last-child, .stock-table td:last-child { text-align: right; }
  .stock-table td { padding: 8px 0; font-size: 14px; border-bottom: 1px solid #f9fafb; }
  .stock-table tr:last-child td { border-bottom: none; }
  .stock-name { font-weight: 600; }
  .stock-code { font-size: 11px; color: #9ca3af; margin-left: 4px; }
  .stock-price { font-weight: 700; font-size: 15px; }

  /* 뉴스 */
  .company-header { display: flex; align-items: center; gap: 8px;
                    padding: 10px 0 6px; border-bottom: 1px solid #f3f4f6;
                    margin-bottom: 8px; }
  .company-header:not(:first-child) { margin-top: 18px; }
  .company-name { font-weight: 700; font-size: 15px; color: #1a202c; }
  .company-code { font-size: 11px; color: #9ca3af; background: #f3f4f6;
                  border-radius: 4px; padding: 1px 6px; }
  .news-item { padding: 7px 0; border-bottom: 1px solid #f9fafb; }
  .news-item:last-child { border-bottom: none; }
  .news-title { font-size: 14px; color: #1a56db; text-decoration: none;
                font-weight: 500; display: block; line-height: 1.45; }
  .news-title:hover { text-decoration: underline; }
  .news-title.high { font-weight: 700; color: #1e40af; }
  .news-meta { font-size: 11px; color: #9ca3af; margin-top: 2px; }

  /* 색상 — 한국 증권 관행: 상승=빨강, 하락=파랑 */
  .up { color: #dc2626; }
  .dn { color: #1d4ed8; }
  .nt { color: #9ca3af; }

  /* 주요 뉴스 */
  .top-news-item { padding: 8px 0; border-bottom: 1px solid #f3f4f6; display: flex; gap: 10px; align-items: flex-start; }
  .top-news-item:last-child { border-bottom: none; }
  .top-news-num { font-size: 11px; font-weight: 700; color: #6b7280; min-width: 18px; padding-top: 2px; }
  .top-news-body { flex: 1; }
  .top-news-title { font-size: 14px; color: #1a56db; text-decoration: none; font-weight: 500; display: block; line-height: 1.45; }
  .top-news-title:hover { text-decoration: underline; }
  .top-news-meta { font-size: 11px; color: #9ca3af; margin-top: 2px; }

  /* 금리 매트릭스 */
  .rate-matrix { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .rate-group-title { font-size: 11px; font-weight: 700; color: #6b7280;
                      text-transform: uppercase; letter-spacing: 0.6px;
                      margin-bottom: 8px; padding-bottom: 5px;
                      border-bottom: 1px solid #e5e7eb; }
  .rate-row { display: flex; justify-content: space-between; align-items: center;
              padding: 5px 0; border-bottom: 1px solid #f9fafb; font-size: 13px; }
  .rate-row:last-child { border-bottom: none; }
  .rate-label { color: #374151; }
  .rate-val { font-weight: 700; color: #111827; }
  .rate-source { font-size: 10px; color: #9ca3af; margin-top: 8px; }

  /* 푸터 */
  .footer { text-align: center; font-size: 12px; color: #9ca3af;
            padding: 16px 0 8px; }
  @media (max-width: 480px) {
    .market-grid { grid-template-columns: 1fr 1fr; }
    .market-item .price { font-size: 16px; }
    .rate-matrix { grid-template-columns: 1fr; }
  }
</style>
"""


def build_rates_section(us_rates, rates_cfg):
    """금리 매트릭스 HTML — 한국(수동) + 미국(자동)"""
    kr = rates_cfg.get('kr', {})
    us_cfg = rates_cfg.get('us', {})
    cfg_updated = rates_cfg.get('_meta', {}).get('updated', '미확인')

    # 한국 금리 행
    kr_rows = ''
    for key, label in [('base_rate','기준금리'), ('cd_3m','CD 3M'),
                        ('ktb_1y','국고채 1Y'), ('ktb_3y','국고채 3Y'), ('ktb_5y','국고채 5Y')]:
        val = kr.get(key, {}).get('value')
        val_str = f'{val:.2f}%' if val is not None else '─'
        kr_rows += f'<div class="rate-row"><span class="rate-label">{label}</span><span class="rate-val">{val_str}</span></div>'

    # 미국 금리 행 — Treasury 자동 + Fed/SOFR 정적
    def _us_val(key, us_rates_dict, fallback_cfg):
        v = us_rates_dict.get(key)
        if v is not None:
            return f'{v:.2f}%'
        v2 = fallback_cfg.get(key, {}).get('value') if isinstance(fallback_cfg.get(key), dict) else None
        return f'{v2:.2f}%' if v2 else '─'

    fed_val = f'{us_cfg.get("fed_rate", {}).get("value", "N/A"):.2f}%' if isinstance(us_cfg.get("fed_rate"), dict) else '─'
    sofr_val = f'{us_cfg.get("sofr_3m", {}).get("value", "N/A"):.2f}%' if isinstance(us_cfg.get("sofr_3m"), dict) else '─'
    us_rows = (
        f'<div class="rate-row"><span class="rate-label">Fed Rate</span><span class="rate-val">{fed_val}</span></div>'
        f'<div class="rate-row"><span class="rate-label">SOFR 3M</span><span class="rate-val">{sofr_val}</span></div>'
        f'<div class="rate-row"><span class="rate-label">T-Note 1Y</span><span class="rate-val">{_us_val("1y", us_rates, {})}</span></div>'
        f'<div class="rate-row"><span class="rate-label">T-Note 3Y</span><span class="rate-val">{_us_val("3y", us_rates, {})}</span></div>'
        f'<div class="rate-row"><span class="rate-label">T-Note 5Y</span><span class="rate-val">{_us_val("5y", us_rates, {})}</span></div>'
    )

    return f"""
    <div class="card">
      <div class="card-title">📉 금리 매트릭스</div>
      <div class="rate-matrix">
        <div>
          <div class="rate-group-title">🇰🇷 한국</div>
          {kr_rows}
          <div class="rate-source">기준: {cfg_updated} (수동 업데이트)</div>
        </div>
        <div>
          <div class="rate-group-title">🇺🇸 미국</div>
          {us_rows}
          <div class="rate-source">T-Note: Treasury.gov 자동 / Fed·SOFR 수동</div>
        </div>
      </div>
    </div>"""


def build_html_report(market_data, stock_data, sections, now, session, us_rates=None, rates_cfg=None, top_news=None):
    _days_ko = ['월', '화', '수', '목', '금', '토', '일']
    date_str = f'{now.strftime("%Y년 %m월 %d일")} ({_days_ko[now.weekday()]})'
    time_str = now.strftime('%H:%M') + ' KST'
    price_label = '전일 종가' if now.hour < 12 else '현재가'

    # ── 마켓 카드 (JS 실시간 업데이트용 id 부여) ──────
    market_html = ''
    js_market_symbols = {}  # {html_id: {sym, invert, fmt}}
    for m in market_data:
        ar  = _arrow_html(m['change'])
        rt  = _rate_html(m['rate'])
        chg_abs = abs(m['change'])
        if m['label'] in ('USD/KRW',):
            chg_str = f'{chg_abs:.1f}'
        elif m['label'] in ('미국 10Y',):
            chg_str = f'{chg_abs:.3f}pp'
        else:
            chg_str = f'{chg_abs:,.2f}'
        hid = m.get('html_id', '')
        ysym = m.get('yahoo_sym', '')
        if hid and ysym:
            js_market_symbols[hid] = {'sym': ysym, 'invert': m.get('invert', False),
                                       'fmt': ',.1f' if 'KRW' in m['label'] else ',.2f'}
        market_html += f"""
        <div class="market-item" id="{hid}">
          <div class="label">{m['label']}</div>
          <div class="price" id="{hid}-p">{m['price_str']}</div>
          <div class="chg" id="{hid}-c">{ar} {chg_str} &nbsp; {rt}</div>
        </div>"""

    # ── 주가 테이블 (JS 실시간 업데이트용 id 부여) ────
    stock_rows = ''
    js_stock_symbols = {}  # {code: yahoo_sym}
    for s in stock_data:
        ar   = _arrow_html(s['change'])
        rt   = _rate_html(s['rate'])
        code = s['code']
        ysym = s.get('yahoo_sym', '')
        if ysym:
            js_stock_symbols[code] = ysym
        stock_rows += f"""
        <tr id="s-{code}">
          <td><span class="stock-name">{s['name']}</span><span class="stock-code">{code}</span></td>
          <td class="stock-price" id="s-{code}-p">{s['price']:,}</td>
          <td id="s-{code}-c">{ar} {rt}</td>
        </tr>"""

    # 생성 시각 표시 (JS가 실시간으로 교체)
    created_at = now.strftime('%m/%d %H:%M')
    stock_ts = f'<div id="stock-timestamp" class="rate-source" style="margin-top:8px;text-align:right">생성: {created_at} KST</div>'

    stock_section = ''
    if stock_rows:
        stock_section = f"""
        <div class="card">
          <div class="card-title">📈 워치리스트 주가</div>
          <table class="stock-table">
            <thead><tr><th>종목</th><th>현재가</th><th style="text-align:right">등락</th></tr></thead>
            <tbody>{stock_rows}</tbody>
          </table>
          {stock_ts}
        </div>"""

    # ── JS 심볼 테이블 (Python → JS로 전달) ────────
    import json as _json
    js_data = _json.dumps({'market': js_market_symbols, 'stock': js_stock_symbols})

    # ── 뉴스 섹션 ──────────────────────────────────
    news_html = ''
    total = 0
    for comp, picks in sections:
        code_badge = ''
        if comp.get('listed') and comp.get('stock_code'):
            code_badge = f'<span class="company-code">{comp["stock_code"]}</span>'
        market_badge = f'<span class="company-code">{comp["market"]}</span>' if comp.get('market') else ''
        news_html += f"""
        <div class="company-header">
          <span class="company-name">{comp['name']}</span>{code_badge}{market_badge}
        </div>"""
        for a in picks:
            t      = a['title']
            cls    = 'news-title high' if a['score'] >= 5 else 'news-title'
            src    = a.get('source') or ''
            news_html += f"""
        <div class="news-item">
          <a class="{cls}" href="{a['link']}" target="_blank" rel="noopener">{t}</a>
          <div class="news-meta">{src}</div>
        </div>"""
            total += 1

    if not news_html:
        news_html = '<p style="color:#9ca3af;font-size:14px;padding:8px 0">신규 뉴스 없음</p>'

    # ── 주요 뉴스 5 ─────────────────────────────────
    top_news_html = ''
    for i, a in enumerate(top_news or [], 1):
        t   = a['title']
        src = a.get('source', '')
        top_news_html += f"""
        <div class="top-news-item">
          <div class="top-news-num">{i}</div>
          <div class="top-news-body">
            <a class="top-news-title" href="{a['link']}" target="_blank" rel="noopener">{t}</a>
            <div class="top-news-meta">{src}</div>
          </div>
        </div>"""
    top_section = ''
    if top_news_html:
        top_section = f"""
    <div class="card">
      <div class="card-title">📌 오늘의 주요 뉴스</div>
      {top_news_html}
    </div>"""

    # ── 금리 매트릭스 ────────────────────────────────
    rates_section = build_rates_section(us_rates or {}, rates_cfg or {})

    news_section = f"""
    <div class="card">
      <div class="card-title">📰 뉴스 — {total}건 · {len(sections)}개사</div>
      {news_html}
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IB 뉴스브리프 {date_str} {session}</title>
  {HTML_STYLE}
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📋 IB 뉴스브리프 <span class="session-badge">{session}</span></h1>
    <div class="meta">{date_str} &nbsp;·&nbsp; {time_str} &nbsp;·&nbsp; {price_label} 기준</div>
  </div>

  <div class="card">
    <div class="card-title">📊 마켓 스냅샷</div>
    <div class="market-grid">{market_html}
    </div>
  </div>

  {stock_section}

  {top_section}

  {rates_section}

  {news_section}

  <div class="footer">신한투자증권 IB &nbsp;·&nbsp; 뉴스 생성: {now.strftime("%Y-%m-%d %H:%M")} KST &nbsp;·&nbsp; 주가: 페이지 로드 기준</div>
</div>

<script>
/* 실시간 주가·지수 업데이트 — Yahoo Finance API (CORS 허용) */
const _DATA = {js_data};

function _fmtNum(v, fmt) {{
  if (fmt === ',.1f') return v.toLocaleString('ko-KR', {{minimumFractionDigits:1, maximumFractionDigits:1}});
  return v.toLocaleString('ko-KR', {{minimumFractionDigits:2, maximumFractionDigits:2}});
}}
function _arrow(chg) {{
  return chg > 0 ? '<span class="up">▲</span>' : chg < 0 ? '<span class="dn">▼</span>' : '<span class="nt">─</span>';
}}
function _rateSpan(rate) {{
  const cls = rate > 0 ? 'up' : rate < 0 ? 'dn' : 'nt';
  const sign = rate > 0 ? '+' : '';
  return `<span class="${{cls}}">${{sign}}${{rate.toFixed(2)}}%</span>`;
}}

/* Yahoo Finance는 브라우저 직접 CORS 차단 → corsproxy.io 경유 */
async function _fetch(sym) {{
  const yahoo = `https://query1.finance.yahoo.com/v8/finance/chart/${{encodeURIComponent(sym)}}?interval=1d&range=1d`;
  const proxy = `https://corsproxy.io/?${{encodeURIComponent(yahoo)}}`;
  let d = null;
  /* 1차: corsproxy.io */
  try {{
    const r = await fetch(proxy, {{signal: AbortSignal.timeout(6000)}});
    d = await r.json();
  }} catch(e) {{
    /* 2차: allorigins.win fallback */
    try {{
      const r2 = await fetch(`https://api.allorigins.win/raw?url=${{encodeURIComponent(yahoo)}}`, {{signal: AbortSignal.timeout(6000)}});
      d = await r2.json();
    }} catch(e2) {{ return null; }}
  }}
  const m = d?.chart?.result?.[0]?.meta;
  if (!m) return null;
  const price = m.regularMarketPrice;
  const prev  = m.chartPreviousClose || price;
  return {{price, prev, chg: price - prev}};
}}

async function updateAll() {{
  const tasks = [];

  /* 지수·환율 */
  for (const [id, cfg] of Object.entries(_DATA.market)) {{
    tasks.push((async () => {{
      const d = await _fetch(cfg.sym).catch(() => null);
      if (!d) return;
      let chg = cfg.invert ? -d.chg : d.chg;
      const rate = d.prev ? chg / d.prev * 100 : 0;
      const pEl = document.getElementById(id + '-p');
      const cEl = document.getElementById(id + '-c');
      if (!pEl) return;
      pEl.textContent = _fmtNum(d.price, cfg.fmt);
      cEl.innerHTML = _arrow(chg) + ' ' + _fmtNum(Math.abs(chg), cfg.fmt) + ' &nbsp; ' + _rateSpan(rate);
    }})());
  }}

  /* 개별 주가 */
  for (const [code, sym] of Object.entries(_DATA.stock)) {{
    tasks.push((async () => {{
      const d = await _fetch(sym).catch(() => null);
      if (!d) return;
      const rate = d.prev ? d.chg / d.prev * 100 : 0;
      const pEl = document.getElementById('s-' + code + '-p');
      const cEl = document.getElementById('s-' + code + '-c');
      if (!pEl) return;
      pEl.textContent = Math.round(d.price).toLocaleString('ko-KR');
      pEl.style.color  = d.chg > 0 ? '#dc2626' : d.chg < 0 ? '#1d4ed8' : '';
      cEl.innerHTML = _arrow(d.chg) + ' ' + _rateSpan(rate);
    }})());
  }}

  await Promise.allSettled(tasks);

  /* 기준 시각 업데이트 */
  const ts = document.getElementById('stock-timestamp');
  if (ts) {{
    const t = new Date();
    const hhmm = t.toLocaleTimeString('ko-KR', {{hour:'2-digit', minute:'2-digit'}});
    ts.innerHTML = '🔄 실시간&nbsp;&nbsp;' + hhmm + ' KST';
    ts.style.color = '#16a34a';
  }}
}}

document.addEventListener('DOMContentLoaded', updateAll);
</script>
</body>
</html>"""


def save_html_report(html, session):
    os.makedirs(DOCS_DIR, exist_ok=True)
    filename = 'morning.html' if session == '오전' else 'afternoon.html'
    path = os.path.join(DOCS_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'  HTML 저장: {path}')
    return f'{PAGES_BASE}/{filename}'


# ─────────────────────────────────────────
# 텔레그램
# ─────────────────────────────────────────

def send_telegram(text, silent=False):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    resp = requests.post(url, data={
        'chat_id': CHAT_ID, 'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'false',
        'disable_notification': 'true' if silent else 'false',
    }, timeout=30)
    if not resp.json().get('ok'):
        print(f'  전송 실패: {resp.text[:300]}')


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

def main():
    now     = datetime.now(KST)
    session = '오전' if now.hour < 12 else '오후'
    _days_ko = ['월', '화', '수', '목', '금', '토', '일']
    date_str = f'{now.strftime("%Y-%m-%d")} ({_days_ko[now.weekday()]})'
    print(f'[{now}] IB 뉴스브리프 ({session})')

    if not BOT_TOKEN or not CHAT_ID:
        print('  IB_TEAM_BOT_TOKEN / CHAT_ID 누락')
        return

    companies = load_watchlist()
    sent      = load_sent()

    # ── 1. 시장 데이터 & 주가 & 금리 ────────────────
    print('  마켓 데이터 수집...')
    market_data = collect_market_data()
    stock_data  = collect_stock_data(companies)
    print('  금리 데이터 수집...')
    us_rates    = fetch_us_rates()
    rates_cfg   = load_rates_config()

    # ── 2. 주요 뉴스 5 (기업 무관 IB 시장) ──────────
    print('  주요 뉴스 수집...')
    top_news = fetch_top_market_news(5)
    print(f'  주요 뉴스: {len(top_news)}건')

    # ── 3. 기업별 뉴스 수집 ──────────────────────────
    sections       = []
    total_fetched  = 0
    total_skip_dup = 0
    total_skip_sc  = 0

    for comp in companies:
        articles = fetch_news(comp['name'], comp.get('alias'))
        total_fetched += len(articles)
        seen, unique, dup_n = set(), [], 0
        for a in articles:
            h  = article_hash(a['title'], a['link'])
            tk = re.sub(r'\s+', '', a['title'].lower())[:50]
            if h in sent or tk in seen:
                dup_n += 1; continue
            seen.add(tk); a['hash'] = h; unique.append(a)
        total_skip_dup += dup_n
        for a in unique:
            a['score'] = score_article(a, comp)
        sc_skip = sum(1 for a in unique if a['score'] <= 0)
        total_skip_sc += sc_skip
        filtered = sorted([a for a in unique if a['score'] > 0],
                          key=lambda x: x['score'], reverse=True)[:5]
        if filtered:
            sections.append((comp, filtered))
        print(f'  [{comp["name"]}] {len(articles)}건 → {len(filtered)}건 채택')

    total_news = sum(len(p) for _, p in sections)

    # ── 3. HTML 생성 & 저장 ───────────────────────
    html     = build_html_report(market_data, stock_data, sections, now, session,
                                  us_rates=us_rates, rates_cfg=rates_cfg, top_news=top_news)
    page_url = save_html_report(html, session)

    # ── 4. 텔레그램: 링크 + 요약만 ───────────────
    # 마켓 스냅샷 1줄 요약
    mkt_summary = ''
    for m in market_data[:3]:  # KOSPI, KOSDAQ, S&P500
        ar   = '▲' if m['change'] > 0 else ('▼' if m['change'] < 0 else '─')
        sign = '+' if m['rate'] > 0 else ''
        mkt_summary += f'{m["label"]} <b>{m["price_str"]}</b> {ar}{sign}{m["rate"]:.1f}%   '

    # SKIP 안내
    skip_info = ''
    if total_skip_dup + total_skip_sc > 0:
        skip_info = f'\n<i>(중복 {total_skip_dup} · 필터 {total_skip_sc} SKIP)</i>'
    if total_fetched > 0 and total_news == 0:
        skip_info = f'\n⚠️ 전체 SKIP — fetch {total_fetched}건'

    tg_msg = (
        f'<b>📋 IB 뉴스브리프</b>  {date_str}  [{session}]\n'
        f'\n'
        f'<b>🔗 리포트 보기</b>\n'
        f'<a href="{page_url}">{page_url}</a>\n'
        f'\n'
        f'{mkt_summary.strip()}\n'
        f'\n'
        f'📰 뉴스 <b>{total_news}건</b>  ·  <b>{len(sections)}개사</b>'
        f'{skip_info}'
    )

    send_telegram(tg_msg)
    print(f'  텔레그램 전송 완료 — {page_url}')

    # ── 5. sent 업데이트 ──────────────────────────
    for _, picks in sections:
        for a in picks:
            sent.add(a['hash'])
    save_sent(sent)


if __name__ == '__main__':
    main()
