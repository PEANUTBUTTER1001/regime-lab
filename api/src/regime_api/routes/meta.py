"""빌더 초기값(/api/meta)과 예상 대상 종목 수(/api/universe/preview, design.md §6.2)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from regime_api.errors import ApiError
from regime_api.schemas import MAX_STRATEGIES, PreviewRequest, to_engine_dict
from regime_lab.backtest import METRIC_DEFS
from regime_lab.context import STAGES
from regime_lab.runs import CAP_GROUPS, DISCLAIMER, FDR_NOTE, Strategy, StrategyError, entry_mask

router = APIRouter(tags=["meta"])

PATTERN_INFO = {
    "ma_cross_5_20": ("5/20 golden cross", "SMA5(t-1) ≤ SMA20(t-1) and SMA5(t) > SMA20(t)"),
    "breakout_20d": ("20-day high breakout", "Close(t) > max high of the prior 20 trading days"),
    "breakout_vol": ("Breakout with volume surge", "20-day breakout and volume(t) ≥ 2.0 × prior 20-day average"),
    "rsi_rebound": ("RSI 30 recovery", "RSI14(t-1) < 30 and RSI14(t) ≥ 30 (14-day simple average)"),
    "bb_lower_recover": ("Bollinger lower recovery", "Close(t-1) < lower band(t-1) and close(t) ≥ lower band(t), 20d 2σ"),
}


@router.get("/health")
def health(request: Request):
    s = request.app.state.rl
    return {"status": s.status, "error": s.load_error}


@router.get("/meta")
def meta(request: Request):
    s = request.app.state.rl
    cfg = s.cfg
    return {
        "status": s.status,
        "scope": "sample30" if s.tickers else "all",
        "data_as_of": cfg["data"]["as_of_date"],
        "backtest_start": cfg["data"]["backtest_start"],
        "patterns": [{"name": k, "label": v[0], "rule": v[1]} for k, v in PATTERN_INFO.items()],
        "combine": ["and", "or"],
        "exit_defaults": cfg["exit"],
        "exit_limits": cfg["exit_limits"],
        "markets": cfg["data"]["markets"],
        "cap_groups": CAP_GROUPS,
        "min_avg_value_krw": cfg["universe"]["min_avg_value_krw"],
        "execution": {"entry": "Next-day open", "round_trip_cost_pct": cfg["execution"]["round_trip_cost_pct"],
                      "limit_up_pct": cfg["execution"]["limit_up_pct"]},
        "validation": {"split_date": cfg["analysis"]["split_date"], "fdr_q": cfg["analysis"]["fdr_q"],
                       "random_iterations": cfg["analysis"]["random_bench_iterations"],
                       "min_cell_trades": cfg["analysis"]["min_cell_trades"], "fdr_note": FDR_NOTE},
        "max_strategies": MAX_STRATEGIES,
        "stages": STAGES,
        "metric_definitions": {k: {"name": n, "unit": u, "formula": f} for k, (n, u, f) in METRIC_DEFS.items()},
        "disclaimer": DISCLAIMER,
    }


@router.post("/universe/preview")
def preview(req: PreviewRequest, request: Request):
    s = request.app.state.rl
    prep = s.require_prep()
    strat = Strategy.from_dict({"name": "preview", "patterns": ["breakout_20d"], **to_engine_dict(req)})
    try:
        strat.validate(s.cfg)
    except StrategyError as e:
        raise ApiError(422, "validation_failed", "Check the input values.", {"fields": e.errors}) from None
    f = prep.frame
    m = entry_mask(f, strat) & f["eligible"].astype(bool)
    if strat.period is None:
        m &= f["date"] >= s.cfg["data"]["backtest_start"]
    days = f.loc[m, "date"]
    tickers = f.loc[m, "ticker"].astype(str)
    n_days = int(days.nunique())
    return {
        "eligible_tickers": int(tickers.nunique()),
        "ticker_days": int(m.sum()),
        "trading_days": n_days,
        "avg_tickers_per_day": round(float(m.sum()) / n_days, 1) if n_days else 0.0,
        "note": "Securities passing the universe rules (common stock, 250 listed trading days, liquidity, managed-stock exclusion) and the input filters. No backtest is run.",
    }
