"""핵심 패턴 5종 — 구현_계획.md §4.1 정의 그대로."""

from __future__ import annotations

import pandas as pd

from regime_lab.patterns.base import Pattern


class MaCross520(Pattern):
    """SMA5(t-1) ≤ SMA20(t-1) 이고 SMA5(t) > SMA20(t)."""

    name = "ma_cross_5_20"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        return (self.prev(f, "sma5") <= self.prev(f, "sma20")) & (f["sma5"] > f["sma20"])


class Breakout20d(Pattern):
    """종가(t) > 직전 20거래일(t-20 ~ t-1) 고가 최댓값."""

    name = "breakout_20d"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        return f["close"] > f["high_max_prev20"]


class BreakoutVol(Pattern):
    """breakout_20d 이고 거래량(t) ≥ 직전 20거래일 평균 거래량 × volume_mult."""

    name = "breakout_vol"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        vol_ok = f["volume"] >= f["vol_mean_prev20"] * float(self.params["volume_mult"])
        return (f["close"] > f["high_max_prev20"]) & vol_ok & (f["vol_mean_prev20"] > 0)


class RsiRebound(Pattern):
    """RSI14(t-1) < threshold 이고 RSI14(t) ≥ threshold."""

    name = "rsi_rebound"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        th = float(self.params["threshold"])
        return (self.prev(f, "rsi14") < th) & (f["rsi14"] >= th)


class BbLowerRecover(Pattern):
    """종가(t-1) < 볼린저 하단(t-1) 이고 종가(t) ≥ 볼린저 하단(t)."""

    name = "bb_lower_recover"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        return (self.prev(f, "close") < self.prev(f, "bb_lower")) & (f["close"] >= f["bb_lower"])


CORE_PATTERNS: dict[str, type[Pattern]] = {
    p.name: p for p in (MaCross520, Breakout20d, BreakoutVol, RsiRebound, BbLowerRecover)
}


def get_pattern(name: str, cfg: dict) -> Pattern:
    try:
        return CORE_PATTERNS[name](cfg)
    except KeyError:
        raise ValueError(f"지원하지 않는 패턴: {name} (핵심 5종: {sorted(CORE_PATTERNS)})") from None
