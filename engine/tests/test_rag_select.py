"""P3-11 시점 선택. 수집 쪽 ingest/tests/test_contract.py 의 §4 as_of 사례를 같은 기대값으로 복제한다
(엔진은 regime_ingest 를 import할 수 없어 규칙을 다시 두므로, 두 구현이 어긋나면 여기서 잡는다)."""

from datetime import datetime, timedelta

import pytest
from rag_synth import KST, T0, make_doc

from regime_lab.rag.select import select_as_of, with_tickers


def _backfilled(i, published: str):
    """2026-10-07 에 소급 수집한 공시: available_at = 접수일 다음 날 00:00 KST (계약 §4-2)."""
    day = datetime.fromisoformat(published).replace(tzinfo=KST)
    return make_doc(i, "회사", "제목", "", T0, available=day + timedelta(days=1), published_at=published)


def test_observed_vs_historical_assumed():
    docs = [_backfilled(i, f"2021-01-0{d}") for i, d in enumerate(range(4, 9))]
    t = datetime(2021, 1, 6, 12, tzinfo=KST)
    hist = select_as_of(docs, t, "historical_assumed")
    assert {docs[i]["published_at"] for i in hist} == {"2021-01-04", "2021-01-05"}  # 접수일 다음 날 0시부터
    assert select_as_of(docs, t, "observed") == []  # 실제로는 2026-10-07 에 처음 봄
    assert len(select_as_of(docs, T0 + timedelta(hours=1), "observed")) == len(docs)
    with pytest.raises(ValueError):
        select_as_of(docs, t.replace(tzinfo=None))


def test_picks_latest_allowed_version():
    later = T0 + timedelta(hours=5)
    v1 = make_doc(1, "회사", "제목", "", T0)
    v2 = {**make_doc(1, "회사", "제목 [정정]", "", later), "version": 2}
    docs = [v2, v1]  # 파일 순서와 무관
    assert [docs[i]["version"] for i in select_as_of(docs, T0 + timedelta(hours=1))] == [1]
    assert [docs[i]["version"] for i in select_as_of(docs, later + timedelta(minutes=1))] == [2]


def test_deleted_is_excluded_at_any_time_and_bad_mode_rejected():
    docs = [make_doc(1, "a", "x", "", T0), make_doc(2, "b", "y", "", T0, status="deleted")]
    assert select_as_of(docs, T0 + timedelta(days=365)) == [0]
    with pytest.raises(ValueError):
        select_as_of(docs, T0, "published")



@pytest.mark.parametrize("mode,cutoff,expected", [
    ("observed", "2026-10-07T09:00:00+09:00", [("a", 1)]),
    ("observed", "2026-10-07T00:59:59+00:00", [("a", 1), ("b", 1)]),
    ("observed", "2026-10-07T10:00:00+09:00", [("a", 2), ("b", 1)]),
    ("historical_assumed", "2026-10-07T00:00:00+00:00", [("a", 1), ("late", 1)]),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_shared_as_of_boundary_matrix(mode, cutoff, expected, reverse):
    """engine/test_rag_select ↔ ingest/test_contract: 같은 입력·기대값, 직접 import 없이 계약 검증."""
    docs = [
        {"doc_id": "a", "version": 1, "first_seen_at": "2026-10-07T00:00:00+00:00", "available_at": "2026-10-07T09:00:00+09:00"},
        {"doc_id": "a", "version": 2, "first_seen_at": "2026-10-07T01:00:00+00:00", "available_at": "2026-10-07T10:00:00+09:00"},
        {"doc_id": "b", "version": 1, "first_seen_at": "2026-10-07T09:30:00+09:00", "available_at": "2026-10-07T00:30:00+00:00"},
        {"doc_id": "late", "version": 1, "first_seen_at": "2026-10-07T11:00:00+09:00", "available_at": "2026-10-06T00:00:00+09:00"},
    ]
    if reverse:
        docs.reverse()
    rows = [docs[i] for i in select_as_of(docs, datetime.fromisoformat(cutoff), mode)]
    assert [(d["doc_id"], d["version"]) for d in rows] == expected


def test_with_tickers():
    docs = [make_doc(0, "a", "x", "005930", T0), make_doc(1, "b", "y", "", T0), make_doc(2, "c", "z", "010140", T0)]
    assert with_tickers(docs, [0, 1, 2], ["010140"]) == [2]
    assert with_tickers(docs, [0, 1, 2], []) == [0, 1, 2]


def test_deleted_newer_row_currently_leaves_old_version_visible():
    """현행 동작 기록만. 문서 전체 삭제 정책 승인/구현 완료를 뜻하지 않는다 (P3-11 ⑨ 미확정)."""
    v1 = make_doc(1, "회사", "제목", "", T0)
    v2 = make_doc(1, "회사", "제목", "", T0 + timedelta(hours=1), version=2, status="deleted")
    assert select_as_of([v1, v2], T0 + timedelta(days=1)) == [0]
