"""단순 기준선 (P2-5.1). 모델(P2-6)은 이 기준선보다 나아야 의미가 있다.

persistence : 마지막 값 유지 — 분위 모두 0, 상승 확률 0.5 (방향은 항상 "불확실")
historical  : 같은 종목(·같은 slot)의 과거 실현 목표 분위와 상승 비율. as_of 에 이미 실현된 목표만 쓴다
              (realized_at ≤ as_of). 표본이 min_samples 보다 적으면 예측하지 않는다.
결과 열: q{분위} (예: q0.1·q0.5·q0.9), p_up, reason(예측 못 한 사유, 예측했으면 None).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _qcol(q: float) -> str:
    return f"q{q:g}"


def persistence(targets: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = pd.DataFrame({_qcol(q): 0.0 for q in cfg["forecast"]["quantiles"]}, index=targets.index)
    out["p_up"] = 0.5
    out["reason"] = None
    return out


def historical(targets: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    bcfg = cfg["forecast"]["baseline"]
    qs = cfg["forecast"]["quantiles"]
    keys = ["code", "slot"] if bcfg["by_slot"] else ["code"]
    t = targets.sort_values(["code", "as_of"], kind="stable")
    cols = {_qcol(q): np.full(len(t), np.nan) for q in qs}
    p_up = np.full(len(t), np.nan)
    pos = 0
    order = []
    for _, g in t.groupby(keys, sort=False):
        idx = g.index.to_numpy()
        valid = g["y"].notna().to_numpy()
        as_of = g["as_of"].to_numpy(dtype="datetime64[ns]")
        # 창은 목표가 있는 표본만 센다. 그 실현 시각은 표본 순서대로 늘어나므로 as_of 에 이미 실현된 것은 앞쪽 k 개
        realized = g["realized_at"].to_numpy(dtype="datetime64[ns]")[valid]
        k = np.searchsorted(np.maximum.accumulate(realized), as_of, side="right") if len(realized) else np.zeros(len(g), int)
        ys = pd.Series(g["y"].to_numpy(dtype=float)[valid])
        roll = ys.rolling(bcfg["window"], min_periods=bcfg["min_samples"])
        stats = {_qcol(q): roll.quantile(q).to_numpy() for q in qs}
        up = (ys > 0).astype(float).rolling(bcfg["window"], min_periods=bcfg["min_samples"]).mean().to_numpy()
        take = k - 1  # k 개가 쓸 수 있으면 0..k-1 까지의 창 값
        ok = take >= 0
        if not len(ys):
            stats = {c: np.array([np.nan]) for c in stats}
            up = np.array([np.nan])
        sl = slice(pos, pos + len(g))
        for c, v in stats.items():
            cols[c][sl] = np.where(ok, v[np.maximum(take, 0)], np.nan)
        p_up[sl] = np.where(ok, up[np.maximum(take, 0)], np.nan)
        order.append(idx)
        pos += len(g)
    index = np.concatenate(order) if order else np.array([], dtype=int)
    out = pd.DataFrame({**cols, "p_up": p_up}, index=index).reindex(targets.index)
    out["reason"] = np.where(out["p_up"].isna(), "insufficient_history", None)
    return out
