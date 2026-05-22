"""
밸류에이션 초안 자동화
- 기업명 or 종목코드 입력 → DART 재무 자동 수집 (연간/LTM)
- EV/EBIT, PER, PBR 범위 산출
- Markdown 리포트 출력 (IB 보고 형식)

사용법:
  python3 valuation_auto.py 솔루엠
  python3 valuation_auto.py 248070
  python3 valuation_auto.py 솔루엠 --comps 삼성전기 LG이노텍 파트론
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / '.env').exists():
            load_dotenv(_p / '.env')
            break
except ImportError:
    pass

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

DART_API_KEY = os.environ.get('DART_API_KEY', '')
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'

# ─── DART reprt_code 상수 ───
RC_ANNUAL  = '11011'  # 사업보고서
RC_1Q      = '11013'  # 1분기
RC_HALF    = '11012'  # 반기
RC_3Q      = '11014'  # 3분기

# ─── 한국 시장 업종별 기준 멀티플 (EV/영업이익 중앙값, 2024~2025 기준) ───
INDUSTRY_MULTIPLES = {
    '전기전자':    {'ev_ebit': (12, 18), 'per': (15, 25), 'name': '전기/전자'},
    '화학':        {'ev_ebit': (8,  14), 'per': (10, 18), 'name': '화학'},
    '제약':        {'ev_ebit': (18, 35), 'per': (25, 45), 'name': '제약/바이오'},
    '바이오':      {'ev_ebit': (18, 35), 'per': (25, 45), 'name': '제약/바이오'},
    '소프트웨어':  {'ev_ebit': (15, 25), 'per': (20, 35), 'name': 'IT/소프트웨어'},
    '반도체':      {'ev_ebit': (14, 22), 'per': (18, 30), 'name': '반도체'},
    '자동차':      {'ev_ebit': (7,  12), 'per': (8,  15), 'name': '자동차'},
    '건설':        {'ev_ebit': (6,  10), 'per': (7,  12), 'name': '건설'},
    '철강':        {'ev_ebit': (5,  9),  'per': (6,  11), 'name': '철강/금속'},
    '음식료':      {'ev_ebit': (9,  15), 'per': (12, 20), 'name': '음식료'},
    '유통':        {'ev_ebit': (7,  12), 'per': (10, 18), 'name': '유통'},
    '금융':        {'ev_ebit': None,     'per': (7,  12), 'name': '금융'},
    '기타':        {'ev_ebit': (8,  14), 'per': (10, 18), 'name': '기타'},
}


# ════════════════════════════════════════════════════════════
# DART API 헬퍼
# ════════════════════════════════════════════════════════════

def dart_api(endpoint, params, max_retry=3):
    url = f'https://opendart.fss.or.kr/api/{endpoint}'
    params['crtfc_key'] = DART_API_KEY
    for attempt in range(max_retry):
        try:
            r = requests.get(url, params=params, timeout=30)
            data = r.json()
            if data.get('status') == '000':
                return data.get('list', []) or data
            if data.get('status') == '013':  # 데이터 없음
                return []
        except Exception as e:
            if attempt == max_retry - 1:
                print(f'    DART API 오류 ({endpoint}): {e}')
        time.sleep(0.5)
    return []


def _load_corp_cache():
    """DART 종목코드 → corp_code 캐시 로드 (workspace/scripts/_dart_corp_codes.json)"""
    for p in [
        Path(__file__).parent / '_dart_corp_codes.json',
        Path.home() / 'Library' / 'CloudStorage' / 'OneDrive-개인' / '바탕 화면' / 'workspace' / 'scripts' / '_dart_corp_codes.json',
    ]:
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
    return {}


def find_corp_code(name_or_code):
    """기업명 or 종목코드 → (corp_code, corp_name, stock_code, induty_code, corp_cls)"""
    cache = _load_corp_cache()

    # 6자리 숫자: 종목코드 직접 조회
    if name_or_code.isdigit() and len(name_or_code) == 6:
        info = cache.get(name_or_code)
        if info:
            corp_code = info.get('corp_code')
            corp_name = info.get('name', '')
            # DART company.json으로 상세 정보 조회
            detail = dart_api('company.json', {'corp_code': corp_code})
            if isinstance(detail, dict) and detail.get('status') == '000':
                return corp_code, detail.get('corp_name', corp_name), name_or_code, detail.get('induty_code', ''), detail.get('corp_cls', '')
            return corp_code, corp_name, name_or_code, '', ''
        return None, None, None, '', ''

    # 기업명: 캐시에서 검색 (부분 일치)
    name_lower = name_or_code.lower()
    for stock_code, info in cache.items():
        cached_name = info.get('name', '')
        if name_or_code in cached_name or cached_name in name_or_code:
            corp_code = info.get('corp_code')
            # 상세 조회
            detail = dart_api('company.json', {'corp_code': corp_code})
            if isinstance(detail, dict) and detail.get('status') == '000':
                return corp_code, detail.get('corp_name', cached_name), stock_code, detail.get('induty_code', ''), detail.get('corp_cls', '')
            return corp_code, cached_name, stock_code, '', ''

    return None, None, None, '', ''


# ════════════════════════════════════════════════════════════
# 재무 데이터 수집 (L1 SSOT: 연간/LTM 2개 기준 필수)
# ════════════════════════════════════════════════════════════

def fetch_single_account(corp_code, year, reprt_code, fs_div='CFS'):
    """단일 기업 재무제표 전체 계정 조회"""
    items = dart_api('fnlttSinglAcntAll.json', {
        'corp_code': corp_code, 'bsns_year': str(year),
        'reprt_code': reprt_code, 'fs_div': fs_div,
    })
    if not items:  # 연결 없으면 별도
        items = dart_api('fnlttSinglAcntAll.json', {
            'corp_code': corp_code, 'bsns_year': str(year),
            'reprt_code': reprt_code, 'fs_div': 'OFS',
        })
    return items or []


def get_account_value(items, account_names, col='thstrm_amount'):
    """계정과목 리스트에서 첫 매칭 값(억원) 반환"""
    for name in account_names if isinstance(account_names, list) else [account_names]:
        for item in items:
            if item.get('account_nm') == name:
                val = item.get(col, '').replace(',', '').replace('-', '0')
                try:
                    return int(val) / 1e8
                except Exception:
                    pass
    return None


def fetch_financials(corp_code):
    """연간/LTM 재무 데이터 수집 (L1 §재무 데이터 산출 절차)"""
    print(f'  재무 데이터 수집 중...')

    # ── 1. 연간 최신 (FY2024 사업보고서) ──
    items_fy24 = fetch_single_account(corp_code, 2024, RC_ANNUAL)
    fy24 = {}
    if items_fy24:
        fy24 = {
            'revenue':    get_account_value(items_fy24, ['매출액', '수익(매출액)']),
            'ebit':       get_account_value(items_fy24, ['영업이익', '영업이익(손실)']),
            'net_income': get_account_value(items_fy24, ['당기순이익', '당기순이익(손실)']),
            'da':         get_account_value(items_fy24, ['감가상각비', '유형자산상각비']),
            'period':     'FY2024',
        }

    # ── 2. LTM 계산 (최신 분기 기준) ──
    ltm = _calc_ltm_all(corp_code, fy24)

    # ── 3. 최신 BS (순차입금) ──
    net_debt, nd_detail = _calc_net_debt(corp_code)

    return {
        'annual': fy24,
        'ltm':    ltm,
        'net_debt': net_debt,
        'nd_detail': nd_detail,
    }


def _calc_ltm_all(corp_code, fy24):
    """LTM = FY2024 + 최신 누적분기 - 전년 동기 누적분기"""
    year_now = NOW.year

    for reprt_code, label in [
        (RC_3Q,   f'LTM(3Q {year_now})'),
        (RC_HALF, f'LTM(반기 {year_now})'),
        (RC_1Q,   f'LTM(1Q {year_now})'),
    ]:
        curr = fetch_single_account(corp_code, year_now, reprt_code)
        prev = fetch_single_account(corp_code, year_now - 1, reprt_code)

        if not curr or not prev:
            continue

        curr_rev  = get_account_value(curr, ['매출액', '수익(매출액)'])
        prev_rev  = get_account_value(prev, ['매출액', '수익(매출액)'])
        curr_ebit = get_account_value(curr, ['영업이익', '영업이익(손실)'])
        prev_ebit = get_account_value(prev, ['영업이익', '영업이익(손실)'])
        curr_ni   = get_account_value(curr, ['당기순이익', '당기순이익(손실)'])
        prev_ni   = get_account_value(prev, ['당기순이익', '당기순이익(손실)'])
        curr_da   = get_account_value(curr, ['감가상각비', '유형자산상각비'])
        prev_da   = get_account_value(prev, ['감가상각비', '유형자산상각비'])

        fy_rev  = fy24.get('revenue')
        fy_ebit = fy24.get('ebit')
        fy_ni   = fy24.get('net_income')
        fy_da   = fy24.get('da')

        if all(v is not None for v in [fy_ebit, curr_ebit, prev_ebit]):
            ltm_ebit = fy_ebit + curr_ebit - prev_ebit
            ltm_rev  = (fy_rev  + curr_rev  - prev_rev)  if all(v is not None for v in [fy_rev,  curr_rev,  prev_rev])  else None
            ltm_ni   = (fy_ni   + curr_ni   - prev_ni)   if all(v is not None for v in [fy_ni,   curr_ni,   prev_ni])   else None
            ltm_da   = (fy_da   + curr_da   - prev_da)   if all(v is not None for v in [fy_da,   curr_da,   prev_da])   else None
            return {
                'revenue':    ltm_rev,
                'ebit':       ltm_ebit,
                'net_income': ltm_ni,
                'da':         ltm_da,
                'period':     label,
            }

    return fy24  # LTM 불가 시 FY2024로 폴백


def _calc_net_debt(corp_code):
    """최신 BS에서 순차입금 계산"""
    year_now = NOW.year
    for reprt_code, label in [
        (RC_3Q,   f'{year_now} 3Q BS'),
        (RC_HALF, f'{year_now} 반기 BS'),
        (RC_1Q,   f'{year_now} 1Q BS'),
        (RC_ANNUAL, f'{year_now-1} 연간 BS'),
    ]:
        items = fetch_single_account(corp_code, year_now if reprt_code != RC_ANNUAL else year_now - 1, reprt_code)
        if not items:
            continue

        bs_items = [i for i in items if i.get('sj_div') in ('BS', '')]
        total_debt = 0
        total_cash = 0
        debt_detail = []
        cash_detail = []

        for item in bs_items:
            acct = item.get('account_nm', '')
            val_str = item.get('thstrm_amount', '').replace(',', '')
            try:
                val = int(val_str) / 1e8
            except Exception:
                continue

            # 차입금 계열
            if any(k in acct for k in ['차입금', '사채']) and not any(e in acct for e in ['리스', '법인세', '확정급여']):
                total_debt += val
                debt_detail.append((acct, val))

            # 현금 계열
            if any(k in acct for k in ['현금및현금성자산', '현금 및 현금성자산', '단기금융상품']):
                total_cash += val
                cash_detail.append((acct, val))

        if total_debt > 0 or total_cash > 0:
            return total_debt - total_cash, {
                'bs_period': label,
                'total_debt': total_debt,
                'total_cash': total_cash,
                'debt_items': debt_detail[:5],
                'cash_items': cash_detail[:3],
            }

    return None, {}


# ════════════════════════════════════════════════════════════
# 시가총액 / EV 계산
# ════════════════════════════════════════════════════════════

def fetch_market_cap(stock_code):
    """네이버 금융에서 현재가 + 상장주식수 → 시가총액(억원)"""
    if not stock_code:
        return None, None
    try:
        url = f'https://finance.naver.com/item/main.naver?code={stock_code}'
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        import re
        # 현재가
        price_m = re.search(r'<p class="no_today">.*?<span[^>]*>([0-9,]+)</span>', r.text, re.DOTALL)
        price = int(price_m.group(1).replace(',', '')) if price_m else None
        # 상장주식수
        shares_m = re.search(r'상장주식수.*?<td>([0-9,]+)</td>', r.text, re.DOTALL)
        shares = int(shares_m.group(1).replace(',', '')) if shares_m else None
        if price and shares:
            mktcap = price * shares / 1e8
            return price, mktcap
    except Exception as e:
        print(f'    시가총액 조회 실패: {e}')
    return None, None


# ════════════════════════════════════════════════════════════
# 비교기업 멀티플
# ════════════════════════════════════════════════════════════

def fetch_comp_multiples(comp_list):
    """비교기업 목록 → 각 기업의 EV/EBIT, PER 계산"""
    results = []
    for name in comp_list:
        print(f'  비교기업 [{name}] 수집 중...')
        corp_code, corp_name, stock_code, _, _ = find_corp_code(name)
        if not corp_code:
            print(f'    → 코드 없음, 스킵')
            continue

        fins = fetch_financials(corp_code)
        price, mktcap = fetch_market_cap(stock_code)
        ltm = fins.get('ltm', {})
        nd  = fins.get('net_debt')

        ev = (mktcap + nd) if (mktcap is not None and nd is not None) else None
        ebit = ltm.get('ebit')
        ni   = ltm.get('net_income')

        ev_ebit = round(ev / ebit, 1) if (ev and ebit and ebit > 0) else None
        per     = round(mktcap / ni, 1) if (mktcap and ni and ni > 0) else None

        results.append({
            'name':    corp_name or name,
            'ltm_ebit': round(ebit, 0) if ebit else None,
            'nd':       round(nd,   0) if nd   is not None else None,
            'mktcap':   round(mktcap, 0) if mktcap else None,
            'ev':       round(ev,   0) if ev   else None,
            'ev_ebit':  ev_ebit,
            'per':      per,
            'period':   ltm.get('period', ''),
        })
        time.sleep(0.3)

    return results


# ════════════════════════════════════════════════════════════
# 밸류에이션 범위 산출
# ════════════════════════════════════════════════════════════

def calc_valuation_range(fins, mktcap, comp_multiples, induty_code):
    """EV/EBIT, PER 기반 밸류에이션 범위 (최솟값~최댓값)"""
    ltm  = fins.get('ltm', {})
    nd   = fins.get('net_debt', 0) or 0
    ebit = ltm.get('ebit')
    ni   = ltm.get('net_income')

    # 멀티플 범위 결정: 비교기업 우선, 없으면 업종 기준
    ev_ebit_range = None
    per_range     = None

    if comp_multiples:
        evs = [c['ev_ebit'] for c in comp_multiples if c.get('ev_ebit') and c['ev_ebit'] > 0]
        pers = [c['per']     for c in comp_multiples if c.get('per')     and c['per'] > 0]
        if evs:
            ev_ebit_range = (round(min(evs), 1), round(max(evs), 1))
        if pers:
            per_range = (round(min(pers), 1), round(max(pers), 1))

    if not ev_ebit_range:
        # 업종코드로 매핑
        industry_key = '기타'
        for key in INDUSTRY_MULTIPLES:
            if key in str(induty_code):
                industry_key = key
                break
        mult = INDUSTRY_MULTIPLES.get(industry_key, INDUSTRY_MULTIPLES['기타'])
        ev_ebit_range = mult.get('ev_ebit')
        per_range     = per_range or mult.get('per')

    results = {}

    if ebit and ebit > 0 and ev_ebit_range:
        lo_ev = ebit * ev_ebit_range[0]
        hi_ev = ebit * ev_ebit_range[1]
        lo_equity = lo_ev - nd
        hi_equity = hi_ev - nd
        results['ev_ebit'] = {
            'multiple_range':  ev_ebit_range,
            'ltm_ebit':        round(ebit, 0),
            'ev_range':        (round(lo_ev, 0), round(hi_ev, 0)),
            'equity_range':    (round(lo_equity, 0), round(hi_equity, 0)),
        }

    if ni and ni > 0 and per_range:
        lo_eq = ni * per_range[0]
        hi_eq = ni * per_range[1]
        results['per'] = {
            'multiple_range': per_range,
            'ltm_ni':         round(ni, 0),
            'equity_range':   (round(lo_eq, 0), round(hi_eq, 0)),
        }

    # 현재 주가 프리미엄/디스카운트
    if mktcap:
        for method, v in results.items():
            lo, hi = v['equity_range']
            mid = (lo + hi) / 2
            updown = round((mid / mktcap - 1) * 100, 1)
            v['current_mktcap'] = round(mktcap, 0)
            v['upside_midpoint'] = updown

    return results


# ════════════════════════════════════════════════════════════
# 리포트 생성
# ════════════════════════════════════════════════════════════

def generate_report(corp_name, stock_code, fins, mktcap, price, comp_multiples, valuation, induty_code):
    """Markdown 밸류에이션 초안 리포트"""
    now_str = NOW.strftime('%Y-%m-%d %H:%M KST')
    ltm = fins.get('ltm', {})
    fy  = fins.get('annual', {})
    nd  = fins.get('net_debt')
    nd_d = fins.get('nd_detail', {})

    lines = [
        f'# 밸류에이션 초안: {corp_name}',
        f'> 생성: {now_str} | ⚠️ 초안 — 수치 검증 필수, 내부 의사결정용',
        '',
        '---',
        '',
        '## 1. 기본 정보',
        f'- **기업명**: {corp_name}',
        f'- **종목코드**: {stock_code or "비상장"}',
        f'- **현재가**: {price:,}원' if price else '- **현재가**: —',
        f'- **시가총액**: {round(mktcap, 0):,.0f}억원' if mktcap else '- **시가총액**: —',
        '',
        '## 2. 재무 현황 (단위: 억원)',
        '',
        f'| 항목 | {fy.get("period", "FY2024")} | {ltm.get("period", "LTM")} |',
        '| ------ | ------ | ------ |',
    ]

    for label, fy_key, ltm_key in [
        ('매출액',   'revenue',    'revenue'),
        ('영업이익(EBIT)', 'ebit', 'ebit'),
        ('당기순이익', 'net_income', 'net_income'),
        ('D&A',      'da',         'da'),
    ]:
        fy_val  = fy.get(fy_key)
        ltm_val = ltm.get(ltm_key)
        fy_s  = f'{fy_val:,.0f}'  if fy_val  is not None else '—'
        ltm_s = f'{ltm_val:,.0f}' if ltm_val is not None else '—'

        # EBITDA 계산 (EBIT + D&A)
        if fy_key == 'da':
            fy_ebit = fy.get('ebit'); fy_da = fy.get('da')
            ltm_ebit = ltm.get('ebit'); ltm_da = ltm.get('da')
            lines.append(f'| {label} | {fy_s} | {ltm_s} |')
            fy_ebitda_s  = f'{fy_ebit + fy_da:,.0f}'   if (fy_ebit  and fy_da)  else '—'
            ltm_ebitda_s = f'{ltm_ebit + ltm_da:,.0f}' if (ltm_ebit and ltm_da) else '—'
            lines.append(f'| **EBITDA** | **{fy_ebitda_s}** | **{ltm_ebitda_s}** |')
        else:
            lines.append(f'| {label} | {fy_s} | {ltm_s} |')

    lines += [
        '',
        '### 순차입금',
    ]
    if nd is not None:
        lines.append(f'- **순차입금**: {nd:,.0f}억원 ({nd_d.get("bs_period", "")})')
        lines.append(f'  - 총차입금: {nd_d.get("total_debt", 0):,.0f}억원')
        lines.append(f'  - 현금/금융상품: {nd_d.get("total_cash", 0):,.0f}억원')
    else:
        lines.append('- 순차입금: 데이터 없음 (수동 입력 필요)')

    # 비교기업 테이블
    if comp_multiples:
        lines += [
            '',
            '## 3. 비교기업 Trading Multiples',
            '',
            '| 기업명 | LTM 영익(억) | 시총(억) | EV(억) | EV/EBIT | PER | 기준 |',
            '| ------ | ------ | ------ | ------ | ------ | ------ | ------ |',
        ]
        for c in comp_multiples:
            ev_s  = f'{c["ev_ebit"]}x'  if c.get('ev_ebit') else '—'
            per_s = f'{c["per"]}x'      if c.get('per')     else '—'
            ebit_s = f'{c["ltm_ebit"]:,.0f}' if c.get('ltm_ebit') else '—'
            mc_s   = f'{c["mktcap"]:,.0f}'   if c.get('mktcap')   else '—'
            ev_val = f'{c["ev"]:,.0f}'        if c.get('ev')       else '—'
            lines.append(f'| {c["name"]} | {ebit_s} | {mc_s} | {ev_val} | {ev_s} | {per_s} | {c.get("period", "")} |')

        # 중앙값
        evs  = [c['ev_ebit'] for c in comp_multiples if c.get('ev_ebit')]
        pers = [c['per']     for c in comp_multiples if c.get('per')]
        if evs:
            sorted_evs = sorted(evs)
            median_ev = sorted_evs[len(sorted_evs)//2]
            lines.append(f'| **중앙값** | | | | **{median_ev:.1f}x** | | |')

    # 밸류에이션 범위
    lines += ['', '## 4. 밸류에이션 범위 (단위: 억원)', '']

    def updown_str(v):
        pct = v.get('upside_midpoint', 0)
        arrow = 'UP' if pct > 0 else 'DOWN'
        return f'{arrow} {abs(pct):.1f}% (중간값 기준) | |'

    for method, v in valuation.items():
        method_label = {'ev_ebit': 'EV/EBIT', 'per': 'PER'}.get(method, method)
        lo, hi = v['equity_range']
        mid = (lo + hi) / 2
        mult = v['multiple_range']
        lines.append(f'### {method_label} ({mult[0]}x ~ {mult[1]}x)')

        if method == 'ev_ebit':
            ltm_ebit = v.get('ltm_ebit', 0)
            ev_lo, ev_hi = v.get('ev_range', (0, 0))
            lines += [
                f'| 항목 | 하단 | 상단 |',
                f'| ------ | ------ | ------ |',
                f'| LTM EBIT | {ltm_ebit:,.0f} | {ltm_ebit:,.0f} |',
                f'| EV | {ev_lo:,.0f} | {ev_hi:,.0f} |',
                f'| (-)순차입금 | {nd:,.0f} | {nd:,.0f} |' if nd is not None else '| (-)순차입금 | — | — |',
                f'| **Equity Value** | **{lo:,.0f}** | **{hi:,.0f}** |',
                f'| 현재 시총 대비 | {updown_str(v)} |',
                '',
            ]
        else:
            ni = v.get('ltm_ni', 0)
            lines += [
                f'| 항목 | 하단 | 상단 |',
                f'| ------ | ------ | ------ |',
                f'| LTM 당기순이익 | {ni:,.0f} | {ni:,.0f} |',
                f'| **Equity Value** | **{lo:,.0f}** | **{hi:,.0f}** |',
                f'| 현재 시총 대비 | {updown_str(v)} |',
                '',
            ]

    # 시사점
    lines += [
        '## 5. 주요 가정 & 유의사항',
        '',
        f'- 재무 데이터: DART 공개 재무제표 ({ltm.get("period", "LTM")} 기준)',
        '- D&A 누락 시 EBITDA 산출 불가 → EBIT 기준으로만 산출',
        '- 비상장사: 시가총액 없음 → EV/EBIT 범위만 참고',
        '- 멀티플 기준: ' + ('비교기업 실측값' if comp_multiples else '업종 기준값 (주관적)'),
        '',
        '> ⚠️ 본 초안은 공개 데이터 기반 자동 산출물입니다.',
        '> 공식 보고서 사용 전 재무팀 또는 선임과 수치 검증 필수.',
    ]

    return '\n'.join(lines)


# ════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print('사용법: python3 valuation_auto.py <기업명 or 종목코드> [--comps 비교기업1 비교기업2 ...]')
        sys.exit(1)

    target = sys.argv[1]

    # --comps 파싱
    comp_names = []
    if '--comps' in sys.argv:
        idx = sys.argv.index('--comps')
        comp_names = sys.argv[idx + 1:]

    print(f'[{NOW.strftime("%Y-%m-%d %H:%M")}] 밸류에이션 초안 자동화: {target}')

    # 기업 코드 조회
    corp_code, corp_name, stock_code, induty_code, corp_cls = find_corp_code(target)
    if not corp_code:
        print(f'  오류: [{target}] DART 코드 없음. 종목코드나 정확한 기업명으로 재시도하세요.')
        sys.exit(1)
    print(f'  기업: {corp_name} | 코드: {corp_code} | 종목: {stock_code or "비상장"}')

    # 재무 데이터
    fins = fetch_financials(corp_code)
    ltm = fins.get('ltm', {})
    print(f'  재무 기준: {ltm.get("period", "?")} | EBIT: {ltm.get("ebit"):,.0f}억' if ltm.get('ebit') else '  재무: EBIT 없음')

    # 시가총액
    price, mktcap = fetch_market_cap(stock_code)
    if mktcap:
        print(f'  시가총액: {mktcap:,.0f}억 ({price:,}원)')

    # 비교기업
    comp_multiples = []
    if comp_names:
        print(f'  비교기업 {len(comp_names)}개 수집 중...')
        comp_multiples = fetch_comp_multiples(comp_names)

    # 밸류에이션
    valuation = calc_valuation_range(fins, mktcap, comp_multiples, induty_code)

    # 리포트
    report = generate_report(corp_name, stock_code, fins, mktcap, price, comp_multiples, valuation, induty_code)

    # 저장
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = ''.join(c for c in corp_name if c.isalnum() or c in '_-')
    out_path = DATA_DIR / f'valuation_{safe_name}_{NOW.strftime("%Y%m%d")}.md'
    out_path.write_text(report, encoding='utf-8')
    print(f'\n  저장: {out_path}')
    print('\n' + '='*60)
    print(report)


if __name__ == '__main__':
    main()
