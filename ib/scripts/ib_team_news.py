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

BOT_TOKEN    = os.environ.get('IB_TEAM_BOT_TOKEN', '')
CHAT_ID      = os.environ.get('IB_TEAM_CHAT_ID', '')
DART_API_KEY = os.environ.get('DART_API_KEY', '')

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
        return {'price':  _n(d.get('closePrice') or d.get('price')),
                'change': _n(d.get('compareToPreviousClosePrice')),
                'rate':   _n(d.get('fluctuationsRatio')),
                'mktcap': _n(d.get('marketValueFull') or d.get('marketValueFullRaw'))}
    except Exception as e:
        print(f'  [stock {code}] err: {e}')
        return None


def collect_market_data():
    """지수·환율 데이터 리스트 (html_id, yahoo_sym 포함 — JS 실시간 업데이트용)"""
    items = []
    MKT_CFG = [
        # (naver_code, label, html_id, yahoo_sym, invert, naver_url)
        ('KOSPI',  'KOSPI',   'mkt-kospi',  '^KS11',  False,
         'https://finance.naver.com/sise/sise_index.naver?code=KOSPI'),
        ('KOSDAQ', 'KOSDAQ',  'mkt-kosdaq', '^KQ11',  False,
         'https://finance.naver.com/sise/sise_index.naver?code=KOSDAQ'),
    ]
    for naver_code, label, html_id, ysym, invert, nurl in MKT_CFG:
        d = fetch_naver_index(naver_code)
        if d:
            items.append({'label': label, 'price_str': f'{d["price"]:,.2f}',
                          'change': d['change'], 'rate': d['rate'],
                          'html_id': html_id, 'yahoo_sym': ysym,
                          'invert': invert, 'naver_url': nurl})
    for sym, label, html_id, ysym, nurl in [
        ('%5EGSPC', 'S&P 500', 'mkt-sp500', '^GSPC',
         'https://finance.yahoo.com/quote/%5EGSPC'),
        ('%5EDJI',  'DOW',     'mkt-dow',   '^DJI',
         'https://finance.yahoo.com/quote/%5EDJI'),
    ]:
        d = fetch_yahoo(sym)
        if d:
            items.append({'label': label, 'price_str': f'{d["price"]:,.2f}',
                          'change': d['change'], 'rate': d['rate'],
                          'html_id': html_id, 'yahoo_sym': ysym,
                          'invert': False, 'naver_url': nurl})
    fx = fetch_yahoo('KRW=X')
    if fx:
        items.append({'label': 'USD/KRW', 'price_str': f'{fx["price"]:,.1f}',
                      'change': -fx['change'], 'rate': -fx['rate'],
                      'html_id': 'mkt-usdkrw', 'yahoo_sym': 'KRW=X',
                      'invert': True, 'naver_url': 'https://finance.naver.com/marketindex/'})
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
        if not d:
            continue
        mkt    = comp.get('market', '')
        code   = comp['stock_code']
        suffix = '.KQ' if mkt == 'KOSDAQ' else '.KS'
        ysym   = f'{code}{suffix}'

        # ── Yahoo 심볼 교차 검증 (네이버 vs Yahoo, 10% 이상 괴리 시 경고 + 차단) ──
        naver_p = d['price']
        if naver_p > 0:
            ydata = fetch_yahoo(ysym.replace('^', '%5E'))
            if ydata and ydata['price'] > 0:
                diff_rate = abs(ydata['price'] - naver_p) / naver_p
                if diff_rate > 0.10:
                    print(f'  ⚠️ [{comp["name"]}] Yahoo심볼 불일치 (네이버={naver_p:,} Yahoo={ydata["price"]:,.0f} {diff_rate:.0%}) — Yahoo 사용 차단')
                    ysym = ''  # JS에서 해당 종목 실시간 업데이트 안 함

        rows.append({'name': comp['name'], 'code': code, 'market': mkt,
                     'price': int(d['price']), 'change': d['change'], 'rate': d['rate'],
                     'yahoo_sym': ysym, 'mktcap': d.get('mktcap', 0)})
    return rows


def fmt_mktcap(val):
    """시가총액 포맷: 1596조 / 40.4조 / 318억"""
    if not val or val <= 0:
        return ''
    if val >= 100e12:        # 100조+
        return f'{val/1e12:,.0f}조'
    elif val >= 1e12:        # 1조~100조
        return f'{val/1e12:.1f}조'
    elif val >= 1e8:         # 1억~1조
        return f'{val/1e8:,.0f}억'
    else:
        return f'{val/1e6:,.0f}백만'


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


def _parse_pub_date(raw):
    """Google News RSS pubDate → 'MM/DD HH:MM' KST 형식"""
    if not raw:
        return ''
    try:
        from email.utils import parsedate_to_datetime
        dt_utc = parsedate_to_datetime(raw)
        dt_kst = dt_utc.astimezone(KST)
        return dt_kst.strftime('%m/%d %H:%M')
    except Exception:
        return raw[:10]


def fetch_news(company_name, aliases=None):
    terms = [company_name] + (aliases or [])
    query = '+OR+'.join([f'%22{t}%22' for t in terms])
    url   = f'https://news.google.com/rss/search?q={query}+when:12h&hl=ko&gl=KR&ceid=KR:ko'
    out   = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        for item in ET.fromstring(resp.content).findall('.//item'):
            title   = re.sub(r'<[^>]+>', '', item.findtext('title', '')).strip()
            link    = item.findtext('link', '').strip()
            source  = item.findtext('source', '').strip()
            pub_raw = item.findtext('pubDate', '').strip()
            if title and link:
                out.append({'title': title, 'link': link, 'source': source,
                            'pub': _parse_pub_date(pub_raw)})
    except Exception as e:
        print(f'  뉴스 에러 ({company_name}): {e}')
    return out


# 신한증권 IB 딜 관련 뉴스 필터 키워드
SHINHAN_IB_KEYWORDS = [
    'IPO', '기업공개', '상장주관', '주관사', '공동주관', '대표주관',
    '발행어음', 'ABS', 'MBS', 'PF', '프로젝트파이낸싱', '부동산금융',
    'M&A', '인수금융', '매각주관', '구조화금융', 'DCM', 'ECM',
    '채권발행', '회사채', 'CB', 'EB', 'BW', '증자', '유증',
    '딜', '투자은행', 'IB', '인수합병',
]
SHINHAN_NOISE_KEYWORDS = [
    'ETF', '펀드', '연금', 'ISA', 'CMA', '적금', '이자', '예금',
    '신용카드', '개인금융', '리테일', '소매', '개인대출', '주택대출',
    '모바일뱅킹', '앱', '이벤트', '경품', '프로모션',
]


def fetch_shinhan_ib_news(n=5):
    """신한증권/신한IB 뉴스 — IB 딜 관련만 필터"""
    query = '%22신한투자증권%22+OR+%22신한증권%22+OR+%22신한IB%22'
    url   = f'https://news.google.com/rss/search?q={query}+when:12h&hl=ko&gl=KR&ceid=KR:ko'
    arts  = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        for item in ET.fromstring(resp.content).findall('.//item'):
            title   = re.sub(r'<[^>]+>', '', item.findtext('title', '')).strip()
            link    = item.findtext('link', '').strip()
            source  = item.findtext('source', '').strip()
            pub_raw = item.findtext('pubDate', '').strip()
            if not (title and link):
                continue
            # 노이즈 키워드 포함 → 제외
            if any(k in title for k in SHINHAN_NOISE_KEYWORDS):
                continue
            # IB 딜 키워드 없으면 점수 0 → 낮은 점수
            ib_score = sum(3 for k in SHINHAN_IB_KEYWORDS if k in title)
            src_score = next((w for k, w in SOURCE_WEIGHT.items() if k in source), 0)
            arts.append({'title': title, 'link': link, 'source': source,
                         'pub': _parse_pub_date(pub_raw), 'score': ib_score + src_score})
    except Exception as e:
        print(f'  [신한IB 뉴스] err: {e}')
    # IB 점수 높은 순, 점수 0 이하 제외
    arts = [a for a in arts if a['score'] > 0]
    arts.sort(key=lambda x: -x['score'])
    print(f'  신한IB 뉴스: 총 {len(arts)}건 필터됨 → 상위 {min(n, len(arts))}건')
    return arts[:n]


def fetch_dart_disclosures(companies, days=3):
    """워치리스트 기업 최근 N일 DART 공시 수집"""
    if not DART_API_KEY:
        print('  [DART] API key 없음 — 공시 섹션 스킵')
        return []
    now_kst = datetime.now(KST)
    bgn = (now_kst - timedelta(days=days)).strftime('%Y%m%d')
    end = now_kst.strftime('%Y%m%d')
    results = []  # [(date_str, corp_name, report_nm, rcept_no)]
    for comp in companies:
        dart_code = comp.get('dart_code')
        if not dart_code:
            continue
        try:
            resp = requests.get(
                'https://opendart.fss.or.kr/api/list.json',
                params={'crtfc_key': DART_API_KEY, 'corp_code': dart_code,
                        'bgn_de': bgn, 'end_de': end, 'page_count': 20},
                headers=HEADERS, timeout=15)
            data = resp.json()
            if data.get('status') == '000':
                for d in data.get('list', []):
                    rcept_no  = d.get('rcept_no', '')
                    report_nm = d.get('report_nm', '').strip()
                    rcept_dt  = d.get('rcept_dt', '')  # YYYYMMDD
                    # 날짜 포맷
                    try:
                        date_fmt = f"{rcept_dt[4:6]}/{rcept_dt[6:8]}"
                    except Exception:
                        date_fmt = rcept_dt
                    dart_url = f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}'
                    results.append({
                        'corp': comp['name'], 'date': date_fmt,
                        'title': report_nm, 'url': dart_url,
                        'rcept_dt': rcept_dt,
                    })
        except Exception as e:
            print(f'  [DART {comp["name"]}] err: {e}')
    # 최신순 정렬
    results.sort(key=lambda x: x['rcept_dt'], reverse=True)
    print(f'  DART 공시: {len(results)}건 (최근 {days}일)')
    return results


def fetch_top_market_news(n=5):
    """IB 시장 주요 뉴스 — 기업 무관, 매체 가중치 상위 N건 (날짜 포함)"""
    query = 'IPO+OR+상장+OR+M%26A+OR+인수+OR+채권+OR+기업공개+OR+사모펀드+OR+PE+OR+딜'
    url   = f'https://news.google.com/rss/search?q={query}+when:12h&hl=ko&gl=KR&ceid=KR:ko'
    arts  = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        for item in ET.fromstring(resp.content).findall('.//item'):
            title   = re.sub(r'<[^>]+>', '', item.findtext('title', '')).strip()
            link    = item.findtext('link', '').strip()
            source  = item.findtext('source', '').strip()
            pub_raw = item.findtext('pubDate', '').strip()
            if title and link:
                score = next((w for k, w in SOURCE_WEIGHT.items() if k in source), 0)
                arts.append({'title': title, 'link': link, 'source': source,
                             'pub': _parse_pub_date(pub_raw), 'score': score})
    except Exception as e:
        print(f'  [top news] err: {e}')
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
  .market-item { background: #f8fafc; border-radius: 8px; padding: 11px 13px;
                 text-decoration: none; color: inherit;
                 display: flex; flex-direction: row; flex-wrap: nowrap;
                 align-items: center; justify-content: space-between;
                 gap: 8px; transition: background .15s; }
  .market-item:hover { background: #f0f4f8; }
  .market-item-info { flex: 1; min-width: 0; overflow: hidden; }
  .market-item .label { font-size: 11px; color: #9ca3af; font-weight: 600;
                        text-transform: uppercase; letter-spacing: 0.5px; }
  .market-item .price { font-size: 17px; font-weight: 700; margin: 2px 0; color: #111827; white-space: nowrap; }
  .market-item .chg { font-size: 11px; white-space: nowrap; }
  .market-spark { flex-shrink: 0; display: flex; align-items: center; }

  /* 주가 테이블 */
  .stock-table { width: 100%; border-collapse: collapse; }
  .stock-table th { font-size: 11px; color: #9ca3af; font-weight: 600;
                    text-align: left; padding: 4px 0 8px; border-bottom: 1px solid #f3f4f6; }
  .stock-table td { padding: 7px 0; font-size: 14px; border-bottom: 1px solid #f9fafb; }
  .stock-table tr:last-child td { border-bottom: none; }
  .stock-name-link { font-weight: 600; color: #1a202c; text-decoration: none; }
  .stock-name-link:hover { color: #1a56db; text-decoration: underline; }
  .stock-code { font-size: 11px; color: #9ca3af; margin-left: 4px; }
  .stock-price { font-weight: 700; font-size: 15px; }
  .stock-spark { text-align: right; }
  .spark-svg { display: inline-block; vertical-align: middle; overflow: visible; }

  /* 그룹 헤더 */
  .group-header { display: flex; align-items: center; gap: 8px;
                  padding: 12px 0 4px; border-bottom: 2px solid #e5e7eb;
                  margin-bottom: 4px; margin-top: 16px; }
  .group-header:first-child { margin-top: 0; }
  .group-name { font-weight: 800; font-size: 13px; color: #374151;
                text-transform: uppercase; letter-spacing: 0.4px; }
  .group-badge { font-size: 11px; color: #9ca3af; background: #f3f4f6;
                 border-radius: 4px; padding: 1px 6px; }

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

  /* 공시 테이블 */
  .dart-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .dart-table th { font-size: 11px; color: #9ca3af; font-weight: 600; text-align: left;
                   padding: 4px 8px 8px; border-bottom: 1px solid #f3f4f6; white-space: nowrap; }
  .dart-table td { padding: 6px 8px; border-bottom: 1px solid #f9fafb; font-size: 13px; }
  .dart-table tr:last-child td { border-bottom: none; }
  .dart-corp { font-weight: 700; color: #374151; white-space: nowrap; }
  .dart-date { color: #9ca3af; white-space: nowrap; font-size: 12px; }
  .dart-title a { color: #1a56db; text-decoration: none; }
  .dart-title a:hover { text-decoration: underline; }

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


def build_html_report(market_data, stock_data, sections, now, session, us_rates=None, rates_cfg=None, top_news=None, shinhan_news=None, dart_disclosures=None):
    _days_ko = ['월', '화', '수', '목', '금', '토', '일']
    date_str = f'{now.strftime("%Y년 %m월 %d일")} ({_days_ko[now.weekday()]})'
    time_str = now.strftime('%H:%M') + ' KST'
    price_label = '전일 종가' if now.hour < 12 else '현재가'

    # ── 마켓 카드 (스파크라인 + 링크 + JS id) ──────────
    market_html = ''
    js_market_symbols = {}
    js_spark_market   = {}  # {html_id: yahoo_sym} — 스파크라인용
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
        hid  = m.get('html_id', '')
        ysym = m.get('yahoo_sym', '')
        nurl = m.get('naver_url', '#')
        if hid and ysym:
            js_market_symbols[hid] = {'sym': ysym, 'invert': m.get('invert', False),
                                       'fmt': ',.1f' if 'KRW' in m['label'] else ',.2f'}
            js_spark_market[hid] = ysym
        market_html += f"""
        <a class="market-item" id="{hid}" href="{nurl}" target="_blank" rel="noopener">
          <div class="market-item-info">
            <div class="label">{m['label']}</div>
            <div class="price" id="{hid}-p">{m['price_str']}</div>
            <div class="chg" id="{hid}-c">{ar} {chg_str} &nbsp; {rt}</div>
          </div>
          <div class="market-spark"><svg id="spark-{hid}" class="spark-svg" width="60" height="26" style="display:block"></svg></div>
        </a>"""

    # ── 주가 테이블 (스파크라인 + 네이버 링크 + JS id) ──
    stock_rows = ''
    js_stock_symbols = {}
    js_spark_stock   = {}  # {code: yahoo_sym}
    for s in stock_data:
        ar   = _arrow_html(s['change'])
        rt   = _rate_html(s['rate'])
        code = s['code']
        ysym = s.get('yahoo_sym', '')
        naver_url = f'https://finance.naver.com/item/main.naver?code={code}'
        if ysym:
            js_stock_symbols[code] = ysym
            js_spark_stock[code]   = ysym
        mktcap_str = fmt_mktcap(s.get('mktcap', 0))
        mktcap_html = f'<div style="font-size:10px;color:#9ca3af;margin-top:1px">{mktcap_str}</div>' if mktcap_str else ''
        stock_rows += f"""
        <tr id="s-{code}">
          <td><a class="stock-name-link" href="{naver_url}" target="_blank" rel="noopener">{s['name']}</a><span class="stock-code">{code}</span></td>
          <td><div class="stock-price" id="s-{code}-p">{s['price']:,}</div>{mktcap_html}</td>
          <td id="s-{code}-c">{ar} {rt}</td>
          <td class="stock-spark"><svg id="spark-s-{code}" class="spark-svg" width="60" height="26" style="display:block"></svg></td>
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
            <thead><tr><th>종목</th><th>현재가</th><th style="text-align:center">등락</th><th style="text-align:right">3M 추이</th></tr></thead>
            <tbody>{stock_rows}</tbody>
          </table>
          {stock_ts}
          <div style="margin-top:10px;text-align:right">
            <a href="https://fomonono.com/treemap" target="_blank" rel="noopener"
               style="display:inline-block;background:#f3f4f6;color:#374151;font-size:12px;
                      font-weight:600;padding:5px 12px;border-radius:6px;text-decoration:none;">
              🗺️ 시장 전체 히트맵 (fomonono) &rarr;
            </a>
          </div>
        </div>"""

    # ── JS 심볼 테이블 (Python → JS로 전달) ────────
    import json as _json
    js_data = _json.dumps({
        'market': js_market_symbols,
        'stock':  js_stock_symbols,
        'spark_market': js_spark_market,
        'spark_stock':  js_spark_stock,
    })

    # ── 뉴스 섹션 (그룹 있으면 그룹 헤더로 묶기) ────
    # sections = [(comp_dict, [article, ...]), ...]
    # 그룹화: group 필드 기준으로 묶은 후 개별 기업 렌더링

    def _comp_news_html(comp, picks):
        code_badge   = f'<span class="company-code">{comp["stock_code"]}</span>' if comp.get('listed') and comp.get('stock_code') else ''
        market_badge = f'<span class="company-code">{comp["market"]}</span>' if comp.get('market') else ''
        out = f"""
        <div class="company-header">
          <span class="company-name">{comp['name']}</span>{code_badge}{market_badge}
        </div>"""
        for a in picks[:3]:  # 기업당 최대 3건
            t   = a['title']
            cls = 'news-title high' if a['score'] >= 5 else 'news-title'
            src = a.get('source') or ''
            pub = a.get('pub') or ''
            meta = f'{src}  <span style="color:#d1d5db">|</span>  {pub}' if src and pub else (src or pub)
            out += f"""
        <div class="news-item">
          <a class="{cls}" href="{a['link']}" target="_blank" rel="noopener">{t}</a>
          <div class="news-meta">{meta}</div>
        </div>"""
        return out

    # 그룹별로 묶기
    from collections import OrderedDict
    grouped   = OrderedDict()  # group_name → [(comp, picks)]
    ungrouped = []
    for comp, picks in sections:
        g = comp.get('group')
        if g:
            grouped.setdefault(g, []).append((comp, picks))
        else:
            ungrouped.append((comp, picks))

    news_html = ''
    total = 0

    # 개별 기업 (그룹 없음) 먼저
    for comp, picks in ungrouped:
        news_html += _comp_news_html(comp, picks)
        total += len(picks)

    # 그룹 섹션
    for grp_name, items in grouped.items():
        n_items = sum(len(p) for _, p in items)
        news_html += f"""
        <div class="group-header">
          <span class="group-name">📂 {grp_name}</span>
          <span class="group-badge">{len(items)}개사 · {n_items}건</span>
        </div>"""
        for comp, picks in items:
            news_html += _comp_news_html(comp, picks)
            total += len(picks)

    if not news_html:
        news_html = '<p style="color:#9ca3af;font-size:14px;padding:8px 0">신규 뉴스 없음</p>'

    def _news_meta_str(a):
        src = a.get('source','')
        pub = a.get('pub','')
        return f'{src}  <span style="color:#d1d5db">|</span>  {pub}' if src and pub else (src or pub)

    # ── 주요 뉴스 5 ─────────────────────────────────
    top_news_html = ''
    for i, a in enumerate(top_news or [], 1):
        t = a['title']
        top_news_html += f"""
        <div class="top-news-item">
          <div class="top-news-num">{i}</div>
          <div class="top-news-body">
            <a class="top-news-title" href="{a['link']}" target="_blank" rel="noopener">{t}</a>
            <div class="top-news-meta">{_news_meta_str(a)}</div>
          </div>
        </div>"""
    top_section = ''
    if top_news_html:
        top_section = f"""
    <div class="card">
      <div class="card-title">📌 오늘의 주요 뉴스</div>
      {top_news_html}
    </div>"""

    # ── 신한증권 IB 뉴스 ─────────────────────────────
    shinhan_html = ''
    for i, a in enumerate(shinhan_news or [], 1):
        t = a['title']
        shinhan_html += f"""
        <div class="top-news-item">
          <div class="top-news-num">{i}</div>
          <div class="top-news-body">
            <a class="top-news-title" href="{a['link']}" target="_blank" rel="noopener">{t}</a>
            <div class="top-news-meta">{_news_meta_str(a)}</div>
          </div>
        </div>"""
    shinhan_section = ''
    if shinhan_html:
        shinhan_section = f"""
    <div class="card">
      <div class="card-title">🏦 신한증권 IB 뉴스</div>
      {shinhan_html}
    </div>"""

    # ── DART 공시 섹션 ───────────────────────────────
    dart_rows = ''
    for d in (dart_disclosures or []):
        t = d['title'].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        dart_rows += f"""
        <tr>
          <td class="dart-corp">{d['corp']}</td>
          <td class="dart-date">{d['date']}</td>
          <td class="dart-title"><a href="{d['url']}" target="_blank" rel="noopener">{t}</a></td>
        </tr>"""
    dart_section = ''
    if dart_rows:
        dart_section = f"""
    <div class="card">
      <div class="card-title">📢 워치리스트 공시 <span style="font-size:10px;color:#9ca3af">(최근 3일 · DART)</span></div>
      <table class="dart-table">
        <thead><tr><th>기업</th><th>일자</th><th>공시명</th></tr></thead>
        <tbody>{dart_rows}</tbody>
      </table>
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
  <title>{now.strftime('%y%m%d')} 신한증권 IB종합금융부 Daily Brief</title>
  <meta property="og:title" content="{now.strftime('%y%m%d')} 신한증권 IB종합금융부 Daily Brief">
  <meta property="og:description" content="{date_str} 마켓 스냅샷 · 워치리스트 주가 · IB 뉴스">
  <meta property="og:type" content="website">
  {HTML_STYLE}
</head>
<body>
<div class="container">
  <div class="header">
    <h1>신한증권 IB종합금융부 Daily Brief <span class="session-badge">{session}</span></h1>
    <div class="meta">{date_str} &nbsp;·&nbsp; {time_str} &nbsp;·&nbsp; {price_label} 기준</div>
  </div>

  <div class="card">
    <div class="card-title">📊 마켓 스냅샷</div>
    <div class="market-grid">{market_html}
    </div>
  </div>

  {stock_section}

  {shinhan_section}

  {dart_section}

  {top_section}

  {rates_section}

  {news_section}

  <div class="footer">신한증권 IB종합금융부 &nbsp;·&nbsp; 뉴스 생성: {now.strftime("%Y-%m-%d %H:%M")} KST &nbsp;·&nbsp; 주가: 페이지 로드 기준</div>
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

  /* 스파크라인 렌더링 (비동기, 백그라운드) */
  drawAllSparks();
}}

/* ── 스파크라인 ────────────────────────────────── */
/* 스파크라인: Actions에서 생성한 prices3m.json을 같은 도메인에서 직접 읽기 (CORS 없음) */
function _drawSpark(svgId, closes) {{
  const el = document.getElementById(svgId);
  if (!el || closes.length < 3) return;
  const W=70, H=24;
  const min=Math.min(...closes), max=Math.max(...closes), rng=max-min||1;
  const pts = closes.map((v,i)=>{{
    const x=(i/(closes.length-1)*W).toFixed(1);
    const y=(H-(v-min)/rng*(H-2)-1).toFixed(1);
    return `${{x}},${{y}}`;
  }}).join(' ');
  const col = closes[closes.length-1] >= closes[0] ? '#dc2626' : '#1d4ed8';
  el.setAttribute('viewBox',`0 0 ${{W}} ${{H}}`);
  el.innerHTML = `<polyline points="${{pts}}" fill="none" stroke="${{col}}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>`;
}}

async function drawAllSparks() {{
  try {{
    const r = await fetch('prices3m.json', {{cache: 'no-cache'}});
    if (!r.ok) return;
    const data = await r.json();
    for (const [id, closes] of Object.entries(data.market || {{}})) {{
      _drawSpark('spark-' + id, closes);
    }}
    for (const [code, closes] of Object.entries(data.stock || {{}})) {{
      _drawSpark('spark-s-' + code, closes);
    }}
  }} catch(e) {{ console.log('spark load err', e); }}
}}

document.addEventListener('DOMContentLoaded', updateAll);
</script>
</body>
</html>"""


def save_html_report(html, session):
    os.makedirs(DOCS_DIR, exist_ok=True)
    # 단일 파일 (index.html) + 세션별 복사
    for fname in ['index.html']:
        path = os.path.join(DOCS_DIR, fname)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
    print(f'  HTML 저장: docs/index.html')
    return f'{PAGES_BASE}/index.html'


def save_prices3m(stock_data, market_data):
    """3개월 주가 데이터 → docs/prices3m.json (스파크라인용, CORS 없이 직접 읽기)"""
    os.makedirs(DOCS_DIR, exist_ok=True)
    result = {'market': {}, 'stock': {}}

    # 마켓 지수
    for m in market_data:
        hid  = m.get('html_id', '')
        ysym = m.get('yahoo_sym', '')
        if not (hid and ysym):
            continue
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ysym.replace("^","%5E")}?interval=1d&range=3mo'
            r   = requests.get(url, headers=HEADERS, timeout=15)
            q   = r.json()['chart']['result'][0]['indicators']['quote'][0]
            closes = [v for v in q.get('close', []) if v is not None]
            if closes:
                result['market'][hid] = [round(v, 2) for v in closes]
                print(f'    spark {hid}: {len(closes)}개')
        except Exception as e:
            print(f'    spark err {hid}: {e}')

    # 개별 종목
    for s in stock_data:
        code = s['code']
        ysym = s.get('yahoo_sym', '')
        if not ysym:
            continue
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ysym}?interval=1d&range=3mo'
            r   = requests.get(url, headers=HEADERS, timeout=15)
            q   = r.json()['chart']['result'][0]['indicators']['quote'][0]
            closes = [v for v in q.get('close', []) if v is not None]
            if closes:
                result['stock'][code] = [round(v, 0) for v in closes]
        except Exception as e:
            print(f'    spark err {code}: {e}')

    out_path = os.path.join(DOCS_DIR, 'prices3m.json')
    with open(out_path, 'w') as f:
        json.dump(result, f)
    n_mkt = len(result['market'])
    n_stk = len(result['stock'])
    print(f'  prices3m.json 저장 (마켓 {n_mkt}개 / 종목 {n_stk}개)')


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

    # ── 2. 주요 뉴스 5 + 신한IB 뉴스 + DART 공시 ─────
    print('  주요 뉴스 수집...')
    top_news         = fetch_top_market_news(5)
    shinhan_news     = fetch_shinhan_ib_news(5)
    dart_disclosures = fetch_dart_disclosures(companies, days=3)
    print(f'  주요 뉴스: {len(top_news)}건 / 신한IB: {len(shinhan_news)}건')

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

    # ── 3. HTML 생성 & 저장 + 3M 스파크라인 데이터 ──
    html     = build_html_report(market_data, stock_data, sections, now, session,
                                  us_rates=us_rates, rates_cfg=rates_cfg,
                                  top_news=top_news, shinhan_news=shinhan_news,
                                  dart_disclosures=dart_disclosures)
    page_url = save_html_report(html, session)
    print('  3M 스파크라인 데이터 수집...')
    save_prices3m(stock_data, market_data)

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

    date_tag = now.strftime('%y%m%d')  # 260520 형식
    tg_msg = (
        f'<b>📋 신한증권 IB종합금융부 Daily Brief</b>  {date_str}\n'
        f'\n'
        f'<b>🔗 리포트 보기</b>\n'
        f'<a href="{page_url}">{date_tag} | {page_url}</a>\n'
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
