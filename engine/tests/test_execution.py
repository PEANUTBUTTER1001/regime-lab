"""test_execution (FR-E1~E5): 진입·청산 가격, 상/하한가, 거래정지, 상장폐지 수기 사례."""

import numpy as np
import pandas as pd
import pytest
from synth import make_frame

from regime_lab.backtest import ExitRule, run_backtest


def _index(df, ret_per_day=0.0):
    dates = df["date"].drop_duplicates().sort_values()
    lvl = 1000 * (1 + ret_per_day) ** np.arange(len(dates))
    return pd.concat([pd.DataFrame({"date": dates, "market": m, "open": lvl, "close": lvl})
                      for m in ("KOSPI", "KOSDAQ")], ignore_index=True)


def _run(df, sig_days, cfg, **kw):
    sig = pd.Series(False, index=df.index)
    sig.iloc[list(sig_days)] = True
    return run_backtest(df, sig, _index(df, kw.pop("idx_ret", 0.0)), cfg, **kw)


def test_entry_next_open_and_time_exit(cfg):
    df = make_frame(np.full(60, 100.0))
    df.loc[6, "open"] = 101.0
    tr, sk = _run(df, [5], cfg)
    t = tr.iloc[0]
    assert t.entry_date == df.at[6, "date"] and t.entry_price == 101.0
    # 진입일 포함 20거래일째(25) 종가 검사 → 26 시가 청산
    assert t.exit_reason == "time" and t.exit_signal_date == df.at[25, "date"] and t.exit_date == df.at[26, "date"]
    assert t.hold_days == 20
    assert t.gross_ret == pytest.approx(100 / 101 - 1)


def test_stop_loss_and_take_profit_on_close(cfg):
    df = make_frame(np.full(40, 100.0))
    df.loc[8, "close"] = 92.0  # -8% 정확히 → 손절
    df.loc[9, "open"] = 91.0
    tr, _ = _run(df, [5], cfg)
    t = tr.iloc[0]
    assert t.exit_reason == "stop_loss" and t.exit_date == df.at[9, "date"] and t.exit_price == 91.0

    df = make_frame(np.full(40, 100.0))
    df.loc[7, "close"] = 119.9  # +19.9% → 미충족
    df.loc[8, "close"] = 120.0  # +20% → 익절
    df.loc[9, "open"] = 121.0
    t = _run(df, [5], cfg)[0].iloc[0]
    assert t.exit_reason == "take_profit" and t.exit_price == 121.0


def test_limit_up_skip_uses_raw_prices(cfg):
    df = make_frame(np.full(40, 100.0))
    df.loc[5, "close_raw"] = 1000.0
    df.loc[6, "open_raw"] = 1300.0  # +30% → 스킵
    tr, sk = _run(df, [5], cfg)
    assert tr.empty and sk.iloc[0].reason == "limit_up"
    df.loc[6, "open_raw"] = 1299.0
    df.loc[6, "open"] = 150.0  # 수정주가 +50% 여도 판정은 원주가
    tr, sk = _run(df, [5], cfg)
    assert len(tr) == 1 and sk.empty and tr.iloc[0].entry_price == 150.0


def test_entry_halted_skip(cfg):
    df = make_frame(np.full(40, 100.0))
    df.loc[6, ["open", "volume", "halted"]] = [np.nan, 0.0, True]
    tr, sk = _run(df, [5], cfg)
    assert tr.empty and sk.iloc[0].reason == "halted_entry"


def test_exit_retry_on_halt_zero_volume_and_limit_down(cfg):
    df = make_frame(np.full(40, 100.0))
    df.loc[8, "close"] = 90.0  # 손절 결정
    df.loc[9, ["open", "volume", "halted"]] = [np.nan, 0.0, True]  # 정지
    df.loc[10, "volume"] = 0.0  # 거래량 0
    df.loc[10, "close_raw"] = 1000.0
    df.loc[11, "open_raw"] = 700.0  # 하한가 시가 (≤ 전일 원주가 × 0.70)
    df.loc[12, "open"] = 80.0
    t = _run(df, [5], cfg)[0].iloc[0]
    assert t.exit_date == df.at[12, "date"] and t.exit_retries == 3 and t.exit_price == 80.0


def test_one_position_per_ticker(cfg):
    df = make_frame(np.full(80, 100.0))
    tr, _ = _run(df, [5, 10, 20, 26, 30], cfg)
    # 5 → 6 진입, 26 청산 체결. 10·20 무시, 26(청산 체결일 종가 신호) 허용, 30 무시
    assert list(tr["signal_date"]) == [df.at[5, "date"], df.at[26, "date"]]


def test_delisted_exit_at_last_close(cfg):
    df = make_frame(np.full(15, 100.0), ticker="DL")
    df.loc[14, "close"] = 50.0
    tr, _ = _run(df, [5], cfg, delisted_tickers={"DL"})
    t = tr.iloc[0]
    assert t.exit_reason == "delisted" and t.exit_date == df.at[14, "date"] and t.exit_price == 50.0
    assert t.exit_at_close and not t.excluded


def test_end_of_data_is_excluded(cfg):
    df = make_frame(np.full(15, 100.0))
    t = _run(df, [5], cfg)[0].iloc[0]
    assert t.exit_reason == "end_of_data" and t.excluded


def test_warmup_ineligible_and_pre_start_signals_ignored(cfg):
    df = make_frame(np.full(60, 100.0), start="2020-08-03")
    df["eligible"] = True
    df.loc[:19, "is_warmup"] = True  # 2020-08-03 ~ 2020-08-28
    df.loc[40, "eligible"] = False
    tr, sk = _run(df, [3, 19, 40], cfg)
    assert tr.empty and sk.empty


def test_excess_return_vs_market_index(cfg):
    df = make_frame(np.full(60, 100.0))
    df.loc[26, "open"] = 110.0
    tr, _ = _run(df, [5], cfg, idx_ret=0.001)
    t = tr.iloc[0]
    idx_ret = 1.001 ** 20 - 1  # 진입일(6) 시가 → 청산일(26) 시가
    assert t.index_ret == pytest.approx(idx_ret)
    assert t.excess_ret == pytest.approx(t.net_ret - idx_ret)
    assert t.market == "KOSPI"


def test_exit_rule_validation(cfg):
    lim = cfg["exit_limits"]
    with pytest.raises(ValueError):
        ExitRule.from_cfg({**cfg["exit"], "trailing_stop_pct": 5}, lim)
    with pytest.raises(ValueError):
        ExitRule.from_cfg({**cfg["exit"], "max_hold_days": None}, lim)
    with pytest.raises(ValueError):
        ExitRule.from_cfg({**cfg["exit"], "stop_loss_pct": -60}, lim)
    assert ExitRule.from_cfg({**cfg["exit"], "take_profit_pct": None}, lim).take_profit_pct is None


def test_trade_records_liquidity_group_at_signal_date(cfg):
    df = make_frame(np.full(40, 100.0))
    df["liq_group"] = "mid"
    df.loc[5, "liq_group"] = "large"
    t = _run(df, [5], cfg)[0].iloc[0]
    assert t.liq_group == "large"
