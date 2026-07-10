#!/usr/bin/env python3
"""Local launchd gate for Garmin sync.

GitHub Actions can be rate-limited by Garmin. This local runner keeps the same
KST cadence while using the user's Mac/network as the preferred fallback path.
"""

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))
REPO = Path(__file__).resolve().parents[2]


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
    hhmm = (now.hour, now.minute)

    if hhmm == (8, 20):
        ok = today.weekday() < 5 and not is_holiday
        reason = "weekday 08:20 KST" if ok else "08:20 skipped: weekend/holiday"
    elif hhmm in ((12, 20), (16, 20)):
        ok = is_weekend or is_holiday
        reason = "weekend/holiday sync" if ok else "midday skipped: regular weekday"
    else:
        ok = False
        reason = f"not scheduled minute: {now:%H:%M}"
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
    return subprocess.run(
        [sys.executable, "workout/scripts/garmin_sync.py", "sync"],
        cwd=str(REPO),
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
