"""S2 워밍업 보정 (A2, FR-E8) — test_lookahead 일부 포함."""

import numpy as np
import pandas as pd
import pytest

from regime_lab.data.warmup import attach_warmup, jump_tickers, link_ratios

pytestmark = pytest.mark.data
T0 = pd.Timestamp("2020-09-01")


def test_warmup_rows_only_before_start(sample_frame):
    w = sample_frame[sample_frame["is_warmup"]]
    assert len(w) > 0
    assert w["date"].max() < T0
    assert w["date"].min() >= pd.Timestamp("2019-08-01")
    assert not sample_frame[~sample_frame["is_warmup"]]["date"].lt(T0).any()
    assert not sample_frame.duplicated(["ticker", "date"]).any()


def test_link_continuity_and_units(sample_daily, warm_source, sample_frame):
    """연결일 kor_price 종가×비율 == store 종가, 거래대금은 원 단위로 변환."""
    r = link_ratios(sample_daily, warm_source, "2020-09-01")
    k = warm_source[warm_source["date"] == T0].set_index("ticker")
    s = sample_daily[sample_daily["date"] == T0].set_index("ticker")
    common = r.index
    assert np.allclose(k.loc[common, "close"] * r[common], s.loc[common, "close"])
    # 거래대금 단위: 연결일 기준 kor_price(백만원)×1e6 과 store(원)가 같은 자릿수
    ratio_val = (k.loc[common, "value_mil"] * 1e6 / s.loc[common, "value"]).replace([np.inf], np.nan).dropna()
    assert ratio_val.median() == pytest.approx(1.0, rel=0.05)
    w = sample_frame[sample_frame["is_warmup"] & (sample_frame["ticker"] == "005930")]
    assert w["value"].median() > 1e11  # 삼성전자 일 거래대금은 수천억 원 단위


def test_jump_guard_excludes_unadjusted_history(cfg):
    dates = pd.bdate_range("2020-08-26", periods=4)
    w = pd.DataFrame({"ticker": "X", "date": dates[:3], "close": [100.0, 100.0, 50.0]})
    d = pd.DataFrame({"ticker": ["X"], "date": [dates[3]], "close": [50.0]})
    assert jump_tickers(w, d, dates[3], 0.35) == {"X"}
    w2 = w.assign(close=[50.0, 51.0, 50.0])
    assert jump_tickers(w2, d, dates[3], 0.35) == set()


def test_no_warmup_when_source_missing(sample_daily, cfg):
    out = attach_warmup(sample_daily, None, cfg)
    assert not out["is_warmup"].any()
    assert len(out) == len(sample_daily)
