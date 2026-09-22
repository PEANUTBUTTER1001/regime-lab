"""test_indicators (FR-P5): SMA·RSI·볼린저·VWAP_20 수기 계산 및 kor_indicators 참조값 대조."""

import numpy as np
import pandas as pd
import pytest
from synth import make_frame

from regime_lab import indicators as I

# 참조 테이블은 소수 둘째 자리 반올림 값 → 절대 1e-2, 큰 가격의 부동소수 오차용 상대 1e-7
ATOL, RTOL = 1e-2, 1e-7
# 참조 테이블 자체 이상치 허용 비율. 확인된 사례: 011810 2025-07-28 BB_*_20 이 같은 입력의
# numpy 정확 계산(하단 3517.8528)과 0.017 차이 — 참조값 쪽 오차로 판단 (S5 보고)
MAX_MISMATCH_RATIO = 1e-4


def test_sma_hand():
    df = make_frame([1, 2, 3, 4, 5, 6])
    s = I.sma(df, 5)
    assert s.iloc[:4].isna().all()
    assert s.iloc[4] == 3.0 and s.iloc[5] == 4.0


def test_rsi_sma_hand():
    # 14일 상승 합 = 7, 하락 합 = 3 → RSI = 100 - 100/(1+7/3) = 70
    closes = [100.0]
    moves = [1, -1, 1, -1, 1, -1, 1, 1, 1, 1, 0, 0, 0, 0]
    for m in moves:
        closes.append(closes[-1] + m)
    df = make_frame(closes)
    r = I.rsi(df, 14, "sma")
    assert r.iloc[:14].isna().all()
    assert r.iloc[14] == pytest.approx(70.0)


def test_bollinger_ddof_hand():
    df = make_frame([1.0, 2.0, 3.0, 4.0])
    b0 = I.bollinger(df, 4, 2.0, ddof=0)
    b1 = I.bollinger(df, 4, 2.0, ddof=1)
    assert b0["bb_mid"].iloc[3] == 2.5
    assert b0["bb_lower"].iloc[3] == pytest.approx(2.5 - 2 * np.sqrt(1.25))
    assert b1["bb_lower"].iloc[3] == pytest.approx(2.5 - 2 * np.sqrt(5 / 3))


def test_vwap_hand():
    df = make_frame([10.0, 20.0], volume=[1.0, 3.0])
    df["high"] = [13.0, 23.0]
    df["low"] = [7.0, 17.0]
    v = I.vwap(df, 2)
    # 전형가격 10, 20 → (10×1 + 20×3)/4 = 17.5
    assert v.iloc[1] == pytest.approx(17.5)


def test_prev_window_excludes_today():
    df = make_frame([1, 2, 3, 100.0])
    m = I.rolling_max_prev(df, df["high"], 3)
    assert m.iloc[3] == 3.0  # t일(100) 미포함


def test_windows_do_not_cross_tickers():
    df = pd.concat([make_frame([1.0] * 5, "A"), make_frame([9.0] * 3, "B")], ignore_index=True)
    s = I.sma(df, 3)
    assert s.iloc[5:7].isna().all() and s.iloc[7] == 9.0


@pytest.mark.data
@pytest.mark.parametrize("mine,ref_col", [
    ("sma5", "MA_5"), ("sma20", "MA_20"), ("rsi14", "RSI_14"),
    ("bb_lower", "BB_Lower_20"), ("bb_upper", "BB_Upper_20"), ("bb_mid", "BB_Middle_20"),
    ("vwap20", "VWAP_20"),
])
def test_matches_kor_indicators(indicator_reference, cfg, mine, ref_col):
    """kor_indicators 는 kor_price 로 계산된 값이므로 같은 입력으로 공식을 대조한다."""
    price, ref = indicator_reference
    df = price.sort_values(["ticker", "date"]).reset_index(drop=True)
    out = I.compute_indicators(df.assign(value=df["value_mil"] * 1e6), cfg)
    m = out.merge(ref, on=["ticker", "date"])
    m = m[m["date"] >= "2020-09-01"]
    both = m[mine].notna() & m[ref_col].notna()
    assert both.sum() > 30_000
    ok = np.isclose(m.loc[both, mine], m.loc[both, ref_col], atol=ATOL, rtol=RTOL)
    assert (~ok).mean() <= MAX_MISMATCH_RATIO, m.loc[both][~ok][["ticker", "date", mine, ref_col]].head()
    assert np.abs(m.loc[both, mine] - m.loc[both, ref_col]).max() < 0.05
