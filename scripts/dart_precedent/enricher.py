"""Phase 2: 구조화 API로 상세 데이터 보강"""

import json
from .dart_client import DARTClient
from .config import DETAIL_API, TREASURY_SUB_CATEGORIES
from . import db


def enrich_disclosure(client: DARTClient, row, conn):
    """개별 공시의 구조화 API 조회 → detail_json 저장"""
    category = row['category']
    endpoint = DETAIL_API.get(category)
    if not endpoint:
        return False

    corp_code = row['corp_code']
    rcept_dt = row['rcept_dt']

    # 구조화 API는 corp_code + 날짜범위 필요
    # 해당 공시일 ±30일 범위로 조회
    data = client.get(endpoint, {
        'corp_code': corp_code,
        'bgn_de': rcept_dt,
        'end_de': rcept_dt,
    })

    if data.get('status') != '000':
        # 날짜 범위 확장 시도 (±30일)
        from datetime import datetime, timedelta
        dt = datetime.strptime(rcept_dt, '%Y%m%d')
        bgn = (dt - timedelta(days=30)).strftime('%Y%m%d')
        end = (dt + timedelta(days=30)).strftime('%Y%m%d')
        data = client.get(endpoint, {
            'corp_code': corp_code,
            'bgn_de': bgn,
            'end_de': end,
        })

    if data.get('status') != '000':
        # 데이터 없음 → 빈 JSON 저장하여 재시도 방지
        db.update_detail(conn, row['rcept_no'], '{}')
        return False

    detail_list = data.get('list', [])
    if not detail_list:
        db.update_detail(conn, row['rcept_no'], '{}')
        return False

    # rcept_no가 일치하는 항목만 사용 (오매칭 방지)
    matched = None
    for item in detail_list:
        if item.get('rcept_no') == row['rcept_no']:
            matched = item
            break
    if not matched:
        db.update_detail(conn, row['rcept_no'], '{}')
        return False

    # 자사주 처분의 경우 세부 분류 업데이트
    if category == 'treasury_disposal':
        sub = classify_treasury_sub(matched)
        if sub:
            conn.execute(
                "UPDATE disclosures SET sub_category = ? WHERE rcept_no = ?",
                (sub, row['rcept_no'])
            )

    db.update_detail(conn, row['rcept_no'], json.dumps(matched, ensure_ascii=False))
    return True


def classify_treasury_sub(detail: dict) -> str:
    """구조화 API 응답에서 자사주 처분 세부 분류"""
    dp_pp = detail.get('dp_pp', '')  # 처분목적
    dp_m = detail.get('dp_m', '')    # 처분방법
    text = f"{dp_pp} {dp_m}"

    for sub, keywords in TREASURY_SUB_CATEGORIES.items():
        if any(kw in text for kw in keywords):
            return sub
    return 'other'


def enrich_all(client: DARTClient, category: str = None):
    """미조회 공시 전체 보강"""
    conn = db.get_conn()
    try:
        pending = db.get_pending_enrichment(conn, category)
        print(f"\n  보강 대상: {len(pending)}건" + (f" (카테고리: {category})" if category else ""))

        success = 0
        fail = 0
        for i, row in enumerate(pending):
            if (i + 1) % 50 == 0:
                print(f"    진행: {i+1}/{len(pending)} (성공: {success}, 실패: {fail})")
                conn.commit()

            ok = enrich_disclosure(client, row, conn)
            if ok:
                success += 1
            else:
                fail += 1

        conn.commit()
        print(f"  보강 완료: 성공 {success} / 실패 {fail} / 총 {len(pending)}")
        return success, fail
    finally:
        conn.close()
