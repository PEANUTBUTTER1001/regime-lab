"""test_cost (FR-E4): 비용 차감액이 거래 수 × 비용률과 일치."""

import numpy as np
import pandas as pd
import pytest
from synth import make_frame

from regime_lab.backtest import run_backtest, summarize
from regime_lab.backtest.metrics import equity_curve, max_drawdown


def _idx(df):
    d = df["date"].drop_duplicates()
    return pd.DataFrame({"date": d, "market": "KOSPI", "open": 1.0, "close": 1.0})


def test_cost_per_trade_and_total(cfg):
    df = make_frame(np.full(120, 100.0))
    sig = pd.Series(False, index=df.index)
    sig.iloc[[5, 30, 60, 90]] = True
    tr, sk = run_backtest(df, sig, _idx(df), cfg)
    assert len(tr) == 4
    assert np.allclose(tr["cost"], 0.003)
    assert np.allclose(tr["net_ret"], tr["gross_ret"] - 0.003)
    s = summarize(tr, sk, df)
    assert s["total_cost"] == pytest.approx(4 * 0.003)
    assert s["mean_ret"] == pytest.approx(-0.003)


def test_cost_rate_from_config(cfg):
    import copy

    c2 = copy.deepcopy(cfg)
    c2["execution"]["round_trip_cost_pct"] = 0.5
    df = make_frame(np.full(40, 100.0))
    sig = pd.Series(False, index=df.index)
    sig.iloc[5] = True
    tr, _ = run_backtest(df, sig, _idx(df), c2)
    assert tr.iloc[0].net_ret == pytest.approx(-0.005)


def test_equity_curve_equal_weight_and_mdd(cfg):
    a = make_frame([100, 100, 100, 110, 99, 99, 99], ticker="A")
    b = make_frame([100, 100, 100, 100, 100, 100, 100], ticker="B")
    df = pd.concat([a, b], ignore_index=True)
    tr = pd.DataFrame({
        "ticker": ["A", "B"], "entry_date": [a.date[2], b.date[2]], "exit_date": [a.date[5], b.date[5]],
        "entry_price": [100.0, 100.0], "exit_price": [99.0, 100.0], "exit_at_close": [False, False],
        "cost": [0.0, 0.0],
    })
    eq = equity_curve(tr, df)
    # 일별: day2 0, day3 (0.10+0)/2, day4 (-0.10+0)/2, day5 (0+0)/2
    assert np.allclose(eq["ret"].to_numpy(), [0.0, 0.05, -0.05, 0.0])
    assert max_drawdown(eq["equity"]) == pytest.approx(-0.05)
