"""
프리시던트 DB CLI

사용법:
  # 초기 적재 (기본: 2020-01-01~오늘)
  python -m scripts.dart_precedent.main backfill
  python -m scripts.dart_precedent.main backfill --from 20150101

  # 일일 업데이트 (오늘 공시)
  python -m scripts.dart_precedent.main daily

  # 구조화 API 보강 (Phase 2)
  python -m scripts.dart_precedent.main enrich
  python -m scripts.dart_precedent.main enrich --category cb

  # 통계
  python -m scripts.dart_precedent.main stats

  # CSV 내보내기
  python -m scripts.dart_precedent.main export
"""

import sys
import csv
import json
import os
from datetime import datetime, timezone, timedelta

from .config import DART_API_KEY, SHEET_GROUPS
from .dart_client import DARTClient
from .collector import collect_and_store
from .enricher import enrich_all
from . import db

KST = timezone(timedelta(hours=9))


def cmd_backfill(args):
    bgn = '20200101'
    for i, a in enumerate(args):
        if a == '--from' and i + 1 < len(args):
            bgn = args[i + 1]
    end = datetime.now(KST).strftime('%Y%m%d')

    print(f"=== Phase 1: 공시 목록 수집 ({bgn} ~ {end}) ===")
    client = DARTClient(DART_API_KEY)
    saved, skipped = collect_and_store(client, bgn, end)
    print(f"\n총 저장: {saved}건, 스킵: {skipped}건, API 호출: {client.call_count}회")

    print(f"\n=== Phase 2: 구조화 API 보강 ===")
    success, fail = enrich_all(client)
    print(f"\n총 API 호출: {client.call_count}회")


def cmd_daily(args):
    today = datetime.now(KST).strftime('%Y%m%d')
    print(f"=== 일일 업데이트 ({today}) ===")
    client = DARTClient(DART_API_KEY)

    saved, _ = collect_and_store(client, today, today)
    if saved > 0:
        enrich_all(client)

    print(f"총 API 호출: {client.call_count}회")


def cmd_enrich(args):
    category = None
    for i, a in enumerate(args):
        if a == '--category' and i + 1 < len(args):
            category = args[i + 1]

    print(f"=== 구조화 API 보강 ===")
    client = DARTClient(DART_API_KEY)
    enrich_all(client, category)
    print(f"총 API 호출: {client.call_count}회")


def cmd_stats(args):
    db.init_db()
    conn = db.get_conn()
    rows = db.get_stats(conn)

    print("=== 프리시던트 DB 통계 ===\n")
    print(f"{'카테고리':<25s} {'전체':>6s} {'상세완료':>8s} {'비율':>6s}")
    print("-" * 50)
    grand_total = 0
    grand_enriched = 0
    for row in rows:
        total = row['total']
        enriched = row['enriched']
        pct = f"{enriched/total*100:.0f}%" if total > 0 else "-"
        print(f"{row['category']:<25s} {total:>6d} {enriched:>8d} {pct:>6s}")
        grand_total += total
        grand_enriched += enriched
    print("-" * 50)
    pct = f"{grand_enriched/grand_total*100:.0f}%" if grand_total > 0 else "-"
    print(f"{'합계':<25s} {grand_total:>6d} {grand_enriched:>8d} {pct:>6s}")
    conn.close()


def cmd_export(args):
    db.init_db()
    conn = db.get_conn()
    out_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'precedent_export')
    os.makedirs(out_dir, exist_ok=True)

    for sheet_name, categories in SHEET_GROUPS.items():
        rows = db.export_category(conn, categories)
        if not rows:
            print(f"  {sheet_name}: 0건 (스킵)")
            continue

        path = os.path.join(out_dir, f"{sheet_name}.csv")
        # 공통 필드 + detail 필드 병합
        fieldnames = ['rcept_dt', 'corp_name', 'corp_cls', 'category',
                       'sub_category', 'report_nm', 'rcept_no']

        # detail 필드 수집
        detail_keys = set()
        for r in rows:
            if r.get('detail') and isinstance(r['detail'], dict):
                detail_keys.update(r['detail'].keys())
        # 불필요 키 제거
        detail_keys.discard('rcept_no')
        detail_keys.discard('corp_cls')
        detail_keys.discard('corp_code')
        detail_keys.discard('corp_name')
        detail_keys = sorted(detail_keys)
        fieldnames.extend(detail_keys)

        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for r in rows:
                out_row = {
                    'rcept_dt': r['rcept_dt'],
                    'corp_name': r['corp_name'],
                    'corp_cls': r['corp_cls'],
                    'category': r['category'],
                    'sub_category': r.get('sub_category', ''),
                    'report_nm': r['report_nm'],
                    'rcept_no': r['rcept_no'],
                }
                if r.get('detail') and isinstance(r['detail'], dict):
                    for k in detail_keys:
                        out_row[k] = r['detail'].get(k, '')
                writer.writerow(out_row)

        print(f"  {sheet_name}: {len(rows)}건 → {path}")

    conn.close()
    print(f"\n내보내기 완료: {out_dir}")


COMMANDS = {
    'backfill': cmd_backfill,
    'daily': cmd_daily,
    'enrich': cmd_enrich,
    'stats': cmd_stats,
    'export': cmd_export,
}


def main():
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        print("사용법: python -m scripts.dart_precedent.main <command>")
        print(f"명령어: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    cmd = args[0]

    # API 키 필요한 명령어는 키 검증
    if cmd in ('backfill', 'daily', 'enrich') and not DART_API_KEY:
        print("[오류] DART_API_KEY 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    COMMANDS[cmd](args[1:])


if __name__ == '__main__':
    main()
