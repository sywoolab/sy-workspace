"""
DART 주요 공시 알림 스크립트
- EB, CB, 자사주 처분/취득, 유상증자 등 IB 관련 공시 모니터링
- SY Workspace 텔레그램 봇으로 알림 전송
"""

import os
import json
import requests
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

# 중복 체크용 파일
SENT_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'dart_sent.json')

# IB 관련 공시 키워드
IB_KEYWORDS = [
    '자기주식', '자사주', '교환사채', '전환사채',
    '신주인수권부사채', '유상증자', '무상증자',
    '주식교환', '주식이전', '합병', '분할',
    '영업양수', '영업양도', '공개매수',
    '채권발행', '사채발행',
]


def load_sent():
    """이미 전송한 공시 rcept_no 목록"""
    try:
        with open(SENT_FILE, 'r') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_sent(sent_set):
    os.makedirs(os.path.dirname(SENT_FILE), exist_ok=True)
    # 최근 500건만 유지
    recent = sorted(sent_set)[-500:]
    with open(SENT_FILE, 'w') as f:
        json.dump(recent, f)


def fetch_dart_disclosures():
    """DART 오늘 공시 조회"""
    url = 'https://opendart.fss.or.kr/api/list.json'
    params = {
        'crtfc_key': DART_API_KEY,
        'bgn_de': TODAY,
        'end_de': TODAY,
        'page_count': 100,
        'sort': 'date',
        'sort_mth': 'desc',
    }

    all_items = []
    page = 1
    while True:
        params['page_no'] = page
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()

        if data.get('status') != '000':
            break

        items = data.get('list', [])
        if not items:
            break

        all_items.extend(items)

        total_page = data.get('total_page', 1)
        if page >= total_page:
            break
        page += 1

    return all_items


def filter_ib_disclosures(items):
    """IB 관련 공시만 필터링"""
    filtered = []
    for item in items:
        report_nm = item.get('report_nm', '')
        if any(kw in report_nm for kw in IB_KEYWORDS):
            filtered.append(item)
    return filtered


def format_message(items):
    """텔레그램 메시지 포맷"""
    if not items:
        return None

    date_str = datetime.now(KST).strftime('%Y-%m-%d (%a)')
    lines = [f"📢 DART IB 공시 알림 ({date_str})", f"신규 {len(items)}건\n"]

    for item in items:
        corp = item.get('corp_name', '?')
        report = item.get('report_nm', '?')
        rcept_no = item.get('rcept_no', '')
        rcept_dt = item.get('rcept_dt', '')
        link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

        lines.append(f"• [{corp}] {report}")
        lines.append(f"  {link}")
        lines.append("")

    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        'chat_id': CHAT_ID,
        'text': text,
        'disable_web_page_preview': 'true',
    }, timeout=30)
    return resp.json().get('ok', False)


def main():
    print(f"[{datetime.now(KST)}] DART 공시 체크 시작 (날짜: {TODAY})")

    # 공시 조회
    items = fetch_dart_disclosures()
    print(f"  전체 공시: {len(items)}건")

    # IB 관련 필터
    ib_items = filter_ib_disclosures(items)
    print(f"  IB 관련: {len(ib_items)}건")

    if not ib_items:
        print("  신규 IB 공시 없음")
        return

    # 중복 체크
    sent = load_sent()
    new_items = [i for i in ib_items if i.get('rcept_no') not in sent]
    print(f"  신규(미전송): {len(new_items)}건")

    if not new_items:
        print("  모두 이미 전송됨")
        return

    # 메시지 전송
    msg = format_message(new_items)
    if msg:
        ok = send_telegram(msg)
        print(f"  텔레그램 전송: {'성공' if ok else '실패'}")

    # 전송 기록 저장
    for item in new_items:
        sent.add(item.get('rcept_no'))
    save_sent(sent)


if __name__ == '__main__':
    main()
