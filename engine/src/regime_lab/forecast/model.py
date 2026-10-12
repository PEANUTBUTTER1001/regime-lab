"""예측 모델 (P2-6, scikit-learn — 2026-10-08 Seonghwanaa 승인).

- 회색 구간: 분위마다 HistGradientBoostingRegressor(loss=quantile). 분위가 엇갈리면 행마다 정렬한다.
- 구간 보정(conformal): 검증 구간에서 실제가 [아래, 위] 밖으로 나간 정도의 (위·아래 분위 차) 분위를 구해
  양쪽으로 넓힌다 — 학습 구간 과적합으로 구간이 좁아지는 것을 막는다.
- 상승 확률: HistGradientBoostingClassifier(y > 0) 를 학습 구간에서 맞추고, 검증 구간에서 isotonic 으로 보정한다.
학습은 학습 구간, 보정은 검증 구간만 쓴다(시간 순서). 최종 평가 구간은 맞추는 데 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression


@dataclass
class FittedModel:
    quantiles: list[float]
    regressors: dict = field(default_factory=dict)
    classifier: HistGradientBoostingClassifier | None = None
    calibrator: IsotonicRegression | None = None
    widen: float = 0.0
    features: list[str] = field(default_factory=list)


def _params(mcfg: dict) -> dict:
    return {k: mcfg[k] for k in ("max_iter", "learning_rate", "max_leaf_nodes", "min_samples_leaf", "l2_regularization")} | {
        "random_state": mcfg["random_state"], "early_stopping": False}


def _subsample(X: pd.DataFrame, y: pd.Series, n: int, seed: int) -> tuple[pd.DataFrame, pd.Series]:
    if len(X) <= n:
        return X, y
    idx = np.random.default_rng(seed).choice(len(X), size=n, replace=False)
    idx.sort()
    return X.iloc[idx], y.iloc[idx]


def fit(X_train: pd.DataFrame, y_train: pd.Series, X_valid: pd.DataFrame, y_valid: pd.Series, cfg: dict) -> FittedModel:
    fcfg = cfg["forecast"]
    mcfg = fcfg["model"]
    qs = list(fcfg["quantiles"])
    Xt, yt = _subsample(X_train, y_train, mcfg["max_train_rows"], mcfg["random_state"])
    m = FittedModel(quantiles=qs, features=list(X_train.columns))
    for q in qs:
        m.regressors[q] = HistGradientBoostingRegressor(loss="quantile", quantile=q, **_params(mcfg)).fit(Xt, yt)
    m.classifier = HistGradientBoostingClassifier(**_params(mcfg)).fit(Xt, (yt > 0).astype(int))
    if len(X_valid):
        raw = predict(m, X_valid)
        lo, hi = raw[f"q{qs[0]:g}"].to_numpy(), raw[f"q{qs[-1]:g}"].to_numpy()
        excess = np.maximum(lo - y_valid.to_numpy(), y_valid.to_numpy() - hi)
        if mcfg["conformal"]:
            target = qs[-1] - qs[0]  # 0.1~0.9 → 80%
            m.widen = float(max(0.0, np.quantile(excess, target)))
        m.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(
            raw["p_up_raw"].to_numpy(), (y_valid.to_numpy() > 0).astype(float))
    return m


def predict(m: FittedModel, X: pd.DataFrame) -> pd.DataFrame:
    """특징 → q{분위}·p_up (보정 뒤), p_up_raw (보정 전). 분위는 행마다 오름차순으로 맞추고 conformal 폭만큼 넓힌다."""
    X = X[m.features]
    qv = np.column_stack([m.regressors[q].predict(X) for q in m.quantiles]) if len(X) else np.empty((0, len(m.quantiles)))
    qv.sort(axis=1)
    if len(m.quantiles) >= 2:
        qv[:, 0] -= m.widen
        qv[:, -1] += m.widen
    out = pd.DataFrame(qv, columns=[f"q{q:g}" for q in m.quantiles], index=X.index)
    p_raw = m.classifier.predict_proba(X)[:, 1] if len(X) else np.empty(0)
    out["p_up_raw"] = p_raw
    out["p_up"] = m.calibrator.predict(p_raw) if m.calibrator is not None else p_raw
    out["reason"] = None
    return out
