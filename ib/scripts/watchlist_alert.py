"""
Watchlist 기업 모니터링
- DART 공시 체크 (30분마다)
- 뉴스 키워드 검색 (Google News RSS)
- SY Workspace 텔레그램 봇으로 알림
"""

import os
import json
import re
import hashlib
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone, timedelta

# L0 §"환경변수 부트스트랩": 부모 경로 거슬러 올라가며 .env 탐색
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
TODAY = datetime.now(KST).strftime('%Y%m%d')

DART_API_KEY = os.environ.get('DART_API_KEY', '')
# L0 §"봇 토큰 fallback 체인" (기본 — DART/watchlist)
BOT_TOKEN = os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
WATCHLIST_FILE = os.path.join(BASE_DIR, 'watchlist.json')
SENT_FILE = os.path.join(BASE_DIR, 'data', 'watchlist_sent.json')


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


def item_hash(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]


# ============================================================
# 1. DART 공시 체크
# ============================================================
def fetch_dart_by_corp(corp_code):
    """특정 기업 DART 공시 조회"""
    url = 'https://opendart.fss.or.kr/api/list.json'
    params = {
        'crtfc_key': DART_API_KEY,
        'corp_code': corp_code,
        'bgn_de': TODAY,
        'end_de': TODAY,
        'page_count': 50,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get('status') == '000':
            return data.get('list', [])
    except Exception as e:
        print(f"  DART 에러 ({corp_code}): {e}")
    return []


# ============================================================
# 2. 뉴스 검색 (Google News RSS)
# ============================================================
def fetch_news(company_name, aliases=None):
    """기업명으로 Google News RSS 검색"""
    search_terms = [company_name]
    if aliases:
        search_terms.extend(aliases)

    # OR 검색
    query = '+OR+'.join([f'%22{t}%22' for t in search_terms])
    url = f'https://news.google.com/rss/search?q={query}+when:1d&hl=ko&gl=KR&ceid=KR:ko'

    articles = []
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=15)
        root = ET.fromstring(resp.content)

        for item in root.findall('.//item'):
            title = item.findtext('title', '').strip()
            link = item.findtext('link', '').strip()
            source = item.findtext('source', '').strip()

            if title and link:
                # HTML 태그 제거
                title = re.sub(r'<[^>]+>', '', title).strip()
                articles.append({
                    'title': title,
                    'link': link,
                    'source': source,
                })
    except Exception as e:
        print(f"  뉴스 에러 ({company_name}): {e}")

    return articles


# ============================================================
# 3. 텔레그램 발송
# ============================================================
def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    # 4096자 분할
    chunks = []
    current = ""
    for line in text.split('\n'):
        if len(current) + len(line) + 1 > 4000:
            chunks.append(current)
            current = line
        else:
            current += '\n' + line if current else line
    if current:
        chunks.append(current)

    for chunk in chunks:
        requests.post(url, data={
            'chat_id': CHAT_ID,
            'text': chunk,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true',
        }, timeout=30)


# ============================================================
# Main
# ============================================================
def main():
    now = datetime.now(KST)
    print(f"[{now}] Watchlist 모니터링 시작")

    companies = load_watchlist()
    sent = load_sent()

    all_alerts = []

    for comp in companies:
        name = comp['name']
        dart_code = comp.get('dart_code')
        aliases = comp.get('alias', [])

        # DART 공시
        if dart_code:
            disclosures = fetch_dart_by_corp(dart_code)
            for d in disclosures:
                rcept_no = d.get('rcept_no', '')
                h = item_hash(f"dart_{rcept_no}")
                if h not in sent:
                    report = d.get('report_nm', '')
                    link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                    all_alerts.append({
                        'type': '공시',
                        'company': name,
                        'title': report,
                        'link': link,
                        'hash': h,
                    })

        # 뉴스
        news = fetch_news(name, aliases)
        for n in news:
            h = item_hash(f"news_{n['title'][:50]}")
            if h not in sent:
                all_alerts.append({
                    'type': '뉴스',
                    'company': name,
                    'title': n['title'],
                    'link': n['link'],
                    'source': n.get('source', ''),
                    'hash': h,
                })

        print(f"  [{name}] DART: {len(disclosures) if dart_code else '-'}, 뉴스: {len(news)}")

    # 중복 제거 (제목 유사도)
    seen_titles = set()
    unique_alerts = []
    for a in all_alerts:
        title_key = re.sub(r'\s+', '', a['title'].lower())[:40]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_alerts.append(a)

    print(f"\n  신규 알림: {len(unique_alerts)}건")

    if not unique_alerts:
        print("  신규 알림 없음")
        return

    # 메시지 포맷
    date_str = now.strftime('%Y-%m-%d %H:%M')
    lines = [f"🔔 Watchlist 알림 ({date_str})\n"]

    # 공시 먼저
    disclosures = [a for a in unique_alerts if a['type'] == '공시']
    news_items = [a for a in unique_alerts if a['type'] == '뉴스']

    num = 1
    if disclosures:
        lines.append("📢 공시")
        for a in disclosures:
            title_escaped = a['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            lines.append(f'{num}. [{a["company"]}] <a href="{a["link"]}">{title_escaped}</a>')
            num += 1
        lines.append("")

    if news_items:
        lines.append("📰 뉴스")
        for a in news_items[:20]:  # 뉴스는 최대 20건
            title_escaped = a['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            lines.append(f'{num}. [{a["company"]}] <a href="{a["link"]}">{title_escaped}</a>')
            num += 1
        lines.append("")

    lines.append(f"총 {num - 1}건")

    msg = "\n".join(lines)
    send_telegram(msg)
    print(f"  텔레그램 전송 완료")

    # 전송 기록
    for a in unique_alerts:
        sent.add(a['hash'])
    save_sent(sent)


if __name__ == '__main__':
    main()
