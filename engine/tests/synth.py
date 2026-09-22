"""합성 일봉 프레임 (단위 테스트용)."""

import numpy as np
import pandas as pd


def make_frame(closes, ticker="T1", start="2021-01-04", **cols) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    df = pd.DataFrame({
        "date": pd.bdate_range(start, periods=n),
        "ticker": ticker,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": 1000.0, "value": 1e9, "marketcap": 1e11,
        "market": "KOSPI", "dept": "", "kind": "보통주",
        "open_raw": closes, "close_raw": closes, "volume_raw": 1000.0,
        "halted": False, "is_warmup": False,
    })
    for k, v in cols.items():
        df[k] = v
    return df
