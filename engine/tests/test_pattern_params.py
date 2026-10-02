"""P1-4 조합별 패턴 수치: 검증·기록·신호·절단 불변·기존 결과 불변·탐색 축·조합 저장."""

import numpy as np
import pandas as pd
import pytest
from synth import make_frame, make_market

from regime_lab.config import Paths
from regime_lab.indicators import compute_indicators
from regime_lab.patterns import compute_signals, make_pattern
from regime_lab.presets import PresetStore, presets_dir
from regime_lab.runs import Strategy, StrategyError, execute
from regime_lab.search import SearchError, SearchRequest, count_candidates, generate_candidates

CUTS = ["2021-03-15", "2022-06-30", "2024-02-29"]


def _s(**kw):
    return Strategy.from_dict({"name": "x", "patterns": ["rsi_rebound", "breakout_vol"], **kw})


# ---------------------------------------------------------------- 검증·기록
@pytest.mark.parametrize("pp,key", [
    ({"ma_cross_5_20": {"fast": 3}}, "pattern_params.ma_cross_5_20"),       # 고르지 않은 패턴
    ({"rsi_rebound": {"window": 10}}, "pattern_params.rsi_rebound.window"),  # 준비 프레임에 박힌 수치는 조정 불가
    ({"rsi_rebound": {"threshold": 60}}, "pattern_params.rsi_rebound.threshold"),
    ({"rsi_rebound": {"threshold": True}}, "pattern_params.rsi_rebound.threshold"),
    ({"rsi_rebound": {"threshold": "25"}}, "pattern_params.rsi_rebound.threshold"),
    ({"breakout_vol": {"volume_mult": 0.5}}, "pattern_params.breakout_vol.volume_mult"),
    ({"rsi_rebound": {}}, "pattern_params.rsi_rebound"),
    ([1], "pattern_params"),
])
def test_validation(cfg, pp, key):
    with pytest.raises(StrategyError) as e:
        _s(pattern_params=pp).validate(cfg)
    assert key in e.value.errors, e.value.errors


def test_effective_unchanged_without_params(cfg):
    eff = _s().effective(cfg)
    assert "pattern_params" not in eff  # 기존 전략의 메타데이터·결과 형식 그대로
    assert "pattern_params" not in _s().to_dict()


def test_effective_records_adjustable_values_and_round_trips(cfg):
    s = _s(pattern_params={"rsi_rebound": {"threshold": 25}})
    s.validate(cfg)
    eff = s.effective(cfg)
    assert eff["pattern_params"] == {"rsi_rebound": {"threshold": 25}}
    s2 = Strategy.from_dict(eff)  # 기록된 값을 그대로 다시 넣어도 통과
    s2.validate(cfg)
    assert s2.effective(cfg) == eff


def test_pattern_uses_params_over_defaults(cfg):
    assert make_pattern("rsi_rebound", cfg).params["threshold"] == 30
    p = make_pattern("rsi_rebound", cfg, {"threshold": 25})
    assert p.params == {"window": 14, "threshold": 25}
    assert cfg["patterns"]["rsi_rebound"]["threshold"] == 30  # 설정은 바뀌지 않는다
    with pytest.raises(ValueError):
        make_pattern("nope", cfg)


# ---------------------------------------------------------------- 신호
def test_threshold_changes_signals_as_defined(cfg):
    # 하락 뒤 반등하는 가격: 문턱마다 RSI 상향 돌파 정의 그대로 신호가 나야 한다
    rng = np.random.default_rng(3)
    closes = np.r_[100 - np.cumsum(np.abs(rng.normal(0.8, 0.2, 40))), np.full(1, 0)]
    closes[-1] = closes[-2] * 1.02
    f = make_frame(closes)
    base = compute_signals(f, ["rsi_rebound"], "or", cfg)
    same = compute_signals(f, ["rsi_rebound"], "or", cfg, {"rsi_rebound": {"threshold": 30}})
    pd.testing.assert_series_equal(base, same)
    r = compute_indicators(f, cfg)["rsi14"]
    for th in (20, 25, 35, 40):
        s = compute_signals(f, ["rsi_rebound"], "or", cfg, {"rsi_rebound": {"threshold": th}})
        expect = (r.shift(1) < th) & (r >= th) & ~f["halted"]
        assert s.tolist() == expect.fillna(False).tolist(), th


@pytest.mark.parametrize("cut", CUTS)
@pytest.mark.parametrize("pp", [{"rsi_rebound": {"threshold": 25}}, {"breakout_vol": {"volume_mult": 1.5}}])
def test_truncation_invariance(cfg, cut, pp):
    """P1-4.3: 바꾼 수치로도 t일 신호는 t일까지의 값만 쓴다 (입력 끝을 잘라도 앞 구간 신호 불변)."""
    m = make_market(cfg, n_tickers=12, seed=4)
    full = m.frame
    short = full[full["date"] <= pd.Timestamp(cut)].reset_index(drop=True)
    names = list(pp)
    a = compute_signals(full, names, "or", cfg, pp)
    b = compute_signals(short, names, "or", cfg, pp)
    a = a[(full["date"] <= pd.Timestamp(cut)).to_numpy()].reset_index(drop=True)
    assert a.tolist() == b.tolist()


# ---------------------------------------------------------------- 실행 결과
@pytest.fixture(scope="module")
def market(cfg):
    return make_market(cfg, n_tickers=40, seed=6)


def test_default_values_give_identical_results(market, cfg):
    """기본값을 명시해도 수치를 안 준 것과 결과가 같다 (기존 성과 불변, P1-11.2)."""
    a = execute(_s(), market, cfg)["results"]["x"]["summary"]
    b = execute(_s(pattern_params={"rsi_rebound": {"threshold": 30}, "breakout_vol": {"volume_mult": 2.0}}),
                market, cfg)["results"]["x"]["summary"]
    for k in ("trades", "win_rate", "mean_ret", "mdd"):
        assert a[k] == pytest.approx(b[k], nan_ok=True), k


def test_changed_values_change_results(market, cfg):
    a = execute(_s(), market, cfg)["results"]["x"]["summary"]["trades"]
    b = execute(_s(pattern_params={"breakout_vol": {"volume_mult": 1.0}}), market, cfg)["results"]["x"]["summary"]["trades"]
    assert b > a  # 거래량 배수를 낮추면 신호가 늘어난다


# ---------------------------------------------------------------- 탐색 축
def _req(cfg, **axes):
    return SearchRequest.from_dict({"name": "pp", "target_win_rate": 0.5,
                                    "axes": {"patterns": ["rsi_rebound", "breakout_20d"], **axes}}, cfg)


def test_search_without_param_axis_keeps_numbering(cfg):
    req = _req(cfg)
    assert req.axes["pattern_params"] == {}
    cands = generate_candidates(req)
    assert len(cands) == count_candidates(req) == 4 and all(c.pattern_params is None for c in cands)


def test_search_param_axis_multiplies_only_combos_with_pattern(cfg):
    req = _req(cfg, pattern_params={"rsi_rebound": {"threshold": [35, 25]}})
    cands = generate_candidates(req)
    # breakout_20d 단독 1 + rsi 단독 2 + (and·or) × 2 = 7
    assert len(cands) == count_candidates(req) == 7
    rsi = [c for c in cands if "rsi_rebound" in c.patterns]
    assert all(c.pattern_params["rsi_rebound"]["threshold"] in (25, 35) for c in rsi)
    assert [c.pattern_params["rsi_rebound"]["threshold"] for c in rsi[:2]] == [25, 35]  # 허용 값 순서
    assert all(c.pattern_params is None for c in cands if "rsi_rebound" not in c.patterns)


@pytest.mark.parametrize("pp,key", [
    ({"breakout_vol": {"volume_mult": [2.0]}}, "axes.pattern_params.breakout_vol"),   # 고르지 않은 패턴
    ({"rsi_rebound": {"window": [10]}}, "axes.pattern_params.rsi_rebound.window"),
    ({"rsi_rebound": {"threshold": [27]}}, "axes.pattern_params.rsi_rebound.threshold"),
    ({"rsi_rebound": {"threshold": []}}, "axes.pattern_params.rsi_rebound.threshold"),
    ({"rsi_rebound": {"threshold": [25, 25]}}, "axes.pattern_params.rsi_rebound.threshold"),
])
def test_search_param_axis_validation(cfg, pp, key):
    with pytest.raises(SearchError) as e:
        _req(cfg, pattern_params=pp)
    assert key in e.value.errors, e.value.errors


def test_preset_keeps_pattern_params(cfg, tmp_path):
    paths = Paths(tmp_path / "s", tmp_path / "d", tmp_path / "c", tmp_path / "runs")
    store = PresetStore(presets_dir(paths), cfg)
    p = store.create("수치 조합", {"name": "x", "patterns": ["rsi_rebound"], "pattern_params": {"rsi_rebound": {"threshold": 25}}})
    assert p["strategy"]["pattern_params"] == {"rsi_rebound": {"threshold": 25}}
    Strategy.from_dict(store.get(p["id"])["strategy"]).validate(cfg)
