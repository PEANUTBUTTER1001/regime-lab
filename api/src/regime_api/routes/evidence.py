"""근거 검색 API (P3-11, SRS FR-N5)와 자료 보관함 API (P3-13, SRS FR-N7).
계산은 엔진 retrieval 에만 두고, 여기서는 입력 전달과 오류 변환만 한다.

GET /api/evidence/search?q=&as_of=&mode=&tickers=&k=
→ {status: ok|no_evidence, items: [...], index: {...}, disclaimer}
GET /api/evidence/documents?as_of=&mode=&source_type=&start=&end=&tickers=&status=&page=&page_size=
→ {status: ok|no_documents, items: [...], total, page, page_size, pages, facets, index, disclaimer}
GET /api/evidence/documents/{doc_id}?as_of=&mode=
→ {doc, versions, amends_candidate, amended_by_candidates, content_policy, as_of, mode, disclaimer}
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from regime_api.errors import ApiError
from regime_lab import retrieval
from regime_lab.runs import DISCLAIMER

router = APIRouter(tags=["evidence"])


@router.get("/evidence/search")
def search(request: Request, q: str = "", as_of: str = "", mode: str = "observed", tickers: str = "",
           k: int | None = None):
    state = request.app.state.rl
    ei = state.require_rag()
    try:
        res = retrieval.search(ei, state.cfg, q, as_of, mode, tickers.split(",") if tickers else None, k)
    except retrieval.RetrievalError as e:
        raise ApiError(422, e.code, e.message, e.detail) from None
    return {**res, "disclaimer": DISCLAIMER}


_NOT_FOUND = {"not_found": "document_not_found", "not_available_at_as_of": "not_available_at_as_of"}


def _raise(e: retrieval.RetrievalError):
    if e.code in _NOT_FOUND:
        raise ApiError(404, _NOT_FOUND[e.code], e.message, e.detail) from None
    raise ApiError(422, e.code, e.message, e.detail) from None


@router.get("/evidence/documents")
def documents(request: Request, as_of: str = "", mode: str = "observed", source_type: str = "", start: str = "",
              end: str = "", tickers: str = "", status: str = "all", page: int = 1, page_size: int | None = None):
    state = request.app.state.rl
    ei = state.require_rag()
    try:
        res = retrieval.browse(ei, state.cfg, as_of, mode, source_type or None, start or None, end or None,
                               tickers.split(",") if tickers else None, status, page, page_size)
    except retrieval.RetrievalError as e:
        _raise(e)
    return {**res, "disclaimer": DISCLAIMER}


@router.get("/evidence/documents/{doc_id}")
def document(doc_id: str, request: Request, as_of: str = "", mode: str = "observed"):
    state = request.app.state.rl
    ei = state.require_rag()
    try:
        res = retrieval.document(ei, state.cfg, doc_id, as_of, mode)
    except retrieval.RetrievalError as e:
        _raise(e)
    return {**res, "disclaimer": DISCLAIMER}
