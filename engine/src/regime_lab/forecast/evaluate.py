"""예측 평가 틀 (P2-5.3, 채점 지표 확정은 P2-8). 백테스트 승률과 섞지 않는다.

지표 (h 하나, 묶음별):
- n            : 예측이 있고 목표가 실현된 표본 수
- coverage     : 실제 목표가 [가장 낮은 분위, 가장 높은 분위] 안에 든 비율 (0.1~0.9 면 목표 80%)
- pinball      : 분위 손실 평균 (분위별 평균의 평균, 낮을수록 좋음)
- dir_share    : "상승 쪽"·"하락 쪽"으로 방향을 낸 표본 비율 (나머지는 "불확실")
- dir_hit      : 방향을 낸 표본 중 실제 부호가 맞은 비율
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import binomtest


def direction(p_up: pd.Series, cfg: dict) -> pd.Series:
    """상승 확률 → up / down / uncertain (통계적 방향 — 화면은 E17 고정 문구와 함께 표시)."""
    d = cfg["forecast"]["direction"]
    return pd.Series(np.select([p_up >= d["up"], p_up <= d["down"]], ["up", "down"], "uncertain"),
                     index=p_up.index).where(p_up.notna())


def score(targets: pd.DataFrame, pred: pd.DataFrame, cfg: dict, by: list[str]) -> pd.DataFrame:
    """targets(y + 묶음 열) 와 같은 index 의 pred(q… 열, p_up) → 묶음별 지표 표."""
    qs = cfg["forecast"]["quantiles"]
    qcols = [f"q{q:g}" for q in qs]
    ok = targets["y"].notna() & pred["p_up"].notna()
    t, p = targets[ok], pred[ok]
    y = t["y"]
    pin = sum(np.maximum(q * (y - p[c]), (q - 1) * (y - p[c])) for q, c in zip(qs, qcols)) / len(qs)
    d = direction(p["p_up"], cfg)
    called = d.isin(["up", "down"])
    hit = ((d == "up") & (y > 0)) | ((d == "down") & (y < 0))
    frame = t[by].assign(cov=((y >= p[qcols[0]]) & (y <= p[qcols[-1]])).astype(float), pin=pin,
                         called=called.astype(float), hit=hit.where(called).astype(float))
    out = frame.groupby(by, dropna=False, observed=True).agg(
        n=("cov", "size"), coverage=("cov", "mean"), pinball=("pin", "mean"),
        dir_share=("called", "mean"), dir_hit=("hit", "mean")).reset_index()
    return out


def direction_policy(y: pd.Series, model_p_up: pd.Series, baseline_p_up: pd.Series, cfg: dict) -> dict:
    """P2-8: 이 봉·h 에서 화면에 방향("상승 쪽"·"하락 쪽")을 보여도 되는지 판정 표본(y 와 같은 index)으로 정한다.

    조건 (모두): 모델이 방향을 낸 표본이 min_called 이상, 그 적중률이 0.5 보다 크다는 단측 이항검정 p < alpha,
    그리고 같은 표본에서 기준선(과거 실현 분위) 방향 적중률보다 높음. 아니면 화면은 "불확실".
    판정 표본은 호출자가 고른다 — 모델 보정에 쓴 표본·최종 평가 구간이 아니어야 한다(낙관 편향).
    """
    pcfg = cfg["forecast"]["direction_policy"]
    va = y.notna()
    y = y[va]

    def hits(p_up: pd.Series) -> tuple[int, int]:
        d = direction(p_up[va], cfg)
        called = d.isin(["up", "down"])
        ok = ((d == "up") & (y > 0)) | ((d == "down") & (y < 0))
        return int(called.sum()), int((ok & called).sum())

    n, k = hits(model_p_up)
    bn, bk = hits(baseline_p_up)
    p = float(binomtest(k, n, 0.5, alternative="greater").pvalue) if n else 1.0
    hit, base_hit = (k / n if n else None), (bk / bn if bn else None)
    # 기준선이 방향을 한 번도 안 내면(항상 "불확실") 동전과 같다고 보고 0.5 와 비교한다 (seongmin-claude #54)
    base_cmp = base_hit if base_hit is not None else 0.5
    enabled = (n >= pcfg["min_called"] and p < pcfg["alpha"] and hit > base_cmp)
    return {"enabled": bool(enabled), "valid_called": n, "valid_hit": hit, "p_value": p,
            "baseline_called": bn, "baseline_hit": base_hit, "min_called": pcfg["min_called"], "alpha": pcfg["alpha"]}
