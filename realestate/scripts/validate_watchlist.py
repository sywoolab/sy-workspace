"""
부동산 워치리스트 자동 검증 스크립트 (INBOX #26, 2026-05-25)
realestate-brief.yml 및 realestate.yml 실행 시 watchlist_summary.json 정합성 확인.

검증 항목:
1. 단지 실존 — scored_all.csv에 (구, 법정동, 단지명, 면적) 실존 여부
2. 가격 상한 초과 — price_latest > MAX_PRICE(11.6억) 탐지
3. 데이터 stale — 최종 갱신 14일+ 경과 시 경고

알림 발송 후 항상 exit 0 (CI block 금지).
"""

import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / '.env').exists():
            load_dotenv(_p / '.env')
            break
except ImportError:
    pass

import requests

BOT_TOKEN = (os.environ.get("BOT_TOKEN")
             or os.environ.get("REALESTATE_BOT_TOKEN")
             or os.environ.get("TELEGRAM_BOT_TOKEN", ""))
CHAT_ID = os.environ.get("CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
SEND_TELEGRAM = os.environ.get("VALIDATE_WATCHLIST_TELEGRAM", "1").lower() not in ("0", "false", "no")
MAX_PRICE = 11.6
STALE_DAYS = 14
BASE_DIR = Path(__file__).resolve().parent.parent / "data"


def send_telegram(text):
    if not SEND_TELEGRAM:
        print("[SKIP] VALIDATE_WATCHLIST_TELEGRAM=0 — 텔레그램 스킵")
        return
    if not BOT_TOKEN or not CHAT_ID:
        print("[WARN] BOT_TOKEN/CHAT_ID 미설정 — 텔레그램 스킵")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
    except Exception as e:
        print(f"[WARN] 텔레그램: {e}")


def load_watchlist():
    path = BASE_DIR / "watchlist_summary.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_scored_set():
    path = BASE_DIR / "scored_all.csv"
    if not path.exists():
        return set()
    scored = set()
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            key = (
                row.get("구", "").strip(),
                row.get("법정동", "").strip(),
                row.get("단지명", "").strip(),
                row.get("면적", "").strip(),
            )
            scored.add(key)
    return scored


def main():
    print(f"[validate_watchlist] {datetime.now():%Y-%m-%d %H:%M}")

    watchlist = load_watchlist()
    if watchlist is None:
        msg = "⚠️ [검증] watchlist_summary.json 없음 — 스킵"
        print(msg)
        send_telegram(msg)
        return 0

    scored_set = load_scored_set()
    complexes = watchlist.get("complexes", [])
    updated = watchlist.get("updated", "unknown")
    issues = []

    if not scored_set:
        issues.append("⚠️ scored_all.csv 없음 — 단지 실존 검증 SKIP (가격·stale만 검증)")

    for c in complexes:
        name = c.get("name", "")
        area = c.get("area", "")
        district = c.get("district", "")
        price = c.get("price_latest") or c.get("price_2m_avg") or 0

        parts = district.split(" ", 1)
        gu = parts[0] if parts else ""
        dong = parts[1] if len(parts) > 1 else ""
        key = (gu, dong, name, area)

        if scored_set and key not in scored_set:
            issues.append(f"❌ 실존 미확인: {name} {area} ({district})")

        if price and price > MAX_PRICE:
            issues.append(f"⚠️ 가격 초과: {name} {area} {price:.1f}억 > {MAX_PRICE}억")

    # stale 검사
    try:
        updated_dt = datetime.strptime(updated, "%Y-%m-%d %H:%M")
        stale = (datetime.now() - updated_dt).days
        if stale > STALE_DAYS:
            issues.append(f"⚠️ 데이터 {stale}일 경과 (최종: {updated})")
    except (ValueError, TypeError):
        pass

    if issues:
        msg = (
            f"🔍 <b>부동산 워치리스트 검증 ({len(issues)}건 이슈)</b>\n"
            f"<i>기준일: {updated} | {len(complexes)}개 단지</i>\n\n"
            + "\n".join(issues)
        )
        print(msg)
        send_telegram(msg)
    else:
        print(f"✅ 부동산 워치리스트 검증 통과 ({len(complexes)}건, {updated})")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        err = traceback.format_exc()[:1000]
        print(f"[ERROR] {err}")
        try:
            send_telegram(f"❌ validate_watchlist.py 예외\n{err}")
        except Exception:
            pass
        sys.exit(0)
