"""시점 선택 (docs/P3_수집_계약.md §4). 수집 쪽 `regime_ingest.dedup.select_as_of`와 같은 규칙이다.

엔진은 regime_ingest 를 import할 수 없어(R5) 규칙을 다시 둔다. 두 구현은 같은 경계 사례 테스트로 묶는다
(engine/tests/test_rag_select.py ↔ ingest/tests/test_contract.py).

- observed (기본): first_seen_at ≤ t 이고 available_at ≤ t — 수집기가 실제로 가지고 있던 것만
- historical_assumed: available_at ≤ t — 소급 정책을 받아들인 일봉 연구용
- doc_id 마다 허용 버전 중 최신 1개. status == "deleted" 는 t 와 관계없이 뺀다 (필드가 없으면 활성)
"""

from __future__ import annotations

from datetime import datetime

AS_OF_MODES = ("observed", "historical_assumed")


def aware(value: str | datetime) -> datetime:
    t = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if t.utcoffset() is None:
        raise ValueError("시간대가 있어야 함")
    return t


def select_as_of(docs: list[dict], t: datetime, mode: str = "observed") -> list[int]:
    """t 에 쓸 수 있는 문서 행 번호 (doc_id 오름차순)."""
    if mode not in AS_OF_MODES:
        raise ValueError(f"mode 는 {AS_OF_MODES} 중 하나")
    t = aware(t)
    best: dict[str, int] = {}
    for i, d in enumerate(docs):
        if d.get("status") == "deleted" or aware(d["available_at"]) > t:
            continue
        if mode == "observed" and aware(d["first_seen_at"]) > t:
            continue
        cur = best.get(d["doc_id"])
        if cur is None or d["version"] > docs[cur]["version"]:
            best[d["doc_id"]] = i
    return [best[k] for k in sorted(best)]


def with_tickers(docs: list[dict], rows: list[int], tickers: list[str]) -> list[int]:
    """tickers[].code 가 하나라도 맞는 행만 남긴다. tickers 가 비면 그대로."""
    if not tickers:
        return rows
    want = set(tickers)
    return [i for i in rows if any(x.get("code") in want for x in docs[i].get("tickers") or [])]
