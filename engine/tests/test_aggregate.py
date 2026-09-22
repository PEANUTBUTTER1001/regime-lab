"""S10 집계 (FR-A1, FR-A2)."""

import numpy as np
import pandas as pd

from regime_lab.analysis.aggregate import aggregate_cells


def _trades(n, regime, market="KOSPI", cap="large", ret=0.01):
    return pd.DataFrame({
        "net_ret": np.full(n, ret), "gross_ret": np.full(n, ret + 0.003), "excess_ret": np.full(n, ret),
        "market_regime": regime, "stock_regime": "bull", "market": market, "cap_group": cap, "excluded": False,
    })


def test_cells_counts_and_sample_flag(cfg):
    tr = pd.concat([
        _trades(300, "bull"), _trades(299, "bear"), _trades(10, None, cap="small"),
        _trades(5, "bull").assign(excluded=True),
    ], ignore_index=True)
    cells = aggregate_cells(tr, cfg).set_index(["regime", "market", "cap_group"])
    assert cells.loc[("bull", "KOSPI", "large"), "trades"] == 300  # 제외 거래 미포함
    assert not cells.loc[("bull", "KOSPI", "large"), "sample_insufficient"]
    assert cells.loc[("bear", "KOSPI", "large"), "sample_insufficient"]
    assert cells.loc[("unavailable", "KOSPI", "small"), "trades"] == 10  # 시장 국면 미산출 (A2-1)
    assert cells["trades"].sum() == 609


def test_cell_metrics(cfg):
    tr = _trades(4, "bull")
    tr["net_ret"] = [0.10, -0.05, 0.20, -0.05]
    c = aggregate_cells(tr, cfg).iloc[0]
    assert c.win_rate == 0.5 and np.isclose(c.mean_ret, 0.05) and np.isclose(c.median_ret, 0.025)
    assert np.isclose(c.payoff_ratio, 0.15 / 0.05)
