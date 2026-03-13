"""DART API 클라이언트 — Rate limit 관리 포함"""

import time
import requests
from collections import deque
from .config import BASE_URL


class DARTClient:
    def __init__(self, api_key: str, rpm: int = 95):
        self.api_key = api_key
        self.rpm = rpm
        self._timestamps = deque()
        self._call_count = 0

    def _throttle(self):
        now = time.time()
        while self._timestamps and now - self._timestamps[0] > 60:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.rpm:
            wait = 60 - (now - self._timestamps[0]) + 0.1
            if wait > 0:
                print(f"    [rate limit] {wait:.1f}s 대기...")
                time.sleep(wait)
        self._timestamps.append(time.time())
        self._call_count += 1

    def get(self, endpoint: str, params: dict = None, retries: int = 2) -> dict:
        self._throttle()
        if params is None:
            params = {}
        params['crtfc_key'] = self.api_key
        url = f"{BASE_URL}/{endpoint}"

        for attempt in range(retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=30)
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                print(f"    [네트워크 오류] {endpoint}: {e}")
                return {'status': '999', 'message': str(e)}

            status = data.get('status', '')
            if status == '020' and attempt < retries:
                # 요청 제한 초과 → 대기 후 재시도
                time.sleep(60)
                continue
            if status not in ('000', '013'):
                msg = data.get('message', '')
                print(f"    [API 오류] {endpoint}: {status} {msg}")
            return data

        return {'status': '999', 'message': 'max retries exceeded'}

    @property
    def call_count(self):
        return self._call_count
