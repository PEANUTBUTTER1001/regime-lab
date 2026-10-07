"""추가 매수 패턴 4종 (2026-10-07): MACD 골든크로스·52주 신고가·이격도 반등·스토캐스틱 반등.
참조 구현 일치·수기 사례·절단 불변·미래 충격 무영향·수치 관계 검사."""

import math

import numpy as np
import pandas as pd
import pytest
from synth import make_frame, make_market

from regime_lab.indicators import compute_indicators
from regime_lab.patterns import make_pattern
from regime_lab.patterns.base import Pattern
from regime_lab.runs import Strategy

MORE = ["macd_cross", "high_52w", "disparity_rebound", "stochastic_rebound"]
CUTS = ["2021-03-15", "2022-06-30", "2024-02-29"]
ALT = {"macd_cross": {"fast": 8, "slow": 52, "signal": 5}, "high_52w": {"lookback": 120},
       "disparity_rebound": {"window": 60, "threshold": 95.0},
       "stochastic_rebound": {"k_window": 9, "d_window": 4, "oversold": 30.0}}


def _sig(df, name, cfg, params=None):
    return make_pattern(name, cfg, params).signal(compute_indicators(df, cfg))


# ---------------------------------------------------------------- 참조 구현 (정의를 반복문으로 그대로)
def _ema_ref(x: list[float], n: int) -> list[float]:
    a, out, prev, seen = 2 / (n + 1), [], None, 0
    for v in x:
        if v is None or not math.isfinite(v):
            out.append(math.nan if prev is None or seen < n else prev)
            continue
        prev = v if prev is None else a * v + (1 - a) * prev
        seen += 1
        out.append(prev if seen >= n else math.nan)
    return out


def _gt(a, b):
    return math.isfinite(a) and math.isfinite(b) and a > b


def _le(a, b):
    return math.isfinite(a) and math.isfinite(b) and a <= b


def reference(f: pd.DataFrame, name: str, p: dict) -> list[bool]:
    out = []
    for _, g in f.groupby(f["ticker"].astype(str), sort=False):
        c = g["close"].to_numpy(float)
        c = np.where(np.isfinite(c) & (c > 0), c, np.nan)
        hi = g["high"].fillna(g["close"]).to_numpy(float)
        lo = g["low"].fillna(g["close"]).to_numpy(float)
        hal = g["halted"].to_numpy(bool)
        T = len(g)
        if name == "macd_cross":
            macd = np.subtract(_ema_ref(list(c), p["fast"]), _ema_ref(list(c), p["slow"]))
            sig = np.array(_ema_ref(list(macd), p["signal"]))
            ok = [t >= 1 and _le(macd[t - 1], sig[t - 1]) and _gt(macd[t], sig[t]) for t in range(T)]
        elif name == "high_52w":
            n = p["lookback"]
            h = [max(hi[t - n:t]) if t >= n and np.isfinite(hi[t - n:t]).all() else math.nan for t in range(T)]
            ok = [t >= 1 and _gt(c[t], h[t]) and _le(c[t - 1], h[t - 1]) for t in range(T)]
        elif name == "disparity_rebound":
            n, th = p["window"], p["threshold"]
            m = [np.mean(g["close"].to_numpy(float)[t - n + 1:t + 1]) if t >= n - 1 else math.nan for t in range(T)]
            d = [c[t] / m[t] * 100 if m[t] > 0 else math.nan for t in range(T)]
            ok = [t >= 1 and math.isfinite(d[t - 1]) and math.isfinite(d[t]) and d[t - 1] < th and d[t] >= th
                  for t in range(T)]
        else:
            n, m_, os_ = p["k_window"], p["d_window"], p["oversold"]
            k = []
            for t in range(T):
                if t < n - 1:
                    k.append(math.nan)
                    continue
                hh, ll = max(hi[t - n + 1:t + 1]), min(lo[t - n + 1:t + 1])
                k.append((g["close"].to_numpy(float)[t] - ll) / (hh - ll) * 100 if hh > ll else math.nan)
            d = [np.mean(k[t - m_ + 1:t + 1]) if t >= m_ - 1 and np.isfinite(k[t - m_ + 1:t + 1]).all() else math.nan
                 for t in range(T)]
            ok = [t >= 1 and _le(k[t - 1], d[t - 1]) and _gt(k[t], d[t]) and math.isfinite(k[t - 1]) and k[t - 1] < os_
                  for t in range(T)]
        out += [bool(o) and not hal[t] for t, o in enumerate(ok)]
    return out


@pytest.fixture(scope="module")
def market(cfg):
    m = make_market(cfg, n_tickers=25, seed=21)
    f = m.frame.copy()
    rng = np.random.default_rng(3)
    f.loc[rng.random(len(f)) < 0.01, "halted"] = True  # 거래정지일 섞기
    return f


@pytest.mark.parametrize("name", MORE)
@pytest.mark.parametrize("variant", ["default", "alt"])
def test_matches_reference_implementation(market, cfg, name, variant):
    params = None if variant == "default" else ALT[name]
    p = {**cfg["patterns"][name], **(params or {})}
    got = make_pattern(name, cfg, params).signal(market).tolist()
    assert got == reference(market, name, p)
    assert sum(got) > 0  # 합성 시장에서 신호가 실제로 난다


@pytest.mark.parametrize("name", MORE)
@pytest.mark.parametrize("cut", CUTS)
def test_truncation_invariance(market, cfg, name, cut):
    short = market[market["date"] <= pd.Timestamp(cut)].reset_index(drop=True)
    a = make_pattern(name, cfg).signal(market)[(market["date"] <= pd.Timestamp(cut)).to_numpy()].tolist()
    assert a == make_pattern(name, cfg).signal(short).tolist()


@pytest.mark.parametrize("name", MORE)
def test_future_shock_does_not_leak(market, cfg, name):
    shocked = market.copy()
    after = shocked["date"] > pd.Timestamp("2023-01-02")
    for c in ("open", "high", "low", "close"):
        shocked.loc[after, c] *= 1.7
    keep = (~after).to_numpy()
    assert make_pattern(name, cfg).signal(market)[keep].tolist() == make_pattern(name, cfg).signal(shocked)[keep].tolist()


# ---------------------------------------------------------------- 수기 사례
def test_macd_cross_case(cfg):
    closes = list(np.linspace(120, 100, 60)) + [101, 103, 106, 110]  # 긴 하락 뒤 반등 → MACD 가 시그널 위로
    s = _sig(make_frame(closes), "macd_cross", cfg)
    assert s.sum() == 1 and s.iloc[60:].any()
    assert not _sig(make_frame([100.0] * 80), "macd_cross", cfg).any()  # 평탄: MACD = 시그널 = 0 → 교차 없음


def test_high_52w_case(cfg):
    closes = [100.0] * 260 + [99.0, 101.0, 102.0]
    s = _sig(make_frame(closes), "high_52w", cfg)
    assert s.iloc[261] and not s.iloc[262]  # 처음 넘는 날만 (다음 날은 전일이 이미 위)
    assert not _sig(make_frame(closes[:200] + [101.0]), "high_52w", cfg).any()  # 250일 자료 부족
    assert _sig(make_frame(closes[:200] + [101.0]), "high_52w", cfg, {"lookback": 120}).iloc[-1]


def test_disparity_rebound_case(cfg):
    closes = [100.0] * 30 + [85.0, 95.0]  # t-1: 85/(SMA20≈99.25) ≈ 85.6 < 90, t: 95/98.75 ≈ 96.2 ≥ 90
    s = _sig(make_frame(closes), "disparity_rebound", cfg)
    assert s.iloc[-1] and s.sum() == 1
    assert not _sig(make_frame(closes), "disparity_rebound", cfg, {"threshold": 97.0}).iloc[-1]


def test_stochastic_rebound_case(cfg):
    closes = list(np.linspace(110, 100, 20)) + [100.5]  # 바닥(%K≈0)에서 반등 → %K 가 %D 위로
    df = make_frame(closes)
    df["high"], df["low"] = df["close"], df["close"]
    s = _sig(df, "stochastic_rebound", cfg)
    assert s.iloc[-1] and s.sum() == 1
    flat = make_frame([100.0] * 30)
    flat["high"], flat["low"] = flat["close"], flat["close"]
    assert not _sig(flat, "stochastic_rebound", cfg).any()  # 고가 = 저가 → %K 없음


def test_param_limits(cfg):
    Strategy.from_dict({"name": "x", "patterns": ["macd_cross"],
                        "pattern_params": {"macd_cross": {"fast": 8, "slow": 52, "signal": 5}}}).validate(cfg)
    with pytest.raises(ValueError):
        Strategy.from_dict({"name": "x", "patterns": ["high_52w"],
                            "pattern_params": {"high_52w": {"lookback": 300}}}).validate(cfg)


@pytest.mark.skipif(not hasattr(Pattern, "param_errors"), reason="수치 관계 검사는 PR #18 (Pattern.param_errors) 병합 후")
def test_macd_fast_shorter_than_slow(cfg):
    with pytest.raises(ValueError):
        Strategy.from_dict({"name": "x", "patterns": ["macd_cross"],
                            "pattern_params": {"macd_cross": {"fast": 30, "slow": 26}}}).validate(cfg)
