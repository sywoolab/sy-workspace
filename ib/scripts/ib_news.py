"""
IB News Daily Clipping
- 국내/해외 IB 관련 뉴스 RSS 수집
- 키워드 필터링 + 관련도 순 정렬
- 하루 2회 (오전 8시, 오후 2시) 텔레그램 발송, 최대 20건
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
TODAY = datetime.now(KST).strftime('%Y-%m-%d')

# L0 §"봇 토큰 fallback 체인" (IB 뉴스 봇)
BOT_TOKEN = (os.environ.get('IB_BOT_TOKEN')
             or os.environ.get('BOT_TOKEN')
             or os.environ.get('TELEGRAM_BOT_TOKEN', ''))
CHAT_ID = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')

SENT_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_sent.json')
MAX_ARTICLES = 20

# ============================================================
# RSS 소스 정의
# ============================================================
FEEDS = {
    # 국내 IB 전문 매체 (사용자 1순위 — 2026-05-06 더벨 강조)
    '더벨': 'https://news.google.com/rss/search?q=site:thebell.co.kr&hl=ko&gl=KR&ceid=KR:ko',
    '인베스트조선': 'https://news.google.com/rss/search?q=site:investchosun.com&hl=ko&gl=KR&ceid=KR:ko',
    '딜사이트': 'https://news.google.com/rss/search?q=site:dealsite.co.kr&hl=ko&gl=KR&ceid=KR:ko',
    # 국내 경제지 (RSS 직접)
    '한경': 'https://www.hankyung.com/feed/finance',
    '매경': 'https://www.mk.co.kr/rss/50200011/',
    '조선비즈': 'https://news.google.com/rss/search?q=site:biz.chosun.com&hl=ko&gl=KR&ceid=KR:ko',
    '서울경제': 'https://news.google.com/rss/search?q=site:sedaily.com&hl=ko&gl=KR&ceid=KR:ko',
    '머니투데이': 'https://news.google.com/rss/search?q=site:mt.co.kr&hl=ko&gl=KR&ceid=KR:ko',
    '이데일리': 'https://news.google.com/rss/search?q=site:edaily.co.kr&hl=ko&gl=KR&ceid=KR:ko',
    # 국내 종합지 (가중치 낮음, IB 디테일 부족하지만 큰 딜은 보도)
    '조선일보': 'https://news.google.com/rss/search?q=site:chosun.com+(IPO+OR+M%26A+OR+인수+OR+상장)&hl=ko&gl=KR&ceid=KR:ko',
    '중앙일보': 'https://news.google.com/rss/search?q=site:joongang.co.kr+(IPO+OR+M%26A+OR+인수+OR+상장)&hl=ko&gl=KR&ceid=KR:ko',
    '동아일보': 'https://news.google.com/rss/search?q=site:donga.com+(IPO+OR+M%26A+OR+인수+OR+상장)&hl=ko&gl=KR&ceid=KR:ko',
    # 키워드 광역 검색
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
    # ========================================================
    # 1순위 (3점) — Macro IB 시그널 (사용자 강조: 지배구조·승계·자금조달)
    # ========================================================
    # ── 지배구조 ──
    '지배구조': 3, '거버넌스': 3, '지주회사': 3, '지주사 전환': 3,
    '순환출자': 3, '계열분리': 3, '계열재편': 3, '지분구조': 3,
    '오너': 3, '오너일가': 3, '특수관계인': 3,
    # ── 승계 ──
    '승계': 3, '경영승계': 3, '가업승계': 3, '오너승계': 3,
    '상속': 3, '증여': 3, '후계': 3, '2세': 3, '3세': 3, '4세': 3,
    '자녀 경영': 3, '오너 자녀': 3,
    # ── 자금조달 (매크로 카테고리) ──
    '자금조달': 3, '자금난': 3, '자본확충': 3, '자본조달': 3,
    '유동성 위기': 3, '유동성 확보': 3, '재무 부담': 3,
    # ── 거버넌스 분쟁 ──
    '경영권 분쟁': 3, '경영권': 3, '형제 분쟁': 3, '경영권 다툼': 3,
    '행동주의': 3, '주주제안': 3, '위임장': 3, '표 대결': 3,
    '의결권': 3, '주총 표': 3, '소수주주': 3,
    # ── 신한 IB 특화 (사용자 직접 관련) ──
    '신한지주': 3, '신한증권': 3, '신한투자증권': 3,
    '신한IB': 3, '신한CIB': 3, '교보생명': 3,
    # ========================================================
    # 2순위 (2점) — 트랜잭션 + 도구 + 시장 신호
    # ========================================================
    # ── 딜 결과 ──
    'M&A': 2, '인수합병': 2, '인수': 2, '합병': 2, '매각': 2,
    'IPO': 2, '상장': 2, '상장예비심사': 2, '예심': 2,
    '블록딜': 2, '대량매매': 2, '지분매각': 2, '지분취득': 2,
    '물적분할': 2, '인적분할': 2, '사업재편': 2, '사업분할': 2,
    # ── 자금조달 수단 ──
    '유상증자': 2, '유증': 2, '무증': 2, '공모': 2, '주관사': 2, '대표주관': 2,
    '전환사채': 2, '교환사채': 2, 'CB': 2, 'EB': 2, 'BW': 2,
    '신주인수권': 2, '회사채': 2, '사채발행': 2, '영구채': 2,
    '신종자본증권': 2, 'PRS': 2, '주가수익스왑': 2, '메자닌': 2,
    '리파이낸싱': 2, '차입매수': 2, 'LBO': 2,
    # ── 자사주·주주환원 (밸류업·소각 정책 트렌드) ──
    '자사주': 2, '자사주 소각': 2, '자사주 매입': 2,
    '주주환원': 2, '배당확대': 2, '밸류업': 2, '코리아디스카운트': 2,
    # ── 펀드·PE ──
    'PEF': 2, '사모펀드': 2, '바이아웃': 2,
    '펀드결성': 2, '펀드 클로즈': 2, 'Exit': 2, '회수': 2,
    'GP': 2, 'LP': 2, 'VC': 2, '벤처캐피탈': 2,
    # ── 위기·재무구조 ──
    '워크아웃': 2, '법정관리': 2, '회생': 2, '디폴트': 2,
    '부도': 2, '신용등급 강등': 2, '신용등급 하향': 2,
    '부채 상환': 2, '채권단': 2,
    '대주주': 2, '최대주주': 2, '특수관계자': 2,
    # ── 부동산금융·구조화 ──
    '부동산PF': 2, '프로젝트파이낸싱': 2, '구조화금융': 2,
    # ========================================================
    # 3순위 (1점) — 시장·정책 환경
    # ========================================================
    '자본시장법': 1, '상법 개정': 1, '상속세': 1, '증여세': 1,
    '규제': 1, '인허가': 1, '산업재편': 1,
    '투자은행': 1, '자본시장': 1, '증권': 1,
    '대출': 1, '여신': 1, '인수금융': 1, '중간금융': 1,
    'IB': 1, '딜': 1, '자문': 1,
    # ========================================================
    # 감점 (-3) — 노이즈 차단
    # ========================================================
    '시황': -3, '코스피 마감': -3, '코스닥 마감': -3, '증시 마감': -3,
    '추천종목': -3, '오늘의 종목': -3, '주식 추천': -3,
    '광고': -3, '협찬': -3, '이벤트': -3, '경품': -3,
    '연예': -3, '게임 출시': -3, '영화': -3, '드라마': -3,
    '코인': -2, '가상자산': -2, '비트코인': -2,  # IB 무관 가상화폐 노이즈
}

KEYWORDS_EN = {
    # ========================================================
    # 1순위 (3점) — Macro IB 시그널 (해외도 동일 우선순위)
    # ========================================================
    # ── Governance ──
    'governance': 3, 'corporate governance': 3, 'shareholder activism': 3,
    'proxy fight': 3, 'proxy battle': 3, 'activist': 3,
    'controlling shareholder': 3, 'controlling stake': 3,
    'family feud': 3, 'family dispute': 3, 'boardroom': 3,
    # ── Succession ──
    'succession': 3, 'inheritance': 3, 'heir': 3, 'family business': 3,
    'next generation': 3, 'family-owned': 3,
    # ── Financing (macro) ──
    'capital raise': 3, 'fundraising': 3, 'liquidity crisis': 3,
    'capital injection': 3, 'capital infusion': 3,
    'distressed': 3, 'bankruptcy': 3, 'default': 3,
    # ── Korea-specific ──
    'Shinhan': 3, 'Kyobo': 3,
    # ========================================================
    # 2순위 (2점) — Transactions + Tools
    # ========================================================
    'M&A': 2, 'merger': 2, 'acquisition': 2, 'takeover': 2, 'buyout': 2,
    'IPO': 2, 'listing': 2, 'public offering': 2, 'underwriting': 2,
    'block deal': 2, 'block trade': 2, 'spin-off': 2, 'spinoff': 2,
    'divestiture': 2, 'divestment': 2, 'stake sale': 2,
    'convertible': 2, 'bond issuance': 2, 'equity offering': 2,
    'LBO': 2, 'refinancing': 2, 'leveraged': 2,
    'private equity': 2, 'venture capital': 2, 'restructuring': 2,
    'buyback': 2, 'share repurchase': 2, 'cancellation': 2,
    'PE fund': 2, 'PEF': 2, 'fund close': 2, 'exit': 2,
    # ========================================================
    # 3순위 (1점) — Market context
    # ========================================================
    'investment bank': 1, 'capital markets': 1,
    'Goldman': 1, 'Morgan Stanley': 1, 'JPMorgan': 1,
    'BlackRock': 1, 'KKR': 1, 'Carlyle': 1, 'Blackstone': 1,
    'deal': 1, 'advisory': 1,
    # ========================================================
    # 감점 (-3) — Noise
    # ========================================================
    'crypto': -2, 'bitcoin': -2, 'NFT': -3,
    'celebrity': -3, 'sports': -3, 'gaming': -3,
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
    """텔레그램 메시지 포맷 — 번호 부여, 중요도순"""
    date_str = datetime.now(KST).strftime('%Y-%m-%d (%a)')
    hour = datetime.now(KST).hour
    session = "오전" if hour < 12 else "오후"
    lines = [f"📰 IB Daily Brief ({date_str} {session})\n"]

    num = 1
    if kr_articles:
        lines.append("🇰🇷 국내")
        for a in kr_articles:
            title_escaped = a['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            lines.append(f'{num}. [{a["source"]}] <a href="{a["link"]}">{title_escaped}</a>')
            num += 1
        lines.append("")

    if en_articles:
        lines.append("🌏 해외")
        for a in en_articles:
            title_escaped = a['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            lines.append(f'{num}. [{a["source"]}] <a href="{a["link"]}">{title_escaped}</a>')
            num += 1
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
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true',
        }, timeout=30)
        if not resp.json().get('ok'):
            print(f"  전송 실패: {resp.text[:200]}")


def main():
    print(f"[{datetime.now(KST)}] IB News 클리핑 시작")

    sent = load_sent()
    all_kr = []
    all_en = []

    # 국내 소스 (2026-05-06 확장 — 사용자 IB 매체 리스트 반영)
    kr_sources = [
        # IB 전문 (1순위)
        '더벨', '인베스트조선', '딜사이트',
        # 경제지
        '한경', '매경', '조선비즈', '서울경제', '머니투데이', '이데일리',
        # 종합지 (IB 키워드 필터)
        '조선일보', '중앙일보', '동아일보',
        # 키워드 광역
        '국내IB',
    ]
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

    # 점수 임계점 (2026-05-06 사용자 피드백 반영)
    # - 국내: ≥ 3점만 통과 (한 카테고리 핵심 시그널 必)
    # - 해외: ≥ 5점만 통과 (정말 중요한 글로벌 IB 뉴스만 — 사용자 명시)
    MIN_SCORE_KR = 3
    MIN_SCORE_EN = 5
    all_kr = [a for a in all_kr if a['score'] >= MIN_SCORE_KR]
    all_en = [a for a in all_en if a['score'] >= MIN_SCORE_EN]

    # 점수 높은 순 정렬
    all_kr.sort(key=lambda x: x['score'], reverse=True)
    all_en.sort(key=lambda x: x['score'], reverse=True)

    # 상한: 국내 10, 해외 3 (한가하면 그보다 적게 — 임계점이 자동 컷오프)
    kr_pick = all_kr[:10]
    en_pick = all_en[:3]

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
