"""AI 보고서 API (FR-L1~L4, NFR-7)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from regime_api.errors import ApiError
from regime_api.routes.results import completed_run_dir, read_json

router = APIRouter(tags=["report"])


@router.post("/runs/{run_id}/report")
def report(run_id: str, request: Request, strategy: str | None = None):
    s = request.app.state.rl
    d = completed_run_dir(s, run_id)
    result = read_json(d / "result.json")
    names = [x["strategy"] for x in result["strategies"]]
    strategy = strategy or names[0]
    if strategy not in names:
        raise ApiError(404, "strategy_not_found", f"Strategy not in this run: {strategy}", {"strategies": names})
    return s.reports.generate(run_id, result, strategy)
