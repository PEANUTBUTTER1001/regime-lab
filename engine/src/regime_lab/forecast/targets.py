"""as_of 표본과 h 봉 뒤 실현 목표 (P2-5, 설계 §2 N3·§5).

표본 하나 = 확정된 집계 봉 하나. as_of = 그 봉의 확정 시각, 기준 가격 = 그 봉 종가.
목표 y = log(h 봉 뒤 종가 / 기준 가격). 같은 종목·같은 가격 구간(segment) 안에서만 만들고, 구간이 바뀌면 결측이다.
realized_at = 목표 봉의 확정 시각 — 그 전에는 이 목표를 학습·기준선에 쓸 수 없다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SAMPLE_COLS = ["code", "date", "slot", "as_of", "nxt_period"]


def make_targets(bars: pd.DataFrame, h: int) -> pd.DataFrame:
    """집계 봉(code, date, slot, available_at, close, nxt_period[, segment]) → h 봉 뒤 목표.

    결과 열: code, date, slot, as_of, nxt_period, y, realized_at, cross_day(목표 봉이 다른 거래일이면 True —
    밤사이 갭 포함). 마지막 h 개 봉과 구간이 바뀌는 표본은 y 결측.
    """
    if h < 1:
        raise ValueError(f"h 는 1 이상이어야 합니다: {h}")
    b = bars.sort_values(["code", "available_at"], kind="stable").reset_index(drop=True)
    g = b.groupby("code", sort=False)
    fut_close = g["close"].shift(-h)
    same = g["code"].shift(-h).notna()
    if "segment" in b:
        same &= g["segment"].shift(-h).eq(b["segment"])
    y = np.log(fut_close / b["close"]).where(same)
    out = b[["code", "date", "slot", "available_at", "nxt_period"]].rename(columns={"available_at": "as_of"})
    out["y"] = y
    out["realized_at"] = g["available_at"].shift(-h).where(same)
    out["cross_day"] = g["date"].shift(-h).ne(b["date"]) & same
    return out
