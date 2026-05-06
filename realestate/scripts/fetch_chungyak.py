"""
부동산 청약/장기임대 일일 알림 봇 (SY Real Estate)

- 매일 KST 08시 GitHub Actions 실행
- 4단계 시간축 분류: 오늘 신청 가능 / 한주 안 / 한달 안 / 향후
- 사용자 자격 매트릭스 적용 + 위치 필터 (서울/분당 우선)
- 9개 필수 항목 (memory/feedback_chungyak_alert_format.md 준수):
  1. 오늘 신청 가능 여부 (별도 섹션)
  2. 신청 사이트 URL (클릭 가능)
  3. 정확한 일정 (시작/마감/시간/요일)
  4. 단지 위치
  5. 임대 형태
  6. 자격 조건 (정확 수치)
  7. 사용자 적격성 판정
  8. 모집공고 PDF/원문 URL
  9. 즉시 액션 항목

데이터 소스:
- realestate/data/chungyak/registry.json — 메인이 검증한 단지 (1차 정보)
- 청약홈/SH/LH 스크래핑 → 신규 단지 감지 (registry에 없으면 별도 알림)

작동 모드:
- send: 매일 알림 발송 (디폴트)
- detect: 신규 단지 감지만 (스냅샷 비교)
- dry-run: 메시지 작성만, 발송 X

크로스플랫폼: macOS / Windows / GitHub Actions 동일 동작 (.env 자동 로드, sys.executable, pathlib)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── .env 자동 로드 (sy-workspace 루트). Win/Mac/CI 동일. ──
try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / ".env").exists():
            load_dotenv(_p / ".env")
            break
except ImportError:
    pass

import requests

# ── 환경변수 ──
BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("REALESTATE_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "chungyak"
REGISTRY_PATH = BASE_DIR / "registry.json"
SNAPSHOT_PATH = BASE_DIR / "snapshot.json"
HISTORY_DIR = BASE_DIR / "history"

KST = timezone(timedelta(hours=9))
WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]

# ── 시간 처리 ──

def now_kst():
    return datetime.now(KST)


def parse_dt(s: str) -> datetime:
    """ISO datetime → KST aware. 'T'/'Z'/공백 모두 허용 (Python 3.9~3.12 호환)"""
    s = s.replace(" ", "T").replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


def fmt_datetime(dt: datetime) -> str:
    """5/13(수) 09:00 형식"""
    return f"{dt.month}/{dt.day}({WEEKDAYS_KO[dt.weekday()]}) {dt.strftime('%H:%M')}"


def fmt_date(dt: datetime) -> str:
    return f"{dt.month}/{dt.day}({WEEKDAYS_KO[dt.weekday()]})"


def classify_time_bucket(listing: dict, today: datetime) -> str:
    """
    시간 분류 (날짜 기준 — 시각 무관):
      today_open    — 오늘 신청 가능 (오늘 시작·진행중·마감 모두)
      week_imminent — 7일 내 시작 (오늘 진행 phase 없음)
      month_imminent — 30일 내 시작
      future        — 31일 이후
      ended         — 모든 phase 종료

    사용자 피드백(2026-05-06): "그날 신청가능한거 확실히 알림줘야해"
    → 5/6 10시 시작이라도 09시 시점에서 "오늘 신청 가능"으로 분류해야 함.
    → start.date() <= today.date() <= end.date()로 판정 (시각 X).
    """
    today_date = today.date()
    has_today_phase = False
    has_starts_within_7 = False
    has_starts_within_30 = False
    has_future = False
    all_ended = True

    for phase in listing.get("schedule", []):
        try:
            start = parse_dt(phase["start"])
            end = parse_dt(phase["end"])
        except Exception:
            continue
        # C1 패치: 시각까지 비교. 마감 시간 후에는 today_open 아님.
        if start.date() <= today_date <= end.date() and today <= end:
            has_today_phase = True
            all_ended = False
        elif today_date < start.date() or (today_date == start.date() and today < start):
            all_ended = False
            days = (start.date() - today_date).days
            if days <= 0:  # 오늘 시작이지만 시각 전인 경우는 today_open
                has_today_phase = True
            elif days <= 7:
                has_starts_within_7 = True
            elif days <= 30:
                has_starts_within_30 = True
            else:
                has_future = True
        elif today > end:
            # 이 phase는 끝남 — all_ended 유지/판정 다른 phase에 위임
            pass

    if has_today_phase:
        return "today_open"
    if all_ended:
        return "ended"
    if has_starts_within_7:
        return "week_imminent"
    if has_starts_within_30:
        return "month_imminent"
    if has_future:
        return "future"
    return "ended"


def get_phase_status(phase: dict, today: datetime) -> str:
    """단일 phase가 오늘 어떤 상태인지 — starts_today/active/ends_today/upcoming/ended"""
    try:
        start = parse_dt(phase["start"])
        end = parse_dt(phase["end"])
    except Exception:
        return "unknown"
    today_date = today.date()
    if today_date == start.date() and today < start:
        return "starts_today"
    if start <= today <= end:
        return "active"
    if today_date == end.date() and today > end:
        return "ends_today_passed"
    if today < start:
        return "upcoming"
    return "ended"


# ── 위치 필터 (서울/분당 우선) ──

PRIMARY_KEYWORDS = ["서울", "성남시 분당", "분당구"]
SECONDARY_KEYWORDS = ["과천", "고양", "광명", "성남시"]


def location_priority(listing: dict) -> str:
    """primary / secondary / outside"""
    loc = listing.get("location_summary", "") + " " + " ".join(listing.get("districts", []))
    for kw in PRIMARY_KEYWORDS:
        if kw in loc:
            return "primary"
    for kw in SECONDARY_KEYWORDS:
        if kw in loc:
            return "secondary"
    return "outside"


def is_jjupjjup(listing: dict) -> bool:
    """잔여세대/임의공급/취소후재공급 = 줍줍 단지 (사용자 핵심 관심)"""
    typ = listing.get("type", "")
    return any(kw in typ for kw in ["임의공급", "잔여세대", "취소후재공급", "줍줍"])


# ── HTML 포매팅 ──

def html_escape(s):
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_listing(listing: dict, today: datetime, bucket: str) -> str:
    """단일 단지 메시지 블록 (9개 필수 항목 모두 포함). 적격 단지용 풀 렌더링."""
    lines = []
    name = html_escape(listing["name"])
    typ = html_escape(listing.get("type", ""))
    loc = html_escape(listing.get("location_summary", ""))

    icon = {
        "today_open": "🔴",
        "week_imminent": "🟡",
        "month_imminent": "🔵",
        "future": "⚪",
    }.get(bucket, "⚫")
    if is_jjupjjup(listing):
        icon = "🎲"  # 줍줍 단지 강조

    lines.append(f"{icon} <b>{name}</b>")
    if typ:
        lines.append(f"   <i>{typ}</i>")

    # 1. 오늘 신청 가능 여부 + 일정
    schedule_lines = []
    for phase in listing.get("schedule", []):
        try:
            s = parse_dt(phase["start"])
            e = parse_dt(phase["end"])
        except Exception:
            continue
        status = get_phase_status(phase, today)
        status_tag = ""
        marker = "   "
        if status == "active":
            status_tag = " <b>[진행중]</b>"
            marker = "👉 "
        elif status == "starts_today":
            status_tag = " <b>[오늘 시작]</b>"
            marker = "🔔 "
        elif status == "ends_today_passed":
            status_tag = " <b>[오늘 마감됨]</b>"

        if s.date() == e.date():
            schedule_lines.append(f"{marker}{html_escape(phase['phase'])}: {fmt_datetime(s)} ~ {e.strftime('%H:%M')}{status_tag}")
        else:
            schedule_lines.append(f"{marker}{html_escape(phase['phase'])}: {fmt_datetime(s)} ~ {fmt_datetime(e)}{status_tag}")
    if schedule_lines:
        lines.append("📅 " + schedule_lines[0])
        for l in schedule_lines[1:]:
            lines.append("   " + l)

    # 4. 위치
    if loc:
        lines.append(f"📍 {loc}")

    # 5. 임대 형태 (이미 type에 표시됨)
    tenure = listing.get("tenure_years")
    if tenure:
        sale = "분양전환 가능" if listing.get("sale_convertible") else "분양전환 X"
        lines.append(f"🏘️ 임대 {tenure}년 · {sale}")

    # 공급 + 가격
    supply = listing.get("supply_count")
    price = listing.get("price_summary", "")
    if supply or price:
        s_parts = []
        if supply:
            s_parts.append(f"{supply}세대")
        if price:
            s_parts.append(html_escape(price))
        lines.append("💰 " + " · ".join(s_parts))

    # 6. 자격 조건
    q = listing.get("qualifications", {})
    if q:
        if q.get("note"):
            lines.append(f"✅ <b>{html_escape(q['note'])}</b>")
        else:
            household = q.get("household")
            if household:
                lines.append(f"✅ {html_escape(household)}")
            inc60u = q.get("income_pct_60_under")
            inc60o = q.get("income_pct_60_over")
            if inc60u or inc60o:
                if inc60u:
                    lines.append(f"   소득 60㎡↓: 외벌이 {inc60u.get('single')}%/맞벌이 {inc60u.get('dual')}%")
                if inc60o:
                    lines.append(f"   소득 60㎡↑: 외벌이 {inc60o.get('single')}%/맞벌이 {inc60o.get('dual')}%")
            asset = q.get("asset_total_won")
            if asset:
                lines.append(f"   총자산 한도: {asset/1e8:.2f}억")

    # 7. 사용자 적격성 (H1 패치: 비표준 키도 모두 표시)
    ua = listing.get("user_assessment", {})
    verdict = ua.get("verdict", "")
    if verdict:
        verdict_map = {
            "eligible": "✅ 사용자 적격",
            "boundary": "🟡 사용자 경계 (검증 필수)",
            "ineligible": "❌ 사용자 부적격",
            "out_of_preference": "⚪ 사용자 선호 외 (참고)",
        }
        lines.append(verdict_map.get(verdict, f"판정: {verdict}"))
    # verdict 외 모든 user_assessment 항목 출력 (라그란데처럼 일반/특공 분리 표시 보존)
    for k, v in ua.items():
        if k == "verdict" or not v:
            continue
        # 키도 함께 표시하면 가독성 ↑
        label = {
            "asset": "자산",
            "income": "소득",
            "action": "액션",
            "note": "메모",
        }.get(k, k)
        lines.append(f"   <i>· {label}: {html_escape(str(v))}</i>")

    # 2 + 8. URL
    apply_url = listing.get("apply_url", "")
    info_url = listing.get("info_url", "")
    if apply_url:
        lines.append(f'🌐 <a href="{html_escape(apply_url)}">신청 사이트</a>')
    if info_url:
        lines.append(f'📄 <a href="{html_escape(info_url)}">모집공고</a>')

    return "\n".join(lines)


# ── 메시지 작성 ──

def render_compact(listing: dict, today: datetime, reason: str = "") -> str:
    """부적격/선호 외 단지용 1~2줄 압축 렌더링 — 사용자 시간 절약"""
    name = html_escape(listing["name"])
    loc = html_escape(listing.get("location_summary", "")[:40])
    schedule = listing.get("schedule", [])
    if schedule:
        try:
            s = parse_dt(schedule[0]["start"])
            e = parse_dt(schedule[-1]["end"])
            date_str = f"{fmt_date(s)}~{fmt_date(e)}"
        except Exception:
            date_str = "?"
    else:
        date_str = "?"

    jjup_mark = "🎲 " if is_jjupjjup(listing) else ""
    line = f"• {jjup_mark}<b>{name}</b> · {loc} · {date_str}"
    if reason:
        line += f" · <i>{html_escape(reason)}</i>"
    return line


def build_message(registry: dict, today: datetime) -> list:
    """
    적격성 + 줍줍 우선 구조:
    1. 🎯 적격 단지 (시간 분류)
       - 줍줍은 ⭐별도 섹션
    2. ⚪ 사용자 선호 외 (1줄 압축)
    3. 🚫 부적격 (1줄 압축, 사유 명시)
    4. 즉시 액션
    """
    listings = registry.get("listings", [])

    # 분류 새 규칙 (사용자 피드백 2026-05-06 반영):
    # - 줍줍(잔여세대/임의공급)은 verdict/매수상한 무관 모두 노출 (사용자: "가격 비싸도 줍줍은 알려줘")
    # - 일반 단지만 verdict 기준으로 분리
    def time_bucket_for(li):
        return classify_time_bucket(li, today)

    all_jjup = []  # 줍줍 단지 — verdict 무관 (ineligible은 보통 소득 부적격이라 줍줍은 거의 해당 X)
    eligible_normal_buckets = {"today_open": [], "week_imminent": [], "month_imminent": [], "future": []}
    out_of_pref = []  # 일반 단지 中 사용자 선호 외
    ineligible = []  # 일반 단지 中 부적격

    for li in listings:
        b = time_bucket_for(li)
        if b == "ended":
            continue
        verdict = li.get("user_assessment", {}).get("verdict", "")

        if is_jjupjjup(li):
            # 줍줍은 verdict 무관 모두 노출 (단 ineligible 줍줍은 부적격 섹션으로 — 의미 없음)
            if verdict == "ineligible":
                ineligible.append(li)
            else:
                all_jjup.append((b, li))
            continue

        # 일반 단지
        if verdict == "out_of_preference":
            out_of_pref.append(li)
        elif verdict == "ineligible":
            ineligible.append(li)
        elif b in eligible_normal_buckets:
            eligible_normal_buckets[b].append(li)
        else:
            eligible_normal_buckets[b].append(li)

    # 줍줍은 시간 버킷별로 정렬
    all_jjup.sort(key=lambda t: ["today_open", "week_imminent", "month_imminent", "future"].index(t[0]))

    # 일반 적격 — 위치 + 점수
    def sort_key(li):
        loc_rank = {"primary": 0, "secondary": 1, "outside": 2}[location_priority(li)]
        return (loc_rank, -li.get("priority_score", 0))
    for k in eligible_normal_buckets:
        eligible_normal_buckets[k].sort(key=sort_key)

    parts = []
    today_str = fmt_date(today)
    parts.append(
        f"🏠 <b>부동산 청약/장기임대 일일 알림</b>\n"
        f"{today.strftime('%Y-%m-%d')} {today_str} · 서울/분당 우선"
    )

    # ── 1. 줍줍 (사용자 핵심 관심 — 가장 위) ──
    parts.append("\n━━━━━━━━━━━━━━━━")
    parts.append("🎲 <b>줍줍 (잔여세대/임의공급) — 사용자 핵심 타깃</b>")
    parts.append("━━━━━━━━━━━━━━━━")
    parts.append(
        "<i>※ 줍줍 자격은 단지별로 다릅니다. ✅ 줄에서 정확 자격 확인:\n"
        "   · 임의공급(잔여세대) = 19세 이상 누구나 (보통 1주택자 OK)\n"
        "   · 취소후재공급(불법행위) = 본 청약 자격 동일 (세대주 등 엄격)\n"
        "   · 무순위 = 정부 정책 따라 변동</i>"
    )
    if all_jjup:
        for b, li in all_jjup:
            parts.append("\n" + render_listing(li, today, b))
    else:
        parts.append("\n(현재 등록된 줍줍 단지 없음 — 신규 발견 시 별도 🎲 알림)")

    # ── 2. 일반 적격 단지 — 시간 분류 ──
    has_any_normal = any(eligible_normal_buckets.values())
    if has_any_normal:
        # 오늘
        if eligible_normal_buckets["today_open"]:
            parts.append("\n━━━━━━━━━━━━━━━━")
            parts.append("🔴 <b>오늘 신청 가능</b>")
            parts.append("━━━━━━━━━━━━━━━━")
            for li in eligible_normal_buckets["today_open"]:
                parts.append("\n" + render_listing(li, today, "today_open"))
        # 한주 안
        if eligible_normal_buckets["week_imminent"]:
            parts.append("\n━━━━━━━━━━━━━━━━")
            parts.append("🟡 <b>한주 안 (~D+7)</b>")
            parts.append("━━━━━━━━━━━━━━━━")
            for li in eligible_normal_buckets["week_imminent"]:
                parts.append("\n" + render_listing(li, today, "week_imminent"))
        # 한달 안
        if eligible_normal_buckets["month_imminent"]:
            parts.append("\n━━━━━━━━━━━━━━━━")
            parts.append("🔵 <b>한달 안 (~D+30)</b>")
            parts.append("━━━━━━━━━━━━━━━━")
            for li in eligible_normal_buckets["month_imminent"]:
                parts.append("\n" + render_listing(li, today, "month_imminent"))
        # 향후
        if eligible_normal_buckets["future"]:
            parts.append("\n━━━━━━━━━━━━━━━━")
            parts.append("⚪ <b>향후 주요 일정</b>")
            parts.append("━━━━━━━━━━━━━━━━")
            for li in eligible_normal_buckets["future"]:
                parts.append("\n" + render_listing(li, today, "future"))

    # ── 3. 사용자 선호 외 (1줄 압축) ──
    out_active = [li for li in out_of_pref if classify_time_bucket(li, today) != "ended"]
    if out_active:
        parts.append("\n━━━━━━━━━━━━━━━━")
        parts.append("⚪ <b>참고 — 사용자 선호 외 (자격 OK, 위치/예산 미충족)</b>")
        parts.append("━━━━━━━━━━━━━━━━")
        for li in out_active:
            ua = li.get("user_assessment", {})
            reason = ua.get("reason") or ua.get("note") or ua.get("residency") or ""
            parts.append(render_compact(li, today, reason[:60]))

    # ── 4. 부적격 (1줄 압축) ──
    inel_active = [li for li in ineligible if classify_time_bucket(li, today) != "ended"]
    if inel_active:
        parts.append("\n━━━━━━━━━━━━━━━━")
        parts.append("🚫 <b>참고 — 사용자 부적격 (소득·자산 한도)</b>")
        parts.append("━━━━━━━━━━━━━━━━")
        for li in inel_active:
            ua = li.get("user_assessment", {})
            reason = (ua.get("income") or ua.get("asset") or "")
            # "❌" 같은 prefix 제거
            reason = reason.replace("❌ ", "").split(".")[0][:60]
            parts.append(render_compact(li, today, reason))

    # ── 5. 즉시 액션 ──
    # 줍줍 단지 + 오늘 적격 단지의 action 키 모두 노출 (boundary/eligible)
    parts.append("\n━━━━━━━━━━━━━━━━")
    parts.append("📌 <b>즉시 액션 (사전 준비)</b>")
    parts.append("━━━━━━━━━━━━━━━━")
    actions = []
    candidates = eligible_normal_buckets["today_open"] + [li for _, li in all_jjup]
    for li in candidates:
        ua = li.get("user_assessment", {})
        verdict = ua.get("verdict", "")
        if verdict in ("eligible", "boundary"):
            action = ua.get("action") or ua.get("note") or ""
            if action:
                actions.append(f"• <b>{html_escape(li['name'])}</b>: {html_escape(action)}")
    if actions:
        parts.extend(actions)
    else:
        parts.append("• 즉시 액션 없음 (신규 줍줍 발견 시 자동 알림)")

    parts.append(
        "\n<i>※ 매일 KST 08시 자동 발송. registry 검증 단지만 표시. "
        "신규 줍줍/공고 감지 시 별도 🎲 알림 발송.</i>"
    )

    return split_message("\n".join(parts), limit=4000)


def build_evening_message(registry: dict, today: datetime):
    """
    저녁 19시 리마인더: 오늘 마감 + 내일 시작 단지만 요약.
    임박 단지 없으면 None 반환 (발송 스킵).
    """
    listings = registry.get("listings", [])
    today_date = today.date()
    tomorrow_date = today_date + timedelta(days=1)

    today_ending = []  # 오늘 자정 전 마감
    tomorrow_starting = []  # 내일 시작

    for li in listings:
        verdict = li.get("user_assessment", {}).get("verdict", "")
        # 부적격 일반 단지는 제외 (줍줍은 verdict 무관 포함)
        if verdict == "ineligible" and not is_jjupjjup(li):
            continue
        for phase in li.get("schedule", []):
            try:
                s = parse_dt(phase["start"])
                e = parse_dt(phase["end"])
            except Exception:
                continue
            if e.date() == today_date and today < e:
                today_ending.append((li, phase, e))
            if s.date() == tomorrow_date:
                tomorrow_starting.append((li, phase, s))

    if not today_ending and not tomorrow_starting:
        return None  # 발송 스킵

    parts = [f"🌆 <b>부동산 청약 저녁 리마인더</b>\n{today.strftime('%Y-%m-%d')} {fmt_date(today)} 19:00"]

    if today_ending:
        parts.append("\n━━━━━━━━━━━━━━━━")
        parts.append("⏰ <b>오늘 마감 — 자정 전 신청 필수</b>")
        parts.append("━━━━━━━━━━━━━━━━")
        for li, phase, e in today_ending:
            jjup = "🎲 " if is_jjupjjup(li) else ""
            parts.append(f"\n🔴 {jjup}<b>{html_escape(li['name'])}</b>")
            parts.append(f"   {html_escape(phase['phase'])} 마감: {fmt_datetime(e)}")
            url = li.get("apply_url", "")
            if url:
                parts.append(f'   🌐 <a href="{html_escape(url)}">신청 사이트</a>')
            ua = li.get("user_assessment", {})
            verdict = ua.get("verdict", "")
            if verdict == "eligible":
                parts.append("   ✅ 사용자 적격")
            elif verdict == "boundary":
                parts.append("   🟡 자격 검증 필요")

    if tomorrow_starting:
        parts.append("\n━━━━━━━━━━━━━━━━")
        parts.append("🔔 <b>내일 시작 — 시작 시각 정각 준비</b>")
        parts.append("━━━━━━━━━━━━━━━━")
        for li, phase, s in tomorrow_starting:
            jjup = "🎲 " if is_jjupjjup(li) else ""
            parts.append(f"\n{jjup}<b>{html_escape(li['name'])}</b>")
            parts.append(f"   {html_escape(phase['phase'])} 시작: {fmt_datetime(s)}")
            url = li.get("apply_url", "")
            if url:
                parts.append(f'   🌐 <a href="{html_escape(url)}">신청 사이트</a>')
            ua = li.get("user_assessment", {})
            verdict = ua.get("verdict", "")
            if verdict == "eligible":
                parts.append("   ✅ 사용자 적격")
            elif verdict == "boundary":
                parts.append("   🟡 자격 검증 필요")
            elif verdict == "out_of_preference":
                parts.append("   ⚪ 사용자 선호 외 (참고)")

    parts.append("\n<i>※ 저녁 19시 리마인더. 오늘 마감/내일 시작 임박 단지만 표시.</i>")

    return split_message("\n".join(parts), limit=4000)


def split_message(text: str, limit: int = 4000) -> list:
    """줄 기준 분할. C2 패치: 단일 라인이 limit 초과 시 강제 절단 + 빈 chunk 방지."""
    if len(text) <= limit:
        return [text]
    chunks = []
    cur = ""
    for line in text.split("\n"):
        # 단일 라인이 limit 초과 시 강제 절단
        while len(line) > limit:
            if cur.strip():
                chunks.append(cur.rstrip())
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            if cur.strip():
                chunks.append(cur.rstrip())
            cur = line + "\n"
        else:
            cur += line + "\n"
    if cur.strip():
        chunks.append(cur.rstrip())
    # 빈 chunk 제거
    return [c for c in chunks if c.strip()]


# ── 텔레그램 발송 ──

def send_telegram(text, parse_mode="HTML"):
    if not BOT_TOKEN or not CHAT_ID:
        print("[SKIP] BOT_TOKEN 또는 CHAT_ID 미설정")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }, timeout=30)
        if resp.status_code == 200:
            print(f"[OK] 발송 ({len(text)}자)")
            return True
        print(f"[ERR] HTTP {resp.status_code}: {resp.text[:300]}")
        # HTML 파싱 실패 시 plain text로 재시도
        resp2 = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        }, timeout=30)
        return resp2.status_code == 200
    except Exception as e:
        print(f"[WARN] 텔레그램 예외: {e}")
        return False


# ── 신규 공고 감지 (스냅샷 비교) ──

def load_registry():
    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"listings": []}


def save_snapshot(snapshot):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def load_snapshot():
    if SNAPSHOT_PATH.exists():
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ── 청약홈 신규 감지 (단순 ID 추출) ──

CHUNGYAK_LIST_URL = "https://www.applyhome.co.kr/ai/aia/selectAPTLttotPblancListView.do"
CHUNGYAK_REMNDR_URL = "https://www.applyhome.co.kr/ai/aia/selectAPTRemndrLttotPblancListView.do"
SH_LIST_URL = "https://www.i-sh.co.kr/main/lay2/program/S1T294C295/www/brd/m_241/list.do?multi_itm_seq=2"
LH_LIST_URL = "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancList.do?mi=1026"


def fetch_html(url, timeout=20):
    """HTTP 200 + 응답 길이 1000자+ 일 때만 정상 처리. 그 외 None 반환."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        if r.status_code == 200 and len(r.text) >= 1000:
            return r.text
        print(f"[WARN] fetch suspicious {url}: HTTP {r.status_code} len={len(r.text)}")
    except Exception as e:
        print(f"[WARN] fetch fail {url}: {e}")
    return None


def detect_new_chungyak():
    """
    청약홈 두 페이지를 분리 추출. 반환:
      {
        "remndr": set(잔여세대 단지명) or None  # 줍줍 우선도 高
        "normal": set(일반 분양 단지명) or None
      }
    None은 fetch/파싱 실패 → 스냅샷 갱신 스킵.
    """
    result = {"remndr": None, "normal": None}

    html_r = fetch_html(CHUNGYAK_REMNDR_URL)
    if html_r is not None:
        titles = set(m.group(1).strip() for m in re.finditer(r'data-honm="([^"]+)"', html_r))
        result["remndr"] = titles if titles else None
        if not titles:
            print("[WARN] chungyak remndr: 200 응답 but 0건. 스냅샷 갱신 스킵")

    html_n = fetch_html(CHUNGYAK_LIST_URL)
    if html_n is not None:
        titles = set(m.group(1).strip() for m in re.finditer(r'data-honm="([^"]+)"', html_n))
        result["normal"] = titles if titles else None
        if not titles:
            print("[WARN] chungyak normal: 200 응답 but 0건. 스냅샷 갱신 스킵")

    return result


def detect_new_sh():
    """SH 게시판 공고 제목 추출. None 반환 시 스냅샷 갱신 스킵."""
    html = fetch_html(SH_LIST_URL)
    if html is None:
        print("[WARN] SH: fetch 실패 → 스냅샷 갱신 스킵")
        return None
    titles = set()
    for m in re.finditer(r'class="board_subj[^"]*"[^>]*>\s*<a[^>]*>([^<]+)</a>', html):
        titles.add(m.group(1).strip())
    if not titles:
        for m in re.finditer(r'<a[^>]*goView[^>]*>([^<]+)</a>', html):
            titles.add(m.group(1).strip())
    if not titles:
        print("[WARN] SH: 0 titles parsed → 구조 변경 의심. 스냅샷 갱신 스킵")
        return None
    return titles


def report_new(new_items, source, is_jjup=False):
    """신규 단지 알림. is_jjup=True면 줍줍 강조 (사용자 핵심 타깃)."""
    if not new_items:
        return
    if is_jjup:
        header = f"🎲 <b>[줍줍 신규] {source}에 잔여세대 단지 등록됨</b>"
        footer = (
            "<i>※ 임의공급/잔여세대 = 청약통장·소득·자산 무관. "
            "사용자 매수상한 11.6억 內인지 모집공고 확인 후 신청. "
            "메인이 registry에 추가하면 다음 알림부터 상세 표시.</i>"
        )
    else:
        header = f"🆕 <b>[{source}] 신규 분양 공고</b>"
        footer = (
            "<i>※ 일반 분양 신규 등록. 자격(소득·자산·청약통장)·일정 PDF 확인 필요. "
            "사용자 적격 여부는 메인 검증 후 다음 알림 반영.</i>"
        )
    msg_lines = [header, ""]
    for n in sorted(new_items)[:20]:
        msg_lines.append(f"• {html_escape(n)}")
    msg_lines.append("")
    msg_lines.append(footer)
    send_telegram("\n".join(msg_lines))


# ── 메인 ──

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="send", choices=["send", "detect", "dry-run", "all", "evening", "evening-dry"])
    parser.add_argument("--no-detect", action="store_true", help="신규 감지 스킵")
    args = parser.parse_args()

    registry = load_registry()
    today = now_kst()

    print(f"[fetch_chungyak] {today.isoformat()} mode={args.mode}")
    print(f"  registry: {len(registry.get('listings', []))} listings")

    if args.mode in ("evening", "evening-dry"):
        chunks = build_evening_message(registry, today)
        if chunks is None:
            print("  evening: 임박 단지 없음 → 발송 스킵")
        else:
            print(f"  evening message: {len(chunks)} chunks, total {sum(len(c) for c in chunks)} chars")
            if args.mode == "evening-dry":
                for i, c in enumerate(chunks):
                    print(f"\n--- chunk {i+1}/{len(chunks)} ({len(c)} chars) ---")
                    print(c)
            else:
                for i, c in enumerate(chunks):
                    if i > 0:
                        time.sleep(1.0)
                    send_telegram(c)
        return  # evening 모드는 detect 스킵

    if args.mode in ("send", "all", "dry-run"):
        chunks = build_message(registry, today)
        print(f"  message: {len(chunks)} chunks, total {sum(len(c) for c in chunks)} chars")
        if args.mode == "dry-run":
            for i, c in enumerate(chunks):
                print(f"\n--- chunk {i+1}/{len(chunks)} ({len(c)} chars) ---")
                print(c)
        else:
            for i, c in enumerate(chunks):
                if i > 0:
                    time.sleep(1.0)  # M3 패치: rate limit 회피
                send_telegram(c)

    if args.mode in ("detect", "all") and not args.no_detect:
        snapshot = load_snapshot()
        old_jjup = set(snapshot.get("chungyak_remndr_titles", []))
        old_normal = set(snapshot.get("chungyak_normal_titles", []))
        old_sh = set(snapshot.get("sh_titles", []))

        chungyak_result = detect_new_chungyak()
        new_jjup = chungyak_result["remndr"]
        new_normal = chungyak_result["normal"]
        new_sh = detect_new_sh()

        # 줍줍 (잔여세대) — 사용자 핵심 타깃
        if new_jjup is not None:
            added_jjup = new_jjup - old_jjup
            print(f"  jjup(줍줍): total={len(new_jjup)} new={len(added_jjup)}")
            if added_jjup:
                report_new(added_jjup, "청약홈 잔여세대", is_jjup=True)
        else:
            print("  jjup: skip (fetch/parse fail)")

        # 일반 분양 — 참고용 (낮은 우선도)
        if new_normal is not None:
            added_normal = new_normal - old_normal
            print(f"  normal(분양): total={len(new_normal)} new={len(added_normal)}")
            if added_normal:
                report_new(added_normal, "청약홈 분양정보", is_jjup=False)
        else:
            print("  normal: skip (fetch/parse fail)")

        # SH 게시판
        if new_sh is not None:
            added_sh = new_sh - old_sh
            print(f"  SH: total={len(new_sh)} new={len(added_sh)}")
            if added_sh:
                report_new(added_sh, "SH 임대공고", is_jjup=False)
        else:
            print("  SH: skip (fetch/parse fail)")

        # 스냅샷 갱신 — 성공한 소스만 갱신, 실패한 소스는 이전 스냅샷 보존
        new_snapshot = dict(snapshot)
        if new_jjup is not None:
            new_snapshot["chungyak_remndr_titles"] = sorted(new_jjup)
        if new_normal is not None:
            new_snapshot["chungyak_normal_titles"] = sorted(new_normal)
        if new_sh is not None:
            new_snapshot["sh_titles"] = sorted(new_sh)
        new_snapshot["updated"] = today.isoformat()
        save_snapshot(new_snapshot)


if __name__ == "__main__":
    main()
