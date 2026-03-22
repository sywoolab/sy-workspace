"""
부동산 실거래가 주간 수집 + 전략별 TOP 20 스크립트
- 서울 25개구 전체 수집
- 단지별 매매/전세 집계 → 전략 1(갭투자) / 2(실거주) / 3(전월세) TOP 20
- 텔레그램 주간 리포트
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

# ── 상수 ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent / "data"

# 서울 25개구
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

# ── 매수자 제약조건 (L1 CLAUDE.md 기준) ──────────────────
CASH = 6.0          # 현금 가용 (억)
MAX_MONTHLY = 500   # 월 주거비 한도 (만원)
LTV = 0.70          # 생애최초 LTV
LOAN_CAP = 6.0      # 대출 절대한도 (억)

# 전략1 갭투자: 필요현금 ≤ 5.5억 (잔여 5천만 확보)
GAP_CASH_LIMIT = 5.5
# 전략2 실거주: 매매가 ≤ 12억 (현금6 + 대출6)
LIVE_PRICE_LIMIT = 12.0

# 면적 타입 범위 (59타입: 55~62, 84타입: 80~86)
AREA_TYPES = {
    "59㎡": (55.0, 62.0),
    "84㎡": (80.0, 86.0),
}

# 최소 세대수 (유동성 확보)
MIN_TRADE_COUNT = 1  # 최근 3개월 내 최소 거래 건수

# API 엔드포인트
TRADE_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
RENT_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"

# CSV 컬럼
TRADE_COLUMNS = [
    "시군구코드", "법정동", "단지명", "전용면적", "층",
    "건축년도", "계약년도", "계약월", "계약일",
    "거래금액", "해제여부", "해제사유발생일",
    "거래유형", "도로명", "지번",
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
    "시군구코드", "법정동", "단지명", "전용면적", "층",
    "건축년도", "계약년도", "계약월", "계약일",
    "보증금액", "월세금액", "계약구분", "계약기간",
    "종전보증금", "종전월세", "지번",
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


# ── 유틸 ──────────────────────────────────────────────────

def get_area_type(area_val):
    """전용면적 → 면적 타입 라벨 반환. 해당 없으면 None"""
    try:
        area = float(area_val)
    except (ValueError, TypeError):
        return None
    for label, (lo, hi) in AREA_TYPES.items():
        if lo <= area < hi:
            return label
    return None


def get_months(n=3):
    """최근 n개월의 YYYYMM 리스트"""
    today = datetime.now()
    months = set()
    for i in range(n + 1):
        dt = today - timedelta(days=30 * i)
        months.add(dt.strftime("%Y%m"))
    return sorted(months)[-n:]


def calc_loan(price, strategy="gap", jeonse=0):
    """대출 한도 계산 (억)"""
    raw = min(price * LTV, LOAN_CAP)
    if strategy == "gap":
        return max(raw - jeonse, 0)
    return raw  # 실거주


def calc_required_cash(price, strategy, jeonse=0):
    """필요현금 계산 (억). 취득세는 간이 산정"""
    # 취득세: 6억이하 1.1%, 9억이하 2.2%, 9억초과 3.3% (간이)
    if price <= 6:
        tax_rate = 0.011
    elif price <= 9:
        tax_rate = 0.022
    else:
        tax_rate = 0.033
    tax = price * tax_rate

    if strategy == "gap":
        gap = price - jeonse
        loan = calc_loan(price, "gap", jeonse)
        return gap - loan + tax
    else:  # 실거주
        loan = calc_loan(price, "live")
        return price - loan + tax


# ── API ──────────────────────────────────────────────────

def fetch_api(url, lawd_cd, deal_ymd):
    """API 호출 → item 리스트"""
    params = {
        "serviceKey": API_KEY,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "numOfRows": "99999",
        "pageNo": "1",
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [ERROR] {lawd_cd}/{deal_ymd}: {e}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []

    result_code = root.findtext(".//resultCode")
    if result_code and result_code not in ("00", "000"):
        return []

    return root.findall(".//item")


def parse_items(items, tag_map):
    """XML items → dict 리스트"""
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
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


# ── 수집 ──────────────────────────────────────────────────

def collect_all():
    """서울 25개구 전체 수집"""
    months = get_months(3)
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"수집 시작: {today_str}")
    print(f"대상 월: {months}")
    print(f"대상: 서울 25개구 전체\n")

    all_trade = {}
    all_rent = {}

    for code, name in DISTRICTS.items():
        trade_rows = []
        rent_rows = []

        for ym in months:
            items = fetch_api(TRADE_URL, code, ym)
            trade_rows.extend(parse_items(items, TRADE_TAG_MAP))

            items = fetch_api(RENT_URL, code, ym)
            rent_rows.extend(parse_items(items, RENT_TAG_MAP))

        # 매매: 해제 건 제외
        valid_trade = [r for r in trade_rows if r.get("해제여부") != "O"]
        # 전세만 (월세=0)
        jeonse = [r for r in rent_rows if r.get("월세금액", "0") == "0"]

        all_trade[code] = valid_trade
        all_rent[code] = jeonse

        # CSV 저장
        save_csv(valid_trade, TRADE_COLUMNS, BASE_DIR / "trade" / f"{code}_{name}.csv")
        save_csv(jeonse, RENT_COLUMNS, BASE_DIR / "jeonse" / f"{code}_{name}.csv")

        t_cnt = len(valid_trade)
        j_cnt = len(jeonse)
        print(f"  {name}: 매매 {t_cnt}건, 전세 {j_cnt}건")

    return all_trade, all_rent


# ── 단지별 집계 ──────────────────────────────────────────

def aggregate_complexes(all_trade, all_rent):
    """
    단지+면적타입 단위로 집계.
    반환: {(구, 단지명, 면적타입): {매매가, 전세가, 괴리율, ...}}
    """
    today = datetime.now()
    two_months_ago = today - timedelta(days=60)

    # 단지별 매매 거래 모으기
    trade_by_complex = defaultdict(list)  # key: (구코드, 단지명, 면적타입)
    for code, rows in all_trade.items():
        gu = DISTRICTS[code]
        for r in rows:
            area_type = get_area_type(r.get("전용면적"))
            if not area_type:
                continue
            try:
                amount = int(r["거래금액"]) / 10000
                y, m, d = int(r["계약년도"]), int(r["계약월"]), int(r["계약일"])
                dt = datetime(y, m, d)
            except (ValueError, KeyError):
                continue
            key = (gu, r["단지명"], area_type)
            trade_by_complex[key].append({"date": dt, "amount": amount})

    # 단지별 전세 모으기
    rent_by_complex = defaultdict(list)
    for code, rows in all_rent.items():
        gu = DISTRICTS[code]
        for r in rows:
            area_type = get_area_type(r.get("전용면적"))
            if not area_type:
                continue
            try:
                deposit = int(r["보증금액"]) / 10000
            except (ValueError, KeyError):
                continue
            key = (gu, r["단지명"], area_type)
            rent_by_complex[key].append(deposit)

    # 집계
    results = []
    for key, trades in trade_by_complex.items():
        gu, name, area_type = key
        if len(trades) < MIN_TRADE_COUNT:
            continue

        trades.sort(key=lambda x: x["date"])

        older = [t for t in trades if t["date"] < two_months_ago]
        recent = [t for t in trades if t["date"] >= two_months_ago]
        latest = trades[-1]

        price_2m_ago = older[-1]["amount"] if older else None
        price_2m_avg = round(sum(t["amount"] for t in recent) / len(recent), 2) if recent else None
        price_latest = latest["amount"]
        latest_date = latest["date"].strftime("%m.%d")

        gap_pct = None
        if price_2m_ago and price_2m_ago > 0:
            gap_pct = round((price_latest - price_2m_ago) / price_2m_ago * 100, 1)

        # 대표 매매가 = 최근 2개월 평균 or 최근 거래
        price = price_2m_avg if price_2m_avg else price_latest

        # 전세
        rents = rent_by_complex.get(key, [])
        jeonse_median = None
        jeonse_range = ""
        if rents:
            rents_sorted = sorted(rents)
            jeonse_median = rents_sorted[len(rents_sorted) // 2]
            jeonse_range = f"{min(rents):.1f}~{max(rents):.1f}"

        results.append({
            "구": gu,
            "단지명": name,
            "면적": area_type,
            "매매가": price,
            "2개월전": price_2m_ago,
            "2개월평균": price_2m_avg,
            "최근거래": price_latest,
            "최근일자": latest_date,
            "괴리율": gap_pct,
            "전세중위": jeonse_median,
            "전세범위": jeonse_range,
            "매매건수": len(trades),
            "전세건수": len(rents),
        })

    return results


# ── 전략별 TOP 20 ────────────────────────────────────────

def strategy1_gap(complexes):
    """전략1 갭투자: 갭 최소 + 필요현금 ≤ 5.5억"""
    ranked = []
    for c in complexes:
        if not c["전세중위"]:
            continue
        price = c["매매가"]
        jeonse = c["전세중위"]
        gap = price - jeonse
        if gap <= 0:
            continue
        required = calc_required_cash(price, "gap", jeonse)
        if required > GAP_CASH_LIMIT:
            continue
        remaining = CASH - required
        c_copy = dict(c)
        c_copy["갭"] = round(gap, 2)
        c_copy["필요현금"] = round(required, 2)
        c_copy["잔여현금"] = round(remaining, 2)
        c_copy["전세가율"] = round(jeonse / price * 100, 1) if price > 0 else 0
        ranked.append(c_copy)

    # 정렬: 필요현금 낮은 순 → 전세가율 높은 순
    ranked.sort(key=lambda x: (x["필요현금"], -x["전세가율"]))
    return ranked[:20]


def strategy2_live(complexes):
    """전략2 실거주: 매매가 ≤ 12억 + 필요현금 기준"""
    ranked = []
    for c in complexes:
        price = c["매매가"]
        if price > LIVE_PRICE_LIMIT:
            continue
        required = calc_required_cash(price, "live")
        if required > CASH:
            continue
        loan = calc_loan(price, "live")
        remaining = CASH - required
        # 월 상환 추정 (4%, 30년 원리금균등)
        if loan > 0:
            r_monthly = 0.04 / 12
            n_months = 360
            monthly = loan * 10000 * r_monthly / (1 - (1 + r_monthly) ** -n_months)
            monthly_man = round(monthly)  # 만원
        else:
            monthly_man = 0

        if monthly_man > MAX_MONTHLY:
            continue

        c_copy = dict(c)
        c_copy["대출"] = round(loan, 2)
        c_copy["필요현금"] = round(required, 2)
        c_copy["잔여현금"] = round(remaining, 2)
        c_copy["월상환"] = monthly_man
        ranked.append(c_copy)

    # 정렬: 잔여현금 많은 순 → 매매가 낮은 순
    ranked.sort(key=lambda x: (-x["잔여현금"], x["매매가"]))
    return ranked[:20]


def strategy3_wait(complexes):
    """전략3 전월세 거주: 전세 보증금 낮은 순 (현금 보존)"""
    ranked = []
    for c in complexes:
        if not c["전세중위"]:
            continue
        jeonse = c["전세중위"]
        if jeonse > CASH:  # 보증금이 가용 현금 초과
            continue
        remaining = CASH - jeonse
        # 연간 기회비용 (3.5%)
        opp_cost = round(jeonse * 0.035, 2)
        # 잔여현금 운용수익
        invest_return = round(remaining * 0.035, 2)
        net_cost = round(opp_cost - invest_return, 2)

        c_copy = dict(c)
        c_copy["보증금"] = round(jeonse, 2)
        c_copy["잔여현금"] = round(remaining, 2)
        c_copy["기회비용"] = opp_cost
        c_copy["운용수익"] = invest_return
        c_copy["순비용"] = net_cost
        ranked.append(c_copy)

    # 정렬: 순비용 낮은 순 → 보증금 낮은 순
    ranked.sort(key=lambda x: (x["순비용"], x["보증금"]))
    return ranked[:20]


# ── 텔레그램 메시지 ──────────────────────────────────────

def format_strategy_message(top1, top2, top3):
    """전략별 TOP 20 텔레그램 메시지"""
    today_str = datetime.now().strftime("%Y.%m.%d")
    messages = []

    # 전략1: 갭투자
    lines = [f"🏠 주간 부동산 리포트 ({today_str})", "", "━━ 전략1: 갭투자 TOP 20 ━━"]
    lines.append("(필요현금 ≤ 5.5억, 갭 작은 순)")
    lines.append("")
    for i, c in enumerate(top1, 1):
        g = c.get("괴리율")
        trend = f"📈+{g}%" if g and g > 0 else (f"📉{g}%" if g and g < 0 else "")
        lines.append(
            f"{i}. [{c['구']}] {c['단지명']} {c['면적']}\n"
            f"   매매 {c['매매가']:.1f} 전세 {c['전세중위']:.1f} "
            f"갭 {c['갭']:.1f} 필요 {c['필요현금']:.1f}억 {trend}"
        )
    messages.append("\n".join(lines))

    # 전략2: 실거주
    lines = ["", "━━ 전략2: 실거주 TOP 20 ━━"]
    lines.append("(매매 ≤ 12억, 잔여현금 많은 순)")
    lines.append("")
    for i, c in enumerate(top2, 1):
        g = c.get("괴리율")
        trend = f"📈+{g}%" if g and g > 0 else (f"📉{g}%" if g and g < 0 else "")
        lines.append(
            f"{i}. [{c['구']}] {c['단지명']} {c['면적']}\n"
            f"   매매 {c['매매가']:.1f} 대출 {c['대출']:.1f} "
            f"필요 {c['필요현금']:.1f} 잔여 {c['잔여현금']:.1f}억 "
            f"월{c['월상환']}만 {trend}"
        )
    messages.append("\n".join(lines))

    # 전략3: 전월세 대기
    lines = ["", "━━ 전략3: 전월세 거주 TOP 20 ━━"]
    lines.append("(순비용 낮은 순, 현금 보존)")
    lines.append("")
    for i, c in enumerate(top3, 1):
        lines.append(
            f"{i}. [{c['구']}] {c['단지명']} {c['면적']}\n"
            f"   보증금 {c['보증금']:.1f}억 잔여 {c['잔여현금']:.1f}억 "
            f"순비용 {c['순비용']:.1f}억/년"
        )
    messages.append("\n".join(lines))

    return "\n".join(messages)


def send_telegram(text):
    """텔레그램 발송 (4096자 분할)"""
    if not BOT_TOKEN or not CHAT_ID:
        print("[SKIP] 텔레그램 환경변수 미설정")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 4000:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)

    for chunk in chunks:
        try:
            resp = requests.post(url, data={
                "chat_id": CHAT_ID,
                "text": chunk,
                "disable_web_page_preview": "true",
            }, timeout=30)
            if resp.status_code != 200:
                print(f"[WARN] 텔레그램: {resp.status_code}")
        except requests.RequestException as e:
            print(f"[WARN] 텔레그램: {e}")


def save_summary(top1, top2, top3):
    """전략별 TOP 20 JSON 저장"""
    summary = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "strategy1_gap": [
            {k: v for k, v in c.items() if not callable(v)} for c in top1
        ],
        "strategy2_live": [
            {k: v for k, v in c.items() if not callable(v)} for c in top2
        ],
        "strategy3_wait": [
            {k: v for k, v in c.items() if not callable(v)} for c in top3
        ],
    }
    path = BASE_DIR / "strategy_top20.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"요약 저장: {path}")


# ── 메인 ──────────────────────────────────────────────────

def main():
    if not API_KEY:
        print("[ERROR] DATA_GO_KR_API_KEY 미설정")
        sys.exit(0)

    # 1. 서울 25개구 전체 수집
    all_trade, all_rent = collect_all()

    total_trade = sum(len(v) for v in all_trade.values())
    total_rent = sum(len(v) for v in all_rent.values())
    print(f"\n수집 완료: 매매 {total_trade}건, 전세 {total_rent}건")

    # 2. 단지별 집계
    complexes = aggregate_complexes(all_trade, all_rent)
    print(f"집계 단지 수: {len(complexes)}")

    # 3. 전략별 TOP 20
    top1 = strategy1_gap(complexes)
    top2 = strategy2_live(complexes)
    top3 = strategy3_wait(complexes)
    print(f"전략1(갭투자): {len(top1)}건 / 전략2(실거주): {len(top2)}건 / 전략3(전월세): {len(top3)}건")

    # 4. 저장
    save_summary(top1, top2, top3)

    # 5. 텔레그램
    msg = format_strategy_message(top1, top2, top3)
    print(f"\n{msg}")
    send_telegram(msg)


if __name__ == "__main__":
    main()
