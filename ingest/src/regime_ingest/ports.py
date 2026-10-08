"""포트: 유스케이스(collect)가 인프라 구현 대신 의존하는 좁은 경계 (R1, 계약 docs/P3_수집_계약.md §2).

구현은 sources/opendart.py(OpenDartList)·store.py(Store), 테스트는 같은 구현에 가짜 fetch·임시 폴더를 넣는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Protocol

from regime_ingest.dedup import Seen


class DartError(RuntimeError):
    """재시도해도 안 되는 오류 (키 오류·잘못된 요청·응답 형식 위반 등). 서버 문구는 담지 않는다(키·개인정보 혼입 방지)."""

    def __init__(self, status: str, detail: str = ""):
        super().__init__(f"OpenDART {status}" + (f": {detail}" if detail else ""))
        self.status = status


class RateLimited(DartError):
    """오류 020·HTTP 429 또는 하루 요청 상한 도달 → 그 창을 gap 으로 두고 멈춘다."""


class Incomplete(DartError):
    """페이지를 끝까지 받았지만 건수·고유 접수번호·total_count 가 맞지 않음 (수집 중 목록 변동 등)."""


class SourceLocked(RuntimeError):
    """같은 출처 수집이 이미 실행 중 (이 경우만 skipped)."""


class ListClient(Protocol):
    requests: int

    def pages(self, bgn: date, end: date, corp_cls: str) -> Iterator[tuple[int, dict]]: ...


class DocStore(Protocol):
    def lock(self, source: str) -> Path: ...
    def unlock(self, p: Path) -> None: ...
    def save_raw(self, source: str, day: str, run_id: str, records: list[dict]) -> None: ...
    def stage_docs(self, source: str, run_id: str, docs: list[dict]) -> list[Path]: ...
    def publish(self, paths: list[Path]) -> None: ...
    def discard(self, paths: list[Path]) -> None: ...
    def seen(self, doc_ids: list[str]) -> dict[str, Seen]: ...
    def mark_seen(self, docs: list[dict]) -> None: ...
    def report_history(self, keys: set[tuple[str, str]]) -> dict: ...
    def add_report_history(self, rows: list) -> None: ...
    def set_coverage(self, source: str, days: list[str], corp_cls: str, state: str, run_id: str,
                     observed_at: str) -> None: ...
    def coverage(self, source: str) -> dict[tuple[str, str], str]: ...
    def set_cursor(self, source: str, mode: str, value: str) -> None: ...
    def requests_on(self, source: str, day: str) -> int: ...
    def set_requests(self, source: str, day: str, n: int) -> None: ...
    def save_run(self, run: dict) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class NewsRequestError(RuntimeError):
    """Sanitized transport failure; server bodies, URLs and credentials never stored."""
    def __init__(self, code: str, *, retryable: bool = False, retry_after: float = 0):
        super().__init__(code)
        self.code, self.retryable, self.retry_after = code, retryable, retry_after


class NewsStore(DocStore, Protocol):
    def read_docs(self, source: str) -> list[dict]: ...
    def cursor(self, source: str, mode: str) -> str | None: ...
