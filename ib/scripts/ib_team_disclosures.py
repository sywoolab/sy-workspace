"""
IB 팀 채널 — DART 공시 즉시 알림
- watchlist_team.json 기준
- 중요 공시: 🔴 prefix + sound on
- 일반 공시: 📋 polling 1회 = 요약 1메시지, silent push
- 15분 폴링 (24시간)
"""

import os
import json
import hashlib
import html
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
PAGES_URL = 'https://sywoolab.github.io/sy-workspace/index.html?live=1#dart'
NORMAL_DIGEST_LIMIT = 12

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


def escape_html(text):
    return html.escape(str(text or ''), quote=False)


def disclosure_link(rcept_no):
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def format_single_alert(item):
    prefix = '🔴'
    return (
        f"{prefix} [공시·중요] {escape_html(item['name'])}{item['label']}\n"
        f"{escape_html(item['report'])}\n"
        f'🔗 <a href="{item["link"]}">DART 원문</a>'
    )


def format_normal_digest(items, now):
    shown = items[:NORMAL_DIGEST_LIMIT]
    hidden_count = max(0, len(items) - len(shown))
    lines = [
        f"📋 [공시 요약] IB Team",
        f"{now.strftime('%Y-%m-%d %H:%M')} 기준 · 일반 {len(items)}건",
        "",
    ]

    for item in shown:
        lines.append(
            f"• {escape_html(item['name'])}{item['label']} — "
            f'<a href="{item["link"]}">{escape_html(item["report"])}</a>'
        )

    if hidden_count:
        lines.append(f"• 외 {hidden_count}건")

    lines.extend(["", f'🔗 <a href="{PAGES_URL}">전체 리포트 보기</a>'])
    return '\n'.join(lines)


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
    normal_items = []

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
            urgent = is_urgent(report)

            item = {
                'rcept_no': rcept_no,
                'name': name,
                'label': format_market_label(comp),
                'report': report,
                'link': disclosure_link(rcept_no),
            }

            if urgent and send_telegram(format_single_alert(item), silent=False):
                sent.add(rcept_no)
                new_count += 1
                urgent_count += 1
                print(f"  → 🔴 {name}: {report[:40]}")
            elif not urgent:
                normal_items.append(item)

    if normal_items:
        msg = format_normal_digest(normal_items, now)
        if send_telegram(msg, silent=True):
            for item in normal_items:
                sent.add(item['rcept_no'])
            new_count += len(normal_items)
            print(f"  → 📋 일반 공시 요약 {len(normal_items)}건")

    print(f"  신규 {new_count}건 (긴급 {urgent_count})")
    if new_count:
        save_sent(sent)


if __name__ == '__main__':
    main()
