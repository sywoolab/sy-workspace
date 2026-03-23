"""
서울 법정동 278개의 대표 좌표(위경도)를 확보하여 JSON 파일로 저장하는 스크립트.

데이터 소스:
  - southkorea/seoul-maps (GitHub): 서울 법정동 경계 GeoJSON
    https://github.com/southkorea/seoul-maps
  - 각 Polygon의 centroid(무게중심)를 대표 좌표로 사용

출력:
  /Users/sywoo/sy-workspace/realestate/data/dong_coords.json
"""

import json
import glob
import os
import urllib.request
from collections import defaultdict

import pandas as pd
from shapely.geometry import shape

# ─────────────────────────────────────────────
# 1. 실거래 CSV에서 278개 법정동 목록 추출
# ─────────────────────────────────────────────
TRADE_DIR = "/Users/sywoo/sy-workspace/realestate/data/trade"
OUTPUT_PATH = "/Users/sywoo/sy-workspace/realestate/data/dong_coords.json"

# 시군구코드 → 구명 매핑 (파일명에서 추출)
sgg_code_to_gu = {}
target_dongs = set()  # "구명_동명" 형태

for fpath in sorted(glob.glob(os.path.join(TRADE_DIR, "*.csv"))):
    fname = os.path.basename(fpath)
    # 파일명 형식: 11110_종로구.csv
    parts = fname.replace(".csv", "").split("_", 1)
    sgg_code = int(parts[0])
    gu_name = parts[1]
    sgg_code_to_gu[sgg_code] = gu_name

    df = pd.read_csv(fpath, usecols=["법정동"])
    for dong in df["법정동"].dropna().unique():
        dong = dong.strip()
        target_dongs.add(f"{gu_name}_{dong}")

print(f"[1] 실거래 데이터에서 추출한 법정동: {len(target_dongs)}개")

# ─────────────────────────────────────────────
# 2. 서울 법정동 경계 GeoJSON 다운로드
# ─────────────────────────────────────────────
GEOJSON_URL = (
    "https://raw.githubusercontent.com/southkorea/seoul-maps/"
    "master/juso/2015/json/seoul_neighborhoods_geo.json"
)

print("[2] 법정동 경계 GeoJSON 다운로드 중...")
with urllib.request.urlopen(GEOJSON_URL) as resp:
    geojson_data = json.loads(resp.read().decode("utf-8"))

features = geojson_data["features"]
print(f"    다운로드 완료: {len(features)}개 피처")

# ─────────────────────────────────────────────
# 3. EMD_CD 앞 5자리 → 구명 매핑 구축
# ─────────────────────────────────────────────
# EMD_CD: 8자리 (시군구5자리 + 동3자리)
emd_sgg_to_gu = {}
for sgg_code, gu_name in sgg_code_to_gu.items():
    emd_sgg_to_gu[str(sgg_code)] = gu_name

# ─────────────────────────────────────────────
# 4. GeoJSON에서 centroid 계산 → 구_동 매핑
# ─────────────────────────────────────────────
print("[3] Centroid 계산 중...")

# GeoJSON의 동명 → centroid 매핑
# 동일한 구_동이 여러 Polygon일 수 있으므로 (MultiPolygon 등) 모두 모아서 처리
geo_coords = {}  # "구명_동명" → {"lat": ..., "lon": ...}

# 같은 구_동에 여러 피처가 있을 수 있음 (분리된 영역)
dong_geometries = defaultdict(list)

for feat in features:
    props = feat["properties"]
    emd_cd = props["EMD_CD"]
    dong_name = props["EMD_KOR_NM"]
    sgg_prefix = emd_cd[:5]

    gu_name = emd_sgg_to_gu.get(sgg_prefix)
    if gu_name is None:
        # 혹시 매핑에 없는 시군구코드면 스킵
        continue

    key = f"{gu_name}_{dong_name}"
    geom = shape(feat["geometry"])
    dong_geometries[key].append(geom)

# 각 구_동의 centroid 계산 (면적 가중 평균)
for key, geoms in dong_geometries.items():
    if len(geoms) == 1:
        centroid = geoms[0].centroid
    else:
        # 면적 가중 평균 centroid
        total_area = sum(g.area for g in geoms)
        if total_area == 0:
            centroid = geoms[0].centroid
        else:
            cx = sum(g.centroid.x * g.area for g in geoms) / total_area
            cy = sum(g.centroid.y * g.area for g in geoms) / total_area
            from shapely.geometry import Point
            centroid = Point(cx, cy)

    geo_coords[key] = {
        "lat": round(centroid.y, 6),
        "lon": round(centroid.x, 6),
    }

print(f"    GeoJSON에서 추출한 구_동: {len(geo_coords)}개")

# ─────────────────────────────────────────────
# 5. 278개 대상 법정동과 매칭
# ─────────────────────────────────────────────
print("[4] 매칭 진행 중...")

result = {}
matched = set()
unmatched = set()

for dong_key in sorted(target_dongs):
    if dong_key in geo_coords:
        result[dong_key] = geo_coords[dong_key]
        matched.add(dong_key)
    else:
        unmatched.add(dong_key)

print(f"    직접 매칭 성공: {len(matched)}개")
print(f"    직접 매칭 실패: {len(unmatched)}개")

# ─────────────────────────────────────────────
# 6. 매칭 실패 처리: 유사 이름 검색
# ─────────────────────────────────────────────
if unmatched:
    print("\n[5] 매칭 실패 법정동 유사 이름 검색...")

    still_unmatched = set()

    for dong_key in sorted(unmatched):
        gu, dong = dong_key.split("_", 1)

        # 전략 1: 숫자 제거하여 검색 (예: "상도1동" → "상도동")
        import re
        base_dong = re.sub(r"\d+", "", dong)
        base_key = f"{gu}_{base_dong}"
        if base_key in geo_coords:
            result[dong_key] = geo_coords[base_key]
            print(f"    [유사매칭] {dong_key} → {base_key}")
            continue

        # 전략 2: "N가" 제거 (예: "금호동1가" → "금호동")
        stripped = re.sub(r"\d+가$", "", dong)
        if stripped != dong:
            stripped_key = f"{gu}_{stripped}"
            if stripped_key in geo_coords:
                result[dong_key] = geo_coords[stripped_key]
                print(f"    [유사매칭] {dong_key} → {stripped_key}")
                continue

        # 전략 3: "N가" → 숫자만 제거 (예: "당산동1가" → "당산동")
        stripped2 = re.sub(r"\d+가$", "", dong)
        stripped2 = re.sub(r"\d+$", "", stripped2)
        if stripped2 != dong:
            stripped2_key = f"{gu}_{stripped2}"
            if stripped2_key in geo_coords:
                result[dong_key] = geo_coords[stripped2_key]
                print(f"    [유사매칭] {dong_key} → {stripped2_key}")
                continue

        # 전략 4: 같은 구 내 부분 문자열 매칭
        found = False
        for geo_key in geo_coords:
            if geo_key.startswith(f"{gu}_"):
                geo_dong = geo_key.split("_", 1)[1]
                # 동 이름의 기본 부분이 일치하는지 확인
                dong_base = re.sub(r"[\d가]+$", "", dong)
                geo_base = re.sub(r"[\d가]+$", "", geo_dong)
                if dong_base == geo_base and dong_base:
                    result[dong_key] = geo_coords[geo_key]
                    print(f"    [유사매칭] {dong_key} → {geo_key}")
                    found = True
                    break
        if found:
            continue

        still_unmatched.add(dong_key)

    # ─────────────────────────────────────────
    # 7. 최종 실패 → 해당 구의 구청 좌표(구 centroid) fallback
    # ─────────────────────────────────────────
    if still_unmatched:
        print(f"\n[6] 최종 매칭 실패: {len(still_unmatched)}개 → 구 centroid fallback")

        # 구별 centroid 계산 (해당 구의 모든 동 좌표 평균)
        gu_centroids = defaultdict(list)
        for key, coord in geo_coords.items():
            gu = key.split("_", 1)[0]
            gu_centroids[gu].append(coord)

        gu_avg = {}
        for gu, coords in gu_centroids.items():
            avg_lat = round(sum(c["lat"] for c in coords) / len(coords), 6)
            avg_lon = round(sum(c["lon"] for c in coords) / len(coords), 6)
            gu_avg[gu] = {"lat": avg_lat, "lon": avg_lon}

        for dong_key in sorted(still_unmatched):
            gu = dong_key.split("_", 1)[0]
            if gu in gu_avg:
                result[dong_key] = gu_avg[gu]
                print(f"    [fallback] {dong_key} → {gu} 구 centroid: {gu_avg[gu]}")
            else:
                print(f"    [실패] {dong_key} → 구 centroid도 없음!")

# ─────────────────────────────────────────────
# 8. 결과 저장
# ─────────────────────────────────────────────
# 키 정렬하여 저장
sorted_result = dict(sorted(result.items()))

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(sorted_result, f, ensure_ascii=False, indent=2)

print(f"\n[완료] {len(sorted_result)}개 법정동 좌표 저장")
print(f"  → {OUTPUT_PATH}")

# 검증: 278개 모두 있는지
missing = target_dongs - set(sorted_result.keys())
if missing:
    print(f"\n[경고] 누락된 법정동: {len(missing)}개")
    for m in sorted(missing):
        print(f"  - {m}")
else:
    print("\n[검증] 278개 법정동 모두 좌표 확보 완료!")
