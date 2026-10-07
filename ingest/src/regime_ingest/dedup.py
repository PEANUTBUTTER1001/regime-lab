"""중복·버전 판정과 정정 공시 연결 (도메인, plan/03-04 §6.1·§7.1). 입출력 없음.

- 같은 접수번호를 다시 받으면: 내용 지문이 같으면 중복(버림), 다르면 version + 1 로 새 행(덮어쓰지 않음).
- 정정 공시는 자기 접수번호를 따로 가진다. 같은 회사·같은 기본 보고서명의 직전 공시를 amends_doc_id 로 잇는다.
"""

from __future__ import annotations

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
    return "changed", {**doc, "version": prev.version + 1}


def link_amendments(docs: list[dict], last_by_report: dict[tuple[str, str], str]) -> dict[tuple[str, str], str]:
    """접수번호 순서로 보며 정정 공시의 amends_doc_id 를 채운다 (docs 를 제자리에서 바꿈).

    last_by_report: (corp_code, report_base) → 지금까지 본 마지막 doc_id (이전 실행에서 이어 받음). 갱신본을 돌려준다.
    원공시가 수집 범위 밖이면 None 으로 둔다.
    """
    out = dict(last_by_report)
    for d in sorted(docs, key=lambda d: d["rcept_no"]):
        key = (d["corp_code"], d["report_base"])
        if d["is_amendment"]:
            d["amends_doc_id"] = out.get(key)
        out[key] = d["doc_id"]
    return out
