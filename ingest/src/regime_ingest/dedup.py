"""중복·버전 판정과 정정 공시 후보 연결 (도메인, plan/03-04 §6.1·§7.1). 입출력 없음.

- 같은 접수번호를 다시 받으면: 내용 지문이 같으면 중복(버림), 다르면 version + 1 로 새 행(덮어쓰지 않음).
  새 버전은 **처음 본 시각부터만** 쓸 수 있다(available_at = first_seen_at). 소급 모드라도 접수일로 앞당기지 않아
  과거 as_of 의 결과가 바뀌지 않는다.
- 정정 공시는 자기 접수번호를 따로 가진다. 같은 회사·같은 기본 보고서명에서 **자기보다 앞선 접수번호** 중 가장 늦은
  공시를 amends_candidate_doc_id 로 남긴다(수집 순서와 무관). 보고서명만으로 원공시 관계를 확정하지 않으며
  근거는 amends_basis 에 적는다 (확정 규칙은 P3-6 계약·plan §11 1-4 실측 뒤).
"""

from __future__ import annotations

from bisect import bisect_left, insort
from dataclasses import dataclass


@dataclass(frozen=True)
class Seen:
    version: int
    content_hash: str


def classify(doc: dict, seen: dict[str, Seen]) -> tuple[str, dict | None]:
    """('new' | 'changed' | 'duplicate', 저장할 문서 또는 None)."""
    prev = seen.get(doc["doc_id"])
    if prev is None:
        return "new", doc
    if prev.content_hash == doc["content_hash"]:
        return "duplicate", None
    return "changed", {**doc, "version": prev.version + 1, "available_at": doc["first_seen_at"]}


ReportKey = tuple[str, str]  # (corp_code, report_base)


def link_amendments(docs: list[dict], history: dict[ReportKey, list[tuple[str, str]]]) -> list[tuple[ReportKey, str, str]]:
    """정정 공시의 amends_candidate_doc_id 를 채운다 (docs 를 제자리에서 바꿈).

    history: 키별 이미 저장된 (rcept_no, doc_id) 목록. 새 문서도 같은 규칙으로 섞어 보고, 후보는 자기 접수번호보다
    엄격히 앞선 것 중 최댓값이다 → 자기 자신·미래 접수번호는 후보가 될 수 없다.
    반환: 이력에 새로 넣을 (key, rcept_no, doc_id) 목록 (같은 접수번호의 새 버전은 이미 있으므로 넣지 않음).
    """
    hist = {k: sorted(v) for k, v in history.items()}
    added = []
    for d in docs:
        key = (d["corp_code"], d["report_base"])
        rows = hist.setdefault(key, [])
        if not any(r == d["rcept_no"] for r, _ in rows):
            insort(rows, (d["rcept_no"], d["doc_id"]))
            added.append((key, d["rcept_no"], d["doc_id"]))
    for d in docs:
        if not d["is_amendment"]:
            continue
        rows = hist[(d["corp_code"], d["report_base"])]
        i = bisect_left(rows, (d["rcept_no"], ""))
        d["amends_candidate_doc_id"] = rows[i - 1][1] if i > 0 else None
        d["amends_basis"] = "same_corp_base_title" if d["amends_candidate_doc_id"] else None
    return added
