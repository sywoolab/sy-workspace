"""
철인3종 대회 모니터링
- 대한철인3종협회 사이트에서 대회 목록 크롤링
- 접수중/접수예정 대회 알림
- 접수일 다가오면 매일 리마인드
"""

import os
import json
import re
import requests
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.strftime('%Y-%m-%d')

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
EVENTS_FILE = os.path.join(BASE_DIR, 'data', 'triathlon_events.json')
BASE_URL = 'https://www.triathlon.or.kr/events/tour/'


# ============================================================
# HTML 파서
# ============================================================
class TriathlonParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.events = []
        self.current_event = {}
        self.in_tr = False
        self.in_td = False
        self.td_count = 0
        self.in_strong = False
        self.in_em = False
        self.in_p = False
        self.in_span = False
        self.in_tbody = False
        self.current_text = ''
        self.current_href = ''

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'tbody':
            self.in_tbody = True
        elif tag == 'tr' and self.in_tbody:
            self.in_tr = True
            self.td_count = 0
            self.current_event = {}
        elif tag == 'td' and self.in_tr:
            self.in_td = True
            self.td_count += 1
        elif tag == 'strong' and self.in_td and self.td_count == 1:
            self.in_strong = True
            self.current_text = ''
        elif tag == 'em' and self.in_td and self.td_count == 1:
            cls = attrs_dict.get('class', '')
            if 'event_status' in cls:
                self.in_em = True
                self.current_text = ''
        elif tag == 'span' and self.in_td and self.td_count == 1:
            if attrs_dict.get('class', '') == 'subcomment':
                self.in_span = True
                self.current_text = ''
        elif tag == 'a' and self.in_td and self.td_count == 1:
            href = attrs_dict.get('href', '')
            if 'overview' in href:
                self.current_href = href
        elif tag == 'p' and self.in_td and self.td_count == 2:
            self.in_p = True
            self.current_text = ''

    def handle_endtag(self, tag):
        if tag == 'tbody':
            self.in_tbody = False
        elif tag == 'tr' and self.in_tr:
            if self.current_event.get('name'):
                self.events.append(self.current_event)
            self.in_tr = False
        elif tag == 'td':
            self.in_td = False
        elif tag == 'strong' and self.in_strong:
            self.current_event['name'] = self.current_text.strip()
            self.in_strong = False
        elif tag == 'em' and self.in_em:
            status = self.current_text.strip()
            if status and '접수' in status:
                self.current_event['status'] = status
            self.in_em = False
        elif tag == 'span' and self.in_span:
            text = self.current_text.strip()
            if text.startswith('장소:'):
                self.current_event['location'] = text.replace('장소:', '').strip()
            elif text.startswith('코스:'):
                self.current_event['course'] = text.replace('코스:', '').strip()
            self.in_span = False
        elif tag == 'p' and self.in_p:
            text = self.current_text.strip()
            if text and re.match(r'\d{4}', text):
                self.current_event['date'] = text
            self.in_p = False
        elif tag == 'a' and self.current_href and self.td_count == 1:
            self.current_event['url'] = 'https://www.triathlon.or.kr' + self.current_href if self.current_href.startswith('/') else self.current_href
            self.current_href = ''

    def handle_data(self, data):
        if self.in_strong or self.in_em or self.in_span or self.in_p:
            self.current_text += data


class DetailParser(HTMLParser):
    """대회 상세 페이지에서 접수기간 추출"""
    def __init__(self):
        super().__init__()
        self.in_th = False
        self.in_td = False
        self.found_reg_th = False
        self.reg_period = ''
        self.current_text = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'th':
            self.in_th = True
            self.current_text = ''
        elif tag == 'td' and self.found_reg_th:
            self.in_td = True
            self.current_text = ''

    def handle_endtag(self, tag):
        if tag == 'th' and self.in_th:
            self.in_th = False
            if '접수기간' in self.current_text.strip():
                self.found_reg_th = True
        elif tag == 'td' and self.in_td and self.found_reg_th:
            self.in_td = False
            self.reg_period = self.current_text.strip()
            self.found_reg_th = False  # stop after first match

    def handle_data(self, data):
        if self.in_th or self.in_td:
            self.current_text += data


def fetch_registration_period(url):
    """대회 상세 페이지에서 접수기간 추출"""
    try:
        resp = requests.get(url, timeout=15, verify=False)
        resp.encoding = 'utf-8'
        parser = DetailParser()
        parser.feed(resp.text)
        return parser.reg_period if parser.reg_period else None
    except Exception as e:
        print(f"  상세페이지 에러: {e}")
        return None


def fetch_events():
    """철인3종협회 사이트에서 대회 목록 크롤링"""
    all_events = []
    for page in range(1, 4):  # 최대 3페이지
        url = f'{BASE_URL}?vType=list&sYear={NOW.year}&page={page}'
        try:
            resp = requests.get(url, timeout=15, verify=False)
            resp.encoding = 'utf-8'
            parser = TriathlonParser()
            parser.feed(resp.text)
            if not parser.events:
                break
            all_events.extend(parser.events)
        except Exception as e:
            print(f"  페이지 {page} 에러: {e}")
            break

    # 접수중/접수예정 대회만 상세 페이지에서 접수기간 가져오기
    for event in all_events:
        if event.get('status') in ('접수중', '접수예정') and event.get('url'):
            reg = fetch_registration_period(event['url'])
            if reg:
                event['reg_period'] = reg
                print(f"    접수기간: {event.get('name', '?')} → {reg}")

    return all_events


def load_known_events():
    """저장된 대회 목록 로드"""
    try:
        with open(EVENTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_events(events):
    """대회 목록 저장"""
    os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
    with open(EVENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def parse_event_date(date_str):
    """대회 날짜 문자열 파싱 → datetime"""
    if not date_str:
        return None
    # '2026.10.04' or '2026.05.16 ~ 17'
    match = re.match(r'(\d{4})\.(\d{2})\.(\d{2})', date_str)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=KST)
        except ValueError:
            pass
    return None


def format_triathlon_message(events):
    """텔레그램 메시지 포맷"""
    if not events:
        return None

    lines = [f"🏊🚴🏃 철인3종 대회 알림 ({TODAY})\n"]

    # 접수중
    open_events = [e for e in events if e.get('status') == '접수중']
    upcoming_events = [e for e in events if e.get('status') == '접수예정']

    # 대회일 기준 정렬
    def sort_key(e):
        d = parse_event_date(e.get('date', ''))
        return d if d else datetime(2099, 12, 31, tzinfo=KST)

    def format_event_block(e):
        """개별 대회 메시지 블록 생성"""
        name = e.get('name', '?')
        date = e.get('date', '?')
        location = e.get('location', '')
        course = e.get('course', '')
        reg_period = e.get('reg_period', '')

        # 대회일까지 D-day
        event_dt = parse_event_date(date)
        dday = ''
        if event_dt:
            diff = (event_dt - NOW).days
            dday = f' (D-{diff})' if diff >= 0 else ''

        url = e.get('url', '')
        block = []
        if url:
            name_escaped = name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            block.append(f'  • <a href="{url}">{name_escaped}</a>')
        else:
            block.append(f'  • {name}')

        detail = f'    {date}{dday} | {location} {course}'
        block.append(detail)

        if reg_period:
            # 접수 마감까지 D-day 계산
            reg_match = re.search(r'~\s*(\d{4}-\d{2}-\d{2})', reg_period)
            reg_dday = ''
            if reg_match:
                try:
                    end_dt = datetime.strptime(reg_match.group(1), '%Y-%m-%d').replace(tzinfo=KST)
                    diff = (end_dt - NOW).days
                    if diff >= 0:
                        reg_dday = f' (마감 D-{diff})'
                except ValueError:
                    pass
            block.append(f'    📝 접수: {reg_period}{reg_dday}')

        return block

    if open_events:
        open_events.sort(key=sort_key)
        lines.append("🟢 접수중")
        for e in open_events:
            lines.extend(format_event_block(e))
        lines.append("")

    if upcoming_events:
        upcoming_events.sort(key=sort_key)
        lines.append("🟡 접수예정")
        for e in upcoming_events:
            lines.extend(format_event_block(e))
        lines.append("")

    # 접수중도 예정도 없으면
    if not open_events and not upcoming_events:
        return None

    total = len(open_events) + len(upcoming_events)
    lines.append(f"접수중 {len(open_events)}건 / 접수예정 {len(upcoming_events)}건")
    lines.append(f"🔗 https://www.triathlon.or.kr/events/tour/")

    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        'chat_id': CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true',
    }, timeout=30)
    return resp.json().get('ok', False)


def main():
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    print(f"[{NOW}] 철인3종 대회 모니터링")

    # 크롤링
    events = fetch_events()
    print(f"  수집된 대회: {len(events)}건")

    for e in events:
        print(f"    {e.get('status', '?'):6s} | {e.get('name', '?'):30s} | {e.get('date', '?')}")

    # 저장
    save_events(events)

    # 접수중/예정만 필터
    active = [e for e in events if e.get('status') in ('접수중', '접수예정')]

    if not active:
        print("  접수중/예정 대회 없음")
        return

    # 메시지 생성 & 전송
    msg = format_triathlon_message(events)
    if msg:
        ok = send_telegram(msg)
        print(f"  텔레그램 전송: {'성공' if ok else '실패'}")
    else:
        print("  전송할 내용 없음")


if __name__ == '__main__':
    main()
