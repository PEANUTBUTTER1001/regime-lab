"""국면 × 시장 × 시총그룹 집계 (FR-A1, FR-A2, A2-1).

- 집계 대상은 집계 제외(end_of_data)가 아닌 거래.
- 국면 축은 진입 당시(신호일) 시장 국면이 기본이다. 산출 불가(2021-07-21 이전) 거래는 'unavailable' 셀로 분리한다.
- 거래 수가 min_cell_trades 미만인 셀은 sample_insufficient=True 로 표시하고 결론·선정에 쓰지 않는다.
"""

from __future__ import annotations

import pandas as pd

from regime_lab.backtest.metrics import trade_stats

UNAVAILABLE = "unavailable"
CELL_KEYS = ("regime", "market", "cap_group")


def aggregate_cells(trades: pd.DataFrame, cfg: dict, regime_col: str = "market_regime") -> pd.DataFrame:
    min_n = int(cfg["analysis"]["min_cell_trades"])
    tr = trades[~trades["excluded"].astype(bool)] if len(trades) else trades
    if tr.empty:
        return pd.DataFrame(columns=[*CELL_KEYS, "trades", "sample_insufficient"])
    key = pd.DataFrame({
        "regime": tr[regime_col].astype(object).fillna(UNAVAILABLE),
        "market": tr["market"].astype(object).fillna(UNAVAILABLE),
        "cap_group": tr["cap_group"].astype(object).fillna(UNAVAILABLE),
    })
    rows = []
    for k, idx in key.groupby(list(CELL_KEYS)).groups.items():
        st = trade_stats(tr.loc[idx])
        rows.append({**dict(zip(CELL_KEYS, k)), **st})
    out = pd.DataFrame(rows)
    out["sample_insufficient"] = out["trades"] < min_n
    out["regime_basis"] = regime_col
    return out.sort_values(list(CELL_KEYS)).reset_index(drop=True)
