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


KRX_COLS = ["code", "date", "open_raw", "high_raw", "low_raw", "close_raw", "volume_raw", "value"]


def _krx(codes=("005930", "000660", "035720", "068270", "105560"), days=320, seed=1, start="2025-01-02"):
    """합성 KRX 원주가 일봉 (일봉 예측 P2-11 입력)."""
    rng = np.random.default_rng(seed)
    rows = []
    for code in codes:
        close = 20000
        for d in pd.bdate_range(start, periods=days):
            o = max(100, int(close * np.exp(rng.normal(0, 0.005))))
            c = max(100, int(o * np.exp(rng.normal(0, 0.015))))
            hi, lo = max(o, c) + int(rng.integers(0, 200)), min(o, c) - int(rng.integers(0, 200))
            vol = int(rng.integers(1_000, 50_000))
            rows.append((code, d, o, hi, lo, c, vol, vol * c))
            close = c
    return pd.DataFrame(rows, columns=KRX_COLS)


class MemoryStore:
    """ForecastStore 와 같은 메서드를 가진 테스트 대역 (파일 I/O 없음)."""

    def __init__(self, raw=None, krx=None):
        self.raw = raw if raw is not None else pd.DataFrame(columns=COLS)
        self.krx = krx
        last = self.raw.groupby(["code", self.raw["dt"].dt.normalize()])["close_p"].last() if len(self.raw) else None
        self.daily = (pd.DataFrame({"code": last.index.get_level_values(0), "date": last.index.get_level_values(1),
                                    "close_raw": last.to_numpy()}) if last is not None else None)
        self.runs = {}

    def common_codes(self):
        return sorted((self.krx if self.krx is not None else self.raw)["code"].unique())

    def load_minutes(self, codes):
        return self.raw[self.raw["code"].isin(codes)].reset_index(drop=True)

    def load_daily_raw(self, codes):
        return self.daily[self.daily["code"].isin(codes)].reset_index(drop=True)

    def load_krx_raw(self, codes):
        return self.krx[self.krx["code"].isin(codes)].reset_index(drop=True)

    def fingerprint(self, minutes=True):
        fp = {"raw/krx_daily": f"rows={0 if self.krx is None else len(self.krx)}"}
        return {"minutes": f"rows={len(self.raw)}", **fp} if minutes else fp

    def write_run(self, run_id, meta, predictions, scores):
        self.runs[run_id] = (meta, predictions, scores)


def _cfg(cfg):
    """시간봉 시험용: 1분봉 경로(1h·3h)는 제품 설정(결정 D-2b, 1d 만)에서 빠졌지만 코드는 남아 있어 계속 시험한다."""
    f = {**cfg["forecast"], "timeframes": ["1h", "3h", "1d"], "split": {"train_end": "2026-01-16", "valid_end": "2026-02-06"},
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
    assert m["created_at"] == "2026-10-12T09:00:00+09:00" and set(m["versions"]) == {"scikit-learn", "numpy", "pandas"}
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
        run_forecast(MemoryStore(_raw()), c, "1w", NOW)
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


def test_forecast_store_as_of_range_from_parquet_stats(tmp_path):
    fs = ForecastStore(tmp_path / "store", tmp_path / "minutes", tmp_path / "runs")
    at = pd.to_datetime(["2026-09-02 11:00", "2026-09-01 10:00", "2026-09-03 15:31"])
    fs.write_run("1h-r", {}, pd.DataFrame({"code": ["A", "B", "A"], "as_of": at, "h": [1, 1, 2]}), {})
    assert fs.as_of_range("1h-r") == (pd.Timestamp("2026-09-01 10:00"), pd.Timestamp("2026-09-03 15:31"))


# ── 일봉 예측 (P2-11, 결정 D-2b): KRX 원주가 일봉에서 바로 봉을 만든다 ──

def _dcfg(cfg):
    """제품 설정 그대로(1d) + 합성 기간에 맞춘 분할·작은 모델."""
    f = {**cfg["forecast"], "split": {"train_end": "2025-08-29", "valid_end": "2025-11-28"},
         "baseline": {**cfg["forecast"]["baseline"], "window": 20, "min_samples": 10},
         "model": {**cfg["forecast"]["model"], "max_iter": 20, "min_samples_leaf": 20},
         "runs": {"batch_codes": 2, "store_splits": ["test"]}}
    return {**cfg, "forecast": f}


def test_product_config_is_daily_with_agreed_split(cfg):
    """결정 D-2b: 제품 봉 예측은 일봉만, 분할은 역방향 탐색 분할일(학습 ~2024-02-29, 검증 ~2025-02-28)."""
    assert cfg["forecast"]["timeframes"] == ["1d"]
    assert cfg["forecast"]["split"] == {"train_end": "2024-02-29", "valid_end": "2025-02-28"}
    assert cfg["forecast"]["horizon"]["1d"] == [5, 20]


def test_daily_bars_by_hand(cfg):
    """합성 일봉 5행 → 봉 수기 대조: 거래 없는 날·가격 오류 행은 봉이 없고, 가격제한폭 밖 시가 갭에서 새 가격 구간."""
    from regime_lab.bars import daily_bars

    k = pd.DataFrame([
        ("000001", "2026-09-01", 1000, 1050, 990, 1020, 100, 102000),
        ("000001", "2026-09-02", 1020, 1020, 1020, 1020, 0, 0),         # 거래 없음
        ("000001", "2026-09-03", 1030, 1000, 1010, 1040, 50, 52000),    # 고가 < 저가 → 가격 오류
        ("000001", "2026-09-04", 1500, 1550, 1480, 1500, 70, 105000),   # 직전 거래일 종가 1020 대비 +47% → 새 구간
        ("000001", "2026-09-07", 1510, 1520, 1490, 1495, 60, 89700),
    ], columns=KRX_COLS).assign(date=lambda d: pd.to_datetime(d["date"]))
    bars, stats = daily_bars(k, cfg)
    assert stats == {"rows_in": 5, "no_trade": 1, "outliers": 1, "rows_out": 3}
    assert list(bars["date"].dt.strftime("%m-%d")) == ["09-01", "09-04", "09-07"]
    assert (bars["slot"] == 1).all() and (bars["bar_minutes"] == 391).all()
    assert bars["bar_start"].iloc[0] == pd.Timestamp("2026-09-01 09:00")
    assert bars["available_at"].iloc[0] == pd.Timestamp("2026-09-01 15:31")  # 정규장 마지막 분 + 1분 = 확정 시각
    assert list(bars["close"]) == [1020.0, 1500.0, 1495.0] and list(bars["value_1m"]) == [102000, 105000, 89700]
    assert list(bars["segment"]) == [0, 1, 1]
    assert bars["nxt_period"].all()  # 2026-04-24(N5) 이후


def test_daily_run_contract(cfg):
    store, c = MemoryStore(krx=_krx()), _dcfg(cfg)
    run_id, meta = run_forecast(store, c, "1d", NOW)
    m, pred, scores = store.runs[run_id]
    assert run_id.startswith("1d-20261012T090000-") and m["format_version"] == FORECAST_RUN_VERSION == 4
    assert m["bar_source"] == "krx_daily" and m["minute_prep_version"] is None
    assert "minutes" not in m["data"] and m["horizon"] == 5 and m["codes"] == 5
    assert m["prep"] == {"rows_in": 1600, "no_trade": 0, "outliers": 0}
    assert set(pred["split"]) == {"test"} and set(pred["h"]) == {1, 2, 3, 4, 5}
    assert pred["as_of"].dt.strftime("%H:%M").eq("15:31").all()  # 일봉 확정 시각
    assert pred["cross_day"].all()  # 일봉의 다음 봉은 늘 다음 거래일
    assert (pred["q0.1"] <= pred["q0.9"]).all() and set(scores) == {"1", "2", "3", "4", "5"}


def test_daily_predictions_ignore_rows_after_as_of(cfg):
    """절단 불변: 평가 구간 한 시점 T 의 일봉 예측은 T 뒤 일봉을 지워도 같다."""
    c, krx = _dcfg(cfg), _krx()
    full = MemoryStore(krx=krx)
    run_forecast(full, c, "1d", NOW)
    pf = next(iter(full.runs.values()))[1]
    t = pf.loc[pf["split"] == "test", "as_of"].sort_values().iloc[len(pf) // 3]
    cut = MemoryStore(krx=krx[krx["date"] <= t.normalize()])
    run_forecast(cut, c, "1d", NOW)
    pc = next(iter(cut.runs.values()))[1]
    cols = ["code", "as_of", "h", "q0.1", "q0.5", "q0.9", "p_up", "direction"]
    a = pf.loc[pf["as_of"] == t, cols].reset_index(drop=True)
    b = pc.loc[pc["as_of"] == t, cols].reset_index(drop=True)
    assert len(a) == 25  # 5종목 × h 5
    pd.testing.assert_frame_equal(a, b)


def test_previous_format_runs_still_read(cfg, tmp_path):
    """형식 3(시간봉, bar_source 없음) run 과 형식 4 일봉 run 이 같은 읽기 함수로 읽힌다."""
    from regime_lab.forecast_runs import list_runs, stock_forecast

    fs = ForecastStore(tmp_path / "store", None, tmp_path / "runs")
    mem = MemoryStore(_raw())
    run_forecast(mem, _cfg(cfg), "1h", NOW)
    (rid3, (m3, p3, s3)), = mem.runs.items()
    m3 = {k: v for k, v in m3.items() if k != "bar_source"} | {"format_version": 3}
    fs.write_run(rid3, m3, p3, s3)
    dmem = MemoryStore(krx=_krx())
    run_forecast(dmem, _dcfg(cfg), "1d", NOW)
    (rid4, (m4, p4, s4)), = dmem.runs.items()
    fs.write_run(rid4, m4, p4, s4)
    assert {r["run_id"]: r["format_version"] for r in list_runs(fs)} == {rid3: 3, rid4: 4}
    for rid, code in ((rid3, "005930"), (rid4, "005930")):
        d = stock_forecast(fs, cfg, rid, code)
        assert d["status"] == "ok" and len(d["steps"]) == (7 if rid == rid3 else 5)
    with pytest.raises(FileNotFoundError, match="1분봉 폴더"):
        fs.load_minutes(["005930"])  # 1분봉 폴더 없이 만든 저장소는 시간봉 입력만 못 읽는다
