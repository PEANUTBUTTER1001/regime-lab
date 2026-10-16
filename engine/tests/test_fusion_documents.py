"""실제 문서 → 자료 특징: 미래 버전·연결 봉·범위 unknown·정상0건·중복 반례."""
import copy

import numpy as np
import pandas as pd
import pytest

from regime_lab.config import load_config
from regime_lab.fusion import build_doc_features
from test_forecast_model import _bars


def fixture_inputs(n=90):
    cfg = load_config({"fusion": {"windows": [1, 5], "markets": ["Y", "K"]},
                       "influence": {"min_train_days": 15, "fold_days": 10, "min_events": 2,
                                     "bootstrap": 8, "min_eval_samples": 2},
                       "forecast": {"split": {"train_end": "2026-02-20", "valid_end": "2026-03-06"},
                                    "horizon": {"1d": [2, 20]}}})
    bars = _bars(n_days=n, codes=("005930", "000660"), slots=(1,), start="2026-01-01")
    bars["available_at"] = bars["date"] + pd.Timedelta(hours=15, minutes=31)
    docs = []
    for i, day in enumerate(pd.bdate_range("2026-01-01", periods=n)):
        for code in ("005930", "000660"):
            at = (day + pd.Timedelta(hours=8)).tz_localize("Asia/Seoul").isoformat()
            docs.append({"doc_id": f"opendart:{code}{i}", "version": 1, "source": "opendart", "source_type": "disclosure",
                         "title": "실적 공시" if i % 2 else "배당 공시", "available_at": at, "first_seen_at": at,
                         "license_scope": "metadata_only", "tickers": [{"code": code, "method": "native", "confidence": 1.0}],
                         "ticker_status": "resolved", "status": "active"})
    coverage = [{"source": "opendart", "seq": i, "day": day.date().isoformat(), "corp_cls": market,
                 "state": "collected", "observed_at": day.tz_localize("Asia/Seoul").isoformat()}
                for i, day in enumerate(pd.date_range("2026-01-01", bars["date"].max())) for market in ("Y", "K")]
    return cfg, bars, docs, coverage


def test_counts_missing_zero_and_trade_windows():
    c, b, d, cov = fixture_inputs(15)
    f, e, a = build_doc_features(d, b, c, "observed", cov)
    assert f.loc[f.as_of.dt.day == 1, "docs_missing"].all()
    row = f[(f.code == "005930") & ~f.docs_missing].iloc[0]
    assert row.f_all_all_1 == 1 and row.f_all_all_5 == 5
    z, _, _ = build_doc_features([], b, c, "observed", cov)
    assert (z.loc[~z.docs_missing, "f_all_all_5"] == 0).all()
    unknown, _, _ = build_doc_features(d, b, c, "observed", [])
    assert unknown.docs_missing.all() and unknown.filter(like="f_").isna().all(axis=None)


def test_strict_open_late_observation_and_future_version_cut():
    c, b, docs, cov = fixture_inputs(20)
    d = copy.deepcopy(docs[0])
    d["available_at"] = d["first_seen_at"] = "2026-01-02T09:00:00+09:00"
    later = {**d, "version": 2, "title": "증자 공시", "available_at": "2026-01-12T08:00:00+09:00",
             "first_seen_at": "2026-01-12T08:00:00+09:00"}
    full, e, _ = build_doc_features([d, later], b, c, "observed", cov)
    assert e.iloc[0].linked_as_of == pd.Timestamp("2026-01-05 15:31")
    cut = pd.Timestamp("2026-01-09 15:31")
    old, _, _ = build_doc_features([d], b[b.available_at <= cut], c, "observed", cov)
    pd.testing.assert_frame_equal(full[full.as_of <= cut].reset_index(drop=True), old.reset_index(drop=True))
    late = {**d, "first_seen_at": "2026-01-07T10:00:00+09:00"}
    _, e, _ = build_doc_features([late], b, c, "observed", cov)
    assert e.iloc[0].linked_as_of == pd.Timestamp("2026-01-08 15:31")


def test_version_conflicts_and_unknown_market():
    c, b, docs, cov = fixture_inputs(15)
    with pytest.raises(ValueError, match="冲突|충돌"):
        build_doc_features([docs[0], {**docs[0], "title": "다른 제목"}], b, c, "observed", cov)
    f, _, _ = build_doc_features(docs, b, c, "observed", [r for r in cov if r["corp_cls"] == "Y"])
    assert f.docs_missing.all()


def test_observed_coverage_future_failure_does_not_change_past():
    c, b, docs, cov = fixture_inputs(20)
    full, _, _ = build_doc_features(docs, b, c, "observed", cov)
    changed = [{**r, "seq": 99999, "state": "gap", "observed_at": "2026-12-01T00:00:00+09:00"} for r in cov]
    after, _, _ = build_doc_features(docs, b, c, "observed", cov + changed)
    pd.testing.assert_frame_equal(full, after)


def test_historical_assumption_is_separate_from_real_observation():
    c, b, docs, cov = fixture_inputs(15)
    docs = [{**docs[12], "first_seen_at": "2026-10-16T10:00:00+09:00"}]
    observed, e, _ = build_doc_features(docs, b, c, "observed", cov)
    assumed, ae, _ = build_doc_features(docs, b, c, "historical_assumed", cov)
    assert e.empty and not ae.empty
    assert assumed.f_all_all_5.sum() > observed.f_all_all_5.sum()


def test_new_unresolved_version_does_not_keep_old_ticker_link():
    c, b, docs, cov = fixture_inputs(20)
    old = {**docs[12], "doc_id": "opendart:versioned"}
    new = {**old, "version": 2, "ticker_status": "none", "tickers": [],
           "available_at": "2026-01-12T08:00:00+09:00", "first_seen_at": "2026-01-12T08:00:00+09:00"}
    f, e, _ = build_doc_features([old, new], b, c, "observed", cov)
    assert (f.loc[f.as_of >= pd.Timestamp("2026-01-12"), "f_all_all_5"] == 0).all()
    assert len(e) == 1 and e.iloc[0]["until"] == pd.Timestamp("2026-01-12 08:00")
