"""P1-5 역방향 탐색: 후보 생성·구간 분할·후보 평가·FDR·상태 분류 (D-1, plan/01 작업 ②)."""

import numpy as np
import pandas as pd
import pytest
from synth import make_market

from regime_lab.analysis.validation import bh_reject
from regime_lab.pipeline import Prepared
from regime_lab.runs import execute
from regime_lab.search import (
    STATUSES,
    SearchError,
    SearchRequest,
    closest_candidate,
    count_candidates,
    estimate_seconds,
    evaluate_candidates,
    fdr_family,
    generate_candidates,
    meets,
    pattern_sets,
    run_search,
    split_periods,
    truncate,
    with_period,
)

P5 = ["bb_lower_recover", "breakout_20d", "breakout_vol", "ma_cross_5_20", "rsi_rebound"]


def _req(cfg, **kw):
    return SearchRequest.from_dict({"name": "s", "target_win_rate": 0.5, **kw}, cfg)


# ---------------------------------------------------------------- 후보 생성 (P1-5.1)
@pytest.mark.parametrize("n,combines,expected", [(5, ["and", "or"], 57), (3, ["and", "or"], 11), (3, ["or"], 7),
                                                 (1, ["and", "or"], 1)])
def test_pattern_set_counts(n, combines, expected):
    assert len(pattern_sets(P5[:n], combines)) == expected


def test_pattern_sets_order_and_single_pattern_once():
    sets = pattern_sets(["rsi_rebound", "breakout_20d"], ["and", "or"])
    assert sets == [(("breakout_20d",), "or"), (("rsi_rebound",), "or"),
                    (("breakout_20d", "rsi_rebound"), "and"), (("breakout_20d", "rsi_rebound"), "or")]


def test_default_request(cfg):
    req = _req(cfg)
    assert req.min_trades == 300
    cands = generate_candidates(req)
    assert len(cands) == count_candidates(req) == 57
    assert [c.name for c in cands[:2]] == ["c001", "c002"]
    assert cands[0].patterns == ["bb_lower_recover"] and cands[0].exit == {
        "stop_loss_pct": -8, "take_profit_pct": 20, "max_hold_days": 20}
    assert estimate_seconds(57, cfg) == pytest.approx(57 * cfg["search"]["sec_per_candidate"])


def test_candidate_order_ignores_input_order(cfg):
    a = _req(cfg, axes={"patterns": ["rsi_rebound", "breakout_20d"], "stop_loss_pct": [None, -5],
                        "max_hold_days": [20, 5]})
    b = _req(cfg, axes={"patterns": ["breakout_20d", "rsi_rebound"], "stop_loss_pct": [-5, None],
                        "max_hold_days": [5, 20]})
    assert [c.to_dict() for c in generate_candidates(a)] == [c.to_dict() for c in generate_candidates(b)]
    first = generate_candidates(a)[:4]
    assert [(c.exit["stop_loss_pct"], c.exit["max_hold_days"]) for c in first] == [(-5, 5), (-5, 20), (None, 5),
                                                                                   (None, 20)]


def test_candidates_carry_filters_and_search_period(cfg):
    req = _req(cfg, axes={"patterns": ["rsi_rebound"]},
               filters={"markets": ["KOSDAQ"], "cap_groups": ["small"],
                        "period": {"start": "2021-01-04", "end": "2025-12-30"}})
    explore, evaluate = split_periods(cfg, req.filters["period"])
    (c,) = generate_candidates(req, explore)
    c.validate(cfg)
    assert c.markets == ["KOSDAQ"] and c.cap_groups == ["small"] and c.period == explore
    assert explore == {"start": "2021-01-04", "end": "2024-02-29"}
    assert evaluate == {"start": "2024-03-01", "end": "2025-12-30"}


# ---------------------------------------------------------------- 요청 검증·경계
@pytest.mark.parametrize("body,key", [
    ({"target_win_rate": None}, "target_win_rate"),
    ({"target_win_rate": 1.5}, "target_win_rate"),
    ({"target_win_rate": "0.5"}, "target_win_rate"),
    ({"target_win_rate": True}, "target_win_rate"),
    ({"min_trades": 29}, "min_trades"),
    ({"min_trades": 5001}, "min_trades"),
    ({"min_trades": 30.5}, "min_trades"),
    ({"axes": {"patterns": ["unknown"]}}, "axes.patterns"),
    ({"axes": {"stop_loss_pct": [-7]}}, "axes.stop_loss_pct"),
    ({"axes": {"max_hold_days": []}}, "axes.max_hold_days"),
    ({"axes": {"combine": ["or", "or"]}}, "axes.combine"),
    ({"axes": {"take_profit_pct": [True]}}, "axes.take_profit_pct"),
    ({"axes": {"period": [1]}}, "axes.period"),
    ({"filters": {"markets": ["NYSE"]}}, "filters.markets"),
    ({"filters": {"min_avg_value_krw": 1}}, "filters.min_avg_value_krw"),
    ({"filters": {"exit": {}}}, "filters.exit"),
    ({"filters": {"period": {"start": "2021-01-04", "end": "2023-12-29"}}}, "filters.period"),  # 분할일 전에 끝남
    ({"filters": {"period": {"start": "2024-03-04", "end": "2025-12-30"}}}, "filters.period"),  # 분할일 뒤에 시작
    ({"name": "bad name!"}, "name"),
    ({"sort_by": "win_rate"}, "_"),
])
def test_request_validation(cfg, body, key):
    with pytest.raises(SearchError) as e:
        SearchRequest.from_dict({"name": "s", "target_win_rate": 0.5, **body}, cfg)
    assert key in e.value.errors, e.value.errors
    assert e.value.code == "validation_failed"


def test_min_trades_bounds_accepted(cfg):
    assert _req(cfg, min_trades=30).min_trades == 30 and _req(cfg, min_trades=5000).min_trades == 5000


def test_too_many_candidates(cfg):
    with pytest.raises(SearchError) as e:
        _req(cfg, axes={"stop_loss_pct": [-5, -8], "take_profit_pct": [10, 20]})  # 57 × 4 = 228
    assert e.value.code == "too_many_candidates"
    assert e.value.detail == {"candidates": 228, "max_candidates": 200}


def test_split_periods_default(cfg):
    explore, evaluate = split_periods(cfg)
    assert explore == {"start": "2020-09-01", "end": "2024-02-29"}
    assert evaluate == {"start": "2024-03-01", "end": "2026-09-18"}


# ---------------------------------------------------------------- 판정·FDR·가장 가까운 후보
def test_meets_uses_win_rate_and_min_trades(cfg):
    req = _req(cfg, target_win_rate=0.55, min_trades=300)
    assert meets(0.55, 300, req)
    assert not meets(0.5499, 300, req)
    assert not meets(0.9, 299, req)
    assert not meets(np.nan, 1000, req)


def test_fdr_family_counts_every_tried_candidate():
    ids = pd.Series(["c001", "c002", "c003"])
    one = pd.Series({"c001": 0.05})
    assert fdr_family(one, ids[:1], 0.10).tolist() == [True]             # m = 1 이면 0.05 ≤ 0.10
    assert fdr_family(one, ids, 0.10).tolist() == [False, False, False]  # m = 3 이면 0.05 > 0.10·1/3
    two = pd.Series({"c001": 0.01, "c003": 0.06})
    assert fdr_family(two, ids, 0.10).tolist() == [True, False, True]    # 0.06 ≤ 0.10·2/3. 평가 안 한 c002 는 p = 1


def test_closest_candidate_prefers_enough_trades(cfg):
    req = _req(cfg, target_win_rate=0.6, min_trades=300)
    ex = pd.DataFrame({"id": ["c001", "c002", "c003", "c004"], "trades": [100, 400, 500, 800],
                       "win_rate": [0.9, 0.50, 0.55, 0.55]})
    assert closest_candidate(ex, req) == {"id": "c004", "win_rate_short": pytest.approx(0.05), "trades_short": 0}
    ex["trades"] = [100, 200, 250, 50]
    assert closest_candidate(ex, req)["id"] == "c003"  # 모두 부족하면 거래 수 부족분이 가장 작은 후보


def test_truncate_keeps_only_data_until_end(cfg):
    m = make_market(cfg, n_tickers=4, seed=3, end="2024-06-28")
    f = m.frame
    f = f[~((f["ticker"] == "000001") & (f["date"] > "2023-05-31"))]  # 000001 은 분할일 전에 상장폐지
    prep = Prepared(f, m.index, {"000000", "000001"}, m.sectors, m.names)
    t = truncate(prep, "2024-02-29")
    assert t.frame["date"].max() == pd.Timestamp("2024-02-29")
    assert t.index["date"].max() <= pd.Timestamp("2024-02-29")
    assert t.delisted == {"000001"}  # 000000 은 분할일 뒤에도 거래되므로 분할일 기준으로는 보유 중이다


# ---------------------------------------------------------------- 합성 시장 (정방향 일치·선택 불변·전체 흐름)
@pytest.fixture(scope="module")
def market(cfg):
    return make_market(cfg, n_tickers=60, seed=1)


@pytest.fixture(scope="module")
def req2(cfg):
    return SearchRequest.from_dict({"name": "e2e", "target_win_rate": 0.45, "min_trades": 30,
                                    "axes": {"patterns": ["breakout_20d", "rsi_rebound"],
                                             "max_hold_days": [5, 20]}}, cfg)


@pytest.fixture(scope="module")
def result(market, req2, cfg):
    return run_search(req2, market, cfg)


def test_explore_scores_match_forward_run_as_of_split(market, req2, cfg):
    """탐색 구간 값 = split_date 까지의 데이터로 같은 전략을 정방향 실행한 값 (P1-11.1)."""
    explore, _ = split_periods(cfg)
    cut = truncate(market, explore["end"])
    cands = generate_candidates(req2, explore)
    scores = evaluate_candidates(cands, cut, cfg).set_index("id")
    for c in cands:
        res = execute(c, cut, cfg)
        fwd, val = res["results"][c.name]["summary"], res["validation"].iloc[0]
        for k in ("trades", "win_rate", "mean_excess", "sharpe", "mdd"):
            assert scores.at[c.name, k] == pytest.approx(fwd[k], nan_ok=True), (c.name, k)
        assert scores.at[c.name, "p_value"] == pytest.approx(val["p_value"], nan_ok=True)


def test_evaluate_scores_match_forward_run(market, result, cfg):
    t = result["table"].dropna(subset=["evaluate_trades"])
    assert len(t) > 0
    by_id = {c.name: c for c in result["candidates"]}
    for r in t.itertuples():
        fwd = execute(with_period(by_id[r.id], result["split"]["evaluate"]), market, cfg)["results"][r.id]["summary"]
        assert r.evaluate_trades == fwd["trades"] and r.evaluate_win_rate == pytest.approx(fwd["win_rate"])


def test_selection_ignores_evaluation_period_data(market, req2, result, cfg):
    """평가 구간 데이터를 바꿔도 탐색 구간 값과 후보 선택은 그대로다."""
    f = market.frame.copy()
    after = f["date"] > pd.Timestamp(cfg["analysis"]["split_date"])
    k = np.random.default_rng(7).uniform(0.5, 1.5, after.sum())
    for c in ("open", "high", "low", "close", "open_raw", "close_raw"):
        f.loc[after, c] = f.loc[after, c].to_numpy() * k
    changed = run_search(req2, Prepared(f, market.index, market.delisted, market.sectors, market.names), cfg)
    cols = [c for c in result["table"].columns if c.startswith("explore_")]
    a = result["table"].set_index("id")[cols].sort_index()
    b = changed["table"].set_index("id")[cols].sort_index()
    pd.testing.assert_frame_equal(a, b)


def test_run_search_statuses_order_and_fdr(result, req2, cfg):
    t = result["table"]
    assert len(t) == len(result["candidates"]) == 4 * 2  # (단일 2 + and·or 2) × 보유일 2
    assert set(t["status"]) <= set(STATUSES)
    assert t["order"].tolist() == list(range(1, len(t) + 1))
    assert t["status"].map({s: i for i, s in enumerate(STATUSES)}).is_monotonic_increasing
    c = result["counts"]
    assert sum(c[s] for s in STATUSES) == c["candidates"] == len(t)

    met = t["explore_win_rate"].ge(req2.target_win_rate) & t["explore_trades"].ge(req2.min_trades)
    assert met.any()
    assert (t["status"].isin(["both", "explore_only"]) == met).all()
    assert t.loc[~met, "evaluate_trades"].isna().all()  # 탐색 미충족 후보는 평가 구간에서 돌리지 않는다
    assert (t.loc[t["status"] == "insufficient_trades", "explore_trades"] < req2.min_trades).all()

    q = cfg["analysis"]["fdr_q"]
    assert t["explore_fdr_pass"].tolist() == bh_reject(t["explore_p_value"].to_numpy(float), q).tolist()
    padded = t["evaluate_p_value"].fillna(1.0).to_numpy(float)  # 평가 FDR 의 m 도 전체 후보 수
    assert t["evaluate_fdr_pass"].astype("boolean").fillna(False).tolist() == bh_reject(padded, q).tolist()
    assert result["closest"] is None


def test_no_candidate_meets_target(market, cfg):
    req = SearchRequest.from_dict({"name": "none", "target_win_rate": 1.0, "min_trades": 30,
                                   "axes": {"patterns": ["rsi_rebound"], "max_hold_days": [5, 20]}}, cfg)
    out = run_search(req, market, cfg)
    t = out["table"]
    assert set(t["status"]) <= {"not_met", "insufficient_trades"}
    assert t["evaluate_trades"].isna().all()
    assert out["closest"]["id"] in set(t["id"]) and out["closest"]["win_rate_short"] > 0


def test_run_search_is_reproducible(market, req2, result, cfg):
    again = run_search(req2, market, cfg)
    pd.testing.assert_frame_equal(result["table"], again["table"])
