"""F1 데이터 서버 동기화 (engine/scripts/sync_data_server.py) — 가짜 서버(합성 표)로 형식·나눠 받기·실패 시 무출력·비밀값 비노출."""

import importlib.util
import io
import json
import os
import urllib.error
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from regime_lab.data.loader import input_file_hashes, load_daily, load_index, load_master, load_sample_tickers
from regime_lab.data.minutes import check_manifest, load_minutes

_spec = importlib.util.spec_from_file_location(
    "sync_data_server", Path(__file__).resolve().parents[1] / "scripts" / "sync_data_server.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(S, "RETRY_WAIT_SEC", 0)   # 일시 오류 재시도 대기 없이

TICKERS = ["000001", "000002", "000003"]

DATES = pd.bdate_range("2026-09-01", periods=12)


def _store_frames() -> dict[str, pd.DataFrame]:
    """store 파일과 같은 형식의 합성 원본."""
    rng = np.random.default_rng(0)
    rows = [(d, t) for t in TICKERS for d in DATES]
    n = len(rows)
    prices = pd.DataFrame({"date": [r[0] for r in rows], "ticker": [r[1] for r in rows],
                           "open": rng.uniform(900, 1100, n).round(), "high": rng.uniform(1100, 1200, n).round(),
                           "low": rng.uniform(800, 900, n).round(), "close": rng.integers(900, 1100, n),
                           "volume": rng.integers(1, 10**6, n), "value": rng.integers(1, 10**9, n)})
    prices.loc[0, ["open", "high", "low"]] = np.nan                       # 거래정지 날 (open 결측)
    marketcap = pd.DataFrame({"date": prices["date"], "ticker": prices["ticker"],
                              "marketcap": rng.integers(10**9, 10**12, n), "shares": rng.integers(10**6, 10**8, n),
                              "market": ["KOSPI"] * n})
    master = pd.DataFrame({"ticker": TICKERS, "name": ["가", "나", "다"], "market": ["KOSPI", "KOSDAQ", "KOSPI"],
                           "sector": ["전기·전자", None, "화학"],
                           "listing_date": pd.to_datetime(["2000-01-04", "2010-05-03", "2015-07-01"]),
                           "kind": ["보통주"] * 3, "price_source": ["naver_adj"] * 3,
                           "is_delisted": [False, False, True]})
    index = pd.DataFrame({"date": list(DATES) * 2, "index": ["KOSPI"] * 12 + ["KOSDAQ"] * 12,
                          "open": rng.uniform(800, 900, 24), "high": rng.uniform(900, 950, 24),
                          "low": rng.uniform(700, 800, 24), "close": rng.uniform(800, 900, 24),
                          "volume": rng.integers(10**6, 10**8, 24)})
    delisted = pd.DataFrame({"ticker": ["000003"], "name": ["다"], "delisting_date": [DATES[-1]], "market": ["KOSPI"]})
    krx = pd.DataFrame({"date": prices["date"], "ticker": prices["ticker"], "name": "x", "market": "KOSPI",
                        "dept": "", **{c: rng.integers(1, 10**6, n) for c in
                                       ("open_raw", "high_raw", "low_raw", "close_raw", "volume_raw", "value",
                                        "marketcap", "shares")}})
    sector = pd.DataFrame({"date": [DATES[4]] * 3 + [DATES[-1]] * 3, "ticker": TICKERS * 2, "sector": ["a", "b", "c"] * 2})
    frames = {"prices": prices, "marketcap": marketcap, "master": master, "index": index, "delisted": delisted,
              "krx_daily": krx, "sector": sector,
              "sample30": prices[prices["ticker"] != "000003"].reset_index(drop=True)}
    for df in frames.values():  # 실제 store 파일처럼 timestamp[ns]
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                df[c] = df[c].astype("datetime64[ns]")
    return frames


def _write_store(root: Path, f: dict) -> Path:
    store = root / "store"
    (store / "raw" / "krx_daily").mkdir(parents=True)
    (store / "raw" / "sector").mkdir(parents=True)
    for k in ("prices", "marketcap", "master", "index", "delisted", "sample30"):
        f[k].to_parquet(store / f"{k}.parquet", index=False)
    for d, part in f["krx_daily"].groupby("date"):
        part.to_parquet(store / "raw" / "krx_daily" / f"{d:%Y%m%d}.parquet", index=False)
    for d, part in f["sector"].groupby("date"):        # 원본은 시장별 파일
        part.to_parquet(store / "raw" / "sector" / f"{d:%Y%m%d}_KOSPI.parquet", index=False)
    return store


def _server_form(df: pd.DataFrame) -> pd.DataFrame:
    """MariaDB 사본·API 처럼: 날짜는 문자열, bool 은 0/1, 결측은 None."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d").where(out[c].notna(), None)
        elif pd.api.types.is_bool_dtype(out[c]):
            out[c] = out[c].astype(int)
    return out.astype(object).where(out.notna(), None)


class FakeClient:
    """데이터 서버 API 대역: 표별 조건 검사와 결과 한도(limit 행)를 흉내 낸다."""

    def __init__(self, frames: dict, limit: int = 40, fail_on: str | None = None):
        f = {f"store_{k}": _server_form(v) for k, v in frames.items() if k != "sample30"}
        f["store_index"] = f["store_index"].rename(columns={"index": "market"})
        self.tables, self.limit, self.fail_on, self.calls = f, limit, fail_on, []

    def health(self):
        return True

    def table(self, name, ticker=None, start=None, end=None):
        self.calls.append(name)
        if name == self.fail_on:
            raise S.SyncError(f"{name} 요청 실패: HTTP 503 DB 오류")
        df = self.tables[name]
        tcol = "code" if name == "stock_minutes" else "ticker"
        dcol = "dt" if name == "stock_minutes" else "date"
        if ticker:
            df = df[df[tcol].isin(ticker.split(","))]
        if start:
            df = df[(df[dcol].str[:10] >= start[:10]) & (df[dcol].str[:10] <= end[:10])]
        if len(df) > self.limit:
            raise S.TooManyRows("결과가 200,000행을 넘습니다")
        return df.reset_index(drop=True)


@pytest.fixture()
def world(tmp_path):
    frames = _store_frames()
    return frames, _write_store(tmp_path / "old", frames)


def test_sync_writes_same_store_format_in_chunks(world, tmp_path):
    frames, old = world
    client = FakeClient(frames, limit=30)            # 지수(24행)는 한 번에, 가격(36행)은 나눠 받게
    counts = S.run(client, tmp_path / "new", compare_store=old, log=lambda *_: None)
    new = tmp_path / "new" / "store"
    for k in ("prices", "marketcap", "master", "index", "delisted", "sample30"):
        a, b = pd.read_parquet(new / f"{k}.parquet"), pd.read_parquet(old / f"{k}.parquet")
        pd.testing.assert_frame_equal(S._sorted(a, S.KEYS[k]), S._sorted(b, S.KEYS[k]))   # 열 순서·자료형·값
    assert len(list((new / "raw" / "krx_daily").glob("*.parquet"))) == len(DATES)
    assert sorted(p.name for p in (new / "raw" / "sector").glob("*.parquet")) == [
        f"{DATES[4]:%Y%m%d}_ALL.parquet", f"{DATES[-1]:%Y%m%d}_ALL.parquet"]
    assert client.calls.count("store_prices") > 1                       # 한도 때문에 거래일 창을 나눠 받음
    assert counts["prices"] == len(frames["prices"])
    # 엔진 loader 가 그대로 읽는다 (loader 는 바꾸지 않음)
    pd.testing.assert_frame_equal(load_daily(new), load_daily(old))
    pd.testing.assert_frame_equal(load_index(new), load_index(old))
    assert load_sample_tickers(new) == load_sample_tickers(old) and len(load_master(new)) == 3
    assert not any(p.name.startswith(".tmp_") for p in tmp_path.iterdir())


def test_mismatch_with_existing_store_writes_nothing(world, tmp_path):
    frames, old = world
    changed = {**frames, "prices": frames["prices"].assign(close=frames["prices"]["close"] + (frames["prices"].index == 5))}
    with pytest.raises(S.SyncError, match="prices.close"):
        S.run(FakeClient(changed), tmp_path / "new", compare_store=old, log=lambda *_: None)
    assert not (tmp_path / "new").exists() and not (tmp_path / ".tmp_new").exists()


def test_failure_midway_leaves_no_partial_files(world, tmp_path):
    frames, old = world
    with pytest.raises(S.SyncError, match="store_krx_daily"):
        S.run(FakeClient(frames, fail_on="store_krx_daily"), tmp_path / "new", compare_store=old, log=lambda *_: None)
    assert not (tmp_path / "new").exists() and not (tmp_path / ".tmp_new").exists()


def test_refuses_existing_output_and_needs_sample_tickers(world, tmp_path):
    frames, old = world
    (tmp_path / "new").mkdir()
    with pytest.raises(S.SyncError, match="이미 있습니다"):
        S.run(FakeClient(frames), tmp_path / "new", compare_store=old, log=lambda *_: None)
    with pytest.raises(S.SyncError, match="sample30"):
        S.run(FakeClient(frames), tmp_path / "new2", compare_store=None, log=lambda *_: None)
    S.run(FakeClient(frames), tmp_path / "new3", sample_tickers=["000001"], log=lambda *_: None)
    assert load_sample_tickers(tmp_path / "new3" / "store") == ["000001"]


def test_synced_store_gets_new_input_fingerprint(world, tmp_path):
    """복사·동기화한 store 는 파일 수정 시각이 달라 준비 캐시 키(input_file_hashes)가 바뀐다 → 캐시는 새로 만든다."""
    frames, old = world
    for f in old.rglob("*.parquet"):
        os.utime(f, (1_600_000_000, 1_600_000_000))
    S.run(FakeClient(frames), tmp_path / "new", compare_store=old, log=lambda *_: None)
    assert input_file_hashes(tmp_path / "new" / "store") != input_file_hashes(old)


class _ErrorOpener:
    def __init__(self, exc):
        self.exc = exc

    def open(self, req, timeout=None):
        raise self.exc


def test_errors_never_contain_address_or_token():
    base, token = "http://10.9.8.7:8100", "secret-token-123"
    url = f"{base}/v1/store_prices?ticker=000001"
    cases = [
        urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(json.dumps({"detail": "API 키가 필요합니다"}).encode())),
        urllib.error.URLError(ConnectionRefusedError(f"refused {base}")),
    ]
    for exc in cases:
        c = S.Client(base, token, opener=_ErrorOpener(exc))
        with pytest.raises(S.SyncError) as e:
            c.table("store_prices", ticker="000001")
        msg = str(e.value)
        assert "10.9.8.7" not in msg and token not in msg and "store_prices" in msg
    too_many = urllib.error.HTTPError(url, 400, "Bad", {}, io.BytesIO(
        json.dumps({"detail": "결과가 200,000행을 넘습니다. 종목·기간을 좁혀 나눠서 요청하세요."}).encode()))
    with pytest.raises(S.TooManyRows):
        S.Client(base, token, opener=_ErrorOpener(too_many)).table("store_prices", ticker="000001")
    with pytest.raises(S.SyncError):
        S.Client("ftp://x")


def test_redirect_is_refused():
    with pytest.raises(S.SyncError, match="다른 주소"):
        S._NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://elsewhere/")


def test_minutes_sync_writes_month_files_and_manifest(world, tmp_path):
    frames, old = world
    dts = pd.date_range("2026-08-31 09:00", periods=3, freq="min").append(
        pd.date_range("2026-09-01 09:00", periods=4, freq="min"))
    m = pd.DataFrame({"code": ["000001"] * 7, "dt": dts.strftime("%Y-%m-%d %H:%M:%S"), "open_p": 100, "high_p": 101,
                      "low_p": 99, "close_p": 100, "volume": 5, "value": np.arange(7) * 500})
    client = FakeClient(frames)
    client.tables["stock_minutes"] = m.astype(object)
    S.run(client, tmp_path / "new", compare_store=old, minutes=True, minutes_start="2026-08", log=lambda *_: None)
    md = tmp_path / "new" / "minutes"
    assert sorted(p.name for p in md.glob("*.parquet")) == ["stock_minutes_202608.parquet", "stock_minutes_202609.parquet"]
    man = check_manifest(md)
    assert man["total_rows"] == 7 and set(man["months"]) == {"202608", "202609"}
    got = load_minutes(md, ["000001"])
    assert len(got) == 7 and str(got["dt"].dtype).startswith("datetime64")


# --- PR #77 리뷰(seongmin-claude) 회귀 ---

def test_compare_tolerates_other_timestamp_unit_in_existing_store(world, tmp_path):
    """P2-2: 기존 store 의 날짜가 ns 가 아니어도(예: pandas 3 로 쓴 us) 값이 같으면 통과."""
    frames, _ = world
    us = {k: v.copy() for k, v in frames.items()}
    for df in us.values():
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                df[c] = df[c].astype("datetime64[us]")
    old = _write_store(tmp_path / "old_us", us)
    assert str(pd.read_parquet(old / "prices.parquet")["date"].dtype) == "datetime64[us]"
    S.run(FakeClient(frames), tmp_path / "new", compare_store=old, log=lambda *_: None)
    assert str(pd.read_parquet(tmp_path / "new" / "store" / "prices.parquet")["date"].dtype) == "datetime64[ns]"


def test_dates_outside_index_stop_instead_of_dropping(world, tmp_path):
    """P2-3: 지수보다 하루 늦게 적재된 일봉(지수에 없는 날짜)을 조용히 빼지 않고 멈춘다 (--no-compare 에서도)."""
    frames, _ = world
    late = DATES[-1] + pd.offsets.BDay(1)
    extra = frames["prices"][frames["prices"]["date"] == DATES[-1]].assign(date=late)
    f = {**frames, "prices": pd.concat([frames["prices"], extra], ignore_index=True)}
    with pytest.raises(S.SyncError, match="store_index 에 없는 날짜"):
        S.run(FakeClient(f, limit=10**6), tmp_path / "new", sample_tickers=["000001"], log=lambda *_: None)
    assert not (tmp_path / "new").exists()
    gap = {**frames, "prices": frames["prices"][frames["prices"]["date"] != DATES[3]]}
    with pytest.raises(S.SyncError, match="비어 있습니다"):
        S.run(FakeClient(gap, limit=10**6), tmp_path / "new2", sample_tickers=["000001"], log=lambda *_: None)


class _Resp:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        raise self.exc


class _ReadFailOpener:
    def __init__(self, exc):
        self.exc = exc

    def open(self, req, timeout=None):
        return _Resp(self.exc)


def test_gateway_timeout_splits_and_broken_read_is_sync_error():
    """P3: 504(쿼리 30초 초과)는 나눠 받기 대상, 응답을 읽다 끊기면 traceback 대신 SyncError."""
    import http.client
    url = "http://10.9.8.7:8100/v1/store_prices"
    to = urllib.error.HTTPError(url, 504, "Gateway Timeout", {}, io.BytesIO(
        json.dumps({"detail": "조회가 30초를 넘어 중단됐습니다."}).encode()))
    with pytest.raises(S.TooManyRows):
        S.Client("http://10.9.8.7:8100", opener=_ErrorOpener(to)).table("store_prices", start="2026-09-01", end="2026-09-30")
    for exc in (http.client.IncompleteRead(b"partial"), ConnectionResetError("reset by 10.9.8.7")):
        with pytest.raises(S.SyncError) as e:
            S.Client("http://10.9.8.7:8100", opener=_ReadFailOpener(exc)).table("store_prices", ticker="000001")
        assert "10.9.8.7" not in str(e.value) and not isinstance(e.value, S.TooManyRows)


def test_minutes_row_count_must_match_existing_copy(world, tmp_path):
    """P3: 기존 1분봉 사본(매니페스트)과 겹치는 달의 행 수가 다르면 저장하지 않는다."""
    frames, old = world
    md = old.parent / "minutes"
    md.mkdir()
    (md / "minutes_manifest.json").write_text(json.dumps({"months": {"202609": {"rows": 999}}}), encoding="utf-8")
    m = pd.DataFrame({"code": ["000001"] * 2, "dt": ["2026-09-01 09:00:00", "2026-09-01 09:01:00"], "open_p": 1,
                      "high_p": 1, "low_p": 1, "close_p": 1, "volume": 1, "value": 1})
    client = FakeClient(frames)
    client.tables["stock_minutes"] = m.astype(object)
    with pytest.raises(S.SyncError, match="기존 1분봉 사본 범위의 행 수가 다릅니다"):
        S.run(client, tmp_path / "new", compare_store=old, minutes=True, log=lambda *_: None)
    assert not (tmp_path / "new").exists()


class _FlakyOpener:
    """처음 n 번은 연결 실패, 그 뒤 정상 응답."""

    def __init__(self, fails: int, body: dict):
        self.fails, self.body, self.calls = fails, body, 0

    def open(self, req, timeout=None):
        self.calls += 1
        if self.calls <= self.fails:
            raise urllib.error.URLError(ConnectionResetError("reset"))
        return io.BytesIO(json.dumps(self.body).encode())


def test_transient_errors_are_retried_then_reported():
    """P3: 연결·읽기 일시 오류는 짧게 다시 시도하고, 계속되면 SyncError (주소 없음)."""
    body = {"table": "store_master", "count": 1, "columns": ["ticker"], "rows": [["000001"]]}
    ok = S.Client("http://10.9.8.7:8100", opener=_FlakyOpener(2, body))
    ok._sleep = lambda *_: None
    assert ok.table("store_master")["ticker"].tolist() == ["000001"] and ok._opener.calls == 3
    bad = S.Client("http://10.9.8.7:8100", opener=_FlakyOpener(9, body))
    bad._sleep = lambda *_: None
    with pytest.raises(S.SyncError) as e:
        bad.table("store_master")
    assert bad._opener.calls == S.RETRIES and "10.9.8.7" not in str(e.value)


def test_minutes_rows_added_after_existing_copy_are_allowed(world, tmp_path):
    """재리뷰 P2: 기존 사본 이후 같은 달에 새로 적재된 행은 허용, 기존 범위 안 행 수만 대조."""
    frames, old = world
    md = old.parent / "minutes"
    md.mkdir()
    man = {"months": {"202609": {"rows": 2, "min_dt": "2026-09-01 09:00:00", "max_dt": "2026-09-01 09:01:00"}}}
    (md / "minutes_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    m = pd.DataFrame({"code": ["000001"] * 4, "dt": ["2026-09-01 09:00:00", "2026-09-01 09:01:00",
                                                     "2026-09-02 09:00:00", "2026-09-02 09:01:00"],
                      "open_p": 1, "high_p": 1, "low_p": 1, "close_p": 1, "volume": 1, "value": 1})
    client = FakeClient(frames)
    client.tables["stock_minutes"] = m.astype(object)
    S.run(client, tmp_path / "new", compare_store=old, minutes=True, log=lambda *_: None)
    assert check_manifest(tmp_path / "new" / "minutes")["months"]["202609"]["rows"] == 4
    client.tables["stock_minutes"] = m.iloc[1:].astype(object)       # 기존 범위 안 행이 하나 빠지면 멈춤
    with pytest.raises(S.SyncError, match="기존 1분봉 사본 범위의 행 수가 다릅니다"):
        S.run(client, tmp_path / "new2", compare_store=old, minutes=True, log=lambda *_: None)
