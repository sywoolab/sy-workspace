"""부동산 SSOT 변경 감지 알림 (수동 입력 흐름).

realestate-brief.yml에서 push 시 호출. git HEAD~1과 HEAD를 비교해
사용자(또는 메인 에이전트)가 직접 수정한 SSOT 변경분을 즉시 텔레로 알림.

대상 SSOT (수동 수정 가능):
- realestate/data/market_config.json   (금리/매수상한/전략)
- realestate/data/watchlist_summary.json  (관심 단지)
- realestate/data/chungyak/registry.json  (청약 단지)

scored_all.csv 등 자동 수집 산출물은 의도적으로 제외 (cron이 주간 갱신).

운동 notify_log_change.py와 동일 패턴. realestate-brief.yml의 re_brief.py
실행 이전에 호출 → diff 알림이 먼저 가고, HTML 갱신 링크가 뒤따른다.
"""
import os
import sys
import json
import subprocess
from pathlib import Path

# 크로스플랫폼 한글 출력
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

# L0 §"환경변수 부트스트랩": .env 자동 로드
try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, *_here.parents]:
        if (_p / '.env').exists():
            load_dotenv(_p / '.env')
            break
except ImportError:
    pass

import requests

BASE_DIR = Path(__file__).resolve().parent.parent  # sy-workspace/realestate
REPO_ROOT = BASE_DIR.parent

TRACKED = {
    'market_config': 'realestate/data/market_config.json',
    'watchlist':     'realestate/data/watchlist_summary.json',
    'chungyak':      'realestate/data/chungyak/registry.json',
}

BOT_TOKEN = (
    os.environ.get('BOT_TOKEN')
    or os.environ.get('REALESTATE_BOT_TOKEN')
    or os.environ.get('TELEGRAM_BOT_TOKEN', '')
)
CHAT_ID = os.environ.get('CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')


def _git_show(ref, rel_path):
    r = subprocess.run(
        ['git', 'show', f'{ref}:{rel_path}'],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=15,
        encoding='utf-8', errors='replace',
    )
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _diff_market_config(prev, head):
    """rates/budget/strategy/market_index 키 단위 변경분."""
    lines = []
    sections = ['rates', 'budget', 'strategy', 'market_index']
    for sec in sections:
        p = (prev or {}).get(sec) or {}
        h = (head or {}).get(sec) or {}
        changed = []
        for k in sorted(set(p.keys()) | set(h.keys())):
            if k.startswith('_'):  # _meta 등 메타 제외
                continue
            pv = p.get(k)
            hv = h.get(k)
            if json.dumps(pv, sort_keys=True, ensure_ascii=False) != json.dumps(hv, sort_keys=True, ensure_ascii=False):
                # value가 dict면 value 또는 current_rec 등 핵심만 추출
                def _short(v):
                    if isinstance(v, dict):
                        for key in ('value', 'current_rec', 'cash_avail', 'max_purchase', 'seoul_jeonse_rate'):
                            if key in v:
                                return v[key]
                        return json.dumps(v, ensure_ascii=False)[:60]
                    return v
                changed.append(f'    {k}: {_short(pv)} → {_short(hv)}')
        if changed:
            lines.append(f'  [{sec}]')
            lines.extend(changed)
    return lines


def _diff_watchlist(prev, head):
    """complexes[] entry name+area 단위 추가/삭제/수정 + updated/_meta 변경."""
    def _idx(d):
        items = (d or {}).get('complexes', []) if isinstance(d, dict) else []
        return {f"{c.get('name','?')}|{c.get('area','?')}": c for c in items if isinstance(c, dict)}
    p_idx = _idx(prev)
    h_idx = _idx(head)
    added   = sorted(set(h_idx) - set(p_idx))
    deleted = sorted(set(p_idx) - set(h_idx))
    modified = []
    for k in sorted(set(p_idx) & set(h_idx)):
        if json.dumps(p_idx[k], sort_keys=True, ensure_ascii=False) != json.dumps(h_idx[k], sort_keys=True, ensure_ascii=False):
            modified.append(k)

    def _summary(c):
        price = c.get('price_latest', '?')
        gap = c.get('gap_pct')
        gap_s = f' ({gap:+.1f}%)' if isinstance(gap, (int, float)) else ''
        return f'{price}억{gap_s}'

    lines = []
    # complexes 외 top-level 키(updated 등) 변경 감지
    def _meta_only(d):
        if not isinstance(d, dict):
            return None
        return {k: v for k, v in d.items() if k != 'complexes'}
    if json.dumps(_meta_only(prev), sort_keys=True, ensure_ascii=False) != \
       json.dumps(_meta_only(head), sort_keys=True, ensure_ascii=False):
        lines.append('  ⚙️ top-level(updated 등) 변경')
    if added:
        lines.append(f'  ➕ 추가 {len(added)}건:')
        for k in added[:5]:
            lines.append(f'    {k} — {_summary(h_idx[k])}')
    if modified:
        lines.append(f'  ✏️ 수정 {len(modified)}건:')
        for k in modified[:5]:
            lines.append(f'    {k} — {_summary(h_idx[k])}')
    if deleted:
        lines.append(f'  🗑️ 삭제 {len(deleted)}건 (⚠️ 의도 확인):')
        for k in deleted[:5]:
            lines.append(f'    {k} — {_summary(p_idx[k])}')
    return lines


def _diff_chungyak(prev, head):
    """listings[] entry id 단위 추가/삭제/수정 + _meta 변경 감지."""
    def _idx(d):
        items = (d or {}).get('listings', []) if isinstance(d, dict) else []
        # id 우선, 없으면 name|type 조합 (위치 인덱스 fallback 금지 — 인서트 시 false-positive 폭증)
        return {
            (item.get('id') or f"{item.get('name','?')}|{item.get('type','?')}"): item
            for item in items if isinstance(item, dict)
        }
    p_idx = _idx(prev)
    h_idx = _idx(head)
    added   = sorted(set(h_idx) - set(p_idx))
    deleted = sorted(set(p_idx) - set(h_idx))
    modified = []
    for k in sorted(set(p_idx) & set(h_idx)):
        if json.dumps(p_idx[k], sort_keys=True, ensure_ascii=False) != json.dumps(h_idx[k], sort_keys=True, ensure_ascii=False):
            modified.append(k)

    def _summary(it):
        name = it.get('name', '?')
        typ = it.get('type', '?')
        return f'{name} [{typ}]'

    lines = []
    # _meta 블록 변경 감지 (user_profile, income_table 등 의사결정 메타)
    if json.dumps((prev or {}).get('_meta'), sort_keys=True, ensure_ascii=False) != \
       json.dumps((head or {}).get('_meta'), sort_keys=True, ensure_ascii=False):
        lines.append('  ⚙️ _meta(가구/소득/자격) 변경 — 직접 확인 필요')
    if added:
        lines.append(f'  ➕ 추가 {len(added)}건:')
        for k in added[:5]:
            lines.append(f'    {_summary(h_idx[k])}')
    if modified:
        lines.append(f'  ✏️ 수정 {len(modified)}건:')
        for k in modified[:5]:
            lines.append(f'    {_summary(h_idx[k])}')
    if deleted:
        lines.append(f'  🗑️ 삭제 {len(deleted)}건 (⚠️ 의도 확인):')
        for k in deleted[:5]:
            lines.append(f'    {_summary(p_idx[k])}')
    return lines


DIFF_FN = {
    'market_config': _diff_market_config,
    'watchlist':     _diff_watchlist,
    'chungyak':      _diff_chungyak,
}

LABEL = {
    'market_config': '📊 market_config (시장 설정)',
    'watchlist':     '🏢 watchlist_summary (관심 단지)',
    'chungyak':      '📋 chungyak/registry (청약)',
}


def _send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print('[SKIP] BOT_TOKEN/CHAT_ID 없음')
        print(text)
        return False
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    r = requests.post(url, data={'chat_id': CHAT_ID, 'text': text}, timeout=30)
    return r.json().get('ok', False)


def main():
    sections = []
    any_change = False
    for key, rel_path in TRACKED.items():
        head = _git_show('HEAD', rel_path)
        prev = _git_show('HEAD~1', rel_path)
        if head is None and prev is None:
            continue  # 파일 자체가 없음
        if prev is None:
            # 신규 파일 — 전체를 추가로 보지 않고 한 줄만
            sections.append(f'{LABEL[key]}')
            sections.append('  ➕ 신규 파일 추가')
            any_change = True
            continue
        if head is None:
            sections.append(f'{LABEL[key]}')
            sections.append('  🗑️ 파일 삭제 (⚠️ 의도 확인)')
            any_change = True
            continue
        diff_lines = DIFF_FN[key](prev, head)
        if diff_lines:
            sections.append(f'{LABEL[key]}')
            sections.extend(diff_lines)
            any_change = True

    if not any_change:
        print('[SKIP] 부동산 SSOT 변경 없음')
        return 0

    header = '🏠 부동산 SSOT 변경 반영'
    body = '\n'.join([header, '', *sections, '', '📊 HTML 갱신은 곧 별도 발송됩니다.'])

    print(body)
    ok = _send_telegram(body)
    print(f'  텔레그램: {"성공" if ok else "실패"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
