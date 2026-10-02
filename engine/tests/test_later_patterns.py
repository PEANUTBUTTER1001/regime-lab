"""X6 후순위 패턴 5종 (정의: 채널 초안 r1). 수기 사례·참조 구현 일치·종목 경계·절단 불변·기존 결과 불변."""

import math

import numpy as np
import pandas as pd
import pytest
from synth import make_frame, make_market

from regime_lab.indicators import compute_indicators
from regime_lab.patterns import make_pattern

LATER = ["three_down_up", "bb_squeeze_break", "pullback_ma20", "granville_buy1", "engulfing"]
CUTS = ["2021-03-15", "2022-06-30", "2024-02-29"]


def _sig(df, name, cfg, params=None):
    return make_pattern(name, cfg, params).signal(compute_indicators(df, cfg))


def _frame(closes, opens=None, lows=None):
    df = make_frame(closes)
    if opens is not None:
        df["open"] = df["open_raw"] = np.asarray(opens, float)
    if lows is not None:
        df["low"] = np.asarray(lows, float)
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = np.minimum(df["low"], df[["open", "close"]].min(axis=1))
    return df


# ---------------------------------------------------------------- 참조 구현 (정의를 반복문으로 그대로)
def _ok(*xs):
    return all(x is not None and math.isfinite(x) and x > 0 for x in xs)


def reference(f: pd.DataFrame, name: str, p: dict) -> list[bool]:
    out = []
    for _, g in f.groupby(f["ticker"].astype(str), sort=False):
        c, o, lo = g["close"].to_numpy(float), g["open"].to_numpy(float), g["low"].to_numpy(float)
        m20, m200, bbl = g["sma20"].to_numpy(float), g["sma200"].to_numpy(float), g["bb_lower"].to_numpy(float)
        hal = g["halted"].to_numpy(bool)
        u = 2 * m20 - bbl
        w = np.where(m20 > 0, (u - bbl) / m20, np.nan)
        for t in range(len(g)):
            s = False
            if hal[t]:
                out.append(False)
                continue
            if name == "three_down_up":
                n = p["down_days"]
                if t - n - 1 >= 0 and _ok(c[t], o[t]) and not hal[t - n - 1:t].any():
                    s = (c[t] > o[t] and c[t] > c[t - 1]
                         and all(_ok(c[t - k], c[t - k - 1]) and c[t - k] < c[t - k - 1] for k in range(1, n + 1)))
            elif name == "bb_squeeze_break":
                n = p["squeeze_lookback"]
                if t - n >= 0:
                    win = w[t - n:t]
                    if np.isfinite(win).all() and (win >= 0).all() and _ok(c[t], c[t - 1], m20[t], m20[t - 1]):
                        s = w[t - 1] <= win.min() and c[t - 1] <= u[t - 1] and c[t] > u[t]
            elif name == "pullback_ma20":
                a, b = p["touch_tolerance_pct"] / 100, p["max_penetration_pct"] / 100
                if t >= 1 and _ok(c[t], c[t - 1], o[t], lo[t], m20[t], m20[t - 1]):
                    s = (c[t - 1] > m20[t - 1] and m20[t] > m20[t - 1] and m20[t] * (1 - b) <= lo[t] <= m20[t] * (1 + a)
                         and c[t] > m20[t] and c[t] > o[t])
            elif name == "granville_buy1":
                n = p["slope_days"]
                if t - 1 - n >= 0 and _ok(c[t], c[t - 1], m200[t], m200[t - 1], m200[t - n], m200[t - 1 - n]):
                    s = (m200[t - 1] - m200[t - 1 - n] <= 0 and m200[t] - m200[t - n] > 0
                         and c[t - 1] <= m200[t - 1] and c[t] > m200[t])
            elif name == "engulfing":
                r, n = p["min_body_ratio"], p["trend_days"]
                if t - 1 - n >= 0 and _ok(c[t], o[t], c[t - 1], o[t - 1], c[t - 1 - n]):
                    s = (o[t - 1] > c[t - 1] and c[t] > o[t] and o[t] <= c[t - 1] and c[t] >= o[t - 1]
                         and (o[t] < c[t - 1] or c[t] > o[t - 1]) and (c[t] - o[t]) >= r * (o[t - 1] - c[t - 1])
                         and c[t - 1] < c[t - 1 - n])
            out.append(bool(s))
    return out


@pytest.fixture(scope="module")
def market(cfg):
    m = make_market(cfg, n_tickers=25, seed=21)
    f = m.frame.copy()
    rng = np.random.default_rng(3)
    f.loc[rng.random(len(f)) < 0.01, "halted"] = True  # 거래정지일 섞기
    return f


@pytest.mark.parametrize("name", LATER)
@pytest.mark.parametrize("variant", ["default", "alt"])
def test_matches_reference_implementation(market, cfg, name, variant):
    alt = {"three_down_up": {"down_days": 2}, "bb_squeeze_break": {"squeeze_lookback": 40},
           "pullback_ma20": {"touch_tolerance_pct": 3.0, "max_penetration_pct": 0.5},
           "granville_buy1": {"slope_days": 2}, "engulfing": {"min_body_ratio": 1.5, "trend_days": 8}}
    params = None if variant == "default" else alt[name]
    p = {**cfg["patterns"][name], **(params or {})}
    got = make_pattern(name, cfg, params).signal(market).tolist()
    want = reference(market, name, p)
    assert got == want
    assert sum(got) > 0 or name == "granville_buy1"  # 합성 시장에서 신호가 실제로 나는지 (그랜빌은 드물 수 있음)


@pytest.mark.parametrize("name", LATER)
@pytest.mark.parametrize("cut", CUTS)
def test_truncation_invariance(market, cfg, name, cut):
    short = market[market["date"] <= pd.Timestamp(cut)].reset_index(drop=True)
    a = make_pattern(name, cfg).signal(market)[(market["date"] <= pd.Timestamp(cut)).to_numpy()].tolist()
    assert a == make_pattern(name, cfg).signal(short).tolist()


@pytest.mark.parametrize("name", LATER)
def test_future_shock_does_not_leak(market, cfg, name):
    shocked = market.copy()
    after = shocked["date"] > pd.Timestamp("2023-01-02")
    for c in ("open", "high", "low", "close"):
        shocked.loc[after, c] *= 1.7
    keep = (~after).to_numpy()
    assert make_pattern(name, cfg).signal(market)[keep].tolist() == make_pattern(name, cfg).signal(shocked)[keep].tolist()


# ---------------------------------------------------------------- 수기 사례 (r1 수용 사례)
BASE = [100.0] * 30


def test_three_down_up_cases(cfg):
    ok = _frame(BASE + [104, 103, 102, 101, 103], opens=BASE + [104, 103, 102, 101, 102])
    assert _sig(ok, "three_down_up", cfg).iloc[-1]
    two = _frame(BASE + [101.5, 103, 102, 101, 103], opens=BASE + [101.5, 103, 102, 101, 102])  # 하락 2번뿐
    assert not _sig(two, "three_down_up", cfg).iloc[-1]
    tie = _frame(BASE + [103, 103, 102, 101, 103], opens=BASE + [103, 103, 102, 101, 102])  # 같은 종가
    assert not _sig(tie, "three_down_up", cfg).iloc[-1]
    doji = _frame(BASE + [104, 103, 102, 101, 103], opens=BASE + [104, 103, 102, 101, 103])  # 시가 = 종가
    assert not _sig(doji, "three_down_up", cfg).iloc[-1]
    halted = ok.copy()
    halted.loc[len(halted) - 3, "halted"] = True  # 비교하는 과거 봉이 거래정지
    assert not _sig(halted, "three_down_up", cfg).iloc[-1]
    assert _sig(two, "three_down_up", cfg, {"down_days": 2}).iloc[-1]  # 조합별 수치 2이면 성공


def test_bb_squeeze_break_cases(cfg):
    rng = np.random.default_rng(1)
    volatile = list(100 + rng.normal(0, 4, 60))
    flat = [100.0 + (0.05 if i % 2 else -0.05) for i in range(40)]
    closes = volatile + flat + [110.0, 112.0]
    df = _frame(closes)
    s = _sig(df, "bb_squeeze_break", cfg, {"squeeze_lookback": 20})
    t = len(closes) - 2
    assert s.iloc[t]  # 수축 뒤 첫 상단 돌파
    assert not s.iloc[t + 1]  # 전일 이미 상단 밖 → 반복 돌파 아님
    assert not _sig(df, "bb_squeeze_break", cfg).iloc[t]  # 기본 125 는 자료 부족 (BB 20 + 125 > 101 행)


def test_pullback_ma20_cases(cfg):
    closes = list(np.linspace(80, 100, 40))
    ind = compute_indicators(_frame(closes), cfg)
    m20 = ind["sma20"].iloc[-1]
    # 마지막 날: 저가가 M20 바로 위 0.5% 로 접근, M20 위 양봉 마감
    opens = closes[:-1] + [closes[-2]]
    lows = closes[:-1] + [m20 * 1.005]
    closes2 = closes[:-1] + [closes[-1] + 0.5]
    assert _sig(_frame(closes2, opens, lows), "pullback_ma20", cfg).iloc[-1]
    deep = closes[:-1] + [m20 * 0.97]  # 3% 침투 > 허용 2%
    assert not _sig(_frame(closes2, opens, deep), "pullback_ma20", cfg).iloc[-1]
    far = closes[:-1] + [m20 * 1.03]  # 3% 위 → 접근 아님
    assert not _sig(_frame(closes2, opens, far), "pullback_ma20", cfg).iloc[-1]
    assert _sig(_frame(closes2, opens, far), "pullback_ma20", cfg, {"touch_tolerance_pct": 4.0}).iloc[-1]


def test_engulfing_cases(cfg):
    pre = list(np.linspace(110, 100, 30))
    # t-1 음봉 O=100 C=98, t 양봉 O=97.5 C=101 → 몸통 감쌈, 직전 하락
    ok = _frame(pre + [98, 101], opens=pre + [100, 97.5])
    assert _sig(ok, "engulfing", cfg).iloc[-1]
    edge = _frame(pre + [98, 101], opens=pre + [100, 98])  # 한쪽 경계 일치(O(t) = C(t-1)) 는 허용
    assert _sig(edge, "engulfing", cfg).iloc[-1]
    both = _frame(pre + [98, 100], opens=pre + [100, 98])  # 양쪽 완전 일치는 제외
    assert not _sig(both, "engulfing", cfg).iloc[-1]
    small = _frame(pre + [98, 99.5], opens=pre + [100, 97.5])  # 전일 시가 미달 → 감싸지 못함
    assert not _sig(small, "engulfing", cfg).iloc[-1]
    up = list(np.linspace(80, 97, 30))  # 직전 상승 추세: C(t-1)=98 > C(t-4)≈95.2
    assert not _sig(_frame(up + [98, 101], opens=up + [100, 97.5]), "engulfing", cfg).iloc[-1]
    assert not _sig(ok, "engulfing", cfg, {"min_body_ratio": 2.0}).iloc[-1]  # 몸통 3.5 < 2 × 2


def test_granville_needs_history(cfg):
    df = _frame([100.0] * 150)  # SMA200 계산 불가
    assert not _sig(df, "granville_buy1", cfg).any()

