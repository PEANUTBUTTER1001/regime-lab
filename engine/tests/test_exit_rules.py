"""선택 청산 규칙 (2026-10-07): 트레일링 스톱·본전 스톱·이동평균 이탈. 기본 꺼짐 → 기존 결과 불변."""

import numpy as np
import pandas as pd
import pytest
from synth import make_frame, make_market
from test_execution import _run

from regime_lab.backtest import run_backtest
from regime_lab.indicators import sma
from regime_lab.patterns import compute_signals
from regime_lab.runs import Strategy, StrategyError

BASE = {"stop_loss_pct": None, "take_profit_pct": None, "max_hold_days": 30}  # 데이터 62행 안에서 끝나는 기간


def _ex(**kw):
    return {**BASE, **kw}


def test_trailing_stop_from_peak(cfg):
    # 진입 6일 시가 100 → 종가가 120 까지 오른 뒤 107(최고 120 대비 -10.8%) → 다음 날 시가 청산
    closes = [100.0] * 8 + [110, 120, 115, 107] + [107.0] * 50
    df = make_frame(closes)
    tr, _ = _run(df, [5], cfg, exit_cfg=_ex(trailing_stop_pct=-10))
    t = tr.iloc[0]
    assert t.exit_reason == "trailing_stop" and t.exit_signal_date == df.at[11, "date"] and t.exit_date == df.at[12, "date"]
    # 115 는 최고 대비 -4.2% 라 청산 아님. 규칙을 끄면 최대 보유까지 간다
    tr2, _ = _run(df, [5], cfg, exit_cfg=BASE)
    assert tr2.iloc[0].exit_reason == "time"


def test_trailing_peak_includes_entry_price(cfg):
    closes = [100.0] * 6 + [89.0] + [89.0] * 60  # 진입가 100 이 최고 기준가 → 89 는 -11%
    df = make_frame(closes)
    df.loc[6, "open"] = 100.0  # 합성 프레임은 시가 = 종가라 진입일 시가를 따로 둔다
    tr, _ = _run(df, [5], cfg, exit_cfg=_ex(trailing_stop_pct=-10))
    assert tr.iloc[0].exit_reason == "trailing_stop" and tr.iloc[0].exit_signal_date == df.at[6, "date"]


def test_breakeven_stop(cfg):
    closes = [100.0] * 8 + [103, 106, 102, 99.9] + [99.9] * 50  # +6% 를 찍은 뒤 진입가 아래로
    df = make_frame(closes)
    tr, _ = _run(df, [5], cfg, exit_cfg=_ex(breakeven_trigger_pct=5))
    t = tr.iloc[0]
    assert t.exit_reason == "breakeven_stop" and t.exit_signal_date == df.at[11, "date"]
    tr2, _ = _run(df, [5], cfg, exit_cfg=_ex(breakeven_trigger_pct=7))  # +7% 는 못 찍음 → 본전 스톱 미작동
    assert tr2.iloc[0].exit_reason == "time"


def test_ma_exit(cfg):
    closes = list(np.linspace(80, 110, 40)) + [105, 100, 95] + [95.0] * 70
    df = make_frame(closes)
    tr, _ = _run(df, [39], cfg, exit_cfg=_ex(ma_exit_window=10))
    t = tr.iloc[0]
    m10 = sma(df, 10)
    d = df.index[df["date"] == t.exit_signal_date][0]
    assert t.exit_reason == "ma_exit" and df.at[d, "close"] < m10[d]
    e = df.index[df["date"] == t.entry_date][0]
    assert all(df.at[i, "close"] >= m10[i] for i in range(e, d))  # 그 전날까지는 이평 위


def test_priority_stop_loss_before_trailing(cfg):
    closes = [100.0] * 6 + [90.0] + [90.0] * 60  # 같은 날 손절(-10%)과 트레일링(-10%) 동시 → 손절
    df = make_frame(closes)
    df.loc[6, "open"] = 100.0
    tr, _ = _run(df, [5], cfg, exit_cfg=_ex(stop_loss_pct=-10, trailing_stop_pct=-10))
    assert tr.iloc[0].exit_reason == "stop_loss"


@pytest.mark.parametrize("bad,key", [
    ({"trailing_stop_pct": 5}, "exit.trailing_stop_pct"),
    ({"trailing_stop_pct": "-10"}, "exit.trailing_stop_pct"),
    ({"breakeven_trigger_pct": 0}, "exit.breakeven_trigger_pct"),
    ({"ma_exit_window": 3}, "exit.ma_exit_window"),
    ({"ma_exit_window": 20.5}, "exit.ma_exit_window"),
])
def test_validation(cfg, bad, key):
    with pytest.raises(StrategyError) as e:
        Strategy.from_dict({"name": "x", "patterns": ["breakout_20d"], "exit": bad}).validate(cfg)
    assert key in e.value.errors, e.value.errors


def test_defaults_off_keep_format(cfg):
    s = Strategy.from_dict({"name": "x", "patterns": ["breakout_20d"]})
    eff = s.effective(cfg)["exit"]
    assert set(eff) == {"stop_loss_pct", "take_profit_pct", "max_hold_days", "trailing_stop_pct"}  # 새 키 없음
    s2 = Strategy.from_dict({"name": "x", "patterns": ["breakout_20d"],
                             "exit": {"trailing_stop_pct": -10, "ma_exit_window": 20, "breakeven_trigger_pct": 5}})
    s2.validate(cfg)
    assert s2.effective(cfg)["exit"]["ma_exit_window"] == 20


@pytest.mark.parametrize("cut", ["2021-06-30", "2023-03-31", "2024-02-29"])
@pytest.mark.parametrize("rule", [{"trailing_stop_pct": -7}, {"breakeven_trigger_pct": 3}, {"ma_exit_window": 30},
                                  {"ma_exit_window": 20}])
def test_truncation_invariance(cfg, rule, cut):
    """cut 이전에 청산이 끝난 거래는 cut 이후 데이터 유무와 무관 (새 청산 규칙도 t일까지 값만)."""
    m = make_market(cfg, n_tickers=10, seed=17)
    full = m.frame
    part = full[full["date"] <= pd.Timestamp(cut)].reset_index(drop=True)
    ex = {"stop_loss_pct": -8, "take_profit_pct": 20, "max_hold_days": 40, **rule}
    cols = ["ticker", "entry_date", "exit_date", "exit_reason", "net_ret"]
    a, _ = run_backtest(full, compute_signals(full, ["breakout_20d"], "or", cfg), m.index, cfg, exit_cfg=ex)
    b, _ = run_backtest(part, compute_signals(part, ["breakout_20d"], "or", cfg), m.index, cfg, exit_cfg=ex)
    a = a[a["exit_date"] < pd.Timestamp(cut)][cols].reset_index(drop=True)
    b = b[(b["exit_date"] < pd.Timestamp(cut)) & (b["exit_reason"] != "end_of_data")][cols].reset_index(drop=True)
    assert len(a) > 0
    pd.testing.assert_frame_equal(a.astype(str), b.astype(str))
    reasons = set(a["exit_reason"].astype(str))
    assert reasons & {"trailing_stop", "breakeven_stop", "ma_exit"}, reasons  # 새 규칙이 실제로 작동
