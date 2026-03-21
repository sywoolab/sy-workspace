"""
철인3종 대회 모니터링
- 대한철인3종협회 사이트에서 대회 목록 크롤링
- 모든 미래 대회를 대회일순 정렬
- 접수 상태/기간을 한눈에 표시
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
            if status:
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
        self.done = False
        self.reg_period = ''
        self.current_text = ''

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == 'th':
            self.in_th = True
            self.current_text = ''
        elif tag == 'td' and self.found_reg_th:
            self.in_td = True
            self.current_text = ''

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag == 'th' and self.in_th:
            self.in_th = False
            text = self.current_text.strip()
            # "접수기간"에만 매칭, "심판 접수기간" 등은 제외
            if text == '접수기간':
                self.found_reg_th = True
        elif tag == 'td' and self.in_td and self.found_reg_th:
            self.in_td = False
            self.reg_period = self.current_text.strip()
            self.found_reg_th = False
            self.done = True

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
    for page in range(1, 4):
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

    # 모든 대회의 상세 페이지에서 접수기간 가져오기
    for event in all_events:
        if event.get('url'):
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
    match = re.match(r'(\d{4})\.(\d{2})\.(\d{2})', date_str)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=KST)
        except ValueError:
            pass
    return None


def parse_reg_dates(reg_period):
    """접수기간 문자열에서 시작일/마감일 파싱"""
    if not reg_period:
        return None, None

    # "2026-03-17 14:00 ~ 2026-03-26 18:00" 형태
    dates = re.findall(r'(\d{4}-\d{2}-\d{2})', reg_period)
    start_dt = None
    end_dt = None
    if len(dates) >= 1:
        try:
            start_dt = datetime.strptime(dates[0], '%Y-%m-%d').replace(tzinfo=KST)
        except ValueError:
            pass
    if len(dates) >= 2:
        try:
            end_dt = datetime.strptime(dates[1], '%Y-%m-%d').replace(tzinfo=KST)
        except ValueError:
            pass
    return start_dt, end_dt


def compact_date(date_str):
    """'2026.05.09 ~ 10' → '05.09~10', '2026.05.09' → '05.09'"""
    if not date_str:
        return '?'
    # 연도 제거
    text = re.sub(r'^\d{4}\.', '', date_str.strip())
    # '~ 10' → '~10', 중간 '~ 2026.05.17' → '~05.17'
    text = re.sub(r'\s*~\s*\d{4}\.', '~', text)
    text = re.sub(r'\s*~\s*', '~', text)
    return text


def reg_status_label(status, reg_period):
    """접수 상태를 이모지+라벨로 변환, 접수일정 요약 포함"""
    reg_start, reg_end = parse_reg_dates(reg_period)
    today_dt = NOW.replace(hour=0, minute=0, second=0, microsecond=0)

    # 사이트 status 기준 분류
    if status == '접수중':
        if reg_end:
            diff = (reg_end - today_dt).days
            if diff <= 0:
                return '🔴', f'오늘 마감!'
            elif diff <= 3:
                return '🟢', f'접수중 (마감 D-{diff} ⚠️)'
            else:
                end_short = reg_end.strftime('%m.%d')
                return '🟢', f'접수중 (~{end_short}, D-{diff})'
        return '🟢', '접수중'

    elif status == '접수예정':
        if reg_start:
            diff = (reg_start - today_dt).days
            if diff <= 0:
                return '🟡', '곧 오픈'
            else:
                start_short = reg_start.strftime('%m.%d')
                return '🟡', f'{start_short} 오픈 (D-{diff})'
        return '🟡', '접수예정 (일정 미정)'

    elif status == '접수마감':
        return '🔴', '접수마감'

    elif status == '대회종료':
        return '⚫', '종료'

    else:
        return '⚪', status if status else '미정'


def format_triathlon_message(events):
    """텔레그램 메시지 포맷 - 대회일순, 가독성 중심"""
    if not events:
        return None

    # 미래 대회만 (오늘 이후)
    today_dt = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    future = []
    for e in events:
        evt_dt = parse_event_date(e.get('date', ''))
        if evt_dt and evt_dt >= today_dt:
            future.append(e)
        elif not evt_dt:
            future.append(e)

    if not future:
        return None

    # 대회일 기준 정렬
    def sort_key(e):
        d = parse_event_date(e.get('date', ''))
        return d if d else datetime(2099, 12, 31, tzinfo=KST)

    future.sort(key=sort_key)

    lines = [f'🏊🚴🏃 <b>철인3종 대회 일정</b> ({TODAY})']
    lines.append('🟢접수중 🟡접수예정 🔴마감')
    lines.append('')

    current_month = None
    count_open = 0
    count_upcoming = 0
    count_closed = 0

    for e in future:
        name = e.get('name', '?')
        date_str = e.get('date', '?')
        status = e.get('status', '')
        reg_period = e.get('reg_period', '')
        location = e.get('location', '')
        url = e.get('url', '')

        # 통계
        if status == '접수중':
            count_open += 1
        elif status == '접수예정':
            count_upcoming += 1
        elif status == '접수마감':
            count_closed += 1

        # 월 구분선
        evt_dt = parse_event_date(date_str)
        if evt_dt:
            month = evt_dt.month
            if month != current_month:
                current_month = month
                lines.append(f'━━━ 📅 {month}월 ━━━')

        # 대회일 D-day
        dday = ''
        if evt_dt:
            diff = (evt_dt - today_dt).days
            dday = f'D-{diff}'

        # 접수 상태
        emoji, reg_label = reg_status_label(status, reg_period)

        # 날짜 간결화
        short_date = compact_date(date_str)

        # 대회명 (링크)
        name_escaped = name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        if url:
            name_line = f'<a href="{url}">{name_escaped}</a>'
        else:
            name_line = name_escaped

        # 한 줄: 이모지 날짜 | 대회명
        lines.append(f'{emoji} <b>{short_date}</b> ({dday}) {name_line}')

        # 두번째 줄: 장소 + 접수 상태
        detail_parts = []
        if location:
            # 장소 축약 (시/군/구까지만, 없으면 20자)
            loc_short = re.match(r'[가-힣]+(?:특별자치도)?\s+[가-힣]+[시군구]', location)
            detail_parts.append(loc_short.group() if loc_short else location[:20])
        detail_parts.append(reg_label)
        lines.append(f'   {" | ".join(detail_parts)}')
        lines.append('')

    # 요약
    lines.append(f'접수중 {count_open} / 예정 {count_upcoming} / 마감 {count_closed}')
    lines.append(f'🔗 https://www.triathlon.or.kr/events/tour/')

    return '\n'.join(lines)


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

    # 미래 대회가 있으면 전송 (접수 상태 무관)
    today_dt = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    future = [e for e in events if parse_event_date(e.get('date', '')) and parse_event_date(e.get('date', '')) >= today_dt]

    if not future:
        print("  미래 대회 없음")
        return

    # 메시지 생성 & 전송
    msg = format_triathlon_message(events)
    if msg:
        ok = send_telegram(msg)
        print(f"  텔레그램 전송: {'성공' if ok else '실패'}")
        print(f"\n--- 메시지 미리보기 ---\n{msg}")
    else:
        print("  전송할 내용 없음")


if __name__ == '__main__':
    main()
