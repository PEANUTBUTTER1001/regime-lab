"""P4-3 외부 자료 결합 예측 (결정 P4-3a) — 합성 특징으로 계약·비교 판정·절단 불변·재현 (P4-2 병합 전)."""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from regime_lab.forecast.evaluate import compare_variants, holm_verdicts
from regime_lab.forecast.features import attach_doc_features
from regime_lab.forecast_runs import FORECAST_RUN_VERSION, build_bars, run_forecast
from test_forecast_runs import NOW, MemoryStore, _cfg, _raw

INFO = {"fingerprint": "docs-synth-1", "as_of_mode": "historical_assumed"}


def _fcfg(cfg, **compare):
    c = _cfg(cfg)
    fc = {**c["forecast"]["fusion_compare"], "min_samples": 50, "min_days": 3, "bootstrap": 200, **compare}
    return {**c, "forecast": {**c["forecast"], "fusion_compare": fc}}


def _docs(store, c, seed=1, cut=None):
    """P4-2 와 같은 모양의 합성 특징: 봉마다 f_n(자료 수)·f_neg(부정 유형 비율), 일부 봉은 자료 없음."""
    bars, _ = build_bars(store, c, store.common_codes(), "1h", batch=10)
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({"code": bars["code"], "as_of": bars["available_at"],
                      "f_n": rng.poisson(1.0, len(bars)).astype(float), "f_neg": rng.random(len(bars)),
                      "docs_missing": rng.random(len(bars)) < 0.2})
    d.loc[d["docs_missing"], ["f_n", "f_neg"]] = np.nan
    return d if cut is None else d[d["as_of"] <= cut]


def test_attach_doc_features_keeps_missing_apart_from_zero():
    feats = pd.DataFrame({"code": ["A", "A", "A"], "as_of": pd.to_datetime(["2026-09-01 10:00", "2026-09-01 11:00",
                                                                            "2026-09-01 12:00"])}, index=[5, 6, 7])
    docs = pd.DataFrame({"code": ["A", "A"], "as_of": feats["as_of"][:2].to_numpy(), "f_n": [0.0, 5.0],
                         "docs_missing": [False, True]})
    out, cols = attach_doc_features(feats, docs)
    assert cols == ["f_n", "docs_missing"] and list(out.index) == [5, 6, 7]
    assert out["f_n"].iloc[0] == 0.0                       # 관측된 0건은 0
    assert out["f_n"].iloc[1:].isna().all()                # 자료 없음·결합 행 없음은 0 이 아니라 결측
    assert list(out["docs_missing"]) == [0.0, 1.0, 1.0]


@pytest.mark.parametrize("bad", [
    lambda d: d.drop(columns="docs_missing"),               # 계약 열 없음
    lambda d: d.drop(columns="f_n"),                        # f_* 없음
    lambda d: pd.concat([d, d.iloc[:1]]),                   # (code, as_of) 중복
    lambda d: d.assign(f_n=d["f_n"].astype(str)),           # 숫자 아닌 특징
])
def test_attach_doc_features_rejects_contract_breaks(bad):
    feats = pd.DataFrame({"code": ["A"], "as_of": [pd.Timestamp("2026-09-01 10:00")]})
    docs = pd.DataFrame({"code": ["A"], "as_of": [pd.Timestamp("2026-09-01 10:00")], "f_n": [1.0],
                         "docs_missing": [False]})
    with pytest.raises(ValueError):
        attach_doc_features(feats, bad(docs))


def _variants(n_days=40, per_day=60, seed=0, informative=True):
    """y = 신호 + 잡음. price 는 신호를 모르는 보정된 분위, fused 는 신호를 아는(informative) 보정된 분위."""
    rng = np.random.default_rng(seed)
    days = np.repeat(pd.bdate_range("2026-06-01", periods=n_days), per_day)
    sig, noise = rng.normal(0, 0.008, len(days)), rng.normal(0, 0.006, len(days))
    t = pd.DataFrame({"as_of": days + pd.Timedelta(hours=10), "y": sig + noise})
    z = norm.ppf([0.1, 0.5, 0.9])

    def pred(center, sd):
        return pd.DataFrame({f"q{q:g}": center + k * sd for q, k in zip([0.1, 0.5, 0.9], z)} | {"p_up": 0.5},
                            index=t.index)
    price = pred(0.0, np.hypot(0.008, 0.006))
    fused = pred(sig, 0.006) if informative else price.copy()
    return t, price, fused


def test_compare_detects_improvement_and_no_difference(cfg):
    c = _fcfg(cfg, min_samples=100, min_days=10)
    t, p, f = _variants()
    better = compare_variants(t, p, f, c)
    assert better["pinball_fused"] < better["pinball_price"] and better["coverage_ok"] and better["p_value"] < 0.01
    t, p, f = _variants(informative=False)
    same = compare_variants(t, p, f, c)
    assert same["pinball_diff"] == 0 and same["p_value"] == 1.0
    v = holm_verdicts({"1": better, "2": same}, c["forecast"]["fusion_compare"]["alpha"])
    assert v["1"]["verdict"] == "improved" and v["2"]["verdict"] == "not_improved"   # 차이 없음 = 검증 부족


def test_compare_needs_minimum_relative_gain(cfg):
    """표본이 커서 아주 작은 개선도 유의해지는 경우: 상대 개선 하한(min_rel_gain) 미만이면 not_improved."""
    c = _fcfg(cfg, min_samples=100, min_days=10, bootstrap=1000)
    t, p, _ = _variants(n_days=40, per_day=5000)
    y, weak = t["y"], p.copy()
    for q in ("q0.1", "q0.5", "q0.9"):
        weak[q] = p[q] + 0.003 * (y - y.mean())                          # 실현값 쪽으로 아주 조금만 옮긴 예측
    r = compare_variants(t, p, weak, c)
    assert r["p_value"] < 0.01 and 0 < r["rel_gain"] < 0.005 and not r["gain_ok"]
    lo, hi = r["pinball_diff_ci95"]
    assert lo <= r["pinball_diff"] <= hi and hi < 0                      # 크기·구간도 함께 저장
    assert holm_verdicts({"1": r}, 0.05)["1"]["verdict"] == "not_improved"
    strong = compare_variants(*_variants(n_days=40, per_day=200), c)
    assert strong["rel_gain"] >= 0.005 and strong["gain_ok"]


def test_compare_insufficient_when_intersection_is_small(cfg):
    c = _fcfg(cfg, min_samples=100, min_days=10)
    t, p, f = _variants()
    empty = compare_variants(t, p, f.assign(p_up=np.nan), c)       # 교집합 0
    few_days = compare_variants(*_variants(n_days=5), c)
    assert empty["verdict"] == "insufficient" and empty["n"] == 0
    assert few_days["verdict"] == "insufficient"
    assert holm_verdicts({"1": empty}, 0.05)["1"]["verdict"] == "insufficient"


def test_fused_run_records_variant_and_vs_price(cfg):
    store, c = MemoryStore(_raw()), _fcfg(cfg)
    run_id, meta = run_forecast(store, c, "1h", NOW, doc_features=_docs(store, c), docs_info=INFO)
    m, pred, scores = store.runs[run_id]
    assert m["format_version"] == FORECAST_RUN_VERSION == 5 and m["bar_source"] == "minutes"
    assert m["variant"] == "fused" and m["doc_features"] == ["f_n", "f_neg", "docs_missing"]
    assert m["docs_fingerprint"] == "docs-synth-1" and m["as_of_mode"] == "historical_assumed"
    assert set(m["docs_missing_rate"]) <= {"train", "valid", "test"} and 0.1 < m["docs_missing_rate"]["train"] < 0.3
    assert (pred["split"] == "test").all()                              # 저장 범위는 그대로 평가 구간만
    for h, s in scores.items():
        assert s["vs_price"]["verdict"] in {"improved", "not_improved", "insufficient"}


def test_price_run_and_docs_info_rules(cfg):
    store, c = MemoryStore(_raw()), _fcfg(cfg)
    _, meta = run_forecast(store, c, "1h", NOW)
    assert meta["variant"] == "price" and meta["docs_fingerprint"] is None and meta["doc_features"] == []
    assert all("vs_price" not in s for s in next(iter(store.runs.values()))[2].values())
    docs = _docs(store, c)
    with pytest.raises(ValueError):
        run_forecast(store, c, "1h", NOW, doc_features=docs)            # 지문·모드 없이 결합 불가
    with pytest.raises(ValueError):
        run_forecast(store, c, "1h", NOW, doc_features=docs, docs_info={**INFO, "as_of_mode": "latest"})


def test_same_input_same_fingerprint(cfg):
    store, c = MemoryStore(_raw()), _fcfg(cfg)
    docs = _docs(store, c)
    _, a = run_forecast(store, c, "1h", NOW, doc_features=docs, docs_info=INFO)
    _, b = run_forecast(store, c, "1h", NOW.replace(minute=5), doc_features=docs, docs_info=INFO)
    _, d = run_forecast(store, c, "1h", NOW, doc_features=docs, docs_info={**INFO, "fingerprint": "docs-synth-2"})
    assert a["run_fingerprint"] == b["run_fingerprint"] != d["run_fingerprint"]
    pa, pb = (store.runs[x["run_id"]][1] for x in (a, b))
    pd.testing.assert_frame_equal(pa, pb)                               # 같은 입력·시드 → 같은 예측


def test_fused_cut_invariance(cfg):
    """T 뒤의 1분봉·자료를 지워도 T 의 결합 특징·예측이 같다 (NFR-4, FR-F4)."""
    raw, c = _raw(), _fcfg(cfg)
    full = MemoryStore(raw)
    run_forecast(full, c, "1h", NOW, doc_features=_docs(full, c), docs_info=INFO)
    pf = next(iter(full.runs.values()))[1]
    t = pf.loc[pf["split"] == "test", "as_of"].sort_values().iloc[len(pf) // 50]
    cut = MemoryStore(raw[raw["dt"] < t])
    run_forecast(cut, c, "1h", NOW, doc_features=_docs(full, c, cut=t), docs_info=INFO)
    pc = next(iter(cut.runs.values()))[1]
    cols = ["code", "as_of", "h", "q0.1", "q0.5", "q0.9", "p_up", "direction"]
    a = pf.loc[pf["as_of"] == t, cols].reset_index(drop=True)
    b = pc.loc[pc["as_of"] == t, cols].reset_index(drop=True)
    assert len(a) > 0
    pd.testing.assert_frame_equal(a, b)
