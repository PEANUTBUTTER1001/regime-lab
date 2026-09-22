"""실행 API (FR-X2·X3·X5): 요청 → run_id → 상태 폴링 → 완료/실패/취소."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from regime_api.errors import ApiError, body
from regime_api.jobs import Busy
from regime_api.schemas import RunRequest, to_engine_dict
from regime_lab.runs import Strategy, StrategyError

router = APIRouter(tags=["runs"])


def parse_strategies(req: RunRequest, cfg: dict) -> list[Strategy]:
    fields: dict[str, str] = {}
    out = []
    for i, s in enumerate(req.strategies):
        try:
            st = Strategy.from_dict(to_engine_dict(s))
            st.validate(cfg)
            out.append(st)
        except StrategyError as e:
            fields.update({f"strategies[{i}]" + (f".{k}" if k and k != "_" else ""): v for k, v in e.errors.items()})
    names = [s.name for s in req.strategies]
    if len(set(names)) != len(names):
        fields["strategies"] = f"Duplicate strategy names: {names}"
    if fields:
        raise ApiError(422, "validation_failed", "Check the input values.", {"fields": fields})
    return out


@router.post("/runs", status_code=202)
def submit(req: RunRequest, request: Request):
    s = request.app.state.rl
    strategies = parse_strategies(req, s.cfg)
    s.require_prep()
    try:
        job = s.jobs.submit(strategies)
    except Busy as b:
        return JSONResponse(body("busy", "Another run is in progress. Try again when it finishes.",
                                 {"run_id": b.run_id}, retryable=True), status_code=409)
    return {"run_id": job.run_id, "status": job.status, "fdr_family_size": len(strategies)}


@router.get("/runs")
def list_runs(request: Request, limit: int = 50):
    return {"runs": request.app.state.rl.jobs.list(limit)}


@router.get("/runs/{run_id}")
def status(run_id: str, request: Request):
    snap = request.app.state.rl.jobs.get(run_id)
    if snap is None:
        raise ApiError(404, "run_not_found", f"Run not found: {run_id}")
    return snap


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel(run_id: str, request: Request):
    s = request.app.state.rl
    snap = s.jobs.get(run_id)
    if snap is None:
        raise ApiError(404, "run_not_found", f"Run not found: {run_id}")
    if not s.jobs.cancel(run_id):
        raise ApiError(409, "not_running", f"The run is not in progress (status={snap['status']}).",
                       {"status": snap["status"]})
    return {"run_id": run_id, "accepted": True,
            "message": "Cancellation requested. The run stops at the next checkpoint and no result is generated."}
