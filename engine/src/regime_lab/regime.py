"""시장·종목 국면 (FR-D5, A6, A2-1). t일 값은 t일까지의 종가만 사용한다.

bull     : 종가 > 200일선 이고 200일선 20일 변화율 > +1%
bear     : 종가 < 200일선 이고 200일선 20일 변화율 < -1%
sideways : 그 외 (산출 가능한 경우)
null     : 200일선 또는 20일 전 200일선이 없음
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from regime_lab.indicators import sma

REGIMES = ["bull", "sideways", "bear"]


def classify(close: pd.Series, ma: pd.Series, ma_prev: pd.Series, threshold_pct: float) -> pd.Series:
    slope = (ma / ma_prev - 1.0) * 100.0
    valid = ma.notna() & ma_prev.notna() & close.notna()
    lab = np.where(
        (close > ma) & (slope > threshold_pct), "bull",
        np.where((close < ma) & (slope < -threshold_pct), "bear", "sideways"),
    )
    out = pd.Series(np.where(valid, lab, None), index=close.index, dtype=object)
    return out.astype(pd.CategoricalDtype(REGIMES))


def stock_regime(frame: pd.DataFrame, cfg: dict) -> pd.Series:
    rcfg = cfg["regime"]
    ma = frame["sma200"] if "sma200" in frame else sma(frame, rcfg["ma_window"])
    ma_prev = ma.groupby(frame["ticker"], observed=True).shift(rcfg["slope_window"])
    return classify(frame["close"], ma, ma_prev, rcfg["slope_threshold_pct"])


def market_regime(index: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """지수 일봉(date, market, close) → (date, market, market_regime).

    A2-1: market_regime_start 이전은 null (지수 이력이 부족해 산출하지 않는다).
    """
    rcfg = cfg["regime"]
    idx = index.sort_values(["market", "date"]).reset_index(drop=True).rename(columns={"market": "ticker"})
    ma = sma(idx, rcfg["ma_window"])
    ma_prev = ma.groupby(idx["ticker"]).shift(rcfg["slope_window"])
    reg = classify(idx["close"], ma, ma_prev, rcfg["slope_threshold_pct"])
    reg[idx["date"] < pd.Timestamp(rcfg["market_regime_start"])] = None
    return pd.DataFrame({"date": idx["date"], "market": idx["ticker"], "market_regime": reg})


def first_computable_date(index: pd.DataFrame, cfg: dict) -> pd.Series:
    """지수별 국면이 처음 산출 가능한 날 (시작일 제한 없이)."""
    c = dict(cfg, regime=dict(cfg["regime"], market_regime_start="1900-01-01"))
    mr = market_regime(index, c)
    return mr.dropna(subset=["market_regime"]).groupby("market")["date"].min()


def attach_regimes(frame: pd.DataFrame, index: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = frame.copy()
    out["stock_regime"] = stock_regime(out, cfg)
    mr = market_regime(index, cfg)
    key = out[["date"]].assign(market=out["market"].astype(object))
    out["market_regime"] = key.merge(mr, on=["date", "market"], how="left")["market_regime"].to_numpy()
    out["market_regime"] = out["market_regime"].astype(pd.CategoricalDtype(REGIMES))
    return out
