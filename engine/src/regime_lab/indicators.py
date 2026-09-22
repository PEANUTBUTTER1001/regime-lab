"""기술 지표 (FR-P5, B7). 모든 값은 t일까지의 데이터만 사용한다 (t일 포함, 후행 창).

입력 프레임은 (ticker, date) 오름차순으로 정렬되어 있어야 한다.
거래정지 행(시가 결측)은 고가·저가를 종가로 채워 계산한다 (거래량 0 이므로 VWAP 가중치 0).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _rolling(df: pd.DataFrame, s: pd.Series, n: int, how: str, **kw) -> pd.Series:
    """종목별로 독립 실행하는 후행 rolling.

    전체 프레임에 한 번에 rolling 을 걸면 pandas 의 누적(online) 계산 오차가 앞 종목·앞 구간에
    따라 달라져 같은 종목 값이 입력 범위에 의존하게 된다. 종목 단위로 계산해 이를 막는다.
    """
    g = s.groupby(df["ticker"].to_numpy(), sort=False)
    return g.transform(lambda x: getattr(x.rolling(n, min_periods=n), how)(**kw))


def _shift(df: pd.DataFrame, s: pd.Series, k: int = 1) -> pd.Series:
    return s.groupby(df["ticker"].to_numpy(), sort=False).shift(k)


def filled_hl(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return df["high"].fillna(df["close"]), df["low"].fillna(df["close"])


def sma(df: pd.DataFrame, n: int, col: str = "close") -> pd.Series:
    return _rolling(df, df[col], n, "mean")


def rsi(df: pd.DataFrame, n: int = 14, smoothing: str = "wilder") -> pd.Series:
    diff = df.groupby("ticker", observed=True)["close"].diff()
    gain, loss = diff.clip(lower=0), (-diff).clip(lower=0)
    if smoothing == "wilder":
        g = gain.groupby(df["ticker"], observed=True)
        l_ = loss.groupby(df["ticker"], observed=True)
        avg_g = g.transform(lambda x: x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean())
        avg_l = l_.transform(lambda x: x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean())
    elif smoothing == "sma":
        avg_g = _rolling(df, gain, n, "mean")
        avg_l = _rolling(df, loss, n, "mean")
    else:
        raise ValueError(f"unknown rsi smoothing: {smoothing}")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + avg_g / avg_l)
    out = out.where(avg_l != 0, 100.0).where(avg_g.notna() & avg_l.notna())
    both_zero = (avg_g == 0) & (avg_l == 0)
    return out.mask(both_zero, np.nan)


def bollinger(df: pd.DataFrame, n: int = 20, k: float = 2.0, ddof: int = 0) -> pd.DataFrame:
    mid = sma(df, n)
    std = _rolling(df, df["close"], n, "std", ddof=ddof)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + k * std, "bb_lower": mid - k * std})


def vwap(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """B7: 전형가격 (고+저+종)/3 의 n일 거래량 가중 평균 (t일 포함)."""
    h, l_ = filled_hl(df)
    tp = (h + l_ + df["close"]) / 3
    vol = df["volume"].fillna(0)
    num = _rolling(df, tp * vol, n, "sum")
    den = _rolling(df, vol, n, "sum")
    return (num / den).where(den > 0)


def rolling_max_prev(df: pd.DataFrame, s: pd.Series, n: int) -> pd.Series:
    """직전 n거래일(t-n ~ t-1) 최댓값."""
    return _rolling(df, _shift(df, s), n, "max")


def rolling_mean_prev(df: pd.DataFrame, s: pd.Series, n: int) -> pd.Series:
    """직전 n거래일(t-n ~ t-1) 평균."""
    return _rolling(df, _shift(df, s), n, "mean")


def compute_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """패턴·국면에 필요한 지표 컬럼을 추가한 새 프레임을 반환한다."""
    icfg, pcfg = cfg["indicators"], cfg["patterns"]
    out = df.copy()
    out["sma5"] = sma(out, pcfg["ma_cross_5_20"]["fast"])
    out["sma20"] = sma(out, pcfg["ma_cross_5_20"]["slow"])
    out["sma200"] = sma(out, cfg["regime"]["ma_window"])
    out["rsi14"] = rsi(out, icfg["rsi_window"], icfg["rsi_smoothing"])
    bb = bollinger(out, icfg["bb_window"], icfg["bb_k"], icfg["bb_ddof"])
    out[bb.columns] = bb
    out["vwap20"] = vwap(out, icfg["vwap_window"])
    out["vwap20_prev"] = out.groupby("ticker", observed=True)["vwap20"].shift(1)  # 전략용 t-1 값
    h, _ = filled_hl(out)
    lb = pcfg["breakout_20d"]["lookback"]
    out["high_max_prev20"] = rolling_max_prev(out, h, lb)
    out["vol_mean_prev20"] = rolling_mean_prev(out, out["volume"].fillna(0), pcfg["breakout_vol"]["lookback"])
    out["avg_value20"] = _rolling(out, out["value"].fillna(0), cfg["universe"]["liquidity_window"], "mean")
    return out
