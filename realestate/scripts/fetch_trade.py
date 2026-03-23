"""
부동산 실거래가 주간 수집 + 점수화 + 전략별 TOP 20
- 서울 25개구 전체 수집
- 단지별 8개 변수 점수화 (추세/가격대/거래량/통근/독립문/연식/할인율/전세가율)
- 전략 1(갭투자) / 2(실거주) / 3(전월세) 각각 가중치 적용 → TOP 20
"""

import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ── 환경변수 ──────────────────────────────────────────────
API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

BASE_DIR = Path(__file__).resolve().parent.parent / "data"

# ── 서울 25개구 ──────────────────────────────────────────
DISTRICTS = {
    "11110": "종로구", "11140": "중구", "11170": "용산구",
    "11200": "성동구", "11215": "광진구", "11230": "동대문구",
    "11260": "중랑구", "11290": "성북구", "11305": "강북구",
    "11320": "도봉구", "11350": "노원구", "11380": "은평구",
    "11410": "서대문구", "11440": "마포구", "11470": "양천구",
    "11500": "강서구", "11530": "구로구", "11545": "금천구",
    "11560": "영등포구", "11590": "동작구", "11620": "관악구",
    "11650": "서초구", "11680": "강남구", "11710": "송파구",
    "11740": "강동구",
}

# ── 구별 통근 시간 (여의도, 청계산입구, 독립문) 단위: 분 ──
COMMUTE = {
    '종로구': (14, 32, 6), '중구': (20, 29, 9), '용산구': (9, 30, 22),
    '성동구': (26, 31, 20), '광진구': (27, 28, 28), '동대문구': (29, 35, 20),
    '중랑구': (39, 39, 33), '성북구': (31, 42, 21), '강북구': (38, 51, 28),
    '도봉구': (53, 59, 48), '노원구': (47, 52, 38), '은평구': (32, 49, 10),
    '서대문구': (13, 41, 22), '마포구': (5, 36, 21), '양천구': (8, 40, 35),
    '강서구': (20, 52, 48), '구로구': (13, 38, 34), '금천구': (17, 42, 45),
    '영등포구': (3, 30, 30), '동작구': (13, 20, 36), '관악구': (20, 28, 43),
    '서초구': (23, 15, 34), '강남구': (18, 9, 30), '송파구': (27, 23, 37),
    '강동구': (40, 35, 39),
}

# ── 구별 KB 전세가율 (%) ──
KB_RATIO = {
    '종로구': 57.1, '중구': 52.5, '용산구': 38.8, '성동구': 41.5, '광진구': 45.9,
    '동대문구': 54.7, '중랑구': 63.0, '성북구': 58.5, '강북구': 61.7, '도봉구': 59.4,
    '노원구': 55.3, '은평구': 59.5, '서대문구': 55.1, '마포구': 47.4, '양천구': 45.2,
    '강서구': 54.6, '구로구': 58.9, '금천구': 62.8, '영등포구': 47.5, '동작구': 47.8,
    '관악구': 57.2, '서초구': 41.5, '강남구': 37.7, '송파구': 38.9, '강동구': 46.4,
}

# ── 매수자 제약 ──────────────────────────────────────────
CASH = 6.0
LTV = 0.70
LOAN_CAP = 6.0
COMMUTE_LIMIT = 60  # 각 직장 60분 이내

# 독립문 동쪽 구 (노도강 제외) — 양육 지원 접근성 우선 탐색 영역
EAST_GU = {'종로구', '중구', '성동구', '동대문구', '성북구', '광진구'}

# ── 스코어링 가중치 ──────────────────────────────────────
# S1:추세 S2:가격대 S3:거래량 S4:통근가중 S5:독립문 S6:연식 S7:할인율 S8:KB전세가율
GAP_WEIGHTS = {'S1': 30, 'S2': 15, 'S3': 10, 'S4': 15, 'S5': 5, 'S6': 5, 'S7': 5, 'S8': 15}
LIVE_WEIGHTS = {'S1': 25, 'S2': 10, 'S3': 10, 'S4': 25, 'S5': 10, 'S6': 10, 'S7': 10, 'S8': 0}
WAIT_WEIGHTS = {'S1': 10, 'S2': 5, 'S3': 10, 'S4': 30, 'S5': 15, 'S6': 10, 'S7': 5, 'S8': 15}

# ── API/파싱 ─────────────────────────────────────────────
TRADE_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
RENT_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"

TRADE_COLUMNS = [
    "시군구코드", "법정동", "단지명", "전용면적", "층", "건축년도",
    "계약년도", "계약월", "계약일", "거래금액", "해제여부",
    "해제사유발생일", "거래유형", "도로명", "지번",
]
TRADE_TAG_MAP = {
    "sggCd": "시군구코드", "umdNm": "법정동", "aptNm": "단지명",
    "excluUseAr": "전용면적", "floor": "층", "buildYear": "건축년도",
    "dealYear": "계약년도", "dealMonth": "계약월", "dealDay": "계약일",
    "dealAmount": "거래금액", "cdealType": "해제여부",
    "cdealDay": "해제사유발생일", "dealingGbn": "거래유형",
    "roadNm": "도로명", "jibun": "지번",
}
RENT_COLUMNS = [
    "시군구코드", "법정동", "단지명", "전용면적", "층", "건축년도",
    "계약년도", "계약월", "계약일", "보증금액", "월세금액",
    "계약구분", "계약기간", "종전보증금", "종전월세", "지번",
]
RENT_TAG_MAP = {
    "sggCd": "시군구코드", "umdNm": "법정동", "aptNm": "단지명",
    "excluUseAr": "전용면적", "floor": "층", "buildYear": "건축년도",
    "dealYear": "계약년도", "dealMonth": "계약월", "dealDay": "계약일",
    "deposit": "보증금액", "monthlyRent": "월세금액",
    "contractType": "계약구분", "contractTerm": "계약기간",
    "preDeposit": "종전보증금", "preMonthlyRent": "종전월세",
    "jibun": "지번",
}


def _calc_acq_tax(price):
    """취득세 + 지방교육세 계산 (억원 단위 입출력)
    - 6억 이하: 1% + 교육세 0.1% = 1.1%
    - 6억 초과~9억 이하: 누진 사선형 (취득가×2/3 - 3)% + 교육세
    - 9억 초과: 3% + 교육세 0.3% = 3.3%
    """
    if price <= 6:
        rate = 0.01
    elif price <= 9:
        rate = (price * 2 / 3 - 3) / 100
    else:
        rate = 0.03
    edu_tax_rate = rate * 0.1  # 지방교육세 = 취득세의 10%
    return round(price * (rate + edu_tax_rate), 4)


def get_area_type(val):
    try:
        a = float(val)
    except (ValueError, TypeError):
        return None
    if 55.0 <= a < 62.0:
        return "59㎡"
    if 80.0 <= a < 86.0:
        return "84㎡"
    return None


def get_months(n=3):
    today = datetime.now()
    months = set()
    for i in range(n + 1):
        months.add((today - timedelta(days=30 * i)).strftime("%Y%m"))
    return sorted(months)[-n:]


def fetch_api(url, lawd_cd, deal_ymd):
    """API 호출. 성공 시 list[Element], 실패 시 None 반환 (빈 리스트 = 정상 0건)"""
    params = {
        "serviceKey": API_KEY, "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd, "numOfRows": "99999", "pageNo": "1",
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        code = root.findtext(".//resultCode")
        if code and code not in ("00", "000"):
            msg = root.findtext(".//resultMsg") or "unknown"
            print(f"  [API ERR] {lawd_cd}/{deal_ymd}: code={code} msg={msg}")
            return None
        return root.findall(".//item")
    except Exception as e:
        print(f"  [API ERR] {lawd_cd}/{deal_ymd}: {type(e).__name__}: {e}")
        return None


def parse_items(items, tag_map):
    rows = []
    for item in items:
        row = {}
        for tag, col in tag_map.items():
            el = item.find(tag)
            val = el.text.strip() if el is not None and el.text else ""
            if col in ("거래금액", "보증금액", "월세금액", "종전보증금", "종전월세"):
                val = val.replace(",", "").strip()
            row[col] = val
        if "해제여부" in row and row["해제여부"].strip():
            row["해제여부"] = "O"
        rows.append(row)
    return rows


def save_csv(rows, columns, filepath):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


# ── 수집 ──────────────────────────────────────────────────

def collect_all():
    months = get_months(3)
    print(f"수집: {datetime.now():%Y-%m-%d} | 월: {months} | 서울 25개구\n")

    all_trade, all_rent = {}, {}
    failed_districts = []  # (구이름, 에러유형) 추적

    for code, name in DISTRICTS.items():
        trade_rows, rent_rows = [], []
        trade_err, rent_err = False, False

        for ym in months:
            t_items = fetch_api(TRADE_URL, code, ym)
            r_items = fetch_api(RENT_URL, code, ym)
            if t_items is None:
                trade_err = True
            else:
                trade_rows.extend(parse_items(t_items, TRADE_TAG_MAP))
            if r_items is None:
                rent_err = True
            else:
                rent_rows.extend(parse_items(r_items, RENT_TAG_MAP))

        valid = [r for r in trade_rows if r.get("해제여부") != "O"]
        jeonse = [r for r in rent_rows if r.get("월세금액", "0") == "0"]
        all_trade[code] = valid
        all_rent[code] = jeonse

        save_csv(valid, TRADE_COLUMNS, BASE_DIR / "trade" / f"{code}_{name}.csv")
        save_csv(jeonse, RENT_COLUMNS, BASE_DIR / "jeonse" / f"{code}_{name}.csv")

        err_tag = ""
        if trade_err or rent_err:
            parts = []
            if trade_err:
                parts.append("매매")
            if rent_err:
                parts.append("전세")
            err_tag = f" ⚠ {'+'.join(parts)} 일부 실패"
            failed_districts.append((name, "+".join(parts)))

        print(f"  {name}: 매매 {len(valid)}, 전세 {len(jeonse)}{err_tag}")

    return all_trade, all_rent, failed_districts


# ── 집계 + 점수화 ────────────────────────────────────────

def aggregate_and_score(all_trade, all_rent):
    today = datetime.now()
    two_months_ago = today - timedelta(days=60)

    # 단지별 매매/전세 모으기
    trade_map = defaultdict(list)  # (구, 단지, 면적) → [{date, amount, year}]
    rent_map = defaultdict(list)   # (구, 단지, 면적) → [deposit]

    for code, rows in all_trade.items():
        gu = DISTRICTS[code]
        for r in rows:
            at = get_area_type(r.get("전용면적"))
            if not at:
                continue
            try:
                amt = int(r["거래금액"]) / 10000
                dt = datetime(int(r["계약년도"]), int(r["계약월"]), int(r["계약일"]))
                by = int(r["건축년도"]) if r.get("건축년도") else None
            except (ValueError, KeyError):
                continue
            trade_map[(gu, r["단지명"], at)].append({"date": dt, "amount": amt, "build_year": by})

    for code, rows in all_rent.items():
        gu = DISTRICTS[code]
        for r in rows:
            at = get_area_type(r.get("전용면적"))
            if not at:
                continue
            try:
                dep = int(r["보증금액"]) / 10000
            except (ValueError, KeyError):
                continue
            rent_map[(gu, r["단지명"], at)].append(dep)

    # 구+면적별 평균가격 (할인율 계산용)
    gu_area_prices = defaultdict(list)
    for (gu, name, at), trades in trade_map.items():
        if len(trades) >= 2:
            avg = sum(t["amount"] for t in trades) / len(trades)
            gu_area_prices[(gu, at)].append(avg)
    gu_area_avg = {}
    for key, prices in gu_area_prices.items():
        gu_area_avg[key] = sum(prices) / len(prices)

    # 단지별 집계 + 스코어
    results = []
    for (gu, name, area_type), trades in trade_map.items():
        if len(trades) < 2:
            continue

        # 통근 필터
        yeouido, cheongye, doklip = COMMUTE.get(gu, (99, 99, 99))
        if yeouido > COMMUTE_LIMIT or cheongye > COMMUTE_LIMIT:
            continue

        trades.sort(key=lambda x: x["date"])
        older = [t for t in trades if t["date"] < two_months_ago]
        recent = [t for t in trades if t["date"] >= two_months_ago]
        latest = trades[-1]

        price_2m_ago = older[-1]["amount"] if older else None
        price_2m_avg = round(sum(t["amount"] for t in recent) / len(recent), 2) if recent else None
        price_latest = latest["amount"]
        price = price_2m_avg if price_2m_avg else price_latest

        gap_pct = None
        if price_2m_ago and price_2m_ago > 0:
            gap_pct = round((price_latest - price_2m_ago) / price_2m_ago * 100, 1)

        # 추세: 전반부 vs 후반부
        n = len(trades)
        first_half = trades[:max(n // 2, 1)]
        second_half = trades[max(n // 2, 1):]
        avg_first = sum(t["amount"] for t in first_half) / len(first_half)
        avg_second = sum(t["amount"] for t in second_half) / len(second_half) if second_half else avg_first
        trend = round((avg_second - avg_first) / avg_first * 100, 1) if avg_first > 0 else 0

        # 건축년도
        years = [t["build_year"] for t in trades if t["build_year"]]
        build_year = max(set(years), key=years.count) if years else None
        age = (2026 - build_year) if build_year else None

        # 전세
        rents = rent_map.get((gu, name, area_type), [])
        jeonse_avg = round(sum(rents) / len(rents), 2) if len(rents) >= 2 else None
        jeonse_range = f"{min(rents):.1f}~{max(rents):.1f}" if rents else ""

        # 할인율
        gu_avg = gu_area_avg.get((gu, area_type), price)
        discount = round((gu_avg - price) / gu_avg * 100, 1) if gu_avg > 0 else 0

        # 통근 가중
        commute_w = round(yeouido * 0.4 + cheongye * 0.6, 1)

        # ── S1~S8 스코어 ──
        s1 = 10 if trend >= 10 else (9 if trend >= 7 else (8 if trend >= 4 else (6 if trend >= 1 else (4 if trend >= 0 else 2))))
        s2 = 10 if price >= 12 else (8 if price >= 10 else (6 if price >= 8 else 4))
        s3 = 10 if len(trades) >= 15 else (8 if len(trades) >= 8 else (6 if len(trades) >= 4 else 3))
        s4 = 10 if commute_w <= 18 else (9 if commute_w <= 24 else (8 if commute_w <= 30 else (7 if commute_w <= 36 else (5 if commute_w <= 45 else 3))))
        s5 = 10 if doklip <= 15 else (8 if doklip <= 25 else (6 if doklip <= 35 else 4))
        if age is None:
            s6 = 5
        elif age <= 5:
            s6 = 10
        elif age <= 10:
            s6 = 9
        elif age <= 15:
            s6 = 7
        elif age <= 20:
            s6 = 6
        elif age >= 35:
            s6 = 7  # 재건축 기대
        else:
            s6 = 5
        s7 = 10 if discount >= 15 else (8 if discount >= 5 else (6 if discount >= 0 else 4))
        kb_ratio = KB_RATIO.get(gu, 50)
        s8 = 10 if kb_ratio >= 60 else (8 if kb_ratio >= 55 else (6 if kb_ratio >= 50 else 4))

        scores = {'S1': s1, 'S2': s2, 'S3': s3, 'S4': s4, 'S5': s5, 'S6': s6, 'S7': s7, 'S8': s8}

        # 전략별 총점
        gap_score = sum(scores[k] * v / 100 for k, v in GAP_WEIGHTS.items())
        live_score = sum(scores[k] * v / 100 for k, v in LIVE_WEIGHTS.items())
        wait_score = sum(scores[k] * v / 100 for k, v in WAIT_WEIGHTS.items())

        # 갭투자 필요현금
        gap_val = round(price - jeonse_avg, 2) if jeonse_avg else None
        if jeonse_avg:
            loan_gap = max(min(price * LTV, LOAN_CAP) - jeonse_avg, 0)
            tax = _calc_acq_tax(price)
            need_gap = round(gap_val - loan_gap + tax, 2)
        else:
            loan_gap = None
            need_gap = None

        # 실거주 필요현금
        loan_live = min(price * LTV, LOAN_CAP)
        tax = _calc_acq_tax(price)
        need_live = round(price - loan_live + tax, 2)

        results.append({
            "구": gu, "단지명": name, "면적": area_type,
            "매매가": price, "2개월전": price_2m_ago, "2개월평균": price_2m_avg,
            "최근거래": price_latest, "최근일자": latest["date"].strftime("%m.%d"),
            "괴리율": gap_pct, "추세": trend,
            "전세평균": jeonse_avg, "전세범위": jeonse_range,
            "매매건수": len(trades), "전세건수": len(rents),
            "준공": build_year, "연식": age,
            "여의도": yeouido, "청계산": cheongye, "독립문": doklip,
            "통근가중": commute_w, "KB전세가율": kb_ratio,
            "할인율": discount,
            "갭": gap_val, "갭대출": round(loan_gap, 2) if loan_gap else None,
            "갭필요현금": need_gap, "실거주대출": round(loan_live, 2),
            "실거주필요현금": need_live,
            "총점_갭": round(gap_score, 2),
            "총점_실거주": round(live_score, 2),
            "총점_전월세": round(wait_score, 2),
            **scores,
        })

    return results


# ── 전략별 TOP 10 ────────────────────────────────────────

def top10_gap(data):
    """전략1: 갭필요현금 ≤ 5.5억, 매매 8억+, 전세 2건+, 총점순"""
    pool = [d for d in data
            if d["갭필요현금"] is not None
            and 0 < d["갭필요현금"] <= 5.5
            and d["매매가"] >= 8
            and d["전세건수"] >= 2]
    pool.sort(key=lambda x: -x["총점_갭"])
    return pool[:10]


def _monthly_payment(loan_eok):
    """월 상환액 계산 (원리금균등, 금리4%, 30년). loan_eok=억원 단위, 반환=만원"""
    principal = loan_eok * 10000  # 억 → 만원
    r = 0.04 / 12  # 월 이율
    n = 360  # 30년
    if principal <= 0:
        return 0
    return principal * (r / (1 - (1 + r) ** -n))


def top10_live(data):
    """전략2: 실거주필요현금 ≤ 6.0억, 매매 8억+, 전세 2건+, DSR 월500만 이내, 총점순"""
    pool = []
    for d in data:
        if d["실거주필요현금"] > 6.0 or d["매매가"] < 8 or d["전세건수"] < 2:
            continue
        # DSR 체크: 월 상환액 ≤ 500만원
        monthly = _monthly_payment(d["실거주대출"])
        if monthly > 500:
            continue
        pool.append(d)
    pool.sort(key=lambda x: -x["총점_실거주"])
    return pool[:10]


def top10_wait(data):
    """전략3: 전세평균 ≤ 5억, 전세 2건+, 총점순"""
    pool = [d for d in data
            if d["전세평균"] is not None
            and d["전세평균"] <= 5.0
            and d["전세건수"] >= 2]
    pool.sort(key=lambda x: -x["총점_전월세"])
    return pool[:10]


# ── 텔레그램 (양식 C: 하이브리드) ────────────────────────

def _shorten_gu(gu):
    """구 이름 축약: 서대문구→서대문"""
    return gu.replace("구", "")


def _trend_str(trend):
    if not trend:
        return ""
    return f"+{trend:.0f}%" if trend > 0 else f"{trend:.0f}%"


def format_gap_message(top, pool_size, region_tag=""):
    """전략1 갭투자 메시지 (HTML)"""
    today = datetime.now().strftime("%Y-%m-%d")
    tag = f" ({region_tag})" if region_tag else ""
    lines = [
        f"🏠 <b>갭투자 TOP {len(top)}{tag}</b>",
        f"<i>{today} | 필요현금 ≤5.5억 | 비과세 6년</i>",
        "",
    ]
    for i, c in enumerate(top, 1):
        gu = _shorten_gu(c['구'])
        t = _trend_str(c['추세'])
        if i <= 3:
            # 상위 3개: 상세
            lines.append(
                f"<b>{i}. [{gu}] {c['단지명']}</b> {c['면적']} ⭐{c['총점_갭']:.1f}\n"
                f"  매매 {c['매매가']:.1f} | 전세 {c['전세평균']:.1f} | "
                f"갭 {c['갭']:.1f} | 필요 {c['갭필요현금']:.1f}\n"
                f"  통근 {c['통근가중']:.0f}분 | 추세 <b>{t}</b>"
            )
        else:
            # 4위~: 한 줄 압축
            lines.append(
                f"{i}. [{gu}] {c['단지명']} {c['면적']}"
                f" | {c['매매가']:.1f} 갭{c['갭']:.1f} 필요{c['갭필요현금']:.1f} {t}"
            )
    lines.append("")
    lines.append(f"<i>후보 {pool_size}개 중 TOP {len(top)} (단위: 억원)</i>")
    return "\n".join(lines)


def format_live_message(top, pool_size, region_tag=""):
    """전략2 실거주 메시지 (HTML)"""
    today = datetime.now().strftime("%Y-%m-%d")
    tag = f" ({region_tag})" if region_tag else ""
    lines = [
        f"🔑 <b>실거주 TOP {len(top)}{tag}</b>",
        f"<i>{today} | 대출 최대6억 | 비과세 2년</i>",
        "",
    ]
    for i, c in enumerate(top, 1):
        gu = _shorten_gu(c['구'])
        t = _trend_str(c['추세'])
        if i <= 3:
            lines.append(
                f"<b>{i}. [{gu}] {c['단지명']}</b> {c['면적']} ⭐{c['총점_실거주']:.1f}\n"
                f"  매매 {c['매매가']:.1f} | 대출 {c['실거주대출']:.1f} | "
                f"필요 {c['실거주필요현금']:.1f}\n"
                f"  통근 {c['통근가중']:.0f}분 | 추세 <b>{t}</b>"
            )
        else:
            lines.append(
                f"{i}. [{gu}] {c['단지명']} {c['면적']}"
                f" | {c['매매가']:.1f} 필요{c['실거주필요현금']:.1f} {t}"
            )
    lines.append("")
    lines.append(f"<i>후보 {pool_size}개 중 TOP {len(top)} (단위: 억원)</i>")
    return "\n".join(lines)


def format_wait_message(top, pool_size, region_tag=""):
    """전략3 전월세 메시지 (HTML)"""
    today = datetime.now().strftime("%Y-%m-%d")
    tag = f" ({region_tag})" if region_tag else ""
    lines = [
        f"📋 <b>전월세 거주 TOP {len(top)}{tag}</b>",
        f"<i>{today} | 전세≤5억 | 현금 축적 후 매수</i>",
        "",
    ]
    for i, c in enumerate(top, 1):
        gu = _shorten_gu(c['구'])
        if i <= 3:
            lines.append(
                f"<b>{i}. [{gu}] {c['단지명']}</b> {c['면적']} ⭐{c['총점_전월세']:.1f}\n"
                f"  전세 {c['전세평균']:.1f} | 통근 {c['통근가중']:.0f}분 | 독립문 {c['독립문']}분"
            )
        else:
            lines.append(
                f"{i}. [{gu}] {c['단지명']} {c['면적']}"
                f" | 전세{c['전세평균']:.1f} 통근{c['통근가중']:.0f}분"
            )
    lines.append("")
    lines.append(f"<i>후보 {pool_size}개 중 TOP {len(top)} (단위: 억원)</i>")
    return "\n".join(lines)


def send_telegram(text, parse_mode="HTML"):
    """텔레그램 발송 (전략별 별도 메시지)"""
    if not BOT_TOKEN or not CHAT_ID:
        print("[SKIP] 텔레그램 미설정")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }, timeout=30)
        if resp.status_code != 200:
            # HTML 파싱 실패 시 plain text로 재시도
            requests.post(url, data={
                "chat_id": CHAT_ID,
                "text": text,
                "disable_web_page_preview": "true",
            }, timeout=30)
    except Exception as e:
        print(f"[WARN] 텔레그램: {e}")


def save_results(data, top1, top2, top3):
    # 전체 데이터 CSV
    if data:
        keys = data[0].keys()
        path = BASE_DIR / "scored_all.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(data)
        print(f"전체 저장: {path} ({len(data)}건)")

    # TOP 10 JSON
    summary = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "strategy1_gap": top1,
        "strategy2_live": top2,
        "strategy3_wait": top3,
    }
    path = BASE_DIR / "strategy_top10.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"TOP10 저장: {path}")


# ── 메인 ──────────────────────────────────────────────────

def main():
    if not API_KEY:
        print("[ERROR] DATA_GO_KR_API_KEY 미설정")
        sys.exit(0)

    all_trade, all_rent, failed_districts = collect_all()
    total_t = sum(len(v) for v in all_trade.values())
    total_r = sum(len(v) for v in all_rent.values())
    print(f"\n수집 완료: 매매 {total_t}, 전세 {total_r}")

    if failed_districts:
        print(f"\n⚠ API 실패 {len(failed_districts)}건:")
        for gu_name, err_type in failed_districts:
            print(f"  - {gu_name}: {err_type} API 일부 호출 실패 (해당 월 데이터 누락 가능)")
        print("  → 해당 구의 점수/순위가 부정확할 수 있습니다.")

    data = aggregate_and_score(all_trade, all_rent)
    print(f"집계+스코어: {len(data)}건 (통근 필터 적용)")

    # 전략별 풀 크기 (메시지 하단 표시용)
    gap_pool = [d for d in data if d["갭필요현금"] is not None and 0 < d["갭필요현금"] <= 5.5 and d["매매가"] >= 8 and d["전세건수"] >= 2]
    live_pool = [d for d in data if d["실거주필요현금"] <= 6.0 and d["매매가"] >= 8 and d["전세건수"] >= 2]
    wait_pool = [d for d in data if d["전세평균"] is not None and d["전세평균"] <= 5.0 and d["전세건수"] >= 2]

    top1 = top10_gap(data)
    top2 = top10_live(data)
    top3 = top10_wait(data)
    print(f"전략1: {len(top1)}/{len(gap_pool)} / 전략2: {len(top2)}/{len(live_pool)} / 전략3: {len(top3)}/{len(wait_pool)}")

    save_results(data, top1, top2, top3)

    # ── 전체 서울 메시지 (3개) ──
    msg1 = format_gap_message(top1, len(gap_pool), "서울 전체")
    msg2 = format_live_message(top2, len(live_pool), "서울 전체")
    msg3 = format_wait_message(top3, len(wait_pool), "서울 전체")

    for msg in [msg1, msg2, msg3]:
        print(f"\n{msg}")
        send_telegram(msg)

    # ── 독립문 동쪽 구 메시지 (3개) ──
    east_data = [d for d in data if d["구"] in EAST_GU]
    east_top1 = top10_gap(east_data)
    east_top2 = top10_live(east_data)
    east_top3 = top10_wait(east_data)

    east_gap_pool = [d for d in east_data if d["갭필요현금"] is not None and 0 < d["갭필요현금"] <= 5.5 and d["매매가"] >= 8 and d["전세건수"] >= 2]
    east_live_pool = [d for d in east_data if d["실거주필요현금"] <= 6.0 and d["매매가"] >= 8 and d["전세건수"] >= 2]
    east_wait_pool = [d for d in east_data if d["전세평균"] is not None and d["전세평균"] <= 5.0 and d["전세건수"] >= 2]

    print(f"\n동쪽: 전략1 {len(east_top1)}/{len(east_gap_pool)} / 전략2 {len(east_top2)}/{len(east_live_pool)} / 전략3 {len(east_top3)}/{len(east_wait_pool)}")

    tag = "독립문 동쪽"
    emsg1 = format_gap_message(east_top1, len(east_gap_pool), tag)
    emsg2 = format_live_message(east_top2, len(east_live_pool), tag)
    emsg3 = format_wait_message(east_top3, len(east_wait_pool), tag)

    for msg in [emsg1, emsg2, emsg3]:
        print(f"\n{msg}")
        send_telegram(msg)


if __name__ == "__main__":
    main()
