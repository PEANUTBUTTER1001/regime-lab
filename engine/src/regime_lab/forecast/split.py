"""시간 순서 학습/검증/최종 평가 분할 (P2-5, AGENTS.md §7 — 학습·검증·평가를 시간 순서로 나눈다).

as_of 날짜로 나누고, 목표 실현 시각이 구간 경계를 넘는 표본은 purged 로 뺀다
(학습 표본의 목표가 검증 기간 가격을 보지 않게).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SPLITS = ["train", "valid", "test"]


def assign_split(targets: pd.DataFrame, cfg: dict) -> pd.Series:
    """targets(as_of, realized_at) → train / valid / test / purged. 실현 시각이 없는 표본(목표 결측)도 purged."""
    scfg = cfg["forecast"]["split"]
    train_end = pd.Timestamp(scfg["train_end"]) + pd.Timedelta(days=1)  # 경계일 하루 끝까지
    valid_end = pd.Timestamp(scfg["valid_end"]) + pd.Timedelta(days=1)
    if train_end >= valid_end:
        raise ValueError("forecast.split.train_end 는 valid_end 보다 앞이어야 합니다")
    a, r = targets["as_of"], targets["realized_at"]
    out = np.select([a < train_end, a < valid_end], ["train", "valid"], "test").astype(object)
    end = np.select([a < train_end, a < valid_end], [train_end, valid_end], pd.NaT)
    crosses = pd.Series(pd.to_datetime(end), index=targets.index).lt(r) | r.isna()
    out[crosses.to_numpy()] = "purged"
    return pd.Series(out, index=targets.index, name="split")
