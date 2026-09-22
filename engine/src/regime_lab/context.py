"""실행 문맥: 단계·진행·취소 알림 (FR-U2, FR-X3, E4).

엔진은 동기 함수로 남고, 호출자(CLI·API)가 문맥 객체를 넘겨 진행을 관찰하거나 취소를 요청한다.
엔진은 서버가 실제로 측정한 값(단계, 처리 수/전체 수)만 알린다. 예상 남은 시간은 만들지 않는다.
"""

from __future__ import annotations

import threading
import time

# FR-U2 단계 (필터 → 로드 → 지표 → 신호·체결 → 국면 결합 → 집계 → 저장)
STAGES = ["filter", "load", "indicators", "signals_execution", "regime_join", "aggregate", "save"]


class RunCancelled(Exception):
    """사용자 취소로 실행을 중단했다. 결과는 남기지 않는다."""


class RunContext:
    """기본 문맥: 아무것도 기록하지 않는다 (CLI 용)."""

    def stage(self, name: str, detail: str | None = None) -> None:
        pass

    def progress(self, done: int, total: int) -> None:
        pass

    def check_cancel(self) -> None:
        pass


class RecordingContext(RunContext):
    """단계·진행을 기록하고 스레드 안전한 취소 요청을 받는 문맥 (API·테스트 용)."""

    def __init__(self, cancel_on_stage: str | None = None):
        self.stages: list[tuple[str, str | None, float]] = []  # (단계, 부가 정보, 시작 경과초)
        self.current: str | None = None
        self.done = 0
        self.total = 0
        self.started = time.time()
        self._cancel = threading.Event()
        self._cancel_on_stage = cancel_on_stage
        self._lock = threading.Lock()

    def stage(self, name: str, detail: str | None = None) -> None:
        if name not in STAGES:
            raise ValueError(f"unknown stage: {name}")
        with self._lock:
            self.stages.append((name, detail, round(time.time() - self.started, 2)))
            self.current, self.done, self.total = name, 0, 0
        if name == self._cancel_on_stage:
            self._cancel.set()
        self.check_cancel()

    def progress(self, done: int, total: int) -> None:
        with self._lock:
            self.done, self.total = int(done), int(total)

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise RunCancelled()

    def stage_log(self) -> list[dict]:
        with self._lock:
            return [{"stage": n, "detail": d, "started_sec": t} for n, d, t in self.stages]

    def snapshot(self) -> dict:
        with self._lock:
            return {"stage": self.current, "stages": STAGES, "processed": self.done, "total": self.total,
                    "elapsed_sec": round(time.time() - self.started, 1)}


NULL_CONTEXT = RunContext()
