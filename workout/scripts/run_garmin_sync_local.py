#!/usr/bin/env python3
"""Local launchd gate for Garmin sync.

GitHub Actions can be rate-limited by Garmin. This local runner keeps the same
KST cadence while using the user's Mac/network as the preferred fallback path.
"""

import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))
REPO = Path(__file__).resolve().parents[2]
NETWORK_WAIT_MAX = 120
NETWORK_CHECK_INTERVAL = 10


def wait_for_network(max_wait=NETWORK_WAIT_MAX, interval=NETWORK_CHECK_INTERVAL):
    """Block until DNS resolves or timeout. Returns True if network is up."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            socket.getaddrinfo("sso.garmin.com", 443, socket.AF_INET)
            return True
        except OSError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            print(f"[네트워크 대기] DNS 미해결 — {interval}초 후 재시도 (남은 대기: {remaining:.0f}s)")
            time.sleep(min(interval, remaining))
    return False


def is_kr_holiday(today):
    try:
        import holidays
    except ImportError:
        return False, ""
    kr_holidays = holidays.country_holidays("KR", years=[today.year])
    return today in kr_holidays, kr_holidays.get(today, "")


def should_run(now):
    today = now.date()
    is_weekend = today.weekday() >= 5
    is_holiday, holiday_name = is_kr_holiday(today)
    h, m = now.hour, now.minute

    # launchd가 지정 시각보다 최대 10분 늦게 실행될 수 있음 → ±10분 허용
    def near(target_h, target_m, tol=10):
        diff = (h * 60 + m) - (target_h * 60 + target_m)
        return -tol <= diff <= tol

    if near(9, 0):
        ok = today.weekday() < 5 and not is_holiday
        reason = "weekday 09:00 KST" if ok else "09:00 skipped: weekend/holiday"
    elif near(12, 30):
        ok = is_weekend or is_holiday
        reason = "weekend/holiday sync 12:30" if ok else "12:30 skipped: regular weekday"
    elif near(16, 30):
        ok = is_weekend or is_holiday
        reason = "weekend/holiday sync 16:30" if ok else "16:30 skipped: regular weekday"
    else:
        ok = False
        reason = f"not scheduled time: {now:%H:%M}"
    return ok, reason, holiday_name


def main():
    force = "--force" in sys.argv
    dry_run = "--dry-run" in sys.argv
    now = datetime.now(KST)
    ok, reason, holiday_name = should_run(now)
    print(
        f"KST now={now:%Y-%m-%d %H:%M:%S} "
        f"weekday={now.date().weekday()} holiday={bool(holiday_name)} {holiday_name} "
        f"should_run={ok or force} reason={reason}"
    )
    if dry_run:
        return 0
    if not ok and not force:
        return 0

    # 네트워크 연결 대기 (launchd가 Mac 슬립 직후 실행될 때 DNS 미해결 방지)
    print(f"[네트워크 확인] 최대 {NETWORK_WAIT_MAX}초 대기...")
    if not wait_for_network():
        print(f"[ERROR] 네트워크 연결 실패 — {NETWORK_WAIT_MAX}초 초과. 동기화 건너뜀.")
        return 1
    print("[네트워크 OK] DNS 확인 완료, 동기화 시작")

    return subprocess.run(
        [sys.executable, "workout/scripts/garmin_sync.py", "sync"],
        cwd=str(REPO),
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
