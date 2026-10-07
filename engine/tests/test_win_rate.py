"""P1-1 승률 정의 회귀 테스트 (FR-E7, METRIC_DEFS["win_rate"]).

역방향 탐색은 목표 승률을 기준으로 후보를 고르므로, 승률 정의가 바뀌면 탐색 결과도 바뀐다.
정의: 집계 포함 거래(end_of_data 제외) 중 비용 차감 후 net_ret > 0 인 거래의 비율. 손익 0 은 승리가 아니다.
"""

import numpy as np
import pandas as pd
import pytest
from synth import make_frame, make_market

from regime_lab.backtest import METRIC_DEFS, run_backtest, summarize, trade_stats
from regime_lab.runs import Strategy, execute


def _idx(df):
    d = df["date"].drop_duplicates()
    return pd.DataFrame({"date": d, "market": "KOSPI", "open": 1.0, "close": 1.0})


def _trades(net):
    net = np.asarray(net, float)
    return pd.DataFrame({"net_ret": net, "gross_ret": net + 0.003, "excess_ret": net})


def test_definition_text():
    assert METRIC_DEFS["win_rate"][2] == "net_ret > 0 거래 비율"


def test_zero_return_is_not_a_win():
    assert trade_stats(_trades([0.1, 0.0, -0.1, 0.0]))["win_rate"] == 0.25


def test_no_trades_is_nan():
    assert np.isnan(trade_stats(_trades([]))["win_rate"])


def test_cost_turns_small_gain_into_loss(cfg):
    # 종가 0.2% 상승은 왕복 비용 0.3% 를 넘지 못해 net_ret < 0 → 승리가 아니다
    closes = np.r_[np.full(10, 100.0), np.full(110, 100.2)]
    df = make_frame(closes)
    sig = pd.Series(False, index=df.index)
    sig.iloc[5] = True  # 진입 6행 시가 100, 시간 청산 후 100.2
    tr, sk = run_backtest(df, sig, _idx(df), cfg)
    assert len(tr) == 1 and tr.at[0, "gross_ret"] > 0 > tr.at[0, "net_ret"]
    assert summarize(tr, sk, df)["win_rate"] == 0.0


def test_end_of_data_trades_are_excluded(cfg):
    # 마지막 신호는 기준일까지 보유 중(end_of_data) → 승률 분모에서 빠진다
    closes = np.r_[np.full(30, 100.0), np.full(30, 130.0)]
    df = make_frame(closes)
    sig = pd.Series(False, index=df.index)
    sig.iloc[[5, len(df) - 3]] = True
    tr, sk = run_backtest(df, sig, _idx(df), cfg)
    assert tr["exit_reason"].tolist() == ["time", "end_of_data"]
    s = summarize(tr, sk, df)
    assert s["trades"] == 1 and s["excluded_trades"] == 1 and s["win_rate"] == 0.0


# 합성 시장(30종목, seed 0)에서 핵심 5종의 (거래 수, 승률). 엔진·설정·합성 데이터가 바뀌면 값이 바뀐다.
GOLDEN = {
    "ma_cross_5_20": (899, 0.457174638487208),
    "breakout_20d": (824, 0.4878640776699029),
    "breakout_vol": (203, 0.4876847290640394),
    "rsi_rebound": (681, 0.44933920704845814),
    "bb_lower_recover": (720, 0.45555555555555555),
}


@pytest.fixture(scope="module")
def market(cfg):
    return make_market(cfg, n_tickers=30, seed=0)


@pytest.mark.parametrize("name", sorted(GOLDEN))  # 핵심 5종 고정값 (후순위 5종은 test_later_patterns)
def test_win_rate_regression(market, cfg, name):
    r = execute(Strategy.from_dict({"name": name, "patterns": [name]}), market, cfg)["results"][name]
    s, tr = r["summary"], r["trades"]
    inc = tr[~tr["excluded"]]
    assert s["win_rate"] == pytest.approx((inc["net_ret"] > 0).mean(), abs=0)
    assert (s["trades"], s["win_rate"]) == pytest.approx(GOLDEN[name], abs=1e-12)
