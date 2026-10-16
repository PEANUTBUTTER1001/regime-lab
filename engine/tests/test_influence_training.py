"""P3-12 실제 학습·시간순 가중치: 최종 평가 개입·동일구간 누수·단위 환산 반례."""
import copy

import numpy as np
import pandas as pd
import pytest

from regime_lab.fusion import build_doc_features
from regime_lab.influence import _fit, predict, train_influence, weighted_features
from test_fusion_documents import fixture_inputs


def test_ridge_raw_units_and_imputation_are_fitted_only_on_train():
    x = pd.DataFrame({"price": [1., 2., 3., 4.], "docs": [0., 1., 0., 1.]})
    y = 2 * x.price + 4 * x.docs
    m = _fit(x, y, 0.000001)
    assert np.allclose(predict(m, x), y, atol=0.0001)
    assert np.allclose(m["coef"], [2, 4], atol=0.0001)
    assert np.isfinite(predict(m, pd.DataFrame({"price": [np.nan], "docs": [1.]}))).all()


def test_training_and_weighted_features_ignore_test_labels():
    c, b, docs, cov = fixture_inputs()
    f, e, _ = build_doc_features(docs, b, c, "observed", cov)
    trained, weights = train_influence(b, f, e, c)
    assert not weights.empty and set(weights.h) == {1, 2}
    w, used = weighted_features(f, trained, c)
    assert used and w.f_influence_missing_h1.iloc[0] == 1
    for s in trained["snapshots"]:
        assert pd.Timestamp(s["knowledge_cutoff"]) <= pd.Timestamp(s["apply_from"])
    mutated = b.copy()
    mutated.loc[mutated.date > pd.Timestamp(c["forecast"]["split"]["valid_end"]), "close"] *= 2
    other, ow = train_influence(mutated, f, e, c)
    # 평가 수치가 바뀌어도 학습된 계수·snapshot·입력은 동일.
    assert trained["snapshots"] == other["snapshots"]
    ww, uu = weighted_features(f, other, c)
    pd.testing.assert_frame_equal(w, ww)
    assert used == uu
    assert np.allclose(weights.weight, ow.weight)


def test_future_snapshots_and_in_sample_apply_are_rejected():
    c, b, docs, cov = fixture_inputs()
    f, e, _ = build_doc_features(docs, b, c, "observed", cov)
    trained, _ = train_influence(b, f, e, c)
    invalid = copy.deepcopy(trained)
    invalid["snapshots"][0]["knowledge_cutoff"] = "2099-01-01"
    with pytest.raises(ValueError, match="cutoff"):
        weighted_features(f, invalid, c)
    future = copy.deepcopy(trained)
    future["snapshots"] += [{**trained["snapshots"][-1], "apply_from": "2099-01-01", "apply_until": None}]
    a, _ = weighted_features(f, trained, c)
    z, _ = weighted_features(f, future, c)
    pd.testing.assert_frame_equal(a, z)


def test_no_coverage_or_no_support_is_not_a_model():
    c, b, docs, _ = fixture_inputs()
    f, e, _ = build_doc_features(docs, b, c, "observed", [])
    with pytest.raises(ValueError, match="부족"):
        train_influence(b, f, e, c)


def test_full_cut_invariance_for_weights_and_input_versions():
    c, b, docs, cov = fixture_inputs()
    f, e, _ = build_doc_features(docs, b, c, "observed", cov)
    trained, _ = train_influence(b, f, e, c)
    at = pd.Timestamp("2026-03-20 15:31")
    cut_b = b[b.available_at <= at]
    cut_docs = [d for d in docs if pd.Timestamp(d["available_at"]).tz_convert("Asia/Seoul").tz_localize(None) <= at]
    cf, ce, _ = build_doc_features(cut_docs, cut_b, c, "observed", cov)
    cut_model, _ = train_influence(cut_b, cf, ce, c)
    a, au = weighted_features(f, trained, c)
    z, zu = weighted_features(cf, cut_model, c)
    pd.testing.assert_frame_equal(a[a.as_of <= at].reset_index(drop=True), z.reset_index(drop=True))
    assert au == zu


def test_evaluation_display_flags_do_not_select_input_coefficients():
    c, b, docs, cov = fixture_inputs()
    f, e, _ = build_doc_features(docs, b, c, "observed", cov)
    trained, weights = train_influence(b, f, e, c)
    a, _ = weighted_features(f, trained, c)
    altered = copy.deepcopy(trained)
    altered["metrics"] = {"1": {"rmse": 99999}, "2": {"rmse": -99999}}
    changed, _ = weighted_features(f, altered, c)
    pd.testing.assert_frame_equal(a, changed)


def test_bootstrap_sufficient_statistics_match_independent_sklearn_fit():
    from sklearn.linear_model import Ridge
    from regime_lab.influence import _confidence

    c, _, _, _ = fixture_inputs()
    x = pd.DataFrame({"price": np.arange(12, dtype=float), "docs": [0, 1, 2] * 4})
    y = x.price * .01 + x.docs * .02
    target = pd.DataFrame({"y": y, "as_of": np.repeat(pd.date_range("2026-01-01", periods=6), 2)})
    mask = pd.Series(True, index=x.index)
    model = _fit(x, y, 1.)
    lohi = _confidence({"model": model}, x, target, mask, c)
    a = (x.to_numpy() - model["mean"]) / model["scale"]
    rng = np.random.default_rng(c["influence"]["seed"])
    coefs = []
    for _ in range(c["influence"]["bootstrap"]):
        day_counts = np.bincount(rng.integers(0, 6, 6), minlength=6)
        fit = Ridge(alpha=1.).fit(a, y, sample_weight=np.repeat(day_counts, 2))
        coefs.append(fit.coef_ / np.asarray(model["scale"]))
    assert np.allclose(lohi, np.percentile(coefs, [2.5, 97.5], axis=0), atol=1e-12)
