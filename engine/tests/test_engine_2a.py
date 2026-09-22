"""2A 엔진 보강: 전략 입력 필터(E1·E2), 진행·취소(E4), 실행별 3중 검증(E3), 결과 화면 데이터(FR-U3·U4)."""

import copy
import dataclasses
import json

import numpy as np
import pandas as pd
import pytest
from synth import make_frame

from regime_lab.analysis.report import HIST_EDGES, pnl_histogram, stock_detail, ticker_table, to_jsonable
from regime_lab.analysis.validation import period_split, random_benchmark, split_applicable
from regime_lab.context import STAGES, RecordingContext, RunCancelled
from regime_lab.runs import Strategy, execute, run_and_save


def _s(**kw):
    return Strategy.from_dict({"name": kw.pop("name", "s1"), "patterns": kw.pop("patterns", ["breakout_20d"]), **kw})


# ------------------------------------------------------------------ 2A-1 입력 필터
@pytest.mark.data
def test_filters_apply_on_signal_date(sample_prepared, cfg):
    base = execute(_s(), sample_prepared, cfg)["results"]["s1"]["trades"]
    s = _s(markets=["KOSDAQ"], period={"start": "2022-01-03", "end": "2023-12-28"},
           min_avg_value_krw=2_000_000_000, cap_groups=["mid", "small"])
    tr = execute(s, sample_prepared, cfg)["results"]["s1"]["trades"]
    assert 0 < len(tr) < len(base)
    assert (tr["market"] == "KOSDAQ").all()
    assert tr["signal_date"].between("2022-01-03", "2023-12-28").all()
    assert tr["cap_group"].isin(["mid", "small"]).all()
    f = sample_prepared.frame.set_index(["ticker", "date"])["avg_value20"]
    assert (f.loc[list(zip(tr["ticker"], tr["signal_date"]))].to_numpy() >= 2e9).all()
    # 청산은 기간 끝 이후에도 규칙대로 진행 (E2)
    assert tr["exit_date"].max() > pd.Timestamp("2023-12-28") or tr["exit_reason"].ne("end_of_data").all()


@pytest.mark.data
def test_filter_does_not_change_groups_or_indicators(sample_prepared, cfg):
    """필터는 진입 후보만 거르고, 전종목 기준 그룹·국면 값은 바꾸지 않는다."""
    a = execute(_s(), sample_prepared, cfg)["results"]["s1"]["trades"]
    b = execute(_s(markets=["KOSPI"]), sample_prepared, cfg)["results"]["s1"]["trades"]
    key = ["ticker", "signal_date"]
    m = b.merge(a, on=key, suffixes=("_b", "_a"))
    assert len(m) == len(b)
    for c in ("cap_group", "liq_group", "market_regime", "stock_regime", "net_ret"):
        x, y = m[f"{c}_b"].astype(object).fillna("NA"), m[f"{c}_a"].astype(object).fillna("NA")
        assert (x == y).all(), c


# ------------------------------------------------------------------ 2A-2 진행·취소
@pytest.mark.data
def test_stage_order_and_progress(sample_prepared, cfg, paths, tmp_path):
    seen = []

    class Spy(RecordingContext):
        def progress(self, done, total):
            super().progress(done, total)
            seen.append((self.current, done, total))

    ctx = Spy()
    run_and_save(_s(), sample_prepared, cfg, dataclasses.replace(paths, runs=tmp_path), run_id="r", ctx=ctx)
    assert [n for n, _, _ in ctx.stages] == STAGES
    sig = [(d, t) for st, d, t in seen if st == "signals_execution"]
    assert sig[-1] == (30, 30) and all(a[0] <= b[0] for a, b in zip(sig, sig[1:]))
    agg = [(d, t) for st, d, t in seen if st == "aggregate"]
    assert agg[-1] == (1000, 1000)
    log = json.loads((tmp_path / "r" / "meta.json").read_text(encoding="utf-8"))["stage_log"]
    assert [x["stage"] for x in log] == STAGES


@pytest.mark.data
@pytest.mark.parametrize("stage", ["signals_execution", "aggregate", "save"])
def test_cancel_leaves_no_result(sample_prepared, cfg, paths, tmp_path, stage):
    ctx = RecordingContext(cancel_on_stage=stage)
    with pytest.raises(RunCancelled):
        run_and_save(_s(), sample_prepared, cfg, dataclasses.replace(paths, runs=tmp_path), run_id="c", ctx=ctx)
    assert not (tmp_path / "c").exists()
    assert not list(tmp_path.glob(".tmp_*"))


def test_cancel_from_other_thread_is_observed():
    ctx = RecordingContext()
    ctx.stage("filter")
    ctx.cancel()
    with pytest.raises(RunCancelled):
        ctx.check_cancel()


# ------------------------------------------------------------------ 2A-3 실행별 검증
@pytest.mark.data
def test_compare_run_family_size(sample_prepared, cfg, paths, tmp_path):
    out = run_and_save([_s(name="a"), _s(name="b", patterns=["breakout_vol"])], sample_prepared, cfg,
                       dataclasses.replace(paths, runs=tmp_path), run_id="cmp")
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert res["fdr_family_size"] == 2 and "m" in res["fdr_note"]
    assert [s["strategy"] for s in res["strategies"]] == ["a", "b"]
    assert all(s["validation"]["fdr_family_size"] == 2 for s in res["strategies"])
    with pytest.raises(ValueError):
        execute([_s(name="a"), _s(name="a")], sample_prepared, cfg)


def test_split_not_applicable_when_period_one_side(cfg):
    assert split_applicable(None, "2024-02-29")
    assert split_applicable({"start": "2023-01-02", "end": "2024-06-28"}, "2024-02-29")
    assert not split_applicable({"start": "2021-01-04", "end": "2024-02-29"}, "2024-02-29")
    assert not split_applicable({"start": "2024-03-04", "end": "2025-01-02"}, "2024-02-29")
    tr = pd.DataFrame({"entry_date": pd.to_datetime(["2021-01-05"] * 400), "excess_ret": 0.01,
                       "net_ret": 0.01, "excluded": False})
    s = period_split({"x": tr}, cfg, {"x": {"start": "2021-01-04", "end": "2023-12-28"}})
    assert s.iloc[0].split_judgement == "not_applicable" and not s.iloc[0].split_pass
    single = period_split({"x": tr}, cfg)  # 전략 1개: weakened 불가, 반기 표본 부족
    assert single.iloc[0].split_judgement == "sample_insufficient"


def test_random_entries_restricted_to_period(cfg):
    closes = np.r_[np.full(100, 100.0), np.full(100, 200.0)]  # 100행 이후 가격 2배
    df = make_frame(closes)
    df.loc[100, "open"] = 150.0
    idx = pd.DataFrame({"date": df["date"], "market": "KOSPI", "open": 1000.0, "close": 1000.0})
    tr = pd.DataFrame({"ticker": "T1", "entry_date": [df.date[10]], "exit_date": [df.date[15]],
                       "excess_ret": [0.0], "net_ret": [0.0], "excluded": False})
    c = copy.deepcopy(cfg)
    c["analysis"]["random_bench_iterations"] = 200
    period = {"start": str(df.date[0].date()), "end": str(df.date[60].date())}
    rb = random_benchmark(tr, df, idx, c, period=period)
    assert np.allclose(rb["random_means"], -0.003)  # 기간 안에서는 가격 변화 없음 → 비용만
    rb_all = random_benchmark(tr, df, idx, c)
    assert rb_all["random_means"].max() > 0  # 전체 기간이면 가격 점프 구간도 추출됨


# ------------------------------------------------------------------ 2A-4 결과 화면 데이터
def _tr(rets, tickers):
    return pd.DataFrame({"ticker": tickers, "net_ret": rets, "excess_ret": rets, "market": "KOSPI",
                         "excluded": False})


def test_histogram_fixed_bins_and_total():
    tr = _tr([-0.5, -0.30, -0.01, 0.0, 0.019, 0.02, 0.29, 0.30, 1.2], ["A"] * 9)
    tr.loc[len(tr)] = {"ticker": "A", "net_ret": 0.1, "excess_ret": 0.1, "market": "KOSPI", "excluded": True}
    h = pnl_histogram(tr)
    assert len(h) == len(HIST_EDGES) - 1 and sum(b["count"] for b in h) == 9
    by = {(b["lo"], b["hi"]): b["count"] for b in h}
    assert by[(-np.inf, -0.30)] == 1 and by[(0.30, np.inf)] == 2 and by[(0.0, 0.02)] == 2
    assert json.dumps(to_jsonable(h))  # inf 는 null 로 직렬화


def test_ticker_table_sorted_by_trades_not_returns():
    tr = _tr([0.5, -0.1, -0.1, -0.1], ["WIN", "LOSE", "LOSE", "LOSE"])
    t = ticker_table(tr, {"WIN": "승자", "LOSE": "패자"}, 300)
    assert [r["ticker"] for r in t] == ["LOSE", "WIN"]
    assert sum(r["trades"] for r in t) == 4 and t[1]["name"] == "승자" and t[0]["sample_insufficient"]


@pytest.mark.data
def test_stock_detail_matches_trades(sample_prepared, cfg):
    res = execute(_s(), sample_prepared, cfg)["results"]["s1"]
    tk = res["trades"]["ticker"].value_counts().index[0]
    d = stock_detail(sample_prepared.frame, res["trades"], tk, sample_prepared.names, cfg)
    n = int((res["trades"]["ticker"] == tk).sum())
    assert len(d["trades"]) == n and len(d["markers"]) == 2 * n
    assert d["name"] == sample_prepared.names[tk]
    assert len(d["series"]["date"]) == len(d["series"]["close"]) > 1000
    assert d["stock_regime_segments"] and d["equity"] and d["buy_and_hold"]
    assert d["stats"]["trades"] == d["sample"]["trades"] == n - d["sample"]["open_at_data_date"]
    json.dumps(to_jsonable(d))


@pytest.mark.data
def test_result_json_consistent_with_trades(sample_prepared, cfg, paths, tmp_path):
    out = run_and_save(_s(), sample_prepared, cfg, dataclasses.replace(paths, runs=tmp_path), run_id="rj")
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))["strategies"][0]
    tr = pd.read_parquet(out / "strategies" / "s1" / "trades.parquet")
    n = int((~tr["excluded"]).sum())
    assert res["summary"]["trades"] == n
    assert sum(b["count"] for b in res["pnl_histogram"]) == n
    assert sum(r["trades"] for r in res["tickers"]) == n
    assert res["split"]["first_half"]["trades"] + res["split"]["second_half"]["trades"] == n
    assert sum(c["trades"] for c in res["cells_market"]) == n
    assert res["strategy_input"]["period"] == {"start": "2020-09-01", "end": "2026-09-18"}
    eq = pd.read_parquet(out / "strategies" / "s1" / "equity.parquet")
    assert len(res["equity"]) == len(eq) and res["equity"][-1]["equity"] == pytest.approx(eq["equity"].iloc[-1])
