"""P1-11 역방향 백테스트 수용 테스트 (plan/01 완료 기준).

P1-11.1 재현성·정방향 일치: 같은 입력이면 같은 탐색 기록, 후보·저장한 조합을 정방향으로 다시 돌리면 같은 값.
P1-11.2 기존 성과 불변: 역방향 탐색·조합 저장·패턴 수치 기능이 기존 정방향 실행 결과를 바꾸지 않는다.

합성 데이터 테스트는 항상 돈다. 원본 데이터(sample30)가 있으면 같은 검사를 실데이터로도 한다(data 마커).
"""

import json

import pytest
from synth import make_market

import regime_lab.search as search
from regime_lab.config import Paths
from regime_lab.patterns import CORE_PATTERNS
from regime_lab.presets import PresetStore, presets_dir
from regime_lab.runs import Strategy, execute
from regime_lab.search import SearchRequest, run_and_save_search, run_search, split_periods, truncate, with_period

KEYS = ("trades", "win_rate", "mean_ret", "mean_excess", "sharpe", "mdd")


def _same(a: dict, b: dict):
    for k in KEYS:
        assert a[k] == pytest.approx(b[k], nan_ok=True, rel=0, abs=1e-12), k


def _request(cfg, min_trades=30):
    return SearchRequest.from_dict({
        "name": "accept", "target_win_rate": 0.40, "min_trades": min_trades,
        "axes": {"patterns": ["breakout_20d", "rsi_rebound", "bb_lower_recover"], "max_hold_days": [10, 20],
                 "pattern_params": {"rsi_rebound": {"threshold": [25, 30]}}},
    }, cfg)


def _check_forward_equality(out, prep, cfg):
    """모든 처리 후보: 탐색 값 = 분할일 기준 정방향, 평가 값 = 평가 기간 정방향."""
    explore_p, evaluate_p = out["split"]["explore"], out["split"]["evaluate"]
    cut = truncate(prep, explore_p["end"])
    by_id = {c.name: c for c in out["candidates"]}
    for r in out["table"].to_dict(orient="records"):
        c = by_id[r["id"]]
        fwd = execute(c, cut, cfg)["results"][c.name]["summary"]
        _same({k: r[f"explore_{k}"] for k in KEYS}, fwd)
        if r["evaluate_trades"] == r["evaluate_trades"]:  # 평가한 후보 (NaN 아님)
            fwd = execute(with_period(c, evaluate_p), prep, cfg)["results"][c.name]["summary"]
            _same({k: r[f"evaluate_{k}"] for k in KEYS}, fwd)


# ================================================================ 합성 데이터
@pytest.fixture(scope="module")
def market(cfg):
    return make_market(cfg, n_tickers=50, seed=11)


def test_p1_11_1_search_reproducible_and_forward_equal(market, cfg):
    req = _request(cfg)
    a, b = run_search(req, market, cfg), run_search(req, market, cfg)
    assert a["table"].equals(b["table"])
    # 단일 4(볼린저·돌파·RSI 문턱 2) + 두 개 10(2 + 4 + 4) + 세 개 4(결합 2 × 문턱 2) = 18, × 보유일 2
    assert len(a["candidates"]) == 2 * (4 + 10 + 4)
    _check_forward_equality(a, market, cfg)


def test_p1_11_1_saved_preset_reruns_like_candidate(market, cfg, tmp_path, monkeypatch):
    """탐색 → 기록 → 후보 저장 → 저장한 조합을 그대로 정방향 실행 = 탐색 기록의 값."""
    monkeypatch.setattr(search, "input_file_hashes", lambda store: {"prices.parquet": "synthetic"})
    paths = Paths(tmp_path / "store", tmp_path / "d.sql", tmp_path / "cache", tmp_path / "runs")
    d = run_and_save_search(_request(cfg), market, cfg, paths, search_id="acc")
    rows = json.loads((d / "search.json").read_text(encoding="utf-8"))["rows"]
    row = next(r for r in rows if r["status"] in ("both", "explore_only"))
    store = PresetStore(presets_dir(paths), cfg)
    p = store.create_from_search(paths.runs / "searches", "acc", row["id"], "수용 테스트")
    st = Strategy.from_dict(PresetStore(presets_dir(paths), cfg).get(p["id"])["strategy"])  # 새로 읽어 와서 실행
    explore_p, evaluate_p = split_periods(cfg)
    got = execute(with_period(st, explore_p), truncate(market, explore_p["end"]), cfg)["results"][st.name]["summary"]
    _same({k: row["explore"][k] for k in KEYS}, got)
    got = execute(with_period(st, evaluate_p), market, cfg)["results"][st.name]["summary"]
    _same({k: row["evaluate"][k] for k in KEYS}, got)


def test_p1_11_2_existing_results_unchanged(market, cfg):
    """핵심 5종 정방향 결과: 패턴 수치를 기본값으로 명시해도, 탐색을 먼저 돌려도 같다."""
    before = {n: execute(Strategy.from_dict({"name": n, "patterns": [n]}), market, cfg)["results"][n]["summary"]
              for n in CORE_PATTERNS}
    run_search(_request(cfg), market, cfg)  # 탐색이 준비 프레임·설정을 바꾸지 않는다
    for n in CORE_PATTERNS:
        defaults = {k: cfg["patterns"][n][k] for k in cfg["pattern_limits"].get(n, {})}
        s = Strategy.from_dict({"name": n, "patterns": [n], **({"pattern_params": {n: defaults}} if defaults else {})})
        _same(before[n], execute(s, market, cfg)["results"][n]["summary"])
        plain = Strategy.from_dict({"name": n, "patterns": [n]})
        assert "pattern_params" not in plain.effective(cfg)  # 결과 형식도 그대로


# ================================================================ 실데이터 (sample30, 원본이 있을 때만)
@pytest.mark.data
def test_p1_11_real_sample(sample_prepared, cfg):
    req = _request(cfg, min_trades=30)
    a, b = run_search(req, sample_prepared, cfg), run_search(req, sample_prepared, cfg)
    assert a["table"].equals(b["table"])
    _check_forward_equality(a, sample_prepared, cfg)
    for n in CORE_PATTERNS:
        plain = execute(Strategy.from_dict({"name": n, "patterns": [n]}), sample_prepared, cfg)["results"][n]["summary"]
        defaults = {k: cfg["patterns"][n][k] for k in cfg["pattern_limits"].get(n, {})}
        if defaults:
            s = Strategy.from_dict({"name": n, "patterns": [n], "pattern_params": {n: defaults}})
            _same(plain, execute(s, sample_prepared, cfg)["results"][n]["summary"])
