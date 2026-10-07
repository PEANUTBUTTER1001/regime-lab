"""OpenDART 공시검색(list.json) 어댑터 (인프라). 네트워크 호출은 주입받은 fetch 로만 한다 → 테스트는 기록 응답 재생.

응답 상태: 000 정상, 013 조회 데이터 없음, 020 요청 제한 초과, 그 밖은 오류.
인증키는 요청 인자로만 쓰고, 오류 문구·기록에 남기지 않는다.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date

OK, NO_DATA, RATE_LIMITED = "000", "013", "020"
TRANSIENT = {"800", "900"}  # 시스템 점검·정의되지 않은 오류 → 재시도

Fetch = Callable[[dict], dict]


class DartError(RuntimeError):
    """재시도해도 안 되는 오류 (키 오류·잘못된 요청 등)."""

    def __init__(self, status: str, message: str):
        super().__init__(f"OpenDART {status}: {message}")
        self.status = status


class RateLimited(DartError):
    """오류 020 또는 하루 요청 상한 도달 → 커서를 남기고 멈춘다."""


def mask(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "***")
        text = text.replace(urllib.parse.quote(secret), "***")
    return text


def http_fetch(base_url: str, api_key: str, user_agent: str, timeout: float) -> Fetch:
    """실제 HTTP 호출 (조립에서만 만든다). 반환 JSON 에는 키가 없다."""

    def fetch(params: dict) -> dict:
        q = urllib.parse.urlencode({"crtfc_key": api_key, **params})
        req = urllib.request.Request(f"{base_url}?{q}", headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 키가 URL 에 있으므로 예외 문구를 가린다
            raise OSError(mask(str(e), api_key)) from None

    return fetch


class OpenDartList:
    """하루 요청 수·간격·재시도를 지키며 공시 목록을 끝 페이지까지 받는다."""

    def __init__(self, fetch: Fetch, cfg: dict, *, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic, requests_today: int = 0):
        self.fetch, self.cfg, self.sleep, self.monotonic = fetch, cfg, sleep, monotonic
        self.requests = requests_today
        self._last: float | None = None

    def _call(self, params: dict) -> dict:
        if self.requests >= int(self.cfg["daily_request_limit"]):
            raise RateLimited("limit", "하루 요청 상한 도달 (daily_request_limit)")
        for attempt in range(int(self.cfg["max_retries"]) + 1):
            if self._last is not None:
                wait = float(self.cfg["request_interval_sec"]) - (self.monotonic() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.monotonic()
            self.requests += 1
            try:
                body = self.fetch(params)
            except OSError:
                body = {"status": "network", "message": "네트워크 오류"}
            status = str(body.get("status", ""))
            if status in (OK, NO_DATA):
                return body
            if status == RATE_LIMITED:
                raise RateLimited(status, str(body.get("message", "")))
            if status not in TRANSIENT | {"network"} or attempt == int(self.cfg["max_retries"]):
                raise DartError(status, str(body.get("message", "")))
            self.sleep(float(self.cfg["backoff_base_sec"]) * 2 ** attempt)
        raise AssertionError("unreachable")

    def pages(self, bgn: date, end: date, corp_cls: str):
        """(page_no, 원본 응답) 을 끝 페이지까지 낸다. 데이터 없음(013)이면 빈 목록 한 번."""
        page = 1
        while True:
            body = self._call({"bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                               "corp_cls": corp_cls, "page_no": page, "page_count": int(self.cfg["page_count"])})
            if str(body.get("status")) == NO_DATA:
                yield page, {**body, "list": []}
                return
            yield page, body
            if page >= int(body.get("total_page") or 1):
                return
            page += 1
