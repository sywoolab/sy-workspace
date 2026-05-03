"""
가민 활동 조회 (READ-ONLY, cross-platform)

용도:
- 윈도우/맥/리눅스에서 가민에 영향 없이 활동만 조회
- 분석/디버깅용. workout_log.json 등 갱신 안 함, 텔레 발송 안 함, git 안 씀

호출 방법:
    python workout/scripts/run_garmin_query.py [days_back]

기본값: 7일.
기존 run_garmin_query_win.sh(윈도우 전용)와 동일 효과 + 맥/리눅스 호환.
"""

import os
import sys
import json
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

from garminconnect import Garmin

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime('%Y-%m-%d')

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN_DIR = REPO_ROOT / 'workout' / 'data' / 'garmin_tokens'

KEYS = [
    'activityName', 'startTimeLocal', 'duration', 'distance',
    'averageHR', 'maxHR', 'averageSwolf', 'strokes', 'calories',
    'averageSpeed', 'averageRunningCadenceInStepsPerMinute',
    'aerobicTrainingEffect', 'anaerobicTrainingEffect',
]


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    start = (datetime.now(KST) - timedelta(days=days)).strftime('%Y-%m-%d')

    email = os.environ.get('GARMIN_EMAIL', '')
    password = os.environ.get('GARMIN_PASSWORD', '')
    if not email or not password:
        print("[ERROR] GARMIN_EMAIL / GARMIN_PASSWORD 환경변수 필요", file=sys.stderr)
        return 1

    g = Garmin(email, password)
    g.login(str(TOKEN_DIR))
    acts = g.get_activities_by_date(start, TODAY)

    out = []
    for a in acts:
        rec = {k: a.get(k) for k in KEYS}
        at = a.get('activityType')
        rec['type'] = at.get('typeKey') if isinstance(at, dict) else at
        out.append(rec)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"[INFO] {len(out)}건 조회 ({start} ~ {TODAY}) — read-only", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
