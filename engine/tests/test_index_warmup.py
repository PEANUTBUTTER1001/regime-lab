"""X5 과거 지수 워밍업 — 시장 국면 산출 시작일 앞당김, 절단 불변."""

import numpy as np
import pandas as pd
import pytest

from regime_lab.data.warmup import attach_index_warmup, index_warmup_file, load_index_warmup
from regime_lab.regime import first_computable_date, market_regime

STORE_START = pd.Timestamp("2020-09-01")


def _index(dates, market="KOSPI", seed=0):
    rng = np.random.default_rng(seed)
    close = 2000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(dates))))
    return pd.DataFrame({"date": dates, "market": market, "open": close, "high": close * 1.01,
                         "low": close * 0.99, "close": close})


def _store_and_warm():
    store_dates = pd.bdate_range(STORE_START, periods=300)
    warm_dates = pd.bdate_range("2019-08-01", STORE_START - pd.Timedelta(days=1))
    full = _index(warm_dates.append(store_dates))
    store = full[full["date"] >= STORE_START].reset_index(drop=True)
    warm = full[full["date"] < STORE_START].reset_index(drop=True)
    # 겹침 날짜: 다른 값으로 넣어 store 값이 남는지 본다
    overlap = store.head(3).assign(close=lambda d: d["close"] * 2)
    return store, pd.concat([warm, overlap], ignore_index=True), full


def test_attach_prepends_only_before_store_start(cfg):
    store, warm, full = _store_and_warm()
    out = attach_index_warmup(store, warm, cfg)
    assert list(out.columns) == list(store.columns)
    assert not out.duplicated(["market", "date"]).any()
    assert out["date"].is_monotonic_increasing
    assert out["date"].min() >= pd.Timestamp(cfg["data"]["warmup_source_start"])
    after = out[out["date"] >= STORE_START].reset_index(drop=True)
    pd.testing.assert_frame_equal(after, store)  # 겹치는 날짜는 store 값
    assert len(out) == len(full[full["date"] >= pd.Timestamp(cfg["data"]["warmup_source_start"])])


def test_attach_without_warmup_is_noop(cfg):
    store, _, _ = _store_and_warm()
    assert attach_index_warmup(store, None, cfg) is store
    assert attach_index_warmup(store, store.iloc[:0], cfg) is store


def test_market_regime_starts_at_backtest_start_with_warmup(cfg):
    store, warm, _ = _store_and_warm()
    without = market_regime(store, cfg)
    with_w = market_regime(attach_index_warmup(store, warm, cfg), cfg)
    need = cfg["regime"]["ma_window"] + cfg["regime"]["slope_window"]  # 200일선 + 20일 기울기
    assert without["market_regime"].notna().idxmax() == need - 1  # store 만으로는 220번째 날부터
    s = with_w[with_w["date"] >= STORE_START]
    assert s["market_regime"].notna().all()  # 워밍업이 있으면 store 첫날부터
    assert with_w.loc[with_w["date"] < pd.Timestamp(cfg["regime"]["market_regime_start"]), "market_regime"].isna().all()
    assert first_computable_date(attach_index_warmup(store, warm, cfg), cfg).iloc[0] < STORE_START


def test_without_warmup_file_same_as_old_start(cfg):
    """과거 지수 파일이 없으면 market_regime_start 를 앞당겨도 결과가 예전 설정(계산 가능한 첫날)과 같다.

    2020-09-01~ 계산 가능일 전까지는 200일선이 부족해 null 로 남는다 (seongmin-claude #25 리뷰).
    """
    store, _, _ = _store_and_warm()
    first = first_computable_date(store, cfg).iloc[0]
    old_cfg = {**cfg, "regime": {**cfg["regime"], "market_regime_start": str(first.date())}}
    new = market_regime(attach_index_warmup(store, None, cfg), cfg)
    old = market_regime(store, old_cfg)
    pd.testing.assert_frame_equal(new, old)
    assert new.loc[new["date"] < first, "market_regime"].isna().all()


def test_market_regime_truncation_invariant_with_warmup(cfg):
    """입력 끝을 잘라도 앞 구간 시장 국면이 같다 (FR-D5, 미래참조 없음)."""
    store, warm, _ = _store_and_warm()
    full = market_regime(attach_index_warmup(store, warm, cfg), cfg)
    for cut in ("2020-10-15", "2021-03-31"):
        part = market_regime(attach_index_warmup(store[store["date"] <= cut], warm, cfg), cfg)
        a = full.set_index(["market", "date"]).loc[part.set_index(["market", "date"]).index, "market_regime"]
        b = part.set_index(["market", "date"])["market_regime"]
        assert (a.astype(object).fillna("NA") == b.astype(object).fillna("NA")).all(), cut


@pytest.mark.data
def test_real_index_warmup_market_regime_from_backtest_start(store, paths, cfg):
    """cache/warmup/index_warmup.parquet 이 있으면 KOSPI·KOSDAQ 시장 국면이 2020-09-01 부터 나온다."""
    from regime_lab.data.loader import load_index

    warm = load_index_warmup(paths.cache)
    if warm is None:
        pytest.skip(f"{index_warmup_file(paths.cache)} 없음 — scripts/fetch_index_history.py 실행 필요")
    idx = attach_index_warmup(load_index(store), warm, cfg)
    mr = market_regime(idx, cfg)
    first = mr.dropna(subset=["market_regime"]).groupby("market")["date"].min()
    assert (first == STORE_START).all()
    assert mr.loc[mr["date"] >= STORE_START, "market_regime"].notna().all()


@pytest.mark.data
def test_real_without_warmup_file_same_as_2021_07_21(store, cfg):
    """실데이터: 과거 지수 파일 없이 2020-09-01 설정으로 돌려도 예전 기본값(2021-07-21) 결과와 같다."""
    from regime_lab.data.loader import load_index

    idx = load_index(store)
    old_cfg = {**cfg, "regime": {**cfg["regime"], "market_regime_start": "2021-07-21"}}
    new = market_regime(attach_index_warmup(idx, None, cfg), cfg)
    pd.testing.assert_frame_equal(new, market_regime(idx, old_cfg))
    assert new.loc[new["date"] < pd.Timestamp("2021-07-21"), "market_regime"].isna().all()


@pytest.mark.data
@pytest.mark.parametrize("cut",["2020-10-15", "2021-03-15", "2022-06-30", "2024-02-29"])
def test_real_index_warmup_truncation(store, paths, cfg, cut):
    """실데이터: 지수 끝을 잘라도 앞 구간 시장 국면이 같다 (과거 지수 연결 후에도 미래참조 없음)."""
    from regime_lab.data.loader import load_index

    warm = load_index_warmup(paths.cache)
    if warm is None:
        pytest.skip(f"{index_warmup_file(paths.cache)} 없음 — scripts/fetch_index_history.py 실행 필요")
    full = market_regime(attach_index_warmup(load_index(store), warm, cfg), cfg)
    part = market_regime(attach_index_warmup(load_index(store, end=cut), warm, cfg), cfg)
    a = full.set_index(["market", "date"]).loc[part.set_index(["market", "date"]).index, "market_regime"]
    b = part.set_index(["market", "date"])["market_regime"]
    assert (a.astype(object).fillna("NA") == b.astype(object).fillna("NA")).all()
