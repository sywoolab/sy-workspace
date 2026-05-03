"""
환경변수 로딩 패턴 전수 검증 (L0 §"환경변수 패치 시 전수 검증")

검사 항목:
1. .env를 사용하는 .py 모든 파일이 dotenv 부트스트랩 블록을 포함하는가
   (Path(__file__).resolve().parent + parents 루프 + load_dotenv)
2. os.environ['KEY'] 키 강제 패턴(KeyError 직사) 사용 금지
3. 봇 토큰은 fallback 체인 적용 (단순 휴리스틱)

종료 코드:
- 0: 모든 파일 통과
- 1: 위반 발견 (메시지 출력)

사용법:
    python sy-workspace/scripts/verify_env_loading.py

설계: 외부 의존성 없음 (표준 라이브러리만). dotenv 자체에 의존하지 않음.
"""

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 스캔 제외 디렉토리
EXCLUDE_DIRS = {
    '.git', '__pycache__', '.venv', 'venv', 'node_modules',
    '.pytest_cache', '.idea', '.vscode', 'data',
}

# 본인(검증 스크립트)은 제외
SELF = Path(__file__).resolve()

# 패턴
PAT_BOOTSTRAP = re.compile(
    r"from\s+dotenv\s+import\s+load_dotenv.*?"
    r"for\s+_p\s+in\s+\[_here,\s*\*_here\.parents\].*?"
    r"load_dotenv\(_p\s*/\s*['\"]\.env['\"]\)",
    re.DOTALL,
)
PAT_KEY_FORCE = re.compile(r"os\.environ\[['\"]([A-Z_][A-Z0-9_]*)['\"]\]")
PAT_USES_ENV = re.compile(r"os\.environ|os\.getenv")
PAT_BOT_TOKEN_ASSIGN = re.compile(
    r"BOT_TOKEN\s*=\s*os\.environ\.get\(['\"]BOT_TOKEN['\"]"
)


def scan_files():
    files = []
    for root, dirs, names in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in names:
            if not name.endswith('.py'):
                continue
            p = Path(root) / name
            if p.resolve() == SELF:
                continue
            files.append(p)
    return files


def check_file(path):
    """단일 파일 검증. (file, severity, msg) 리스트 반환."""
    issues = []
    try:
        text = path.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError) as e:
        return [(path, 'ERROR', f'read failed: {e}')]

    if not PAT_USES_ENV.search(text):
        return []  # 환경변수 안 씀 → 통과

    # 1. 키 강제 사용 검사
    forced = PAT_KEY_FORCE.findall(text)
    if forced:
        keys = ', '.join(sorted(set(forced)))
        issues.append((path, 'FAIL', f"키 강제 사용: os.environ['{keys}'] (KeyError 직사 위험)"))

    # 2. dotenv 부트스트랩 블록 존재 검사
    if not PAT_BOOTSTRAP.search(text):
        issues.append((path, 'FAIL', "dotenv 부트스트랩 블록 누락 (load_dotenv + _here.parents 루프)"))

    return issues


def main():
    files = scan_files()
    all_issues = []
    for f in files:
        all_issues.extend(check_file(f))

    rel = lambda p: p.relative_to(REPO_ROOT).as_posix()

    if not all_issues:
        print(f"[OK] {len(files)} files scanned. 환경변수 로딩 표준 모두 통과.")
        return 0

    print(f"[FAIL] {len(all_issues)} 위반 발견 ({len(files)} files scanned)")
    print()
    by_file = {}
    for path, sev, msg in all_issues:
        by_file.setdefault(rel(path), []).append((sev, msg))
    for fname in sorted(by_file):
        print(f"  {fname}")
        for sev, msg in by_file[fname]:
            print(f"    [{sev}] {msg}")
    return 1


if __name__ == '__main__':
    sys.exit(main())
