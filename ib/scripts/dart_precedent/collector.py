"""Phase 1: list.json으로 전체 시장 공시 목록 수집"""

from datetime import datetime, timedelta
from .dart_client import DARTClient
from .classifier import classify
from .config import PBLNTF_TYPES
from . import db


def split_90days(bgn_de: str, end_de: str):
    """날짜 범위를 90일 단위 청크로 분할"""
    fmt = '%Y%m%d'
    start = datetime.strptime(bgn_de, fmt)
    end = datetime.strptime(end_de, fmt)
    while start <= end:
        chunk_end = min(start + timedelta(days=89), end)
        yield start.strftime(fmt), chunk_end.strftime(fmt)
        start = chunk_end + timedelta(days=1)


def collect_list(client: DARTClient, bgn_de: str, end_de: str, pblntf_ty: str):
    """list.json으로 공시 목록 수집 (자동 페이지네이션 + 90일 분할)"""
    all_items = []
    for chunk_bgn, chunk_end in split_90days(bgn_de, end_de):
        page = 1
        while True:
            data = client.get('list.json', {
                'bgn_de': chunk_bgn,
                'end_de': chunk_end,
                'pblntf_ty': pblntf_ty,
                'page_no': str(page),
                'page_count': '100',
                'sort': 'date',
                'sort_mth': 'desc',
            })
            if data.get('status') != '000':
                break
            items = data.get('list', [])
            if not items:
                break
            all_items.extend(items)
            total_page = int(data.get('total_page', 1))
            if page >= total_page:
                break
            page += 1
    return all_items


def collect_and_store(client: DARTClient, bgn_de: str, end_de: str):
    """Phase 1 전체 실행: 수집 → 분류 → DB 저장"""
    conn = db.get_conn()
    db.init_db()

    total_saved = 0
    total_skipped = 0

    try:
        for pblntf_ty, label in PBLNTF_TYPES.items():
            print(f"\n  [{label}] (pblntf_ty={pblntf_ty}) {bgn_de}~{end_de}")
            items = collect_list(client, bgn_de, end_de, pblntf_ty)
            print(f"    수집: {len(items)}건")

            saved = 0
            for item in items:
                report_nm = item.get('report_nm', '')
                category, sub_category = classify(report_nm)
                if not category:
                    continue

                row = {
                    'rcept_no': item.get('rcept_no', ''),
                    'rcept_dt': item.get('rcept_dt', ''),
                    'corp_code': item.get('corp_code', ''),
                    'corp_name': item.get('corp_name', ''),
                    'corp_cls': item.get('corp_cls', ''),
                    'report_nm': report_nm,
                    'category': category,
                    'sub_category': sub_category,
                }
                db.upsert_disclosure(conn, row)
                saved += 1

            conn.commit()
            total_saved += saved
            total_skipped += len(items) - saved
            print(f"    분류·저장: {saved}건 (미분류 스킵: {len(items) - saved}건)")
    finally:
        conn.close()

    return total_saved, total_skipped
