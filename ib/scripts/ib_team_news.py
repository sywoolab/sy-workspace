"""
IB 팀 채널 — 워치리스트 기반 뉴스 일 2회 발송
- watchlist_team.json 기준 (12개사)
- 기업당 최대 5건 (점수 컷 통과만, 빈 기업은 표시 X)
- 매체 가중치 + 키워드 가중치 필터
- 07:30 KST / 15:00 KST 발송
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
CHAT_ID = os.environ.get('IB_TEAM_CHAT_ID', '')

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
WATCHLIST_FILE = os.path.join(BASE_DIR, 'watchlist_team.json')
SENT_FILE = os.path.join(BASE_DIR, 'data', 'team_news_sent.json')

# 매체 가중치 (Google News RSS의 source 필드 기준 — 부분 매칭)
SOURCE_WEIGHT = {
    '매일경제': 2, '한국경제': 2, '연합인포맥스': 2, '서울경제': 2,
    '이데일리': 2, '머니투데이': 2, '조선비즈': 2, '뉴스핌': 2,
    '인포스탁': 2, '더벨': 2, '비즈니스워치': 2, 'ZDNet': 2,
    '연합뉴스': 1, '뉴시스': 1, '뉴스1': 1, '아시아경제': 1,
    '파이낸셜뉴스': 1, '디지털타임스': 1, 'IT조선': 1,
}

# 키워드 가중치 (제목에 포함 시 가산)
KEYWORD_BOOST = {
    # 딜·이벤트 핵심 (+3)
    '실적': 3, '잠정': 3, '영업이익': 3, '매출': 3,
    'IPO': 3, '상장': 3, '증자': 3, '유증': 3, '무증': 3,
    'M&A': 3, '인수': 3, '합병': 3, '분할': 3,
    'CB': 3, 'EB': 3, '전환사채': 3, '교환사채': 3,
    '자사주': 3, '블록딜': 3, '대주주': 3, '경영권': 3,
    '소송': 3, '제재': 3, '리콜': 3,
    # 비즈니스 모멘텀 (+2)
    '계약': 2, '수주': 2, '공급': 2, '특허': 2,
    '신제품': 2, '출시': 2, '진출': 2, '확장': 2,
    '투자유치': 2, '시리즈': 2,
    # 가십·노이즈 (-2)
    '광고': -2, '협찬': -2, '이벤트': -2, '경품': -2,
}


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
    recent = sorted(sent_set)[-2000:]
    with open(SENT_FILE, 'w') as f:
        json.dump(recent, f)


def article_hash(title, link):
    return hashlib.md5(f"{title[:80]}{link[:80]}".encode()).hexdigest()[:12]


def fetch_news(company_name, aliases=None):
    """기업명+별칭으로 Google News RSS 검색"""
    terms = [company_name] + (aliases or [])
    query = '+OR+'.join([f'%22{t}%22' for t in terms])
    url = f'https://news.google.com/rss/search?q={query}+when:1d&hl=ko&gl=KR&ceid=KR:ko'

    out = []
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        root = ET.fromstring(resp.content)
        for item in root.findall('.//item'):
            title = re.sub(r'<[^>]+>', '', item.findtext('title', '')).strip()
            link = item.findtext('link', '').strip()
            source = item.findtext('source', '').strip()
            if title and link:
                out.append({'title': title, 'link': link, 'source': source})
    except Exception as e:
        print(f"  뉴스 에러 ({company_name}): {e}")
    return out


def score_article(article, company):
    """매체 + 키워드 + 회사명 매칭으로 점수 산출"""
    title = article['title']
    source = article['source']
    score = 0

    # 매체 가중치 (부분 매칭)
    for src_kw, w in SOURCE_WEIGHT.items():
        if src_kw in source:
            score += w
            break

    # 키워드 가중치
    for kw, w in KEYWORD_BOOST.items():
        if kw in title:
            score += w

    # 회사명/별칭이 제목에 포함되면 +1 (정확도)
    name = company['name']
    aliases = company.get('alias', [])
    for term in [name] + aliases:
        if term in title:
            score += 1
            break

    return score


def format_market_label(comp):
    if not comp.get('listed'):
        return ''
    market = comp.get('market', '')
    code = comp.get('stock_code', '')
    if market and code:
        return f' ({market} {code})'
    return ''


def send_telegram(text, silent=False):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = []
    cur = ""
    for line in text.split('\n'):
        if len(cur) + len(line) + 1 > 4000:
            chunks.append(cur)
            cur = line
        else:
            cur += '\n' + line if cur else line
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
            print(f"  전송 실패: {resp.text[:200]}")


def main():
    now = datetime.now(KST)
    hour = now.hour
    session = "오전" if hour < 12 else "오후"
    print(f"[{now}] IB Team 뉴스 ({session})")

    if not BOT_TOKEN or not CHAT_ID:
        print("  IB_TEAM_BOT_TOKEN/CHAT_ID 누락")
        return

    companies = load_watchlist()
    sent = load_sent()

    sections = []  # [(company, [picked articles])]
    for comp in companies:
        articles = fetch_news(comp['name'], comp.get('alias'))
        # 중복 제거 + sent 제외
        seen = set()
        unique = []
        for a in articles:
            h = article_hash(a['title'], a['link'])
            tk = re.sub(r'\s+', '', a['title'].lower())[:50]
            if h in sent or tk in seen:
                continue
            seen.add(tk)
            a['hash'] = h
            unique.append(a)

        # 점수 계산
        for a in unique:
            a['score'] = score_article(a, comp)

        # 점수 0 이하 제외 + 상위 5건
        filtered = [a for a in unique if a['score'] > 0]
        filtered.sort(key=lambda x: x['score'], reverse=True)
        picked = filtered[:5]

        if picked:
            sections.append((comp, picked))
        print(f"  [{comp['name']}] {len(articles)} → {len(picked)}건 채택")

    if not sections:
        print("  IB 관련 신규 뉴스 없음")
        return

    # 메시지 포맷
    date_str = now.strftime('%Y-%m-%d (%a) %H:%M')
    lines = [f"📰 IB 워치리스트 뉴스 ({date_str} {session})", "─────────────────────", ""]
    total = 0
    for comp, picks in sections:
        label = format_market_label(comp)
        lines.append(f"🏢 <b>{comp['name']}</b>{label}")
        for i, a in enumerate(picks, 1):
            t = a['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            src = a['source'] or '-'
            lines.append(f'{i}. <a href="{a["link"]}">{t}</a> [{src}]')
            total += 1
        lines.append("")

    lines.append(f"총 {total}건 ({len(sections)}개사)")
    msg = "\n".join(lines)

    send_telegram(msg, silent=False)
    print(f"  텔레그램 전송 완료 ({total}건)")

    for _, picks in sections:
        for a in picks:
            sent.add(a['hash'])
    save_sent(sent)


if __name__ == '__main__':
    main()
