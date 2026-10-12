"""P2-8 방향 표시 판정·검증 구간 나누기 — 수기 대조 (합성 데이터)."""

import numpy as np
import pandas as pd

from regime_lab.forecast.evaluate import direction_policy
from regime_lab.forecast_runs import _split_valid


def _cfg(cfg, min_called=10, alpha=0.05):
    f = {**cfg["forecast"], "direction_policy": {"min_called": min_called, "alpha": alpha, "holdout_share": 0.5}}
    return {**cfg, "forecast": f}


def test_policy_enabled_only_when_significant_and_better_than_baseline(cfg):
    y = pd.Series([0.01] * 80 + [-0.01] * 20)          # 80% 상승
    up = pd.Series([0.8] * 100)                          # 모델: 항상 "상승 쪽" → 적중 80/100
    flat = pd.Series([0.5] * 100)                        # 기준선: 방향 안 냄
    coin = pd.Series([0.6, 0.4] * 50)                    # 기준선: 반은 상승·반은 하락
    p = direction_policy(y, up, coin, _cfg(cfg))
    assert p["valid_called"] == 100 and np.isclose(p["valid_hit"], 0.8) and p["p_value"] < 1e-6
    assert np.isclose(p["baseline_hit"], 0.5) and p["enabled"]
    # 기준선이 방향을 하나도 안 내면 비교할 수 없어 표시 안 함
    assert not direction_policy(y, up, flat, _cfg(cfg))["enabled"]
    # 표본이 min_called 보다 적으면 표시 안 함
    assert not direction_policy(y, up, coin, _cfg(cfg, min_called=101))["enabled"]


def test_policy_disabled_for_coin_flip(cfg):
    rng = np.random.default_rng(0)
    y = pd.Series(rng.normal(0, 0.01, 2000))
    model = pd.Series(rng.uniform(0, 1, 2000))           # 아무 정보 없는 확률
    base = pd.Series(rng.uniform(0, 1, 2000))
    p = direction_policy(y, model, base, _cfg(cfg, min_called=200))
    assert not p["enabled"] and p["p_value"] > 0.05
    # 결측 목표는 판정에서 빠짐
    y2 = y.copy()
    y2.iloc[:1000] = np.nan
    assert direction_policy(y2, model, base, _cfg(cfg))["valid_called"] <= 1000


def test_validation_split_is_chronological_and_disjoint():
    days = pd.bdate_range("2026-02-02", periods=10)
    t = pd.DataFrame({"as_of": [d + pd.Timedelta(hours=h) for d in days for h in (10, 11)]})
    va = pd.Series(True, index=t.index)
    va.iloc[:2] = False  # 첫날은 검증 아님
    cal, pol = _split_valid(t, va, 0.5)
    assert not (cal & pol).any() and (cal | pol).equals(va)
    assert t.loc[cal, "as_of"].max() < t.loc[pol, "as_of"].min()   # 앞부분 보정, 뒷부분 판정
    assert t.loc[pol, "as_of"].dt.normalize().nunique() == 4 or t.loc[pol, "as_of"].dt.normalize().nunique() == 5
    # 검증 거래일이 하루뿐이면 판정 표본 없음 (표시 안 함으로 이어짐)
    one = pd.Series([False] * 18 + [True, True])
    c1, p1 = _split_valid(t, one, 0.5)
    assert c1.equals(one) and not p1.any()
