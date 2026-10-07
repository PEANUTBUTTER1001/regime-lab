"""OpenDART 공시검색(list.json) 어댑터 (인프라). 네트워크 호출은 주입받은 fetch 로만 한다 → 테스트는 기록 응답 재생.

응답 상태: 000 정상, 013 조회 데이터 없음(정상 0건은 이것만), 020 요청 제한 초과, 그 밖은 오류.
000 이어도 list·페이지 필드가 없거나 틀리면 실패. 끝 페이지까지 받은 건수·고유 접수번호·total_count 를 대조한다.
인증키는 요청 인자로만 쓰고, 오류 문구·기록에 남기지 않는다. 서버 메시지도 기록하지 않고 상태 코드만 남긴다.
리디렉션은 따라가지 않는다(키가 든 URL 이 다른 호스트로 전달되지 않게).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import urllib.error
from collections.abc import Callable
from datetime import date

from regime_ingest.ports import DartError, Incomplete, RateLimited

OK, NO_DATA, RATE_LIMITED = "000", "013", "020"
TRANSIENT = {"800", "900"}  # 시스템 점검·정의되지 않은 오류 → 재시도

Fetch = Callable[[dict], dict]

__all__ = ["DartError", "Incomplete", "OpenDartList", "RateLimited", "http_fetch", "mask"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 따라가지 않음 → HTTPError 로 실패


def mask(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "***")
        text = text.replace(urllib.parse.quote(secret), "***")
    return text


def http_fetch(base_url: str, api_key: str, user_agent: str, timeout: float) -> Fetch:
    """실제 HTTP 호출 (조립에서만 만든다). 반환 JSON 에는 키가 없다."""

    opener = urllib.request.build_opener(_NoRedirect)

    def fetch(params: dict) -> dict:
        q = urllib.parse.urlencode({"crtfc_key": api_key, **params})
        req = urllib.request.Request(f"{base_url}?{q}", headers={"User-Agent": user_agent})
        try:
            with opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                return {"status": RATE_LIMITED, "http": 429}
            if 300 <= e.code < 400:
                raise DartError(f"http{e.code}", "리디렉션 거부") from None
            raise OSError(f"HTTP {e.code}") from None
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
        for attempt in range(int(self.cfg["max_retries"]) + 1):
            if self.requests >= int(self.cfg["daily_request_limit"]):  # 재시도도 요청 1회로 센다
                raise RateLimited("limit", "하루 요청 상한 도달 (daily_request_limit)")
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
                raise RateLimited(status)
            if status not in TRANSIENT | {"network"} or attempt == int(self.cfg["max_retries"]):
                raise DartError(status or "unknown", "재시도 소진" if status in TRANSIENT | {"network"} else "")
            self.sleep(float(self.cfg["backoff_base_sec"]) * 2 ** attempt)
        raise AssertionError("unreachable")

    def pages(self, bgn: date, end: date, corp_cls: str):
        """(page_no, 원본 응답) 을 끝 페이지까지 낸다. 데이터 없음(013)이면 빈 목록 한 번.

        000 응답은 list(배열)·total_count·total_page·page_no 를 검사하고, 페이지 사이에 total_count·total_page 가
        바뀌거나 끝까지 받은 건수·고유 접수번호 수가 total_count 와 다르면 Incomplete."""
        page, first, received, ids = 1, None, 0, set()
        while True:
            body = self._call({"bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                               "corp_cls": corp_cls, "page_no": page, "page_count": int(self.cfg["page_count"]),
                               "last_reprt_at": "N"})
            if str(body.get("status")) == NO_DATA:
                if page != 1:
                    raise Incomplete("013", f"{page}쪽에서 데이터 없음 (목록 변동)")
                yield page, {**body, "list": []}
                return
            items = body.get("list")
            try:
                tc, tp, pn = int(body["total_count"]), int(body["total_page"]), int(body["page_no"])
            except (KeyError, TypeError, ValueError):
                raise DartError("000", "페이지 필드 없음·형식 오류") from None
            if not isinstance(items, list) or pn != page or tp < 1 or tc < 0:
                raise DartError("000", "list·page_no·total 형식 위반")
            if first is None:
                first = (tc, tp)
            elif (tc, tp) != first:
                raise Incomplete("000", "페이지 사이 total_count·total_page 변동")
            received += len(items)
            ids.update(str(it.get("rcept_no")) for it in items if isinstance(it, dict))
            yield page, body
            if page >= tp:
                if received != tc or len(ids) != tc:
                    raise Incomplete("000", f"건수 불일치 (받음 {received}, 고유 {len(ids)}, total_count {tc})")
                return
            page += 1
