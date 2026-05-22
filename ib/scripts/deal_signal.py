"""
딜 소싱 신호 탐지기
- DART 전체 공시에서 M&A/구조조정/자금조달 신호 탐지
- 신호 유형별 점수화 → 주간 TOP 랭킹 생성
- 텔레그램 리포트 + JSON 아카이브
"""

import os
import json
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

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
NOW  = datetime.now(KST)

DART_API_KEY = os.environ.get('DART_API_KEY', '')
BOT_TOKEN    = os.environ.get('BOT_TOKEN') or os.environ.get('IB_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHAT_ID      = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')

BASE_DIR   = Path(__file__).resolve().parent.parent
DATA_DIR   = BASE_DIR / 'data'
SIGNAL_FILE = DATA_DIR / 'deal_signals.json'
WATCHLIST_FILE = BASE_DIR / 'watchlist.json'

# ──────────────────────────────────────────────────────────────
# 신호 매트릭스: (키워드, 카테고리, 점수, 설명)
# 점수 10 = 경영권 거래 확정 / 1 = 약한 지표
# ──────────────────────────────────────────────────────────────
SIGNAL_MATRIX = [
    # 경영권 거래
    ('최대주주변경',        'M&A',    10, '경영권 이전 완료'),
    ('공개매수',            'M&A',    10, 'M&A 진행 중'),
    ('주식의포괄적교환',    'M&A',     9, '완전 자회사화'),
    ('주식의포괄적이전',    'M&A',     9, '지주사 전환'),
    ('합병결정',            'M&A',     9, '합병 진행'),
    ('분할결정',            '구조조정', 8, '분할/스핀오프'),
    ('영업양도',            '자산매각', 8, '사업 매각'),
    ('영업양수',            '자산인수', 8, '사업 인수'),
    ('자산양도',            '자산매각', 7, '자산 처분'),
    ('자산양수',            '자산인수', 7, '자산 취득'),

    # 지분 거래 신호
    ('교환사채',            'EB/지분',  9, '지분 처분 의도 — PE Exit 시그널'),
    ('주요주주변경',        '지분변동', 8, '주요주주 교체'),
    ('주요주주지분변동',    '지분변동', 7, '5% 이상 주주 변동'),
    ('임원ㆍ주요주주지분',  '지분변동', 6, '내부자 지분 변동'),

    # 자금조달 니즈
    ('전환사채',            '자금조달', 7, '자금 부족 신호 — CB 발행'),
    ('신주인수권부사채',    '자금조달', 7, '자금 부족 신호 — BW 발행'),
    ('유상증자결정',        '자금조달', 6, '자금 필요 — 희석 우려'),
    ('사채권발행',          '자금조달', 5, '채권 발행'),
    ('기업어음',            '자금조달', 4, '단기 유동성 필요'),

    # 자사주 관련
    ('자기주식취득',        '자사주',   5, '자사주 매입 — 주가 관리 or 소각'),
    ('자기주식처분',        '자사주',   6, '자사주 처분 — 오버행'),
    ('자기주식소각',        '자사주',   3, '주주 환원'),

    # 구조조정 신호
    ('워크아웃',            '구조조정', 9, '채권단 관리'),
    ('기업회생',            '구조조정', 9, '법원 관리'),
    ('채무조정',            '구조조정', 8, '채무 재구조화'),
    ('대표이사변경',        '경영변화', 4, '경영진 교체'),

    # IB 주목 공시
    ('주요사항보고',        'IB이벤트', 3, '중요 사항 발생'),
    ('외국인투자',          'IB이벤트', 5, '외국인 투자 유치'),
]

# 카테고리별 색상 (텔레그램용)
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
        return {c.get('name', '') for c in companies}
    except Exception:
        return set()


def fetch_dart_period(days_back=7):
    """DART API: 최근 N일 공시 전수 수집"""
    end_dt  = NOW
    start_dt = end_dt - timedelta(days=days_back)
    bgn_de  = start_dt.strftime('%Y%m%d')
    end_de  = end_dt.strftime('%Y%m%d')

    url = 'https://opendart.fss.or.kr/api/list.json'
    all_items = []
    page = 1

    print(f'  DART 조회: {bgn_de} ~ {end_de}')
    while True:
        params = {
            'crtfc_key': DART_API_KEY,
            'bgn_de':    bgn_de,
            'end_de':    end_de,
            'page_no':   page,
            'page_count': 100,
            'sort': 'date',
            'sort_mth': 'desc',
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
            data = resp.json()
        except Exception as e:
            print(f'  DART API 오류 (page {page}): {e}')
            break

        if data.get('status') != '000':
            break

        items = data.get('list', [])
        if not items:
            break

        all_items.extend(items)
        total_page = data.get('total_page', 1)
        print(f'  page {page}/{total_page} — {len(all_items)}건')
        if page >= total_page:
            break
        page += 1

    return all_items


def score_disclosure(report_nm):
    """공시 제목에서 신호 매칭 → (점수합계, [(카테고리, 점수, 설명)])"""
    matches = []
    for keyword, category, score, desc in SIGNAL_MATRIX:
        if keyword in report_nm:
            matches.append((category, score, desc, keyword))

    if not matches:
        return 0, []

    total_score = sum(m[1] for m in matches)
    return total_score, matches


def aggregate_signals(items):
    """기업별 신호 집계 → 점수 높은 순 정렬"""
    company_signals = defaultdict(lambda: {
        'corp_name': '',
        'corp_code': '',
        'stock_code': '',
        'total_score': 0,
        'disclosures': [],
    })

    for item in items:
        corp_name  = item.get('corp_name', '')
        corp_code  = item.get('corp_code', '')
        stock_code = item.get('stock_code', '')
        report_nm  = item.get('report_nm', '')
        rcept_no   = item.get('rcept_no', '')
        rcept_dt   = item.get('rcept_dt', '')

        score, matches = score_disclosure(report_nm)
        if score == 0:
            continue

        key = corp_code or corp_name
        cs = company_signals[key]
        cs['corp_name']  = corp_name
        cs['corp_code']  = corp_code
        cs['stock_code'] = stock_code
        cs['total_score'] += score
        cs['disclosures'].append({
            'report_nm': report_nm,
            'rcept_no':  rcept_no,
            'rcept_dt':  rcept_dt,
            'score':     score,
            'signals':   [{'category': m[0], 'score': m[1], 'desc': m[2], 'keyword': m[3]} for m in matches],
        })

    # 점수 높은 순 정렬
    ranked = sorted(company_signals.values(), key=lambda x: x['total_score'], reverse=True)
    return ranked


def format_report(ranked, watchlist_names, top_n=15, days_back=7):
    """텔레그램 리포트 포맷"""
    date_str = NOW.strftime('%Y-%m-%d')
    start_str = (NOW - timedelta(days=days_back)).strftime('%m/%d')

    lines = [
        f'🔍 <b>딜 소싱 신호 탐지 리포트</b> ({start_str}~{date_str[5:]})',
        f'총 {len(ranked)}개사에서 신호 감지\n',
    ]

    for i, cs in enumerate(ranked[:top_n], 1):
        name  = cs['corp_name']
        score = cs['total_score']
        discs = cs['disclosures']
        on_watchlist = '⭐ ' if name in watchlist_names else ''

        # 카테고리 요약
        categories = set()
        for d in discs:
            for sig in d['signals']:
                categories.add(sig['category'])
        cat_str = ' | '.join(f"{CATEGORY_EMOJI.get(c,'📌')}{c}" for c in sorted(categories))

        lines.append(f'<b>{i}. {on_watchlist}{name}</b> <code>[{score}점]</code>')
        lines.append(f'   {cat_str}')

        # 상위 공시 최대 2개
        for d in discs[:2]:
            link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d['rcept_no']}"
            top_desc = d['signals'][0]['desc'] if d['signals'] else ''
            lines.append(f'   └ <a href="{link}">{d["report_nm"]}</a> ({top_desc})')

        lines.append('')

    lines.append(f'⭐ = 기존 워치리스트 | 점수: M&A 10점 ~ 약신호 3점')
    lines.append(f'🔗 <a href="https://dart.fss.or.kr">DART 바로가기</a>')

    return '\n'.join(lines)


def save_signals(ranked, days_back):
    """JSON 아카이브 저장"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = []
    if SIGNAL_FILE.exists():
        try:
            existing = json.loads(SIGNAL_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass

    entry = {
        'run_date': NOW.strftime('%Y-%m-%d %H:%M KST'),
        'period_days': days_back,
        'top_signals': [
            {
                'corp_name':   cs['corp_name'],
                'corp_code':   cs['corp_code'],
                'total_score': cs['total_score'],
                'categories':  list({s['category'] for d in cs['disclosures'] for s in d['signals']}),
                'top_report':  cs['disclosures'][0]['report_nm'] if cs['disclosures'] else '',
            }
            for cs in ranked[:20]
        ],
    }
    existing.append(entry)
    existing = existing[-52:]  # 최근 52주분 보관
    SIGNAL_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'  저장: {SIGNAL_FILE}')


def send_telegram(text):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    resp = requests.post(url, data={
        'chat_id': CHAT_ID,
        'text':    text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'false',
    }, timeout=30)
    return resp.json().get('ok', False)


def main(days_back=7, top_n=15, send=True):
    print(f'[{NOW.strftime("%Y-%m-%d %H:%M")}] 딜 소싱 신호 탐지 시작 (최근 {days_back}일)')

    watchlist = load_watchlist()
    print(f'  워치리스트: {len(watchlist)}개사')

    items = fetch_dart_period(days_back)
    print(f'  전체 공시: {len(items)}건')

    ranked = aggregate_signals(items)
    print(f'  신호 감지: {len(ranked)}개사')

    if not ranked:
        print('  신호 없음')
        return

    # 상위 5개 콘솔 출력
    print('\n  ── TOP 5 ──')
    for cs in ranked[:5]:
        cats = {s['category'] for d in cs['disclosures'] for s in d['signals']}
        star = '★' if cs['corp_name'] in watchlist else ' '
        print(f'  {star} [{cs["total_score"]:2d}점] {cs["corp_name"]:15s} | {", ".join(sorted(cats))}')

    save_signals(ranked, days_back)

    if send:
        msg = format_report(ranked, watchlist, top_n=top_n, days_back=days_back)
        ok = send_telegram(msg)
        print(f'  텔레그램: {"성공" if ok else "실패"}')
        if not ok:
            print(f'\n--- 리포트 미리보기 ---\n{msg[:1000]}')
    else:
        msg = format_report(ranked, watchlist, top_n=top_n, days_back=days_back)
        print(f'\n--- 리포트 미리보기 ---\n{msg}')


if __name__ == '__main__':
    import sys
    days  = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    top   = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    _send = '--no-send' not in sys.argv
    main(days_back=days, top_n=top, send=_send)
