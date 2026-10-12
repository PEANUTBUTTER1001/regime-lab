"""예측 입력 특징 (P2-6.1). 표본 = 확정된 집계 봉 하나, as_of = 그 봉 확정 시각.

모든 특징은 as_of 에 확정된 봉만 쓴다(FR-F3·F4). 수익률은 같은 종목·같은 가격 구간(N3) 안에서만 만들고
구간이 바뀌면 결측이다. 시장 특징은 같은 as_of 의 다른 종목 값 평균(그 시각에 이미 확정된 값)이다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RET_LAGS = (1, 3, 7, 14)
VOL_WINDOWS = (7, 35)
REL_DAYS = 20  # 거래량·거래대금 상대값: 같은 slot 의 직전 20개 봉 평균 대비

FEATURES = ([f"r{k}" for k in RET_LAGS] + [f"vol{w}" for w in VOL_WINDOWS]
            + ["range", "day_ret", "gap", "vol_rel", "value_rel", "slot", "dow", "nxt",
               "mkt_r1", "mkt_r7"])


def make_features(bars: pd.DataFrame) -> pd.DataFrame:
    """집계 봉(code, date, slot, available_at, open, high, low, close, volume, value_1m, nxt_period[, segment])
    → 표본별 특징. 열: code, as_of, FEATURES. index 는 (code, available_at) 정렬 순서."""
    b = bars.sort_values(["code", "available_at"], kind="stable").reset_index(drop=True)
    seg = b["segment"] if "segment" in b else pd.Series(0, index=b.index)
    key = [b["code"], seg]
    g = b.groupby(key, sort=False)
    lc = np.log(b["close"])
    out = pd.DataFrame({"code": b["code"], "as_of": b["available_at"]})
    for k in RET_LAGS:
        out[f"r{k}"] = lc - g["close"].shift(k).pipe(np.log)
    r1 = out["r1"]
    for w in VOL_WINDOWS:
        out[f"vol{w}"] = r1.groupby(key, sort=False).transform(lambda s: s.rolling(w, min_periods=max(3, w // 2)).std())
    out["range"] = np.log(b["high"] / b["low"])
    # 당일 첫 봉 시가(그날 첫 분 뒤 앎)와 직전 거래일 마지막 종가(같은 구간 안에서만). 당일 종가는 쓰지 않는다
    days = (b.assign(seg=seg).groupby(["code", "date"], sort=False)
            .agg(first_open=("open", "first"), last_close=("close", "last"), seg=("seg", "first")).reset_index())
    days["prev_close"] = days.groupby(["code", "seg"], sort=False)["last_close"].shift(1)
    d = b[["code", "date"]].merge(days[["code", "date", "first_open", "prev_close"]], on=["code", "date"], how="left")
    out["day_ret"] = lc - np.log(d["prev_close"].to_numpy())
    out["gap"] = np.log(d["first_open"].to_numpy()) - np.log(d["prev_close"].to_numpy())
    # 거래량·거래대금: 같은 종목·slot 의 직전 REL_DAYS 개 봉 평균 대비 (현재 봉은 분자에만)
    gs = b.groupby(["code", "slot"], sort=False)
    for col, name in (("volume", "vol_rel"), ("value_1m", "value_rel")):
        prior = gs[col].transform(lambda s: s.shift(1).rolling(REL_DAYS, min_periods=5).mean())
        out[name] = np.log1p(b[col]) - np.log1p(prior)
    out["slot"] = b["slot"].astype(int)
    out["dow"] = b["date"].dt.dayofweek.astype(int)
    out["nxt"] = b["nxt_period"].astype(int)
    # 시장: 같은 확정 시각의 종목 평균
    for k in (1, 7):
        out[f"mkt_r{k}"] = out.groupby("as_of")[f"r{k}"].transform("mean")
    return out
