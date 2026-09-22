"""결과·종목 상세 API (FR-U3·U4·X6). 모든 수치는 같은 run_id 결과 파일에서만 파생한다."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Request

from regime_api.errors import ApiError
from regime_lab.analysis.report import stock_detail, to_jsonable
from regime_lab.pipeline import prep_hash
from regime_lab.runs import DISCLAIMER

router = APIRouter(tags=["results"])

NOT_READY = {
    "queued": ("run_not_ready", "The run has not started yet.", True),
    "running": ("run_not_ready", "The run is still in progress. Results are available when it completes.", True),
    "failed": ("run_failed", "The run failed, so there is no result.", False),
    "cancelled": ("run_cancelled", "The run was cancelled, so no result was generated.", False),
}


def completed_run_dir(state, run_id: str) -> Path:
    snap = state.jobs.get(run_id)
    if snap is None:
        raise ApiError(404, "run_not_found", f"Run not found: {run_id}")
    if snap["status"] != "completed":
        code, msg, retry = NOT_READY[snap["status"]]
        raise ApiError(409, code, msg, {"status": snap["status"], "error": snap.get("error")}, retry)
    d = state.paths.runs / run_id
    if not (d / "result.json").exists():
        raise ApiError(404, "result_not_found", "The result file is missing.", {"run_id": run_id})
    return d


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/runs/{run_id}/result")
def result(run_id: str, request: Request):
    d = completed_run_dir(request.app.state.rl, run_id)
    out = read_json(d / "result.json")
    out["status"] = "completed"
    out.setdefault("disclaimer", DISCLAIMER)
    return out


@router.get("/runs/{run_id}/stocks/{ticker}")
def stock(run_id: str, ticker: str, request: Request, strategy: str | None = None):
    s = request.app.state.rl
    d = completed_run_dir(s, run_id)
    meta = read_json(d / "meta.json")
    prep = s.require_prep()
    if meta.get("prep_hash") != prep_hash(s.cfg):
        raise ApiError(409, "stale_run", "This run was created with different data or settings than the server now uses. Run it again.",
                       {"run_prep_hash": meta.get("prep_hash"), "server_prep_hash": prep_hash(s.cfg)})
    names = [x["name"] for x in meta["strategies"]]
    strategy = strategy or names[0]
    if strategy not in names:
        raise ApiError(404, "strategy_not_found", f"Strategy not in this run: {strategy}", {"strategies": names})
    if not (prep.frame["ticker"].astype(str) == ticker).any():
        raise ApiError(404, "ticker_not_found", f"Security not in the loaded data: {ticker}",
                       {"scope": "sample30" if s.tickers else "all"})
    trades = pd.read_parquet(d / "strategies" / strategy / "trades.parquet")
    detail = stock_detail(prep.frame, trades, ticker, prep.names, s.cfg)
    detail.update(run_id=run_id, strategy=strategy, data_as_of=s.cfg["data"]["as_of_date"], disclaimer=DISCLAIMER)
    return to_jsonable(detail)
