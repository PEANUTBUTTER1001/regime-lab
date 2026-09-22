"""패턴 공통 인터페이스 (FR-P2) 와 AND/OR 결합 (FR-P4)."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from regime_lab.indicators import compute_indicators

REQUIRED_INDICATORS = ("sma5", "sma20", "rsi14", "bb_lower", "high_max_prev20", "vol_mean_prev20")


class Pattern(ABC):
    """signal(frame) -> 날짜별 매수 신호 bool Series.

    frame 은 한 종목 또는 여러 종목의 (ticker, date) 정렬 일봉이다. 지표 컬럼이 없으면 계산한다.
    t일 신호는 t일까지 확정된 값만 사용한다 (FR-P3). 거래정지일과 지표 결측일은 False.
    """

    name: str

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.params = dict(cfg["patterns"][self.name])

    def signal(self, frame: pd.DataFrame) -> pd.Series:
        if not all(c in frame.columns for c in REQUIRED_INDICATORS):
            frame = compute_indicators(frame, self.cfg)
        raw = self._raw_signal(frame)
        halted = frame["halted"].astype(bool) if "halted" in frame else False
        return (raw.fillna(False).astype(bool) & ~halted).rename(self.name)

    @abstractmethod
    def _raw_signal(self, f: pd.DataFrame) -> pd.Series: ...

    @staticmethod
    def prev(f: pd.DataFrame, col: str) -> pd.Series:
        return f.groupby(f["ticker"].to_numpy(), sort=False)[col].shift(1)


def combine_signals(signals: list[pd.Series], how: str) -> pd.Series:
    if not signals:
        raise ValueError("패턴이 최소 1개 필요하다")
    how = how.lower()
    if how not in ("and", "or"):
        raise ValueError(f"combine 은 and/or 만 허용: {how}")
    out = signals[0].copy()
    for s in signals[1:]:
        out = (out & s) if how == "and" else (out | s)
    return out.rename("signal")


def compute_signals(frame: pd.DataFrame, names: list[str], how: str, cfg: dict) -> pd.Series:
    from regime_lab.patterns.core import get_pattern

    return combine_signals([get_pattern(n, cfg).signal(frame) for n in names], how)
