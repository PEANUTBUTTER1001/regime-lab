"""S7 패턴 5종 수기 사례 (A7, FR-P1~P4)."""

import numpy as np
import pandas as pd
import pytest
from synth import make_frame

from regime_lab.indicators import compute_indicators
from regime_lab.patterns import CORE_PATTERNS, combine_signals, compute_signals, get_pattern


def _sig(df, name, cfg):
    return get_pattern(name, cfg).signal(compute_indicators(df, cfg))


def test_five_core_patterns_registered():
    assert set(CORE_PATTERNS) == {"ma_cross_5_20", "breakout_20d", "breakout_vol", "rsi_rebound", "bb_lower_recover"}


def test_ma_cross(cfg):
    # 30일 하락 후 상승 전환 → SMA5 가 SMA20 을 상향 돌파하는 첫날 1회만 신호
    closes = list(np.linspace(130, 100, 30)) + list(np.linspace(101, 130, 15))
    df = make_frame(closes)
    ind = compute_indicators(df, cfg)
    s = get_pattern("ma_cross_5_20", cfg).signal(ind)
    assert s.sum() == 1
    t = s.idxmax()
    assert ind.loc[t - 1, "sma5"] <= ind.loc[t - 1, "sma20"] and ind.loc[t, "sma5"] > ind.loc[t, "sma20"]


def test_breakout_20d_uses_prior_highs_only(cfg):
    closes = [100.0] * 25
    df = make_frame(closes)
    df.loc[10, "high"] = 110.0
    df.loc[24, ["close", "high"]] = [109.0, 115.0]  # 당일 고가는 비교 대상 아님
    assert not _sig(df, "breakout_20d", cfg).iloc[24]  # 109 < 직전 20일 고가 110
    df.loc[24, "close"] = 111.0
    assert _sig(df, "breakout_20d", cfg).iloc[24]
    df.loc[10, "high"] = 100.0
    df.loc[3, "high"] = 200.0  # t-21 이전 고가는 창 밖
    df.loc[24, "close"] = 101.0
    assert _sig(df, "breakout_20d", cfg).iloc[24]


def test_breakout_vol(cfg):
    df = make_frame([100.0] * 24 + [105.0])
    df.loc[24, "volume"] = 1999.0  # 평균 1000 × 2.0 미만
    assert _sig(df, "breakout_20d", cfg).iloc[24]
    assert not _sig(df, "breakout_vol", cfg).iloc[24]
    df.loc[24, "volume"] = 2000.0
    assert _sig(df, "breakout_vol", cfg).iloc[24]


def test_rsi_rebound(cfg):
    closes = list(np.linspace(200, 100, 20)) + [101, 103, 106, 110, 115, 121, 128]
    df = make_frame(closes)
    ind = compute_indicators(df, cfg)
    s = get_pattern("rsi_rebound", cfg).signal(ind)
    assert s.sum() >= 1
    t = s.idxmax()
    assert ind.loc[t - 1, "rsi14"] < 30 <= ind.loc[t, "rsi14"]


def test_bb_lower_recover(cfg):
    closes = [100.0, 101.0] * 12 + [90.0, 100.0]
    df = make_frame(closes)
    ind = compute_indicators(df, cfg)
    s = get_pattern("bb_lower_recover", cfg).signal(ind)
    assert ind.loc[24, "close"] < ind.loc[24, "bb_lower"]
    assert s.iloc[25] and s.sum() == 1


def test_no_signal_on_halted_day(cfg):
    df = make_frame([100.0] * 24 + [105.0])
    df.loc[24, "halted"] = True
    assert not _sig(df, "breakout_20d", cfg).iloc[24]


def test_combine_and_or():
    a = pd.Series([True, True, False, False])
    b = pd.Series([True, False, True, False])
    assert combine_signals([a, b], "and").tolist() == [True, False, False, False]
    assert combine_signals([a, b], "OR").tolist() == [True, True, True, False]
    with pytest.raises(ValueError):
        combine_signals([a, b], "xor")


def test_params_come_from_config(cfg):
    import copy

    c2 = copy.deepcopy(cfg)
    c2["patterns"]["breakout_vol"]["volume_mult"] = 1.5
    df = make_frame([100.0] * 24 + [105.0])
    df.loc[24, "volume"] = 1600.0
    assert not _sig(df, "breakout_vol", cfg).iloc[24]
    assert _sig(df, "breakout_vol", c2).iloc[24]


def test_compute_signals_multi_ticker(cfg):
    df = pd.concat([make_frame([100.0] * 24 + [105.0], "A"), make_frame([100.0] * 25, "B")], ignore_index=True)
    s = compute_signals(compute_indicators(df, cfg), ["breakout_20d", "breakout_vol"], "or", cfg)
    assert s.iloc[24] and not s.iloc[25:].any()
