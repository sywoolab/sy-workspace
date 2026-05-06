"""
IB 팀 채널 — DART 공시 즉시 알림
- watchlist_team.json 기준
- 신규 공시 발견 즉시 1건 = 1메시지 발송
- 중요 공시: 🔴 prefix + sound on
- 일반 공시: 📋 prefix + silent push (disable_notification)
- 15분 폴링 (24시간)
"""

import os
import json
import hashlib
import requests
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
TODAY = datetime.now(KST).strftime('%Y%m%d')

DART_API_KEY = os.environ.get('DART_API_KEY', '')
BOT_TOKEN = os.environ.get('IB_TEAM_BOT_TOKEN', '')
CHAT_ID = os.environ.get('IB_TEAM_CHAT_ID', '')

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
WATCHLIST_FILE = os.path.join(BASE_DIR, 'watchlist_team.json')
SENT_FILE = os.path.join(BASE_DIR, 'data', 'team_disclosures_sent.json')

# 중요 공시 키워드 (포함 시 🔴 + sound on)
URGENT_KEYWORDS = [
    '자기주식', '자기주식취득', '자기주식처분',
    '유상증자', '무상증자', '신주발행', '주식발행',
    '합병', '분할합병', '인수합병', '주식교환',
    '주요사항보고서', '정정',
    '영업실적', '잠정실적', '연결재무제표',
    '전환사채', '교환사채', '신주인수권부사채',
    '경영권', '최대주주변경', '주식양수도',
    '소송', '제재', '거래정지',
]


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
    recent = sorted(sent_set)[-3000:]
    with open(SENT_FILE, 'w') as f:
        json.dump(recent, f)


def fetch_dart_by_corp(corp_code):
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


def is_urgent(report_nm):
    return any(kw in report_nm for kw in URGENT_KEYWORDS)


def format_market_label(comp):
    if not comp.get('listed'):
        return ''
    market = comp.get('market', '')
    code = comp.get('stock_code', '')
    if market and code:
        return f' ({market} {code})'
    return ''


def send_telegram(text, silent=True):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        'chat_id': CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true',
        'disable_notification': 'true' if silent else 'false',
    }, timeout=30)
    ok = resp.json().get('ok', False)
    if not ok:
        print(f"  전송 실패: {resp.text[:200]}")
    return ok


def main():
    now = datetime.now(KST)
    print(f"[{now}] IB Team 공시 폴링")

    if not BOT_TOKEN or not CHAT_ID:
        print("  IB_TEAM_BOT_TOKEN/CHAT_ID 누락")
        return

    companies = load_watchlist()
    sent = load_sent()
    new_count = 0
    urgent_count = 0

    for comp in companies:
        name = comp['name']
        dart_code = comp.get('dart_code')
        if not dart_code:
            continue

        disclosures = fetch_dart_by_corp(dart_code)
        for d in disclosures:
            rcept_no = d.get('rcept_no', '')
            if not rcept_no or rcept_no in sent:
                continue

            report = d.get('report_nm', '').strip()
            link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
            urgent = is_urgent(report)

            label = format_market_label(comp)
            prefix = '🔴' if urgent else '📋'
            tag = '공시·중요' if urgent else '공시'
            report_safe = report.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            msg = (
                f"{prefix} [{tag}] {name}{label}\n"
                f"{report_safe}\n"
                f'🔗 <a href="{link}">DART 원문</a>'
            )

            if send_telegram(msg, silent=not urgent):
                sent.add(rcept_no)
                new_count += 1
                if urgent:
                    urgent_count += 1
                print(f"  → {prefix} {name}: {report[:40]}")

    print(f"  신규 {new_count}건 (긴급 {urgent_count})")
    if new_count:
        save_sent(sent)


if __name__ == '__main__':
    main()
