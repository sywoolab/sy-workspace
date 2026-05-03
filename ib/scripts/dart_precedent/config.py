"""프리시던트 DB 설정 — 카테고리 정의, API 매핑"""

import os
from pathlib import Path

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

DART_API_KEY = os.environ.get('DART_API_KEY', '')
BASE_URL = 'https://opendart.fss.or.kr/api'

# report_nm 패턴 → 카테고리 분류
# 순서 중요: 먼저 매칭되는 것 우선
CATEGORY_RULES = [
    # 자사주
    ('treasury_disposal', ['자기주식처분결정', '자사주처분']),
    ('treasury_acquire', ['자기주식취득결정', '자사주취득']),
    # 주식연계증권
    ('cb', ['전환사채권발행결정']),
    ('bw', ['신주인수권부사채권발행결정']),
    ('eb', ['교환사채권발행결정']),
    # 유상증자
    ('rights_offering', ['유상증자결정']),
    # M&A
    ('ma_merge', ['합병결정', '주요사항보고서(합병']),
    ('ma_split', ['분할결정', '주요사항보고서(분할']),
    ('ma_share_exchange', ['주식교환', '주식이전']),
    ('ma_acquisition', ['타법인주식및출자증권취득결정']),
    ('ma_business_transfer', ['영업양수', '영업양도']),
    # IPO
    ('ipo', ['증권신고서(지분증권)']),
    # 블록딜 (지분공시)
    ('block_deal', ['주식등의대량보유상황보고서']),
]

# Sheet 그룹 매핑
SHEET_GROUPS = {
    '1_자사주처분': ['treasury_disposal', 'treasury_acquire'],
    '2_유상증자': ['rights_offering'],
    '3_주식연계증권': ['cb', 'bw', 'eb'],
    '4_블록딜': ['block_deal'],
    '5_IPO': ['ipo'],
    '6_MA': ['ma_merge', 'ma_split', 'ma_share_exchange',
             'ma_acquisition', 'ma_business_transfer'],
}

# 구조화 API 엔드포인트 매핑
DETAIL_API = {
    'treasury_disposal': 'tsstkDpDecsn.json',
    'treasury_acquire': 'tsstkAqDecsn.json',
    'rights_offering': 'piicDecsn.json',
    'cb': 'cvbdIsDecsn.json',
    'bw': 'bdwtIsDecsn.json',
    'eb': 'exbdIsDecsn.json',
    'ma_merge': 'cmpgDecsn.json',
    'ma_split': 'dvrsDecsn.json',
}

# 공시유형 코드
PBLNTF_TYPES = {
    'B': '주요사항보고',  # 자사주, 증자, CB/EB, M&A
    'C': '발행공시',      # IPO
    'D': '지분공시',      # 블록딜
}

# 자사주 처분 세부 분류
TREASURY_SUB_CATEGORIES = {
    'eb': ['교환사채', '교환'],
    'direct_sale': ['장내매도', '시간외대량', '블록딜', '장내처분'],
    'prs': ['주가연계', 'PRS', '주가수익스왑'],
    'employee_comp': ['임직원', '우리사주', '스톡옵션', '성과보상', '복리후생'],
}
