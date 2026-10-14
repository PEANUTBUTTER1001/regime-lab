"""봉 예측 API (P2-9, SRS FR-F1~F7 — 계약 2026-10-12 Seonghwanaa 승인).
계산(회색 구간 → 가격, 시점 고르기)은 엔진 forecast_runs 에만 두고, 여기서는 입력 전달과 오류 변환만 한다.

GET /api/forecasts/runs
→ {status: ok|no_run, runs: [{run_id, timeframe, created_at, horizon, codes, format_version, test_as_of_range}], disclaimer}
GET /api/forecasts/runs/{run_id}/stocks/{code}?as_of=
→ {status: ok|no_stock|no_as_of|insufficient_history (P2-9a), run_id, timeframe, code, horizon, as_of, base_close,
   steps: [{h, low, high, p_up, direction, actual, cross_day}], history: [{as_of, close}], notice, disclaimer}
   (가운데 분위 q0.5 는 채점용이라 내보내지 않음 — E17)
GET /api/forecasts/runs/{run_id}/scores
→ {run_id, timeframe, horizon, split, scores: {h: {direction_policy, model, baseline, …}}, notice, disclaimer}
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from regime_api.errors import ApiError
from regime_lab import forecast_runs
from regime_lab.runs import DISCLAIMER

router = APIRouter(tags=["forecasts"])


def _raise(e: forecast_runs.ForecastError):
    raise ApiError(404 if e.code == "forecast_run_not_found" else 422, e.code, e.message, e.detail) from None


@router.get("/forecasts/runs")
def runs(request: Request):
    items = forecast_runs.list_runs(request.app.state.rl.forecasts)
    return {"status": "ok" if items else "no_run", "runs": items, "disclaimer": DISCLAIMER}


@router.get("/forecasts/runs/{run_id}/stocks/{code}")
def stock(run_id: str, code: str, request: Request, as_of: str = ""):
    state = request.app.state.rl
    try:
        res = forecast_runs.stock_forecast(state.forecasts, state.cfg, run_id, code, as_of or None)
    except forecast_runs.ForecastError as e:
        _raise(e)
    return {**res, "disclaimer": DISCLAIMER}


@router.get("/forecasts/runs/{run_id}/scores")
def scores(run_id: str, request: Request):
    try:
        res = forecast_runs.run_scores(request.app.state.rl.forecasts, run_id)
    except forecast_runs.ForecastError as e:
        _raise(e)
    return {**res, "disclaimer": DISCLAIMER}
