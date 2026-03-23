"""
법정동별 통근시간 산출 스크립트
- ODsay 대중교통 경로검색 API (searchPubTransPathT) 활용
- 입력: dong_coords.json (법정동별 위경도)
- 출력: dong_commute.json (법정동별 3개 목적지 통근시간)

사용법:
    python build_dong_commute.py

NOTE: ODsay API Key는 서버 호출용(Server Key)이어야 합니다.
      Web Key로는 서버에서 호출 시 ApiKeyAuthFailed 오류가 발생합니다.
      https://lab.odsay.com 에서 Server용 키를 발급받으세요.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ── 설정 ─────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"

INPUT_FILE = DATA_DIR / "dong_coords.json"
OUTPUT_FILE = DATA_DIR / "dong_commute.json"
CHECKPOINT_FILE = DATA_DIR / "dong_commute_checkpoint.json"

ODSAY_API_KEY = "m4Dh6izdx7JXmNTBaSnTB0jBbzNpXh0cEaDO7A2qzQM"
ODSAY_ENDPOINT = "https://api.odsay.com/v1/api/searchPubTransPathT"

# 목적지 좌표 (lon, lat)
DESTINATIONS = {
    "yeouido": {"lon": 126.9244, "lat": 37.5219, "label": "여의도역"},
    "cheongye": {"lon": 127.0548, "lat": 37.4454, "label": "청계산입구역"},
    "doklip": {"lon": 126.9416, "lat": 37.5724, "label": "독립문역"},
}

SLEEP_INTERVAL = 0.5       # API 호출 간격 (초)
MAX_RETRIES = 3            # 실패 시 최대 재시도 횟수
RETRY_DELAY = 2.0          # 재시도 대기 시간 (초)
CHECKPOINT_INTERVAL = 100  # 체크포인트 저장 간격 (건)


# ── ODsay API 호출 ───────────────────────────────────

def call_odsay(sx: float, sy: float, ex: float, ey: float) -> dict | None:
    """
    ODsay searchPubTransPathT API 호출.

    Parameters:
        sx: 출발 경도 (longitude)
        sy: 출발 위도 (latitude)
        ex: 도착 경도
        ey: 도착 위도

    Returns:
        API 응답 JSON dict, 실패 시 None
    """
    params = {
        "SX": sx,
        "SY": sy,
        "EX": ex,
        "EY": ey,
        "apiKey": ODSAY_API_KEY,
    }
    query_string = urllib.parse.urlencode(params)
    url = f"{ODSAY_ENDPOINT}?{query_string}"

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ERROR] API 호출 실패: {e}")
        return None


def extract_total_time(response: dict) -> int | None:
    """
    API 응답에서 최소 totalTime(분)을 추출.
    경로가 여러 개 반환되므로 최소 시간을 선택한다.

    응답 구조: result → path[] → info → totalTime
    """
    if not response:
        return None

    # 에러 응답 체크
    if "error" in response:
        error = response["error"]
        if isinstance(error, list) and len(error) > 0:
            print(f"  [API ERROR] {error[0].get('message', 'Unknown error')}")
        return None

    try:
        paths = response["result"]["path"]
        times = []
        for path in paths:
            total_time = path.get("info", {}).get("totalTime")
            if total_time is not None:
                times.append(int(total_time))
        return min(times) if times else None
    except (KeyError, TypeError, ValueError) as e:
        print(f"  [PARSE ERROR] totalTime 추출 실패: {e}")
        return None


def query_commute_time(
    src_lon: float, src_lat: float, dest_key: str
) -> int | None:
    """
    출발지 → 목적지 통근시간 조회 (재시도 포함).

    Returns:
        통근시간(분), 실패 시 None
    """
    dest = DESTINATIONS[dest_key]

    for attempt in range(1, MAX_RETRIES + 1):
        response = call_odsay(src_lon, src_lat, dest["lon"], dest["lat"])
        total_time = extract_total_time(response)

        if total_time is not None:
            return total_time

        if attempt < MAX_RETRIES:
            print(f"  재시도 {attempt}/{MAX_RETRIES} ({dest['label']})")
            time.sleep(RETRY_DELAY)

    print(f"  [FAIL] {dest['label']} 통근시간 조회 실패 (재시도 소진)")
    return None


# ── 체크포인트 관리 ──────────────────────────────────

def load_checkpoint() -> dict:
    """이전 체크포인트 로드. 없으면 빈 dict 반환."""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            print(f"[CHECKPOINT] {len(data)}건 로드됨: {CHECKPOINT_FILE}")
            return data
    return {}


def save_checkpoint(data: dict) -> None:
    """체크포인트 저장."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[CHECKPOINT] {len(data)}건 저장됨")


def save_output(data: dict) -> None:
    """최종 결과 저장."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OUTPUT] {len(data)}건 저장됨: {OUTPUT_FILE}")


# ── 메인 처리 ────────────────────────────────────────

def main():
    # 입력 파일 확인
    if not INPUT_FILE.exists():
        print(f"[ERROR] 입력 파일 없음: {INPUT_FILE}")
        print("dong_coords.json을 먼저 준비하세요.")
        sys.exit(0)  # 예상 가능한 실패 → exit 0

    # 입력 로드
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        dong_coords = json.load(f)

    total = len(dong_coords)
    print(f"[START] {total}개 법정동 × {len(DESTINATIONS)}개 목적지 = {total * len(DESTINATIONS)}건 조회 예정")

    # 체크포인트 로드 (이미 처리된 법정동은 스킵)
    result = load_checkpoint()
    skipped = 0
    processed = 0
    failed_dongs = []

    for idx, (dong_name, coords) in enumerate(dong_coords.items(), start=1):
        # 이미 처리된 법정동 스킵
        if dong_name in result:
            skipped += 1
            continue

        src_lon = coords["lon"]
        src_lat = coords["lat"]

        print(f"[{idx}/{total}] {dong_name} (lon={src_lon}, lat={src_lat})")

        commute = {}
        has_failure = False

        for dest_key in DESTINATIONS:
            total_time = query_commute_time(src_lon, src_lat, dest_key)
            if total_time is not None:
                commute[dest_key] = total_time
                print(f"  → {DESTINATIONS[dest_key]['label']}: {total_time}분")
            else:
                commute[dest_key] = None
                has_failure = True

            time.sleep(SLEEP_INTERVAL)

        result[dong_name] = commute
        processed += 1

        if has_failure:
            failed_dongs.append(dong_name)

        # 체크포인트 저장 (100건마다)
        if processed % CHECKPOINT_INTERVAL == 0:
            save_checkpoint(result)

    # 최종 저장
    save_output(result)

    # 체크포인트 파일 정리 (완료 시 삭제)
    if CHECKPOINT_FILE.exists() and not failed_dongs:
        CHECKPOINT_FILE.unlink()
        print("[CHECKPOINT] 완료 → 체크포인트 파일 삭제")

    # 요약
    print("\n" + "=" * 50)
    print(f"[SUMMARY]")
    print(f"  총 법정동: {total}")
    print(f"  스킵 (이전 처리): {skipped}")
    print(f"  신규 처리: {processed}")
    print(f"  실패 (일부 목적지): {len(failed_dongs)}")
    if failed_dongs:
        print(f"  실패 목록: {', '.join(failed_dongs[:10])}")
        if len(failed_dongs) > 10:
            print(f"    ... 외 {len(failed_dongs) - 10}건")


if __name__ == "__main__":
    main()
