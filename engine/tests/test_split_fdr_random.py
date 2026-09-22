"""test_split_fdr_random (FR-A3~A5, A12, A13): 분할 중복 없음, FDR 수기 대조, 시드 고정 재현."""

import copy

import numpy as np
import pandas as pd
import pytest
from synth import make_frame

from regime_lab.analysis.validation import (
    bh_reject,
    one_sided_pvalue,
    period_split,
    random_benchmark,
    split_trades,
    validate,
)


def _tr(entry_dates, excess, ticker="T1"):
    d = pd.to_datetime(entry_dates)
    return pd.DataFrame({"ticker": ticker, "entry_date": d, "exit_date": d + pd.Timedelta(days=7),
                         "excess_ret": excess, "net_ret": excess, "excluded": False})


def test_split_no_overlap_and_boundary():
    tr = _tr(["2024-02-28", "2024-02-29", "2024-03-01", "2024-03-04"], [0.1, 0.1, 0.1, 0.1])
    h1, h2 = split_trades(tr, "2024-02-29")
    assert set(h1.index).isdisjoint(h2.index) and len(h1) + len(h2) == len(tr)
    assert h1["entry_date"].max() == pd.Timestamp("2024-02-29")


def test_split_judgements(cfg):
    n = 300
    early = pd.date_range("2021-01-01", periods=n, freq="D")
    late = pd.date_range("2024-06-01", periods=n, freq="D")
    res = {
        "A": _tr(early.append(late), np.r_[np.full(n, 0.02), np.full(n, 0.03)]),
        "B": _tr(early.append(late), np.r_[np.full(n, 0.03), np.full(n, 0.01)]),
        "C": _tr(early.append(late), np.r_[np.full(n, 0.01), np.full(n, -0.01)]),
        "D": _tr(early.append(late[:10]), np.r_[np.full(n, 0.01), np.full(10, 0.01)]),
    }
    s = period_split(res, cfg).set_index("strategy")
    assert s.loc["A", "split_judgement"] == "maintained"  # 후반 순위 1 ≤ 전반 순위 2
    assert s.loc["B", "split_judgement"] == "weakened"    # 전반 1위 → 후반 2위
    assert s.loc["C", "split_judgement"] == "reversed"
    assert s.loc["D", "split_judgement"] == "sample_insufficient"
    assert list(s["split_pass"]) == [True, True, False, False]


def test_bh_fdr_hand_example():
    # m=5, q=0.10: 임계값 0.02, 0.04, 0.06, 0.08, 0.10
    p = np.array([0.01, 0.05, 0.03, 0.20, 0.07])
    # 정렬 0.01≤0.02, 0.03≤0.04, 0.05≤0.06, 0.07≤0.08, 0.20>0.10 → 상위 4개 기각
    assert bh_reject(p, 0.10).tolist() == [True, True, True, False, True]
    assert bh_reject(np.array([0.03, 0.5]), 0.10).tolist() == [True, False]  # 0.03 ≤ 1/2·0.10
    assert bh_reject(np.array([0.06, 0.5]), 0.10).tolist() == [False, False]
    assert bh_reject(np.array([np.nan, 0.001]), 0.10).tolist() == [False, True]


def test_one_sided_t_test():
    rng = np.random.default_rng(1)
    assert one_sided_pvalue(rng.normal(0.05, 0.1, 500)) < 0.001
    assert one_sided_pvalue(rng.normal(-0.05, 0.1, 500)) > 0.99


def _rand_setup():
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    df = make_frame(closes)
    df["open"] = df["close"] * (1 + rng.normal(0, 0.005, 400))
    idx = pd.DataFrame({"date": df["date"], "market": "KOSPI", "open": 1000.0, "close": 1000.0})
    tr = pd.DataFrame({"ticker": "T1", "entry_date": df["date"].iloc[[50, 150, 250]].to_numpy(),
                       "exit_date": df["date"].iloc[[60, 170, 255]].to_numpy(),
                       "excess_ret": [0.05, 0.02, -0.01], "net_ret": [0.05, 0.02, -0.01], "excluded": False})
    return df, idx, tr


def test_random_benchmark_seed_reproducible(cfg):
    df, idx, tr = _rand_setup()
    c = copy.deepcopy(cfg)
    c["analysis"]["random_bench_iterations"] = 200
    a = random_benchmark(tr, df, idx, c)
    b = random_benchmark(tr, df, idx, c)
    d = random_benchmark(tr, df, idx, c, seed=999)
    assert np.array_equal(a["random_means"], b["random_means"]) and a["percentile"] == b["percentile"]
    assert not np.array_equal(a["random_means"], d["random_means"])
    assert len(a["random_means"]) == 200 and 0 <= a["percentile"] <= 1


def test_random_benchmark_hand_single_path(cfg):
    """보유기간이 전체 구간과 같으면 무작위 진입일이 하나뿐 → 분포가 한 값으로 고정."""
    df = make_frame([100.0, 100, 100, 110])
    df["open"] = [100.0, 100.0, 100.0, 110.0]
    idx = pd.DataFrame({"date": df["date"], "market": "KOSPI", "open": [1000.0, 1000, 1000, 1050],
                        "close": 1000.0})
    tr = pd.DataFrame({"ticker": "T1", "entry_date": [df.date[0]], "exit_date": [df.date[3]],
                       "excess_ret": [0.0], "net_ret": [0.0], "excluded": False})
    c = copy.deepcopy(cfg)
    c["analysis"]["random_bench_iterations"] = 10
    rb = random_benchmark(tr, df, idx, c)
    assert np.allclose(rb["random_means"], 0.10 - 0.003 - 0.05)


def test_validate_requires_all_three(cfg):
    df, idx, _ = _rand_setup()
    c = copy.deepcopy(cfg)
    c["analysis"]["random_bench_iterations"] = 50
    tr = _tr(pd.date_range("2021-01-04", periods=2, freq="D"), [0.01, 0.02])
    tr["ticker"] = "T1"
    v = validate({"S": tr}, df, idx, c).iloc[0]
    assert not v.split_pass and not v.analysis_target  # 표본 부족 → 분석 대상 아님
