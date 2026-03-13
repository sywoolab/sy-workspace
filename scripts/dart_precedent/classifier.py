"""공시명(report_nm) → 카테고리 분류"""

from .config import CATEGORY_RULES, TREASURY_SUB_CATEGORIES


def classify(report_nm: str) -> tuple:
    """report_nm → (category, sub_category)"""
    for category, keywords in CATEGORY_RULES:
        if any(kw in report_nm for kw in keywords):
            sub = classify_sub(category, report_nm)
            return category, sub
    return None, None


def classify_sub(category: str, report_nm: str) -> str:
    """자사주 처분 등 세부 분류"""
    if category != 'treasury_disposal':
        return None

    for sub, keywords in TREASURY_SUB_CATEGORIES.items():
        if any(kw in report_nm for kw in keywords):
            return sub
    return 'other'
