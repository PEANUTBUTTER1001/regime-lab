"""P1-5 역방향 탐색: 후보 생성·구간 분할·후보 평가·FDR 선별 (D-1)."""

import numpy as np
import pandas as pd
import pytest
from synth import make_market

from regime_lab.config import load_config
from regime_lab.runs import execute
from regime_lab.search import (
    SearchError,
    SearchRequest,
    evaluate_candidates,
    generate_candidates,
    pattern_sets,
    run_search,
    select,
    split_periods,
    target_met,
    with_period,
)

P5 = ["bb_lower_recover", "breakout_20d", "breakout_vol", "ma_cross_5_20", "rsi_rebound"]


def _req(cfg, **kw):
    return SearchRequest.from_dict({"name": "s", "target": {"win_rate": 0.5}, **kw}, cfg)


# ---------------------------------------------------------------- 후보 생성 (P1-5.1)
@pytest.mark.parametrize("n,combines,expected", [(5, ["and", "or"], 57), (3, ["and", "or"], 11), (3, ["or"], 7),
                                                 (1, ["and", "or"], 1)])
def test_pattern_set_counts(n, combines, expected):
    assert len(pattern_sets(P5[:n], combines)) == expected


def test_pattern_sets_order_and_single_pattern_once():
    sets = pattern_sets(["rsi_rebound", "breakout_20d"], ["and", "or"])
    assert sets == [(("breakout_20d",), "or"), (("rsi_rebound",), "or"),
                    (("breakout_20d", "rsi_rebound"), "and"), (("breakout_20d", "rsi_rebound"), "or")]


def test_default_request_is_57_candidates(cfg):
    cands = generate_candidates(_req(cfg))
    assert len(cands) == 57
    assert [c.name for c in cands[:2]] == ["c001", "c002"]
    assert cands[0].patterns == ["bb_lower_recover"] and cands[0].exit == {
        "stop_loss_pct": -8, "take_profit_pct": 20, "max_hold_days": 20}


def test_candidate_order_ignores_input_order(cfg):
    a = _req(cfg, axes={"patterns": ["rsi_rebound", "breakout_20d"], "stop_loss_pct": [None, -5],
                        "max_hold_days": [20, 5]})
    b = _req(cfg, axes={"patterns": ["breakout_20d", "rsi_rebound"], "stop_loss_pct": [-5, None],
                        "max_hold_days": [5, 20]})
    assert [c.to_dict() for c in generate_candidates(a)] == [c.to_dict() for c in generate_candidates(b)]
    first = generate_candidates(a)[:4]
    assert [(c.exit["stop_loss_pct"], c.exit["max_hold_days"]) for c in first] == [(-5, 5), (-5, 20), (None, 5),
                                                                                   (None, 20)]


def test_candidates_carry_filters_and_period(cfg):
    req = _req(cfg, axes={"patterns": ["rsi_rebound"]}, filters={"markets": ["KOSDAQ"], "cap_groups": ["small"]})
    explore, _ = split_periods(cfg)
    (c,) = generate_candidates(req, explore)
    c.validate(cfg)
    assert c.markets == ["KOSDAQ"] and c.cap_groups == ["small"] and c.period == explore


# ---------------------------------------------------------------- 요청 검증
@pytest.mark.parametrize("body,key", [
    ({"axes": {"patterns": ["unknown"]}}, "axes.patterns"),
    ({"axes": {"stop_loss_pct": [-7]}}, "axes.stop_loss_pct"),
    ({"axes": {"max_hold_days": []}}, "axes.max_hold_days"),
    ({"axes": {"combine": ["or", "or"]}}, "axes.combine"),
    ({"axes": {"take_profit_pct": [True]}}, "axes.take_profit_pct"),
    ({"axes": {"period": [1]}}, "axes.period"),
    ({"target": {"total_cost": 0.1}}, "target.total_cost"),
    ({"target": {"win_rate": "0.5"}}, "target.win_rate"),
    ({"target": {}}, "target"),
    ({"target": {"win_rate": 0.5}, "sort_by": "sharpe"}, "sort_by"),
    ({"filters": {"period": {"start": "2021-01-01", "end": "2022-01-01"}}}, "filters.period"),
    ({"filters": {"markets": ["NYSE"]}}, "filters.markets"),
    ({"filters": {"min_avg_value_krw": 1}}, "filters.min_avg_value_krw"),
    ({"name": "bad name!"}, "name"),
])
def test_request_validation(cfg, body, key):
    with pytest.raises(SearchError) as e:
        SearchRequest.from_dict({"name": "s", "target": {"win_rate": 0.5}, **body}, cfg)
    assert key in e.value.errors, e.value.errors
    assert e.value.code == "validation_failed"


def test_too_many_candidates(cfg):
    with pytest.raises(SearchError) as e:
        _req(cfg, axes={"stop_loss_pct": [-5, -8], "take_profit_pct": [10, 20]})  # 57 × 4 = 228
    assert e.value.code == "too_many_candidates"
    assert e.value.detail == {"candidates": 228, "max_candidates": 200}


def test_sort_by_defaults_to_first_target(cfg):
    assert _req(cfg, target={"sharpe": 0.1, "win_rate": 0.5}).sort_by == "sharpe"


# ---------------------------------------------------------------- 구간 분할 (P1-5.2)
def test_split_periods(cfg):
    explore, evaluate = split_periods(cfg)
    assert explore == {"start": "2020-09-01", "end": "2024-02-29"}
    assert evaluate == {"start": "2024-03-01", "end": "2026-09-18"}


# ---------------------------------------------------------------- 선별 (P1-5.3)
def test_target_met_bounds_and_nan():
    assert target_met({"win_rate": 0.55, "mdd": -0.2}, {"win_rate": 0.55, "mdd": -0.3})
    assert not target_met({"win_rate": 0.5499}, {"win_rate": 0.55})
    assert not target_met({"win_rate": np.nan}, {"win_rate": 0.0})
    assert not target_met({"mdd": -0.31}, {"mdd": -0.3})


def test_select_uses_all_candidates_as_fdr_family(cfg):
    # p=0.05 는 후보가 1개(m=1)면 0.10 이하라 통과지만, m=3 이면 BH 기준 0.10·1/3 을 넘어 탈락한다
    req = _req(cfg, target={"win_rate": 0.5})
    base = {"trades": 500, "mean_ret": 0.0, "median_ret": 0.0, "mean_excess": 0.0, "payoff_ratio": 1.0,
            "sharpe": 0.1, "mdd": -0.1}
    scores = pd.DataFrame([{"id": "c001", **base, "win_rate": 0.60, "p_value": 0.05},
                           {"id": "c002", **base, "win_rate": 0.70, "p_value": 0.20},
                           {"id": "c003", **base, "win_rate": 0.40, "p_value": 0.50}])
    out = select(scores, req, cfg).set_index("id")
    assert out["fdr_pass"].tolist() == [False, False, False]  # 0.05 > 0.10·1/3
    assert out["rank"].isna().all()
    scores["p_value"] = [0.01, 0.02, 0.5]
    out = select(scores, req, cfg).set_index("id")
    assert out["fdr_pass"].tolist() == [True, True, False]
    assert out.loc["c002", "rank"] == 1 and out.loc["c001", "rank"] == 2  # 승률 내림차순
    assert pd.isna(out.loc["c003", "rank"])  # 목표 미충족 + FDR 불통과


def test_select_requires_min_trades(cfg):
    req = _req(cfg, target={"win_rate": 0.5})
    scores = pd.DataFrame([{"id": "c001", "trades": 299, "win_rate": 0.9, "mean_ret": 0.1, "median_ret": 0.1,
                            "mean_excess": 0.1, "payoff_ratio": 2.0, "sharpe": 1.0, "mdd": -0.1, "p_value": 1e-9}])
    out = select(scores, req, cfg)
    assert not out.at[0, "enough_trades"] and pd.isna(out.at[0, "rank"])


# ---------------------------------------------------------------- 합성 시장 (정방향 일치·전체 흐름)
@pytest.fixture(scope="module")
def market(cfg):
    return make_market(cfg, n_tickers=60, seed=1)


def test_candidate_scores_match_forward_run(market, cfg):
    """후보 평가 값 = 같은 전략·기간을 execute() 로 돌린 값 (P1-11.1 정방향 일치)."""
    req = _req(cfg, axes={"patterns": ["breakout_20d", "rsi_rebound"], "combine": ["and", "or"],
                          "stop_loss_pct": [-5, None]})
    explore, _ = split_periods(cfg)
    cands = generate_candidates(req, explore)
    scores = evaluate_candidates(cands, market, cfg).set_index("id")
    for c in cands:
        res = execute(c, market, cfg)
        fwd, val = res["results"][c.name]["summary"], res["validation"].iloc[0]
        for k in ("trades", "win_rate", "mean_excess", "sharpe", "mdd"):
            assert scores.at[c.name, k] == pytest.approx(fwd[k], nan_ok=True), (c.name, k)
        assert scores.at[c.name, "p_value"] == pytest.approx(val["p_value"], nan_ok=True)


def test_run_search_end_to_end(market):
    cfg = load_config({"analysis": {"fdr_q": 1.0, "min_cell_trades": 50}, "search": {"finalists": 3}})
    req = SearchRequest.from_dict({"name": "e2e", "axes": {"patterns": ["breakout_20d", "rsi_rebound"],
                                                          "max_hold_days": [5, 20]},
                                   "target": {"win_rate": 0.0}}, cfg)
    out = run_search(req, market, cfg)
    ex, fin = out["explore"], out["finalists"]
    assert len(out["candidates"]) == len(ex) == 4 * 2  # (단일 2 + and·or 2) × 보유일 2
    assert out["split"]["explore"]["end"] == "2024-02-29"
    assert 0 < len(fin) <= 3
    ranked = ex.dropna(subset=["rank"]).sort_values("rank")
    assert fin["id"].tolist() == ranked["id"].head(3).tolist()
    assert fin["held"].dtype == bool and fin["random_percentile"].between(0, 1).all()
    for r in fin.itertuples():  # 평가 구간 값은 평가 기간으로 돌린 정방향 실행과 같다
        c = next(c for c in out["candidates"] if c.name == r.id)
        fwd = execute(with_period(c, out["split"]["evaluate"]), market, cfg)["results"][r.id]["summary"]
        assert r.trades == fwd["trades"] and r.win_rate == pytest.approx(fwd["win_rate"])
    again = run_search(req, market, cfg)
    pd.testing.assert_frame_equal(ex, again["explore"])
    pd.testing.assert_frame_equal(fin, again["finalists"])
