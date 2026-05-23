#!/usr/bin/env python3
"""sy-workspace pre-commit hook: 누적 데이터 파일 entry 무결성 검증.

2026-05-23 데탑 워크로그 5/16, 5/17 자전거 entry overwrite 사고에서 도출.
HEAD에는 metrics N개 있는 날짜가 staged에서 0개로 줄어들면 commit 차단.
2026-05-23: .githooks/ 정본 이관 (다중 머신 동기화 — core.hooksPath = .githooks).
"""
import json
import subprocess
import sys

PROTECTED_LOG = [
    'workout/workout_log.json',
]
PROTECTED_KEYED = [
    'workout/data/garmin_health.json',
    'workout/workout_schedule.json',
    'ib/watchlist.json',
    'ib/watchlist_team.json',
]


def show(path, ref):
    r = subprocess.run(['git', 'show', f'{ref}:{path}'],
                       capture_output=True, encoding='utf-8', errors='replace')
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def check_workout_log():
    violations = []
    for p in PROTECTED_LOG:
        head = show(p, 'HEAD')
        stagd = show(p, '')
        if head is None or stagd is None:
            continue
        for date, entry in head.items():
            if not isinstance(entry, dict):
                continue
            h_metrics = entry.get('all_metrics', []) or []
            if date not in stagd:
                if h_metrics:
                    violations.append(f"  - {p}: {date} entry 통째 삭제 (HEAD metrics={len(h_metrics)})")
                continue
            s_entry = stagd[date]
            if not isinstance(s_entry, dict):
                continue
            s_metrics = s_entry.get('all_metrics', []) or []
            if len(h_metrics) > 0 and len(s_metrics) == 0:
                types = [m.get('type') for m in h_metrics]
                violations.append(f"  - {p}: {date} metrics {len(h_metrics)}개 → 0 (손실 type: {types})")
    return violations


def check_keyed_files():
    violations = []
    for p in PROTECTED_KEYED:
        head = show(p, 'HEAD')
        stagd = show(p, '')
        if head is None or stagd is None:
            continue
        # 최상위 dict의 key가 줄어드는 경우만 차단 (append-only 보장)
        if isinstance(head, dict) and isinstance(stagd, dict):
            lost_keys = sorted(set(head.keys()) - set(stagd.keys()))
            if lost_keys:
                violations.append(f"  - {p}: key {len(lost_keys)}개 손실 (예: {lost_keys[:5]})")
        # companies 같은 list 필드 검사
        if isinstance(head, dict) and isinstance(stagd, dict):
            for k in ('companies',):
                hv = head.get(k, [])
                sv = stagd.get(k, [])
                if isinstance(hv, list) and isinstance(sv, list) and len(sv) < len(hv):
                    violations.append(f"  - {p}: {k} {len(hv)} → {len(sv)}개 (감소)")
    return violations


def main():
    violations = check_workout_log() + check_keyed_files()
    if violations:
        print("[pre-commit 차단] 누적 데이터 파일 entry 손실 감지:", file=sys.stderr)
        for v in violations:
            print(v, file=sys.stderr)
        print("\n  L0 §\"권한 일탈 금지\" — 사용자 명시 승인 없이 데이터 손실 commit 금지.", file=sys.stderr)
        print("  Diff 전수 확인 + 손실 entry 보고 + 사용자 ack 후에만 --no-verify 사용 가능.", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
