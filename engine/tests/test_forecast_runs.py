"""P2-7 과거 as_of 예측 일괄 생성 — 메모리 저장소 대역으로 흐름·저장 형식·재현성·절단 불변 (합성 데이터)."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from regime_lab.data.forecast_store import ForecastStore
from regime_lab.forecast_runs import FORECAST_RUN_VERSION, build_bars, run_forecast

COLS = ["code", "dt", "open_p", "high_p", "low_p", "close_p", "volume", "value"]
NOW = datetime(2026, 10, 12, 9, 0)


def _raw(codes=("005930", "000660", "035720"), days=70, seed=0, start="2025-12-01"):
    rng = np.random.default_rng(seed)
    rows = []
    for code in codes:
        price = 10000
        for d in pd.bdate_range(start, periods=days):
            cum = 0
            for m in range(9 * 60, 15 * 60 + 31, 10):  # 10분마다 체결 (정규장)
                price = max(100, int(price * np.exp(rng.normal(0, 0.003))))
                vol = int(rng.integers(1, 100))
                cum += price * vol
                rows.append((code, d + pd.Timedelta(minutes=m), price, price + 10, price - 10, price, vol, cum))
    return pd.DataFrame(rows, columns=COLS)


class MemoryStore:
    """ForecastStore 와 같은 메서드를 가진 테스트 대역 (파일 I/O 없음)."""

    def __init__(self, raw):
        self.raw = raw
        last = raw.groupby(["code", raw["dt"].dt.normalize()])["close_p"].last()
        self.daily = pd.DataFrame({"code": last.index.get_level_values(0), "date": last.index.get_level_values(1),
                                   "close_raw": last.to_numpy()})
        self.runs = {}

    def common_codes(self):
        return sorted(self.raw["code"].unique())

    def load_minutes(self, codes):
        return self.raw[self.raw["code"].isin(codes)].reset_index(drop=True)

    def load_daily_raw(self, codes):
        return self.daily[self.daily["code"].isin(codes)].reset_index(drop=True)

    def fingerprint(self):
        return {"minutes": f"rows={len(self.raw)}"}

    def write_run(self, run_id, meta, predictions, scores):
        self.runs[run_id] = (meta, predictions, scores)


def _cfg(cfg):
    f = {**cfg["forecast"], "split": {"train_end": "2026-01-16", "valid_end": "2026-02-06"},
         "baseline": {**cfg["forecast"]["baseline"], "window": 10, "min_samples": 5},
         "model": {**cfg["forecast"]["model"], "max_iter": 20, "min_samples_leaf": 20},
         "runs": {"batch_codes": 2, "store_splits": ["test"]}}
    return {**cfg, "forecast": f}


def test_batches_give_same_bars_as_one_pass(cfg):
    """종목 묶음 크기와 관계없이 같은 봉 (시장 특징은 봉을 모두 모은 뒤 계산)."""
    store, c = MemoryStore(_raw()), _cfg(cfg)
    a, sa = build_bars(store, c, store.common_codes(), "1h", batch=1)
    b, sb = build_bars(store, c, store.common_codes(), "1h", batch=10)
    pd.testing.assert_frame_equal(a, b)
    assert sa == sb and sa["rows_in"] == len(store.raw)


def test_run_writes_contract_and_is_reproducible(cfg):
    store, c = MemoryStore(_raw()), _cfg(cfg)
    run_id, meta = run_forecast(store, c, "3h", NOW)
    assert run_id.startswith("3h-20261012T090000-") and run_id in store.runs
    m, pred, scores = store.runs[run_id]
    assert m["format_version"] == FORECAST_RUN_VERSION and m["horizon"] == 2 and m["codes"] == 3
    assert "매매 신호가 아닙니다" in m["notice"]
    assert list(pred.columns) == ["code", "as_of", "h", "split", "scored", "base_close", "q0.1", "q0.5", "q0.9",
                                  "p_up", "direction", "direction_shown", "reason", "y", "cross_day"]
    assert set(pred["split"]) == {"test"} and set(pred["h"]) == {1, 2}
    assert (pred["q0.1"] <= pred["q0.9"]).all() and pred["direction"].isin(["up", "down", "uncertain"]).all()
    # 마지막 표본은 목표가 아직 없는(실현 전) 예측 — 저장은 되고 채점 대상은 아님
    last = pred[pred["as_of"] == pred["as_of"].max()]
    assert last["y"].isna().all() and not last["scored"].any()
    assert last["cross_day"].all()  # 마지막(15:31 확정) 시점의 다음 봉은 실현 전이어도 다음 거래일로 표시
    assert set(scores) == {"1", "2"} and {"model", "baseline", "conformal_widen", "direction_policy", "model_by_slot",
                                         "model_by_cross_day"} <= set(scores["1"])
    for h in ("1", "2"):  # 판정을 통과하지 못한 h 는 화면 방향이 모두 "불확실"
        if not scores[h]["direction_policy"]["enabled"]:
            assert (pred.loc[pred["h"] == int(h), "direction_shown"] == "uncertain").all()
    assert all(isinstance(r["n"], int) for r in scores["1"]["model"])  # JSON 기본형
    # 같은 데이터·설정·시각 → 같은 결과
    store2 = MemoryStore(_raw())
    run_forecast(store2, c, "3h", NOW)
    pd.testing.assert_frame_equal(pred, store2.runs[run_id][1])


def test_only_test_period_predictions_can_be_stored(cfg):
    """검증 구간 예측은 보정에 그 시점 뒤 값까지 써서 저장하지 않는다 (codex-01a0fb4b #52 시점 검사)."""
    c = _cfg(cfg)
    bad = {**c, "forecast": {**c["forecast"], "runs": {"batch_codes": 2, "store_splits": ["valid", "test"]}}}
    with pytest.raises(ValueError, match="test 만"):
        run_forecast(MemoryStore(_raw()), bad, "3h", NOW)


def test_unknown_timeframe_and_empty_data(cfg):
    c = _cfg(cfg)
    with pytest.raises(ValueError, match="지원하지 않는 봉"):
        run_forecast(MemoryStore(_raw()), c, "1d", NOW)
    with pytest.raises(ValueError, match="집계 봉이 없습니다"):
        run_forecast(MemoryStore(_raw()), c, "1h", NOW, codes=["999999"])


def test_test_period_predictions_ignore_rows_after_as_of(cfg):
    """평가 구간 한 시점의 예측은 그 시점 이후 1분 행을 지워도 같다 (모델은 학습·검증 구간으로만 맞춤)."""
    c = _cfg(cfg)
    raw = _raw()
    full = MemoryStore(raw)
    run_forecast(full, c, "1h", NOW)
    pf = next(iter(full.runs.values()))[1]
    t = pf.loc[pf["split"] == "test", "as_of"].sort_values().iloc[len(pf) // 50]
    cut = MemoryStore(raw[raw["dt"] < t])
    run_forecast(cut, c, "1h", NOW)
    pc = next(iter(cut.runs.values()))[1]
    cols = ["code", "as_of", "h", "q0.1", "q0.5", "q0.9", "p_up", "direction"]
    a = pf.loc[pf["as_of"] == t, cols].reset_index(drop=True)
    b = pc.loc[pc["as_of"] == t, cols].reset_index(drop=True)
    assert len(a) > 0
    pd.testing.assert_frame_equal(a, b)


def test_forecast_store_roundtrip(tmp_path):
    fs = ForecastStore(tmp_path / "store", tmp_path / "minutes", tmp_path / "runs")
    pred = pd.DataFrame({"code": ["005930"], "as_of": [pd.Timestamp("2026-09-01 10:00")], "h": [1]})
    path = fs.write_run("1h-x", {"run_id": "1h-x", "n": np.int64(3)}, pred, {"1": {"model": []}})
    assert path.name == "1h-x" and fs.list_runs() == ["1h-x"]
    meta, p, s = fs.read_run("1h-x")
    assert meta["run_id"] == "1h-x" and s == {"1": {"model": []}}
    pd.testing.assert_frame_equal(p, pred)
    with pytest.raises(FileExistsError):
        fs.write_run("1h-x", {}, pred, {})
    assert not any(x.name.startswith(".tmp") for x in (tmp_path / "runs" / "forecasts").iterdir())
