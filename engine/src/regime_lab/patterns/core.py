"""핵심 패턴 5종(구현_계획.md §4.1), 후순위 패턴 5종(X6, 정의 r1), 추가 패턴 4종(2026-10-07, MACD·52주 신고가·이격도·스토캐스틱)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from regime_lab.indicators import bollinger, filled_hl, rolling_max_prev, rolling_mean_prev, rsi, sma
from regime_lab.patterns.base import REQUIRED_INDICATORS, Pattern

# 핵심 5종의 기간·배수는 준비 프레임 열(sma5·sma20·high_max_prev20·vol_mean_prev20·rsi14·bb_lower)에 기본값으로
# 계산돼 있다. 조합별 수치(P1-4)가 그 기본값과 같으면 그 열을 그대로 쓰고, 다르면 같은 지표 함수로 그 자리에서
# 다시 계산한다 → 기본값이면 결과가 비트 단위로 같고, 준비 캐시(PREP_VERSION)는 바뀌지 않는다.


class MaCross520(Pattern):
    """SMA_fast(t-1) ≤ SMA_slow(t-1) 이고 SMA_fast(t) > SMA_slow(t). 기본 fast 5·slow 20."""

    name = "ma_cross_5_20"

    def _ma(self, f: pd.DataFrame, n: int) -> pd.Series:
        base = self.cfg["patterns"][self.name]
        col = {int(base["fast"]): "sma5", int(base["slow"]): "sma20"}.get(n)
        return f[col] if col in f else sma(f, n)

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        fast, slow = self._ma(f, int(self.params["fast"])), self._ma(f, int(self.params["slow"]))
        return (self.lag(f, fast, 1) <= self.lag(f, slow, 1)) & (fast > slow)

    @classmethod
    def param_errors(cls, p: dict) -> dict[str, str]:
        return {"fast": "Must be shorter than slow"} if p["fast"] >= p["slow"] else {}


def _high_max_prev(pat: Pattern, f: pd.DataFrame, n: int) -> pd.Series:
    if n == int(pat.cfg["patterns"]["breakout_20d"]["lookback"]) and "high_max_prev20" in f:
        return f["high_max_prev20"]
    return rolling_max_prev(f, filled_hl(f)[0], n)


class Breakout20d(Pattern):
    """종가(t) > 직전 lookback 거래일(t-lookback ~ t-1) 고가 최댓값. 기본 20일."""

    name = "breakout_20d"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        return f["close"] > _high_max_prev(self, f, int(self.params["lookback"]))


class BreakoutVol(Pattern):
    """직전 lookback 일 고가 돌파 이고 거래량(t) ≥ 직전 lookback 일 평균 거래량 × volume_mult. 기본 20일·2배."""

    name = "breakout_vol"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        n = int(self.params["lookback"])
        vol_mean = (f["vol_mean_prev20"] if n == int(self.cfg["patterns"]["breakout_vol"]["lookback"]) and "vol_mean_prev20" in f
                    else rolling_mean_prev(f, f["volume"].fillna(0), n))
        vol_ok = f["volume"] >= vol_mean * float(self.params["volume_mult"])
        return (f["close"] > _high_max_prev(self, f, n)) & vol_ok & (vol_mean > 0)


class RsiRebound(Pattern):
    """RSI_window(t-1) < threshold 이고 RSI_window(t) ≥ threshold. 기본 14일(단순평균, A7-1)·30."""

    name = "rsi_rebound"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        icfg = self.cfg["indicators"]
        n = int(self.params["window"])
        r = f["rsi14"] if n == int(icfg["rsi_window"]) and "rsi14" in f else rsi(f, n, icfg["rsi_smoothing"])
        th = float(self.params["threshold"])
        return (self.lag(f, r, 1) < th) & (r >= th)


class BbLowerRecover(Pattern):
    """종가(t-1) < 볼린저 하단(t-1) 이고 종가(t) ≥ 볼린저 하단(t). 기본 20일·2σ(ddof=0)."""

    name = "bb_lower_recover"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        icfg = self.cfg["indicators"]
        n, k = int(self.params["window"]), float(self.params["k"])
        lower = (f["bb_lower"] if n == int(icfg["bb_window"]) and k == float(icfg["bb_k"]) and "bb_lower" in f
                 else bollinger(f, n, k, icfg["bb_ddof"])["bb_lower"])
        return (self.prev(f, "close") < self.lag(f, lower, 1)) & (f["close"] >= lower)


# ---------------------------------------------------------------- 후순위 패턴 5종 (X6)
# 공통: t일 조건은 t일까지 값만 쓴다. 결측·과거 행 부족이면 False (NaN 비교는 False). 당일 거래정지는 기반 클래스가 막는다.
def _pos(s: pd.Series) -> pd.Series:
    return s.where(np.isfinite(s) & (s > 0))


class ThreeDownUp(Pattern):
    """C(t-1) < C(t-2) < … < C(t-n-1) (n=down_days 번 연속 하락), C(t) > O(t), C(t) > C(t-1).
    비교하는 과거 봉(t-1 … t-n-1)에 거래정지가 있으면 False."""

    name = "three_down_up"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        n = int(self.params["down_days"])
        c, o = _pos(f["close"]), _pos(f["open"])
        halted = f["halted"].astype(bool) if "halted" in f else pd.Series(False, index=f.index)
        ok = (c > o) & (c > self.lag(f, c, 1))
        for k in range(1, n + 1):
            ok &= self.lag(f, c, k) < self.lag(f, c, k + 1)
        for k in range(1, n + 2):
            ok &= self.lag(f, halted.astype(float), k) == 0
        return ok


class BbSqueezeBreak(Pattern):
    """U = 2·M20 − bb_lower, W = (U − bb_lower) / M20 (BB 중단 = SMA20 전제).
    W(t-1) ≤ min{W(t-n) … W(t-1)} (n=squeeze_lookback, n 개 모두 유효) 이고 C(t-1) ≤ U(t-1), C(t) > U(t)."""

    name = "bb_squeeze_break"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        n = int(self.params["squeeze_lookback"])
        m20 = _pos(f["sma20"])
        u = 2 * m20 - f["bb_lower"]
        w = (u - f["bb_lower"]) / m20
        w = w.where(np.isfinite(w) & (w >= 0))
        squeezed = self.lag(f, w, 1) <= self.lag(f, self.rolling_min(f, w, n), 1)
        c = _pos(f["close"])
        return squeezed & (self.lag(f, c, 1) <= self.lag(f, u, 1)) & (c > u)


class PullbackMa20(Pattern):
    """C(t-1) > M20(t-1), M20(t) > M20(t-1), M20(t)·(1−b) ≤ L(t) ≤ M20(t)·(1+a), C(t) > M20(t), C(t) > O(t).
    a = touch_tolerance_pct/100, b = max_penetration_pct/100."""

    name = "pullback_ma20"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        a = float(self.params["touch_tolerance_pct"]) / 100
        b = float(self.params["max_penetration_pct"]) / 100
        m20, c, o, low = _pos(f["sma20"]), _pos(f["close"]), _pos(f["open"]), _pos(f["low"])
        return ((self.lag(f, c, 1) > self.lag(f, m20, 1)) & (m20 > self.lag(f, m20, 1))
                & (low >= m20 * (1 - b)) & (low <= m20 * (1 + a)) & (c > m20) & (c > o))


class GranvilleBuy1(Pattern):
    """D(t) = M200(t) − M200(t-n) (n=slope_days). D(t-1) ≤ 0, D(t) > 0, C(t-1) ≤ M200(t-1), C(t) > M200(t)."""

    name = "granville_buy1"
    needs = (*REQUIRED_INDICATORS, "sma200")

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        n = int(self.params["slope_days"])
        m200, c = _pos(f["sma200"]), _pos(f["close"])
        d = m200 - self.lag(f, m200, n)
        return (self.lag(f, d, 1) <= 0) & (d > 0) & (self.lag(f, c, 1) <= self.lag(f, m200, 1)) & (c > m200)


class Engulfing(Pattern):
    """상승 장악형. O(t-1) > C(t-1), C(t) > O(t), O(t) ≤ C(t-1), C(t) ≥ O(t-1) (두 경계 중 하나는 엄격),
    (C(t)−O(t)) ≥ r·(O(t-1)−C(t-1)) (r=min_body_ratio), C(t-1) < C(t-1-n) (n=trend_days, 직전 하락)."""

    name = "engulfing"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        r, n = float(self.params["min_body_ratio"]), int(self.params["trend_days"])
        c, o = _pos(f["close"]), _pos(f["open"])
        c1, o1 = self.lag(f, c, 1), self.lag(f, o, 1)
        return ((o1 > c1) & (c > o) & (o <= c1) & (c >= o1) & ((o < c1) | (c > o1))
                & ((c - o) >= r * (o1 - c1)) & (c1 < self.lag(f, c, 1 + n)))


# ---------------------------------------------------------------- 추가 패턴 4종 (2026-10-07, SRS 범위 확장 승인 필요)
# 모두 종가·고가·저가로 패턴 안에서 종목별로 계산한다 (준비 프레임 열 추가 없음). t일 값은 t일까지의 가격만 쓴다.
def _by_ticker(f: pd.DataFrame, s: pd.Series, fn) -> pd.Series:
    return s.groupby(f["ticker"].to_numpy(), sort=False).transform(fn)


def _ema(f: pd.DataFrame, s: pd.Series, n: int) -> pd.Series:
    """종목별 지수이동평균 (adjust=False, 첫 n개 전에는 NaN). 재귀식이라 t일 값은 t일까지의 값만 쓴다."""
    return _by_ticker(f, s, lambda x: x.ewm(span=n, adjust=False, min_periods=n).mean())


class MacdCross(Pattern):
    """MACD = EMA_fast(종가) − EMA_slow(종가), 시그널 = EMA_signal(MACD).
    MACD(t-1) ≤ 시그널(t-1) 이고 MACD(t) > 시그널(t). 기본 12·26·9."""

    name = "macd_cross"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        c = _pos(f["close"])
        macd = _ema(f, c, int(self.params["fast"])) - _ema(f, c, int(self.params["slow"]))
        sig = _ema(f, macd, int(self.params["signal"]))
        return (self.lag(f, macd, 1) <= self.lag(f, sig, 1)) & (macd > sig)

    @classmethod
    def param_errors(cls, p: dict) -> dict[str, str]:
        return {"fast": "Must be shorter than slow"} if p["fast"] >= p["slow"] else {}


class High52w(Pattern):
    """종가(t) > 직전 lookback 거래일 고가 최댓값 이고, 전날은 그 아래 (처음 넘는 날만). 기본 250거래일(약 52주)."""

    name = "high_52w"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        hi = rolling_max_prev(f, filled_hl(f)[0], int(self.params["lookback"]))
        c = _pos(f["close"])
        return (c > hi) & (self.lag(f, c, 1) <= self.lag(f, hi, 1))


class DisparityRebound(Pattern):
    """이격도 = 종가 / SMA_window × 100. 이격도(t-1) < threshold 이고 이격도(t) ≥ threshold. 기본 20일·90."""

    name = "disparity_rebound"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        n = int(self.params["window"])
        base = self.cfg["patterns"]["ma_cross_5_20"]
        m = f["sma20"] if n == int(base["slow"]) and "sma20" in f else sma(f, n)
        d = _pos(f["close"]) / _pos(m) * 100
        th = float(self.params["threshold"])
        return (self.lag(f, d, 1) < th) & (d >= th)


class StochasticRebound(Pattern):
    """%K = (종가 − k_window일 최저가) / (최고가 − 최저가) × 100 (t일 포함), %D = %K 의 d_window일 평균.
    %K(t-1) ≤ %D(t-1), %K(t) > %D(t), %K(t-1) < oversold. 기본 14·3·20. 고가 = 저가면 %K 없음."""

    name = "stochastic_rebound"

    def _raw_signal(self, f: pd.DataFrame) -> pd.Series:
        n, m = int(self.params["k_window"]), int(self.params["d_window"])
        hi, lo = filled_hl(f)
        hh = _by_ticker(f, hi, lambda x: x.rolling(n, min_periods=n).max())
        ll = _by_ticker(f, lo, lambda x: x.rolling(n, min_periods=n).min())
        rng = (hh - ll).where(lambda r: r > 0)
        k = (f["close"] - ll) / rng * 100
        d = _by_ticker(f, k, lambda x: x.rolling(m, min_periods=m).mean())
        k1 = self.lag(f, k, 1)
        return (k1 <= self.lag(f, d, 1)) & (k > d) & (k1 < float(self.params["oversold"]))


CORE_PATTERNS: dict[str, type[Pattern]] = {
    p.name: p for p in (MaCross520, Breakout20d, BreakoutVol, RsiRebound, BbLowerRecover,
                        ThreeDownUp, BbSqueezeBreak, PullbackMa20, GranvilleBuy1, Engulfing,
                        MacdCross, High52w, DisparityRebound, StochasticRebound)
}


def get_pattern(name: str, cfg: dict) -> Pattern:
    try:
        return CORE_PATTERNS[name](cfg)
    except KeyError:
        raise ValueError(f"지원하지 않는 패턴: {name} (지원 패턴: {sorted(CORE_PATTERNS)})") from None
