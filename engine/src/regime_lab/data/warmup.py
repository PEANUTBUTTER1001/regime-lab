"""워밍업 보정 연결 (A2, FR-E8) 과 첫 거래일 (U1).

- kor_price 의 [warmup_source_start, backtest_start) 구간을 종목별 backtest_start 종가 비율로
  보정해 store 일봉 앞에 붙인다. 거래대금은 백만원 → 원으로 변환한다.
- 비율 계산에 쓰는 kor_price 의 backtest_start 행 자체는 붙이지 않는다 (store 값 사용).
- 워밍업 행은 is_warmup=True 이며, 지표 계산에만 쓰이고 신호·거래는 만들지 않는다.
- store 에 backtest_start 행이 없거나 kor_price 에 같은 날 종가가 없으면 보정하지 않는다
  (해당 종목은 store 데이터만으로 워밍업).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

LONG_HISTORY_OFFSET = 100_000  # warmup_source_start 이전부터 거래된 종목의 상장 거래일 가산값


def load_warmup_source(cache: Path) -> pd.DataFrame | None:
    f = cache / "warmup" / "kor_price_warmup.parquet"
    return pd.read_parquet(f) if f.exists() else None


def load_first_dates(cache: Path) -> pd.DataFrame | None:
    f = cache / "warmup" / "kor_price_first_date.parquet"
    return pd.read_parquet(f) if f.exists() else None


def link_ratios(daily: pd.DataFrame, warm: pd.DataFrame, backtest_start: str) -> pd.Series:
    """종목별 보정 비율 = store 종가 / kor_price 종가 (backtest_start 당일)."""
    t0 = pd.Timestamp(backtest_start)
    s = daily.loc[daily["date"] == t0].set_index("ticker")["close"]
    k = warm.loc[warm["date"] == t0].set_index("ticker")["close"]
    ratio = (s / k).replace([np.inf, -np.inf], np.nan).dropna()
    return ratio[ratio > 0]


def jump_tickers(w_scaled: pd.DataFrame, daily: pd.DataFrame, t0: pd.Timestamp, max_abs_ret: float) -> set[str]:
    """보정된 워밍업 구간(+연결일 store 종가)에서 가격제한폭을 넘는 일간 변동이 있는 종목.

    수정주가 보정이 누락된 것으로 보고 워밍업 연결에서 제외한다.
    """
    link = daily.loc[daily["date"] == t0, ["ticker", "date", "close"]]
    s = pd.concat([w_scaled[["ticker", "date", "close"]], link], ignore_index=True)
    s = s[s["ticker"].isin(w_scaled["ticker"].unique())].sort_values(["ticker", "date"])
    s = s[s["close"] > 0]
    ret = s.groupby("ticker")["close"].pct_change()
    return set(s.loc[ret.abs() > max_abs_ret, "ticker"])


def attach_warmup(daily: pd.DataFrame, warm: pd.DataFrame | None, cfg: dict) -> pd.DataFrame:
    """store 일봉 앞에 보정된 워밍업 구간을 붙이고 is_warmup 컬럼을 추가한다."""
    dcfg = cfg["data"]
    t0 = pd.Timestamp(dcfg["backtest_start"])
    out = daily.copy()
    out["is_warmup"] = False
    if warm is None or warm.empty:
        return out

    ratio = link_ratios(daily, warm, dcfg["backtest_start"])
    w = warm[(warm["date"] >= pd.Timestamp(dcfg["warmup_source_start"])) & (warm["date"] < t0)]
    w = w[w["ticker"].isin(ratio.index)].copy()
    r = w["ticker"].map(ratio)
    for c in ("open", "high", "low", "close"):
        w[c] = w[c] * r
    bad = jump_tickers(w, daily, t0, float(dcfg["warmup_max_abs_daily_ret"]))
    w = w[~w["ticker"].isin(bad)]
    w["value"] = w["value_mil"] * float(dcfg["warmup_value_multiplier"])
    w = w.drop(columns=["value_mil"])
    # kor_price 휴장·정지일: 시가 0 또는 결측을 halted 로 표시
    w["halted"] = w["open"].isna() | (w["open"] <= 0) | (w["volume"] <= 0)
    w.loc[w["open"] <= 0, ["open", "high", "low"]] = np.nan
    w["is_warmup"] = True

    first_meta = (
        daily[daily["date"] == t0].set_index("ticker")[["market", "kind"]].astype(object)
    )
    w = w.join(first_meta, on="ticker")
    out = pd.concat([w, out], ignore_index=True, sort=False)
    for c in ("market", "dept", "kind"):
        if c in out.columns:
            out[c] = out[c].astype("category")
    out = out.sort_values(["ticker", "date"], kind="stable").reset_index(drop=True)
    return out


def first_trade_dates(daily: pd.DataFrame, first: pd.DataFrame | None, backtest_start: str) -> pd.Series:
    """U1 첫 거래일. store 가 backtest_start 부터 있는 종목만 kor_price 이력으로 앞당긴다."""
    t0 = pd.Timestamp(backtest_start)
    store_first = daily.loc[~daily.get("is_warmup", False)].groupby("ticker", observed=True)["date"].min()
    if first is None:
        return store_first
    kp = first.set_index("ticker")["first_date"]
    earlier = store_first[store_first == t0].index.intersection(kp.index)
    out = store_first.copy()
    out.loc[earlier] = np.minimum(kp.loc[earlier].values, store_first.loc[earlier].values)
    return out


def listed_trading_days(frame: pd.DataFrame, first_dates: pd.Series, warmup_source_start: str) -> pd.Series:
    """각 행 시점의 상장 후 거래일 수(해당일 포함).

    frame(워밍업 포함) 안의 누적 행 수로 세고, 첫 거래일이 warmup_source_start 이전인 종목은
    frame 밖에 이미 충분한 이력이 있으므로 큰 값을 더한다.
    """
    n = frame.groupby("ticker", observed=True).cumcount() + 1
    fd = frame["ticker"].map(first_dates)
    long_hist = fd < pd.Timestamp(warmup_source_start)
    return n + np.where(long_hist, LONG_HISTORY_OFFSET, 0)
