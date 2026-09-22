"""S6 국면 판정 규칙 (A6)."""

import numpy as np
import pandas as pd

from regime_lab.regime import classify


def test_classify_rules():
    close = pd.Series([110, 90, 110, 90, 100, 100.0])
    ma = pd.Series([100, 100, 100, 100, 100, np.nan])
    prev = pd.Series([98, 102, 99.5, 100.5, 98, 98.0])  # 변화율 +2.04, -1.96, +0.50, -0.50, +2.04
    r = [x if isinstance(x, str) else None for x in classify(close, ma, prev, 1.0)]
    assert r == ["bull", "bear", "sideways", "sideways", "sideways", None]
