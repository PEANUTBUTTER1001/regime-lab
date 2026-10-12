"""P2-6 특징·모델 — 특징 절단 불변, 수기 대조, 재현성, 분위 정렬·구간 보정 (합성 데이터)."""

import numpy as np
import pandas as pd
import pytest

from regime_lab.forecast import model as M
from regime_lab.forecast.features import FEATURES, make_features
from regime_lab.forecast.split import assign_split
from regime_lab.forecast.targets import make_targets


def _bars(n_days=40, codes=("005930", "000660"), slots=(1, 2, 3), seed=0, start="2025-12-01"):
    rng = np.random.default_rng(seed)
    rows = []
    for code in codes:
        price = 1000.0
        for d in pd.bdate_range(start, periods=n_days):
            for s in slots:
                o = price
                price = price * np.exp(rng.normal(0, 0.01))
                rows.append({"code": code, "date": d, "slot": s, "available_at": d + pd.Timedelta(hours=9 + s),
                             "open": o, "high": max(o, price) * 1.002, "low": min(o, price) * 0.998, "close": price,
                             "volume": float(rng.integers(100, 1000)), "value_1m": float(rng.integers(10**5, 10**6)),
                             "nxt_period": d >= pd.Timestamp("2026-04-24"), "segment": 0})
    return pd.DataFrame(rows)


def test_features_hand_calculated():
    b = _bars(n_days=3, codes=("005930",))
    f = make_features(b)
    c = b["close"].to_numpy()
    assert list(f.columns) == ["code", "as_of"] + FEATURES
    assert np.isclose(f["r1"].iloc[4], np.log(c[4] / c[3])) and np.isnan(f["r3"].iloc[2])
    # 둘째 날 slot 2: 당일 수익률 = 지금 종가 / 전날 마지막 종가, 갭 = 당일 첫 봉 시가 / 전날 마지막 종가
    assert np.isclose(f["day_ret"].iloc[4], np.log(c[4] / c[2]))
    assert np.isclose(f["gap"].iloc[4], np.log(b["open"].iloc[3] / c[2]))
    assert np.isnan(f["day_ret"].iloc[0])  # 첫날은 전날 종가 없음
    assert f["slot"].tolist()[:3] == [1, 2, 3]


def test_returns_do_not_cross_price_segments():
    b = _bars(n_days=3, codes=("005930",))
    b.loc[b["date"] == b["date"].max(), "segment"] = 1
    f = make_features(b)
    assert f.loc[6:, "r1"].isna().iloc[0] and f["day_ret"].iloc[6:].isna().all()


@pytest.mark.parametrize("cut", ["2025-12-10 11:00", "2025-12-24 10:00", "2026-01-09 12:00"])
def test_features_truncation_invariant(cut):
    """as_of 이후 봉을 지워도 그 전 as_of 의 특징이 같다 (FR-F4, 시장 특징 포함)."""
    b, T = _bars(), pd.Timestamp(cut)
    full = make_features(b)
    full = full[full["as_of"] <= T].reset_index(drop=True)
    part = make_features(b[b["available_at"] <= T]).reset_index(drop=True)
    pd.testing.assert_frame_equal(full, part)


def _small_cfg(cfg):
    f = {**cfg["forecast"], "split": {"train_end": "2026-01-09", "valid_end": "2026-01-23"},
         "model": {**cfg["forecast"]["model"], "max_iter": 30, "min_samples_leaf": 20}}
    return {**cfg, "forecast": f}


def _fit(cfg, seed=0):
    b = _bars(n_days=60, seed=seed)
    f, t = make_features(b), make_targets(b, 2)
    t["split"] = assign_split(t, cfg)
    tr, va = t["split"].eq("train"), t["split"].eq("valid")
    m = M.fit(f.loc[tr, FEATURES], t.loc[tr, "y"], f.loc[va, FEATURES], t.loc[va, "y"], cfg)
    return m, f, t


def test_model_reproducible_and_quantiles_ordered(cfg):
    c = _small_cfg(cfg)
    m1, f, t = _fit(c)
    m2, _, _ = _fit(c)
    p1, p2 = M.predict(m1, f[FEATURES]), M.predict(m2, f[FEATURES])
    pd.testing.assert_frame_equal(p1, p2)  # 같은 데이터·설정·시드 → 같은 예측
    assert (p1["q0.1"] <= p1["q0.5"]).all() and (p1["q0.5"] <= p1["q0.9"]).all()
    assert p1["p_up"].between(0, 1).all() and m1.widen >= 0


def test_conformal_brings_validation_coverage_to_target(cfg):
    c = _small_cfg(cfg)
    m, f, t = _fit(c, seed=1)
    va = t["split"].eq("valid") & t["y"].notna()
    p = M.predict(m, f.loc[va, FEATURES])
    cov = ((t.loc[va, "y"] >= p["q0.1"]) & (t.loc[va, "y"] <= p["q0.9"])).mean()
    assert cov >= 0.79  # 검증 구간에서 80% 근처로 넓혀짐 (넓히기만 하므로 이상)


def test_training_ignores_test_period(cfg):
    """최종 평가 구간 목표를 바꿔도 모델이 같다 — 학습·보정은 학습·검증 구간만 본다."""
    c = _small_cfg(cfg)
    b = _bars(n_days=60, seed=2)
    f = make_features(b)
    preds = []
    for scale in (1.0, 5.0):
        t = make_targets(b, 2)
        t["split"] = assign_split(t, c)
        t.loc[t["split"] == "test", "y"] *= scale
        tr, va = t["split"].eq("train"), t["split"].eq("valid")
        m = M.fit(f.loc[tr, FEATURES], t.loc[tr, "y"], f.loc[va, FEATURES], t.loc[va, "y"], c)
        preds.append(M.predict(m, f[FEATURES]))
    pd.testing.assert_frame_equal(preds[0], preds[1])
