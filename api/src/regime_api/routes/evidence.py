"""근거 검색 API (P3-11, SRS FR-N5). 계산은 엔진 retrieval 에만 두고, 여기서는 입력 전달과 오류 변환만 한다.

GET /api/evidence/search?q=&as_of=&mode=&tickers=&k=
→ {status: ok|no_evidence, items: [...], index: {...}, disclaimer}
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
