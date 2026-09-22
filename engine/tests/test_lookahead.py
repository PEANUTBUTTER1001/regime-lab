"""test_lookahead (FR-D5, FR-P3, FR-E8, NFR-4): 입력 끝을 잘라도 앞 구간 결과가 불변."""

import numpy as np
import pandas as pd
import pytest
from synth import make_frame

from regime_lab.indicators import compute_indicators
from regime_lab.regime import attach_regimes, first_computable_date, market_regime

CUTS = ["2021-03-15", "2022-06-30", "2024-02-29"]
IND_COLS = ["sma5", "sma20", "sma200", "rsi14", "bb_lower", "bb_upper", "vwap20", "vwap20_prev",
            "high_max_prev20", "vol_mean_prev20", "avg_value20"]


def _assert_prefix_equal(full: pd.DataFrame, cut: pd.DataFrame, cols, key=("ticker", "date")):
    a = full.set_index(list(key)).loc[cut.set_index(list(key)).index, cols]
    b = cut.set_index(list(key))[cols]
    for c in cols:
        x, y = a[c], b[c]
        if isinstance(x.dtype, pd.CategoricalDtype) or x.dtype == object:
            assert (x.astype(object).fillna("NA") == y.astype(object).fillna("NA")).all(), c
        else:
            assert np.allclose(x.to_numpy(float), y.to_numpy(float), equal_nan=True, rtol=0, atol=1e-9), c


def test_synthetic_future_shock_does_not_leak(cfg):
    rng = np.random.default_rng(0)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    full = make_frame(closes)
    shocked = full.copy()
    shocked.loc[300:, ["open", "high", "low", "close"]] *= 3  # 300일 이후 미래 충격
    a = attach_regimes(compute_indicators(full, cfg), pd.DataFrame(columns=["date", "market", "close"]), cfg)
    b = attach_regimes(compute_indicators(shocked, cfg), pd.DataFrame(columns=["date", "market", "close"]), cfg)
    _assert_prefix_equal(a, b.iloc[:300], IND_COLS + ["stock_regime"])


@pytest.mark.data
@pytest.mark.parametrize("cut", CUTS)
def test_indicators_and_regimes_truncation(sample_frame, store, cfg, cut):
    from regime_lab.data.loader import load_index

    idx = load_index(store)
    full = attach_regimes(compute_indicators(sample_frame, cfg), idx, cfg)
    part_in = sample_frame[sample_frame["date"] <= cut]
    part = attach_regimes(compute_indicators(part_in, cfg), idx[idx["date"] <= cut], cfg)
    _assert_prefix_equal(full, part, IND_COLS + ["stock_regime", "market_regime"])


@pytest.mark.data
def test_market_regime_start(store, cfg):
    """A2-1: 지수가 2020-09-01 부터라 200일선+20일 변화율은 2021-07-21 에 처음 산출된다."""
    from regime_lab.data.loader import load_index

    idx = load_index(store)
    first = first_computable_date(idx, cfg)
    assert (first == pd.Timestamp("2021-07-21")).all()
    mr = market_regime(idx, cfg)
    assert mr.loc[mr["date"] < "2021-07-21", "market_regime"].isna().all()
    assert mr.loc[mr["date"] >= "2021-07-21", "market_regime"].notna().all()


@pytest.mark.data
@pytest.mark.parametrize("cut", CUTS)
def test_pattern_signals_truncation(sample_frame, cfg, cut):
    from regime_lab.patterns import CORE_PATTERNS, get_pattern

    full = compute_indicators(sample_frame, cfg)
    part = compute_indicators(sample_frame[sample_frame["date"] <= cut], cfg)
    for name in CORE_PATTERNS:
        a = get_pattern(name, cfg).signal(full)[full["date"] <= cut].to_numpy()
        b = get_pattern(name, cfg).signal(part).to_numpy()
        assert (a == b).all(), name


@pytest.mark.data
@pytest.mark.parametrize("cut", CUTS)
def test_backtest_truncation(sample_prepared, paths, cfg, cut):
    """cut 이전에 청산이 끝난 거래는 cut 이후 데이터 유무와 무관하게 동일하다."""
    from regime_lab.backtest import run_backtest
    from regime_lab.data.loader import load_sample_tickers
    from regime_lab.patterns import CORE_PATTERNS, compute_signals
    from regime_lab.pipeline import prepare

    full = sample_prepared
    part = prepare(paths, cfg, load_sample_tickers(paths.store), end=cut)
    cols = ["ticker", "signal_date", "entry_date", "exit_date", "entry_price", "exit_price", "exit_reason",
            "net_ret", "index_ret", "stock_regime", "market_regime", "cap_group"]
    for name in CORE_PATTERNS:
        a, _ = run_backtest(full.frame, compute_signals(full.frame, [name], "or", cfg), full.index, cfg,
                            full.delisted, full.sectors)
        b, _ = run_backtest(part.frame, compute_signals(part.frame, [name], "or", cfg), part.index, cfg,
                            full.delisted, full.sectors)
        a = a[a["exit_date"] < pd.Timestamp(cut)][cols].reset_index(drop=True)
        b = b[(b["exit_date"] < pd.Timestamp(cut)) & (b["exit_reason"] != "end_of_data")][cols]
        b = b.reset_index(drop=True)
        assert len(a) > 0 or name in ("bb_lower_recover", "rsi_rebound")
        pd.testing.assert_frame_equal(a.astype(str), b.astype(str), obj=name)
