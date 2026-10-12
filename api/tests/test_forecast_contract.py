"""P2-9 봉 예측 API 계약 테스트. 원본 없이 합성 예측 실행 폴더로 돈다."""

import math
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab.config import Paths, load_config
from regime_lab.data.loader import ForecastStore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine" / "tests"))
from synth import make_market  # noqa: E402

RUN = "1h-20261012T090000-abcdef12"
NOTICE = "과거 데이터로 계산한 통계적 예상 범위이며 실제 가격 전망이나 매매 신호가 아닙니다."
ERROR_KEYS = {"code", "message", "detail", "retryable"}
STEP_KEYS = {"h", "low", "mid", "high", "p_up", "direction", "actual", "cross_day"}


@pytest.fixture(scope="module")
def market():
    return make_market(load_config(), n_tickers=5, seed=3)


def _write_run(runs: Path):
    rows = []
    for i, at in enumerate(pd.date_range("2026-09-01 10:00", periods=3, freq="h")):
        for h in (1, 2):
            rows.append({"code": "005930", "as_of": at, "h": h, "split": "test", "scored": i < 2,
                         "base_close": 1000.0 + i, "q0.1": -0.01, "q0.5": 0.0, "q0.9": 0.02, "p_up": 0.6,
                         "direction": "up", "direction_shown": "up" if h == 2 else "uncertain", "reason": None,
                         "y": 0.01 if i < 2 else None, "cross_day": False})
    meta = {"run_id": RUN, "created_at": "2026-10-12T09:00:00", "format_version": 2, "timeframe": "1h", "horizon": 2,
            "codes": 1, "quantiles": [0.1, 0.5, 0.9], "split": {"train_end": "2026-01-30", "valid_end": "2026-05-29"},
            "notice": NOTICE}
    scores = {"1": {"direction_policy": {"enabled": False}, "model": [], "baseline": []},
              "2": {"direction_policy": {"enabled": True}, "model": [], "baseline": []}}
    ForecastStore(runs, runs, runs).write_run(RUN, meta, pd.DataFrame(rows), scores)


@pytest.fixture()
def client(tmp_path, market):
    paths = Paths(tmp_path / "store", tmp_path / "dump.sql", tmp_path / "cache", tmp_path / "runs", None)
    _write_run(paths.runs)
    with TestClient(create_app(Settings(paths=paths), prep=market, serve_web=False)) as c:
        yield c


def test_runs_list(client):
    r = client.get("/api/forecasts/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["disclaimer"]
    assert body["runs"] == [{"run_id": RUN, "timeframe": "1h", "created_at": "2026-10-12T09:00:00", "horizon": 2,
                             "codes": 1, "format_version": 2}]


def test_runs_empty_is_no_run_not_error(tmp_path, market):
    paths = Paths(tmp_path / "store", tmp_path / "dump.sql", tmp_path / "cache", tmp_path / "runs", None)
    with TestClient(create_app(Settings(paths=paths), prep=market, serve_web=False)) as c:
        r = c.get("/api/forecasts/runs")
    assert r.status_code == 200 and r.json()["status"] == "no_run" and r.json()["runs"] == []


def test_stock_latest_prices_and_direction(client):
    r = client.get(f"/api/forecasts/runs/{RUN}/stocks/005930")
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok" and b["as_of"] == "2026-09-01T12:00:00+09:00" and b["base_close"] == 1002.0
    assert b["notice"] == NOTICE and b["disclaimer"]
    s1, s2 = b["steps"]
    assert set(s1) == STEP_KEYS and [s1["h"], s2["h"]] == [1, 2]
    assert math.isclose(s1["low"], 1002 * math.exp(-0.01)) and math.isclose(s1["high"], 1002 * math.exp(0.02))
    assert s1["mid"] == 1002.0 and s1["actual"] is None          # 마지막 시점은 아직 실현 전
    assert s1["direction"] == "uncertain" and s2["direction"] == "up"  # 표시 판정 통과 h 만 방향
    assert [x["close"] for x in b["history"]] == [1000.0, 1001.0, 1002.0]


def test_stock_as_of_picks_last_confirmed_and_accepts_timezone(client):
    for q in ("2026-09-01T11:30:00+09:00", "2026-09-01T02:30:00Z", "2026-09-01 11:30"):
        b = client.get(f"/api/forecasts/runs/{RUN}/stocks/005930", params={"as_of": q}).json()
        assert b["as_of"] == "2026-09-01T11:00:00+09:00" and b["base_close"] == 1001.0
        assert math.isclose(b["steps"][0]["actual"], 1001 * math.exp(0.01))
        assert [x["close"] for x in b["history"]] == [1000.0, 1001.0]   # 그 시점 뒤 값은 없음


def test_stock_no_prediction_and_errors(client):
    early = client.get(f"/api/forecasts/runs/{RUN}/stocks/005930", params={"as_of": "2026-08-01T10:00:00+09:00"})
    assert early.status_code == 200 and early.json()["status"] == "no_prediction"
    assert client.get(f"/api/forecasts/runs/{RUN}/stocks/000660").json()["status"] == "no_prediction"
    nf = client.get("/api/forecasts/runs/1h-none/stocks/005930")
    assert nf.status_code == 404 and set(nf.json()) == ERROR_KEYS and nf.json()["code"] == "forecast_run_not_found"
    bad = client.get(f"/api/forecasts/runs/{RUN}/stocks/5930")
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_code"
    bad_t = client.get(f"/api/forecasts/runs/{RUN}/stocks/005930", params={"as_of": "어제"})
    assert bad_t.status_code == 422 and bad_t.json()["code"] == "invalid_as_of"


def test_scores(client):
    b = client.get(f"/api/forecasts/runs/{RUN}/scores").json()
    assert b["run_id"] == RUN and b["horizon"] == 2 and b["scores"]["2"]["direction_policy"]["enabled"]
    assert client.get("/api/forecasts/runs/x/scores").status_code == 404
