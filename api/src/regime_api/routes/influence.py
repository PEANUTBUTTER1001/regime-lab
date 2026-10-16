"""영향 분석 API (P3-13.3, SRS FR-N6·FR-N7 — 결정 P3-12a, 2026-10-12 PEANUTBUTTER1001 확정).
계산(모델 고르기·T 시점 자료 거르기·연결 봉)은 엔진 influence_runs 에만 두고, 여기서는 입력 전달과 오류 변환만 한다.

GET /api/influence/model?as_of=
→ {status: ok|no_model, model: {model_version, target, as_of_mode, periods, baseline, min_eval_samples, …}, as_of, notice, disclaimer}
GET /api/influence/weights?as_of=
→ {status: ok|no_model, model, baseline: {name, metric, baseline, model, improved}, rows: [{group_type, group, weight,
   direction, direction_shown, n_events, n_eval, eval_metric, ci_low, ci_high, sample_ok}], as_of, notice, disclaimer}
GET /api/influence/events?ticker=&from=&to=&as_of=&mode=
→ {status: ok|no_model|no_events|coverage_gap, model, ticker, from, to, last_bar, as_of, mode,
   events: [{doc_id, version, available_at, linked_bar_start, group, contribution, period, close, title, url, …}],
   bars: [{date, close}], coverage_gaps: {출처: {gap_days, partial_days, gap, partial}}, notice, disclaimer}
결과를 읽을 수 없으면(형식 버전·파일 손상·ext_store 없음) 503 data_unavailable.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from regime_api.errors import ApiError
from regime_lab import influence_runs
from regime_lab.runs import DISCLAIMER

router = APIRouter(tags=["influence"])

_STATUS = {"validation_failed": 422, "ticker_not_found": 404, "data_unavailable": 503}


def _raise(e: influence_runs.InfluenceError):
    raise ApiError(_STATUS.get(e.code, 422), e.code, e.message, e.detail) from None


@router.get("/influence/model")
def model(request: Request, as_of: str = "", h: int = Query(1, ge=1, le=20)):
    try:
        res = influence_runs.model_view(request.app.state.rl.influence, as_of, h)
    except influence_runs.InfluenceError as e:
        _raise(e)
    return {**res, "disclaimer": DISCLAIMER}


@router.get("/influence/weights")
def weights(request: Request, as_of: str = "", h: int = Query(1, ge=1, le=20)):
    try:
        res = influence_runs.weights_view(request.app.state.rl.influence, as_of, h)
    except influence_runs.InfluenceError as e:
        _raise(e)
    return {**res, "disclaimer": DISCLAIMER}


@router.get("/influence/events")
def events(request: Request, ticker: str = "", as_of: str = "", mode: str = "",
           start: str = Query("", alias="from", description="연결 봉 기간 시작 YYYY-MM-DD"),
           end: str = Query("", alias="to", description="연결 봉 기간 끝 YYYY-MM-DD (기본: 기준 시각까지)")):
    state = request.app.state.rl
    ei, prep = state.require_rag(), state.require_prep()
    try:
        res = influence_runs.events_view(state.influence, ei.docs, state.docs, prep.frame, state.cfg, ticker,
                                         start or None, end or None, as_of, mode or None)
    except influence_runs.InfluenceError as e:
        _raise(e)
    return {**res, "disclaimer": DISCLAIMER}
