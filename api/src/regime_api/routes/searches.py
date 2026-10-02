"""역방향 탐색 API (P1-7, 설계 §4.2): 미리보기 → search_id → 상태 폴링 → 완료·중단·실패 → 결과."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from regime_api.errors import ApiError, body
from regime_api.jobs import Busy
from regime_api.schemas import SearchRequestIn, to_engine_dict
from regime_lab.runs import DISCLAIMER
from regime_lab.search import STATUSES, SearchError, SearchRequest, count_candidates, estimate_seconds

router = APIRouter(tags=["searches"])

NOT_READY = {
    "queued": ("search_not_ready", "The search has not started yet.", True),
    "running": ("search_not_ready", "The search is still in progress. Results are available when it completes.", True),
    "failed": ("search_failed", "The search failed, so there is no result.", False),
}


def parse(req: SearchRequestIn, cfg: dict) -> SearchRequest:
    try:
        return SearchRequest.from_dict(to_engine_dict(req), cfg)
    except SearchError as e:
        if e.code == "too_many_candidates":
            raise ApiError(422, e.code, "Too many candidates. Narrow the search ranges.", e.detail) from None
        raise ApiError(422, "validation_failed", "Check the input values.", {"fields": e.errors}) from None


@router.get("/searches/options")
def options(request: Request):
    """화면 입력용 허용 값·기본값 (설정 search 절)."""
    cfg = request.app.state.rl.cfg
    s = cfg["search"]
    lo, hi = s["min_trades_limits"]
    return {"patterns": list(cfg["patterns"]), "axes": s["axes"], "defaults": s["defaults"],
            "max_candidates": s["max_candidates"], "sec_per_candidate": s["sec_per_candidate"],
            "min_trades": {"default": s["min_trades_default"], "min": lo, "max": hi},
            "split_date": cfg["analysis"]["split_date"], "fdr_q": cfg["analysis"]["fdr_q"],
            "statuses": list(STATUSES), "disclaimer": DISCLAIMER}


@router.post("/searches/preview")
def preview(req: SearchRequestIn, request: Request):
    """실행하지 않고 후보 수·예상 시간만 계산한다. 상한을 넘어도 200 으로 within_limit = false."""
    cfg = request.app.state.rl.cfg
    try:
        n = count_candidates(SearchRequest.from_dict(to_engine_dict(req), cfg))
    except SearchError as e:
        if e.code != "too_many_candidates":
            raise ApiError(422, "validation_failed", "Check the input values.", {"fields": e.errors}) from None
        n = e.detail["candidates"]
    cap = int(cfg["search"]["max_candidates"])
    return {"candidates": n, "max_candidates": cap, "within_limit": n <= cap,
            "estimated_sec": estimate_seconds(n, cfg)}


@router.post("/searches", status_code=202)
def submit(req: SearchRequestIn, request: Request):
    s = request.app.state.rl
    sreq = parse(req, s.cfg)
    s.require_prep()
    try:
        job = s.jobs.submit_search(sreq)
    except Busy as b:
        return JSONResponse(body("busy", "Another run or search is in progress. Try again when it finishes.",
                                 {"kind": b.kind, "id": b.run_id}, retryable=True), status_code=409)
    n = count_candidates(sreq)
    return {"search_id": job.run_id, "status": job.status, "candidates": n,
            "estimated_sec": estimate_seconds(n, s.cfg)}


@router.get("/searches")
def list_searches(request: Request, limit: int = 50):
    return {"searches": request.app.state.rl.jobs.list_searches(limit)}


def _snap(request: Request, search_id: str) -> dict:
    snap = request.app.state.rl.jobs.get_search(search_id)
    if snap is None:
        raise ApiError(404, "search_not_found", f"Search not found: {search_id}")
    return snap


@router.get("/searches/{search_id}")
def status(search_id: str, request: Request):
    return _snap(request, search_id)


@router.post("/searches/{search_id}/cancel", status_code=202)
def cancel(search_id: str, request: Request):
    snap = _snap(request, search_id)
    if not request.app.state.rl.jobs.cancel(search_id):
        raise ApiError(409, "not_running", f"The search is not in progress (status={snap['status']}).",
                       {"status": snap["status"]})
    return {"search_id": search_id, "accepted": True,
            "message": "Cancellation requested. The search stops at the next candidate and records what it processed."}


@router.get("/searches/{search_id}/result")
def result(search_id: str, request: Request):
    """완료·중단(cancelled) 모두 기록을 돌려준다. 중단이면 status = cancelled 와 처리 수가 함께 온다."""
    s = request.app.state.rl
    snap = _snap(request, search_id)
    if snap["status"] in NOT_READY:
        code, msg, retry = NOT_READY[snap["status"]]
        raise ApiError(409, code, msg, {"status": snap["status"], "error": snap.get("error")}, retry)
    f = s.jobs.search_dir / search_id / "search.json"
    if not f.exists():
        raise ApiError(404, "result_not_found", "The search record is missing.", {"search_id": search_id})
    out = json.loads(f.read_text(encoding="utf-8"))
    out.setdefault("disclaimer", DISCLAIMER)
    return out
