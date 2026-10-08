"""P2-5 목표·시간 분할·기준선·평가 틀 — 합성 수기 대조와 as_of 절단 불변."""

import numpy as np
import pandas as pd
import pytest

from regime_lab.forecast.baseline import historical, persistence
from regime_lab.forecast.evaluate import direction, score
from regime_lab.forecast.split import assign_split
from regime_lab.forecast.targets import make_targets


def _bars(closes, code="005930", start="2026-01-29", slots=(1, 2, 3), segments=None):
    """하루 len(slots) 개 봉을 거래일마다 이어 붙인 집계 봉 (available_at = 슬롯 끝 시각)."""
    rows, days = [], pd.bdate_range(start, periods=int(np.ceil(len(closes) / len(slots))))
    for i, c in enumerate(closes):
        d, s = days[i // len(slots)], slots[i % len(slots)]
        rows.append({"code": code, "date": d, "slot": s, "available_at": d + pd.Timedelta(hours=9 + s),
                     "close": float(c), "nxt_period": False})
    b = pd.DataFrame(rows)
    if segments is not None:
        b["segment"] = segments
    return b


def test_targets_hand_calculated():
    b = _bars([100, 110, 121, 100, 50, 55], segments=[0, 0, 0, 0, 1, 1])
    t = make_targets(b, 1)
    assert np.allclose(t["y"].iloc[:3], np.log([1.1, 1.1, 100 / 121]))
    assert np.isnan(t["y"].iloc[3])          # 다음 봉이 새 가격 구간 → 목표 없음
    assert np.isclose(t["y"].iloc[4], np.log(1.1)) and np.isnan(t["y"].iloc[5])  # 마지막 봉
    assert t["cross_day"].tolist() == [False, False, True, False, False, False]
    assert t["realized_at"].iloc[0] == b["available_at"].iloc[1] and pd.isna(t["realized_at"].iloc[3])
    t2 = make_targets(b, 2)
    assert np.isclose(t2["y"].iloc[0], np.log(1.21)) and t2["cross_day"].iloc[1]
    with pytest.raises(ValueError):
        make_targets(b, 0)


def test_targets_never_cross_codes():
    b = pd.concat([_bars([1, 2]), _bars([3, 4], code="000660")])
    t = make_targets(b, 1).sort_values(["code", "as_of"])
    assert t.groupby("code")["y"].apply(lambda s: s.isna().iloc[-1]).all()


def test_split_by_as_of_and_purge_boundary(cfg):
    c = {**cfg, "forecast": {**cfg["forecast"], "split": {"train_end": "2026-01-29", "valid_end": "2026-01-30"}}}
    b = _bars(range(1, 10))  # 01-29(목)·01-30(금)·02-02(월) 각 3봉
    t = make_targets(b, 1)
    s = assign_split(t, c)
    # 01-29 마지막 봉의 목표는 01-30 에 실현 → 경계를 넘어 purged. 마지막 표본은 목표 없음 → purged
    assert s.tolist() == ["train", "train", "purged", "valid", "valid", "purged", "test", "test", "purged"]
    with pytest.raises(ValueError):
        assign_split(t, {**c, "forecast": {**c["forecast"], "split": {"train_end": "2026-02-01", "valid_end": "2026-01-01"}}})


def _cfg(cfg, window=3, min_samples=2, by_slot=False):
    f = {**cfg["forecast"], "baseline": {"window": window, "min_samples": min_samples, "by_slot": by_slot}}
    return {**cfg, "forecast": f}


def test_persistence_is_flat_and_uncertain(cfg):
    t = make_targets(_bars([1, 2, 3]), 1)
    p = persistence(t, cfg)
    assert (p[["q0.1", "q0.5", "q0.9"]] == 0).all().all() and (direction(p["p_up"], cfg) == "uncertain").all()


def test_historical_uses_only_realized_targets(cfg):
    """h=2: as_of i 에서 쓸 수 있는 목표는 i-2 까지. 창 3·최소 2."""
    closes = [100, 110, 121, 133.1, 100, 90, 99]
    t = make_targets(_bars(closes), 2)
    p = historical(t, _cfg(cfg))
    y = t["y"].to_numpy()
    assert p["p_up"].iloc[:3].isna().all()            # i=2 까지는 실현된 목표가 1개 이하
    assert (p["reason"].iloc[:3] == "insufficient_history").all()
    # i=3 (as_of 셋째 날 1시) → 실현된 목표: 0, 1 (각각 2봉 뒤 = 봉 2·3 확정)
    assert np.isclose(p["q0.5"].iloc[3], np.median(y[:2])) and p["p_up"].iloc[3] == 1.0
    # i=6 → 실현된 목표 0..4, 창 3 = 2,3,4
    assert np.isclose(p["q0.5"].iloc[6], np.median(y[2:5]))
    assert np.isclose(p["p_up"].iloc[6], np.mean(y[2:5] > 0))


def test_historical_by_slot_compares_same_time_of_day(cfg):
    closes = [100, 200, 110, 100, 121, 300, 133.1, 150]  # slot 1 꾸준히 +10%, slot 2 크게 흔들림
    t = make_targets(_bars(closes, slots=(1, 2)), 2)
    p = historical(t, _cfg(cfg, window=2, min_samples=2, by_slot=True))
    s1 = t["slot"] == 1
    got = p.loc[s1, "q0.5"].dropna()
    # slot 1 표본 4·6 은 그 시각에 실현된 slot 1 목표 2개(모두 +10%)만 본다 — slot 2 의 큰 흔들림이 섞이지 않음
    assert got.index.tolist() == [4, 6] and np.allclose(got, np.log(1.1))


@pytest.mark.parametrize("cut", ["2026-02-03 11:00", "2026-02-05 10:00", "2026-02-09 12:00"])
def test_historical_truncation_invariant(cfg, cut):
    """as_of 이후 봉을 지워도 그 전 as_of 의 기준선 예측이 같다 (FR-F4)."""
    rng = np.random.default_rng(3)
    b = _bars(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 60))))
    c, T = _cfg(cfg, window=10, min_samples=3, by_slot=True), pd.Timestamp(cut)
    full_t = make_targets(b, 3)
    full = historical(full_t, c)[full_t["as_of"] <= T]
    part_t = make_targets(b[b["available_at"] <= T], 3)
    part = historical(part_t, c)
    pd.testing.assert_frame_equal(full.reset_index(drop=True), part.reset_index(drop=True))
    assert full["p_up"].notna().any()


def test_direction_thresholds(cfg):
    assert direction(pd.Series([0.55, 0.549, 0.45, 0.451, np.nan]), cfg).tolist() == \
        ["up", "uncertain", "down", "uncertain", np.nan]


def test_score_hand_calculated(cfg):
    t = pd.DataFrame({"y": [0.02, -0.01, 0.05, np.nan], "g": ["a", "a", "a", "a"]})
    pred = pd.DataFrame({"q0.1": [-0.01] * 4, "q0.5": [0.0] * 4, "q0.9": [0.03] * 4, "p_up": [0.6, 0.6, 0.5, 0.6]})
    s = score(t, pred, cfg, ["g"]).iloc[0]
    assert s["n"] == 3 and np.isclose(s["coverage"], 2 / 3)   # 0.05 는 구간 밖
    assert np.isclose(s["dir_share"], 2 / 3) and np.isclose(s["dir_hit"], 0.5)
    # 분위 손실 max(τ(y−q), (τ−1)(y−q)): 행마다 (0.003+0.01+0.001)/3, (0+0.005+0.004)/3, (0.006+0.025+0.018)/3
    pin = np.mean([(0.003 + 0.01 + 0.001) / 3, (0.0 + 0.005 + 0.004) / 3, (0.006 + 0.025 + 0.018) / 3])
    assert np.isclose(s["pinball"], pin)
