"""SQLite DB 관리"""

import os
import json
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'precedent.db')


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS disclosures (
            rcept_no     TEXT PRIMARY KEY,
            rcept_dt     TEXT NOT NULL,
            corp_code    TEXT NOT NULL,
            corp_name    TEXT NOT NULL,
            corp_cls     TEXT,
            report_nm    TEXT NOT NULL,
            category     TEXT NOT NULL,
            sub_category TEXT,
            detail_json  TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_disc_category ON disclosures(category);
        CREATE INDEX IF NOT EXISTS idx_disc_corp ON disclosures(corp_code);
        CREATE INDEX IF NOT EXISTS idx_disc_date ON disclosures(rcept_dt);
    """)
    conn.commit()
    conn.close()


def upsert_disclosure(conn, row: dict):
    conn.execute("""
        INSERT INTO disclosures (rcept_no, rcept_dt, corp_code, corp_name,
                                 corp_cls, report_nm, category, sub_category)
        VALUES (:rcept_no, :rcept_dt, :corp_code, :corp_name,
                :corp_cls, :report_nm, :category, :sub_category)
        ON CONFLICT(rcept_no) DO UPDATE SET
            category = excluded.category,
            sub_category = excluded.sub_category
    """, row)


def update_detail(conn, rcept_no: str, detail_json: str):
    conn.execute("""
        UPDATE disclosures SET detail_json = ? WHERE rcept_no = ?
    """, (detail_json, rcept_no))


def get_pending_enrichment(conn, category: str = None):
    """구조화 API 조회가 안 된 공시 목록 (빈 JSON도 재시도 대상)"""
    sql = "SELECT * FROM disclosures WHERE detail_json IS NULL OR detail_json = '{}'"
    params = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    return conn.execute(sql, params).fetchall()


def get_stats(conn):
    rows = conn.execute("""
        SELECT category,
               COUNT(*) as total,
               SUM(CASE WHEN detail_json IS NOT NULL THEN 1 ELSE 0 END) as enriched
        FROM disclosures
        GROUP BY category
        ORDER BY category
    """).fetchall()
    return rows


def export_category(conn, category_codes: list) -> list:
    """카테고리에 해당하는 공시를 딕셔너리 리스트로 반환"""
    placeholders = ','.join('?' * len(category_codes))
    rows = conn.execute(f"""
        SELECT * FROM disclosures
        WHERE category IN ({placeholders})
        ORDER BY rcept_dt DESC
    """, category_codes).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get('detail_json'):
            d['detail'] = json.loads(d['detail_json'])
        result.append(d)
    return result
