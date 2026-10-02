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
    needs: tuple[str, ...] = REQUIRED_INDICATORS  # 이 패턴이 쓰는 지표 열. 없으면 계산한다

    def __init__(self, cfg: dict, params: dict | None = None):
        """params: 조합별 수치 (P1-4). 설정 기본값 위에 덮어쓴다. 허용 범위 검증은 Strategy.validate 가 한다."""
        self.cfg = cfg
        self.params = {**cfg["patterns"][self.name], **(params or {})}

    def signal(self, frame: pd.DataFrame) -> pd.Series:
        if not all(c in frame.columns for c in self.needs):
            frame = compute_indicators(frame, self.cfg)
        raw = self._raw_signal(frame)
        halted = frame["halted"].astype(bool) if "halted" in frame else False
        return (raw.fillna(False).astype(bool) & ~halted).rename(self.name)

    @abstractmethod
    def _raw_signal(self, f: pd.DataFrame) -> pd.Series: ...

    @staticmethod
    def prev(f: pd.DataFrame, col: str) -> pd.Series:
        return f.groupby(f["ticker"].to_numpy(), sort=False)[col].shift(1)

    @staticmethod
    def lag(f: pd.DataFrame, s: pd.Series, k: int) -> pd.Series:
        """같은 종목의 k 거래일 전 값 (X6). 종목 경계를 넘지 않는다."""
        return s.groupby(f["ticker"].to_numpy(), sort=False).shift(k)

    @staticmethod
    def rolling_min(f: pd.DataFrame, s: pd.Series, n: int) -> pd.Series:
        """같은 종목의 t-n+1..t 최솟값, n 개가 모두 있어야 값 (X6). 종목별 rolling (AGENTS 함정)."""
        return s.groupby(f["ticker"].to_numpy(), sort=False).transform(lambda x: x.rolling(n, min_periods=n).min())


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


def compute_signals(frame: pd.DataFrame, names: list[str], how: str, cfg: dict,
                    params: dict[str, dict] | None = None) -> pd.Series:
    """params: {패턴 이름: 조합별 수치} (P1-4). 없으면 설정 기본값."""
    return combine_signals([make_pattern(n, cfg, (params or {}).get(n)).signal(frame) for n in names], how)


def make_pattern(name: str, cfg: dict, params: dict | None = None) -> Pattern:
    from regime_lab.patterns.core import CORE_PATTERNS

    if name not in CORE_PATTERNS:
        raise ValueError(f"지원하지 않는 패턴: {name} (핵심 패턴: {sorted(CORE_PATTERNS)})")
    return CORE_PATTERNS[name](cfg, params)
