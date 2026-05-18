"""
IB 팀 채널 — 프로페셔널 뉴스브리프 (HTML 포맷)
───────────────────────────────────────────
상단: 마켓 스냅샷 (KOSPI·KOSDAQ·S&P500·환율·금리)
중단: 워치리스트 주가 (상장사 한정)
하단: 뉴스 (기업별, 점수 필터)
───────────────────────────────────────────
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

BASE_DIR       = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
WATCHLIST_FILE = os.path.join(BASE_DIR, 'watchlist_team.json')
SENT_FILE      = os.path.join(BASE_DIR, 'data', 'team_news_sent.json')

# ─────────────────────────────────────────
# 매체 가중치
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

HEADERS = {'User-Agent': 'Mozilla/5.0'}


# ─────────────────────────────────────────
# 시장 데이터
# ─────────────────────────────────────────

def _arrow(chg: float) -> str:
    return '▲' if chg > 0 else ('▼' if chg < 0 else '─')


def _n(val, default=0) -> float:
    """쉼표 포함 숫자 문자열 안전 변환"""
    try:
        return float(str(val or default).replace(',', ''))
    except (ValueError, TypeError):
        return float(default)


def fetch_naver_index(code: str):
    """KOSPI·KOSDAQ — 네이버 금융 실시간 API"""
    url = f'https://polling.finance.naver.com/api/realtime/domestic/index/{code}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        d = r.json().get('datas', [{}])[0]
        close = _n(d.get('closePrice') or d.get('price'))
        chg   = _n(d.get('compareToPreviousClosePrice'))
        rate  = _n(d.get('fluctuationsRatio'))
        return {'price': close, 'change': chg, 'rate': rate}
    except Exception as e:
        print(f'  [index {code}] err: {e}')
        return None


def fetch_yahoo(symbol: str):
    """S&P500·미국 금리(^TNX)·환율(KRW=X) — Yahoo Finance v8 JSON"""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        result = r.json()['chart']['result'][0]
        meta = result['meta']
        price  = meta.get('regularMarketPrice', 0)
        prev   = meta.get('chartPreviousClose', price)
        chg    = price - prev
        rate   = chg / prev * 100 if prev else 0
        return {'price': price, 'change': chg, 'rate': rate}
    except Exception as e:
        print(f'  [yahoo {symbol}] err: {e}')
        return None


def fetch_stock_price(code: str):
    """개별 종목 주가 — 네이버 금융 실시간 API"""
    url = f'https://polling.finance.naver.com/api/realtime/domestic/stock/{code}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        d = r.json().get('datas', [{}])[0]
        close = _n(d.get('closePrice') or d.get('price'))
        chg   = _n(d.get('compareToPreviousClosePrice'))
        rate  = _n(d.get('fluctuationsRatio'))
        return {'price': close, 'change': chg, 'rate': rate}
    except Exception as e:
        print(f'  [stock {code}] err: {e}')
        return None


def build_market_section(session: str, now: datetime) -> str:
    """마켓 스냅샷 블록 — 오전엔 전일 종가 기준"""
    is_morning = now.hour < 12
    prefix = '전일 종가' if is_morning else '현재가'

    lines = [f'<b>📊 마켓 스냅샷</b>  <i>({prefix})</i>']

    # 국내 지수
    for code, label in [('KOSPI', 'KOSPI'), ('KOSDAQ', 'KOSDAQ')]:
        d = fetch_naver_index(code)
        if d:
            ar = _arrow(d['change'])
            sign = '+' if d['change'] > 0 else ''
            lines.append(
                f'  {label:<8} <b>{d["price"]:,.2f}</b>  '
                f'{ar} {abs(d["change"]):,.2f}  ({sign}{d["rate"]:.2f}%)'
            )

    # 미국 S&P500 (전일 종가 상시)
    sp = fetch_yahoo('%5EGSPC')
    if sp:
        ar = _arrow(sp['change'])
        sign = '+' if sp['change'] > 0 else ''
        lines.append(
            f'  {"S&P 500":<8} <b>{sp["price"]:,.2f}</b>  '
            f'{ar} {abs(sp["change"]):,.2f}  ({sign}{sp["rate"]:.2f}%)'
        )

    lines.append('')  # 빈 줄

    # 환율
    fx = fetch_yahoo('KRW=X')
    if fx:
        ar = _arrow(-fx['change'])  # KRW=X는 달러당 원 → 원화강세 = 수치하락
        sign = '+' if fx['change'] > 0 else ''
        lines.append(
            f'  USD/KRW  <b>{fx["price"]:,.1f}</b>  {ar} {abs(fx["change"]):.1f}'
        )

    # 미국 10Y 금리 (%TNX = 10-Year Treasury)
    tnx = fetch_yahoo('%5ETNX')
    if tnx:
        ar = _arrow(tnx['change'])
        sign = '+' if tnx['change'] > 0 else ''
        lines.append(
            f'  미국 10Y  <b>{tnx["price"]:.3f}%</b>  {ar} {abs(tnx["change"]):.3f}pp'
        )

    return '\n'.join(lines)


def build_stock_section(companies: list) -> str:
    """워치리스트 주가 블록 (상장사만)"""
    listed = [c for c in companies if c.get('listed') and c.get('stock_code')]
    if not listed:
        return ''

    lines = ['<b>📈 워치리스트 주가</b>']
    for comp in listed:
        d = fetch_stock_price(comp['stock_code'])
        if not d:
            continue
        name  = comp['name']
        code  = comp['stock_code']
        ar    = _arrow(d['change'])
        sign  = '+' if d['change'] > 0 else ''
        rate_str = f'{sign}{d["rate"]:.2f}%'
        # 색상: 볼드(상승) or 이탤릭(하락) — 텔레그램 HTML 활용
        price_int = int(d['price'])
        price_fmt = f'<b>{price_int:,}</b>' if d['change'] >= 0 else f'{price_int:,}'
        lines.append(
            f'  {name:<10} <code>{code}</code>  {price_fmt}  {ar} {rate_str}'
        )
    return '\n'.join(lines)


# ─────────────────────────────────────────
# 뉴스 fetch + 점수
# ─────────────────────────────────────────

def load_watchlist() -> list:
    with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)['companies']


def load_sent() -> set:
    try:
        with open(SENT_FILE, 'r') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_sent(sent_set: set):
    os.makedirs(os.path.dirname(SENT_FILE), exist_ok=True)
    recent = sorted(sent_set)[-2000:]
    with open(SENT_FILE, 'w') as f:
        json.dump(recent, f)


def article_hash(title: str, link: str) -> str:
    return hashlib.md5(f'{title[:80]}{link[:80]}'.encode()).hexdigest()[:12]


def fetch_news(company_name: str, aliases=None):
    terms = [company_name] + (aliases or [])
    query = '+OR+'.join([f'%22{t}%22' for t in terms])
    url = f'https://news.google.com/rss/search?q={query}+when:1d&hl=ko&gl=KR&ceid=KR:ko'
    out = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        root = ET.fromstring(resp.content)
        for item in root.findall('.//item'):
            title  = re.sub(r'<[^>]+>', '', item.findtext('title', '')).strip()
            link   = item.findtext('link', '').strip()
            source = item.findtext('source', '').strip()
            if title and link:
                out.append({'title': title, 'link': link, 'source': source})
    except Exception as e:
        print(f'  뉴스 에러 ({company_name}): {e}')
    return out


def score_article(article: dict, company: dict) -> int:
    title  = article['title']
    source = article['source']
    score  = 0
    for src_kw, w in SOURCE_WEIGHT.items():
        if src_kw in source:
            score += w
            break
    for kw, w in KEYWORD_BOOST.items():
        if kw in title:
            score += w
    for term in [company['name']] + company.get('alias', []):
        if term in title:
            score += 1
            break
    return score


# ─────────────────────────────────────────
# 텔레그램 전송
# ─────────────────────────────────────────

def send_telegram(text: str, silent: bool = False):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    # 4000자 청크 분할
    chunks, cur = [], ''
    for line in text.split('\n'):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = line
        else:
            cur += ('\n' + line) if cur else line
    if cur:
        chunks.append(cur)

    for c in chunks:
        resp = requests.post(url, data={
            'chat_id': CHAT_ID,
            'text': c,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true',
            'disable_notification': 'true' if silent else 'false',
        }, timeout=30)
        if not resp.json().get('ok'):
            print(f'  전송 실패: {resp.text[:300]}')


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

DIVIDER = '━━━━━━━━━━━━━━━━━━━━━━━━━━━'

def main():
    now     = datetime.now(KST)
    session = '오전' if now.hour < 12 else '오후'
    _days_ko = ['월', '화', '수', '목', '금', '토', '일']
    date_str = f'{now.strftime("%Y-%m-%d")} ({_days_ko[now.weekday()]})'
    time_str = now.strftime('%H:%M')
    print(f'[{now}] IB 뉴스브리프 ({session})')

    if not BOT_TOKEN or not CHAT_ID:
        print('  IB_TEAM_BOT_TOKEN / CHAT_ID 누락')
        return

    companies = load_watchlist()
    sent      = load_sent()

    # ── 1. 헤더 ──────────────────────────────────
    header_line = (
        f'<b>📋 IB 뉴스브리프</b>  |  {date_str}  <b>{session}</b>  {time_str} KST'
    )

    # ── 2. 마켓 스냅샷 ───────────────────────────
    market_block = build_market_section(session, now)

    # ── 3. 워치리스트 주가 ───────────────────────
    stock_block = build_stock_section(companies)

    # ── 4. 뉴스 수집 ─────────────────────────────
    sections = []
    total_fetched = 0
    total_skip_dedup = 0
    total_skip_score = 0
    for comp in companies:
        articles = fetch_news(comp['name'], comp.get('alias'))
        total_fetched += len(articles)
        seen, unique = set(), []
        dedup_skip = 0
        for a in articles:
            h  = article_hash(a['title'], a['link'])
            tk = re.sub(r'\s+', '', a['title'].lower())[:50]
            if h in sent or tk in seen:
                dedup_skip += 1
                continue
            seen.add(tk)
            a['hash'] = h
            unique.append(a)
        total_skip_dedup += dedup_skip

        for a in unique:
            a['score'] = score_article(a, comp)

        score_skip = sum(1 for a in unique if a['score'] <= 0)
        total_skip_score += score_skip
        filtered = sorted(
            [a for a in unique if a['score'] > 0],
            key=lambda x: x['score'], reverse=True
        )[:5]

        if filtered:
            sections.append((comp, filtered))
        print(f'  [{comp["name"]}] fetch={len(articles)} dedup_skip={dedup_skip} score_skip={score_skip} → {len(filtered)}건')

    # ── 5. 뉴스 블록 ─────────────────────────────
    news_lines = [f'<b>📰 뉴스</b>']
    total = 0
    for comp, picks in sections:
        code_str = ''
        if comp.get('listed') and comp.get('stock_code'):
            code_str = f'  <code>{comp["stock_code"]}</code>'
        market_str = f' ({comp["market"]})' if comp.get('market') else ''
        news_lines.append(f'\n🏢 <b>{comp["name"]}</b>{code_str}{market_str}')
        for i, a in enumerate(picks, 1):
            t    = a['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            link = a['link'].replace('&', '&amp;')  # href 속성 내 & 이스케이프
            src  = (a['source'] or '─').replace('&', '&amp;')
            # 높은 점수 기사 볼드 (score >= 5)
            title_tag = f'<b>{t}</b>' if a['score'] >= 5 else t
            news_lines.append(f'  {i}. <a href="{link}">{title_tag}</a>  <i>[{src}]</i>')
            total += 1

    if not sections:
        news_lines.append('\n  신규 IB 관련 뉴스 없음')

    # L1 §자동화 산출물 검증: SKIP 건수 노출 (전량 SKIP 시 ⚠️ 경고)
    skip_info = f'중복 {total_skip_dedup}건 · 필터 {total_skip_score}건 SKIP'
    if total_fetched > 0 and total == 0:
        skip_suffix = f'\n⚠️ <b>전체 SKIP</b> — fetch {total_fetched}건 / {skip_info}'
    else:
        skip_suffix = f'\n<i>({skip_info})</i>' if (total_skip_dedup + total_skip_score) > 0 else ''

    footer = f'\n{DIVIDER}\n총 <b>{total}건</b>  ·  <b>{len(sections)}개사</b>{skip_suffix}'

    # ── 6. 최종 조립 ─────────────────────────────
    parts = [
        header_line,
        DIVIDER,
        market_block,
        DIVIDER,
    ]
    if stock_block:
        parts += [stock_block, DIVIDER]
    parts += ['\n'.join(news_lines), footer]

    full_msg = '\n'.join(parts)

    send_telegram(full_msg, silent=False)
    print(f'  전송 완료 — 뉴스 {total}건 / {len(sections)}개사')

    for _, picks in sections:
        for a in picks:
            sent.add(a['hash'])
    save_sent(sent)


if __name__ == '__main__':
    main()
