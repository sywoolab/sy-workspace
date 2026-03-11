"""
IB News Daily Clipping
- 국내/해외 IB 관련 뉴스 RSS 수집
- 키워드 필터링 + 관련도 순 정렬
- 하루 1회 (아침 8시) 텔레그램 발송, 최대 30건
"""

import os
import json
import re
import hashlib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime('%Y-%m-%d')

# 환경변수
BOT_TOKEN = os.environ['IB_BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']

SENT_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_sent.json')
MAX_ARTICLES = 30

# ============================================================
# RSS 소스 정의
# ============================================================
FEEDS = {
    # 국내
    '한경': 'https://www.hankyung.com/feed/finance',
    '매경': 'https://www.mk.co.kr/rss/50200011/',
    '더벨': 'https://news.google.com/rss/search?q=site:thebell.co.kr&hl=ko&gl=KR&ceid=KR:ko',
    '국내IB': 'https://news.google.com/rss/search?q=IPO+OR+유상증자+OR+M%26A+OR+인수합병+OR+지배구조+OR+경영권+OR+상속+OR+증여+OR+사모펀드+OR+블록딜+OR+CB+OR+EB&hl=ko&gl=KR&ceid=KR:ko',
    # 해외
    'FT_Companies': 'https://www.ft.com/rss/companies',
    'FT_Markets': 'https://www.ft.com/rss/markets',
    'CNBC': 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664',
    'Global_IB': 'https://news.google.com/rss/search?q=%22M%26A%22+OR+%22merger%22+OR+%22acquisition%22+OR+%22IPO%22+OR+%22investment+banking%22+OR+%22block+deal%22+when:1d&hl=en&gl=US&ceid=US:en',
}

# ============================================================
# 키워드 (관련도 점수용)
# ============================================================
# 점수가 높을수록 IB 직접 관련
KEYWORDS_KR = {
    # 딜 직접 (3점)
    'IPO': 3, '유상증자': 3, '무상증자': 2, 'CB': 3, 'EB': 3, 'BW': 3,
    '전환사채': 3, '교환사채': 3, '신주인수권': 3, '공모': 3,
    'M&A': 3, '인수합병': 3, '인수': 2, '합병': 3,
    '블록딜': 3, '자사주': 3, '회사채': 2, '사채발행': 3,
    '대표주관': 3, '주관사': 3, '상장': 2, '상장예비심사': 3,
    # 딜 기회 포착 (2점)
    '지배구조': 2, '경영권': 2, '상속': 2, '증여': 2,
    '지주회사': 2, '순환출자': 2, '계열분리': 2,
    '물적분할': 2, '인적분할': 2, '사업재편': 2,
    '대주주': 2, '최대주주': 2, '지분매각': 2, '지분취득': 2,
    'PEF': 2, '사모펀드': 2, '바이아웃': 2,
    '리파이낸싱': 2, '차입매수': 2,
    '구조조정': 2, '워크아웃': 2, '법정관리': 2,
    '부동산PF': 2, '프로젝트파이낸싱': 2,
    # 산업/시장 동향 (1점)
    '규제': 1, '인허가': 1, '산업재편': 1,
    '증권': 1, '투자은행': 1, '자본시장': 1,
    '펀드': 1, '대출': 1, '여신': 1,
}

KEYWORDS_EN = {
    # Direct deal (3)
    'M&A': 3, 'merger': 3, 'acquisition': 3, 'IPO': 3,
    'underwriting': 3, 'bond issuance': 3, 'convertible': 3,
    'equity offering': 3, 'block deal': 3, 'block trade': 3,
    'buyout': 3, 'LBO': 3, 'takeover': 3, 'listing': 2,
    # Deal opportunity (2)
    'private equity': 2, 'venture capital': 2, 'restructuring': 2,
    'divestiture': 2, 'spin-off': 2, 'spinoff': 2,
    'stake sale': 2, 'controlling stake': 2,
    'succession': 2, 'inheritance': 2,
    'refinancing': 2, 'leveraged': 2,
    # Market (1)
    'investment bank': 1, 'capital markets': 1,
    'Goldman': 1, 'Morgan Stanley': 1, 'JPMorgan': 1,
    'deal': 1, 'advisory': 1,
}


def article_hash(title, link):
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()[:12]


def load_sent():
    try:
        with open(SENT_FILE, 'r') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_sent(sent_set):
    os.makedirs(os.path.dirname(SENT_FILE), exist_ok=True)
    recent = sorted(sent_set)[-1000:]
    with open(SENT_FILE, 'w') as f:
        json.dump(recent, f)


def fetch_rss(url, source_name):
    """RSS 파싱하여 기사 리스트 반환"""
    articles = []
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)

        # RSS 2.0
        for item in root.findall('.//item'):
            title = item.findtext('title', '').strip()
            link = item.findtext('link', '').strip()
            pub_date = item.findtext('pubDate', '')
            desc = item.findtext('description', '').strip()

            if title and link:
                # Google News redirect URL에서 실제 URL 추출
                if 'news.google.com' in link and 'url=' in link:
                    link = link.split('url=')[-1]

                articles.append({
                    'source': source_name,
                    'title': clean_html(title),
                    'link': link,
                    'desc': clean_html(desc)[:200],
                    'pub_date': pub_date,
                })

    except Exception as e:
        print(f"  [{source_name}] 에러: {e}")

    return articles


def clean_html(text):
    """HTML 태그 제거"""
    return re.sub(r'<[^>]+>', '', text).strip()


def score_article(article):
    """기사 관련도 점수 계산"""
    text = f"{article['title']} {article['desc']}".lower()
    score = 0

    # 국내 키워드
    for kw, pts in KEYWORDS_KR.items():
        if kw.lower() in text:
            score += pts

    # 해외 키워드
    for kw, pts in KEYWORDS_EN.items():
        if kw.lower() in text:
            score += pts

    return score


def format_message(kr_articles, en_articles):
    """텔레그램 메시지 포맷"""
    date_str = datetime.now(KST).strftime('%Y-%m-%d (%a)')
    lines = [f"📰 IB Daily Brief ({date_str})\n"]

    if kr_articles:
        lines.append("🇰🇷 국내")
        for a in kr_articles:
            lines.append(f"• [{a['source']}] {a['title']}")
            lines.append(f"  {a['link']}")
        lines.append("")

    if en_articles:
        lines.append("🌏 해외")
        for a in en_articles:
            lines.append(f"• [{a['source']}] {a['title']}")
            lines.append(f"  {a['link']}")
        lines.append("")

    total = len(kr_articles) + len(en_articles)
    lines.append(f"총 {total}건")

    return "\n".join(lines)


def send_telegram(text):
    """긴 메시지는 분할 전송"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    # 텔레그램 메시지 제한 4096자
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
        resp = requests.post(url, data={
            'chat_id': CHAT_ID,
            'text': chunk,
            'disable_web_page_preview': 'true',
        }, timeout=30)
        if not resp.json().get('ok'):
            print(f"  전송 실패: {resp.text[:200]}")


def main():
    print(f"[{datetime.now(KST)}] IB News 클리핑 시작")

    sent = load_sent()
    all_kr = []
    all_en = []

    # 국내 소스
    kr_sources = ['한경', '매경', '더벨', '국내IB']
    for name in kr_sources:
        articles = fetch_rss(FEEDS[name], name)
        print(f"  [{name}] {len(articles)}건 수집")
        all_kr.extend(articles)

    # 해외 소스
    en_sources = ['FT_Companies', 'FT_Markets', 'CNBC', 'Global_IB']
    for name in en_sources:
        articles = fetch_rss(FEEDS[name], name)
        print(f"  [{name}] {len(articles)}건 수집")
        all_en.extend(articles)

    # 중복 제거 (제목 기준)
    seen_titles = set()
    def dedup(articles):
        result = []
        for a in articles:
            title_key = re.sub(r'\s+', '', a['title'].lower())[:50]
            h = article_hash(a['title'], a['link'])
            if title_key not in seen_titles and h not in sent:
                seen_titles.add(title_key)
                result.append(a)
        return result

    all_kr = dedup(all_kr)
    all_en = dedup(all_en)

    # 관련도 점수 계산 & 필터
    for a in all_kr:
        a['score'] = score_article(a)
    for a in all_en:
        a['score'] = score_article(a)

    # 점수 0인 기사 제거 (IB 무관)
    all_kr = [a for a in all_kr if a['score'] > 0]
    all_en = [a for a in all_en if a['score'] > 0]

    # 점수 높은 순 정렬
    all_kr.sort(key=lambda x: x['score'], reverse=True)
    all_en.sort(key=lambda x: x['score'], reverse=True)

    # 국내 15~20건, 해외 10~15건 (합계 30건 이내)
    kr_pick = all_kr[:18]
    en_pick = all_en[:12]

    print(f"\n  국내 필터: {len(all_kr)}건 → {len(kr_pick)}건 선별")
    print(f"  해외 필터: {len(all_en)}건 → {len(en_pick)}건 선별")

    total = len(kr_pick) + len(en_pick)
    if total == 0:
        print("  IB 관련 뉴스 없음")
        return

    # 메시지 전송
    msg = format_message(kr_pick, en_pick)
    send_telegram(msg)
    print(f"  텔레그램 전송 완료 ({total}건)")

    # 전송 기록 저장
    for a in kr_pick + en_pick:
        sent.add(article_hash(a['title'], a['link']))
    save_sent(sent)


if __name__ == '__main__':
    main()
