"""P1-7 역방향 탐색·조합 저장 API 계약 테스트 (설계 §4.2·§5). 원본 데이터 없이 합성 시장으로 돈다."""

import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab.config import Paths, load_config

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine" / "tests"))
from synth import make_market  # noqa: E402

ERROR_KEYS = {"code", "message", "detail", "retryable"}
BODY = {"name": "api", "target_win_rate": 0.45, "min_trades": 30,
        "axes": {"patterns": ["breakout_20d", "rsi_rebound"], "max_hold_days": [5, 20]}}


@pytest.fixture(scope="module")
def market():
    return make_market(load_config(), n_tickers=40, seed=2)


@pytest.fixture()
def client(market, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    for f in ("prices.parquet", "marketcap.parquet", "master.parquet", "index.parquet", "delisted.parquet"):
        (store / f).write_bytes(b"")  # 입력 파일 지문용 빈 파일 (내용은 합성 준비 프레임에서 온다)
    paths = Paths(store, tmp_path / "dump.sql", tmp_path / "cache", tmp_path / "runs")
    with TestClient(create_app(Settings(paths=paths), prep=market, serve_web=False)) as c:
        yield c


def search_to_end(client, body=None, timeout=120):
    r = client.post("/api/searches", json=body or BODY)
    assert r.status_code == 202, r.text
    sid = r.json()["search_id"]
    end = __import__("time").time() + timeout
    while True:
        snap = client.get(f"/api/searches/{sid}").json()
        if snap["status"] in ("completed", "cancelled", "failed"):
            return sid, snap
        assert __import__("time").time() < end
        __import__("time").sleep(0.05)


def test_openapi_lists_new_paths(client):
    spec = client.get("/openapi.json").json()["paths"]
    for p in ("/api/searches/options", "/api/searches/preview", "/api/searches", "/api/searches/{search_id}",
              "/api/searches/{search_id}/cancel", "/api/searches/{search_id}/result",
              "/api/presets", "/api/presets/{preset_id}"):
        assert p in spec, p


def test_options(client):
    o = client.get("/api/searches/options").json()
    assert len(o["patterns"]) == 10 and o["max_candidates"] == 200  # 핵심 5종 + 후순위 5종(X6)
    assert o["defaults"]["patterns"] == ["ma_cross_5_20", "breakout_20d", "breakout_vol", "rsi_rebound", "bb_lower_recover"]
    assert o["min_trades"] == {"default": 300, "min": 30, "max": 5000}
    assert 0 <= o["target_win_rate_default"] <= 1
    assert o["axes"]["max_hold_days"] == [3, 5, 10, 20, 40, 60] and o["split_date"] == "2024-02-29"
    assert "not_evaluated" in o["statuses"]


def test_preview_counts_and_limit(client):
    r = client.post("/api/searches/preview", json=BODY).json()
    sec = load_config()["search"]["sec_per_candidate"]
    assert r == {"candidates": 8, "max_candidates": 200, "within_limit": True, "estimated_sec": round(8 * sec, 1)}
    big = {"name": "big", "target_win_rate": 0.5, "axes": {"stop_loss_pct": [-5, -8], "take_profit_pct": [10, 20]}}
    r = client.post("/api/searches/preview", json=big)
    assert r.status_code == 200 and r.json()["candidates"] == 228 and not r.json()["within_limit"]
    r = client.post("/api/searches", json=big)
    assert r.status_code == 422 and r.json()["code"] == "too_many_candidates"
    assert r.json()["detail"] == {"candidates": 228, "max_candidates": 200}


@pytest.mark.parametrize("body,field", [
    ({**BODY, "target_win_rate": 1.5}, "target_win_rate"),
    ({**BODY, "target_win_rate": "0.5"}, "target_win_rate"),
    ({**BODY, "min_trades": 10}, "min_trades"),
    ({**BODY, "axes": {"stop_loss_pct": [-7]}}, "axes.stop_loss_pct"),
    ({**BODY, "axes": {"patterns": ["nope"]}}, "axes.patterns[0]"),
    ({**BODY, "filters": {"period": {"start": "2021-01-04", "end": "2023-12-29"}}}, "filters.period"),
    ({**BODY, "sort_by": "x"}, "sort_by"),
])
def test_search_validation_errors(client, body, field):
    for path in ("/api/searches", "/api/searches/preview"):
        r = client.post(path, json=body)
        assert r.status_code == 422, r.text
        b = r.json()
        assert set(b) == ERROR_KEYS and b["code"] == "validation_failed"
        assert field in b["detail"]["fields"], b["detail"]["fields"]


def test_search_lifecycle(client):
    sid, snap = search_to_end(client)
    assert snap["status"] == "completed" and snap["search_id"] == sid and snap["stage"] == "save"
    assert snap["stages"] == ["generate", "explore", "evaluate", "save"] and "met_so_far" in snap
    res = client.get(f"/api/searches/{sid}/result").json()
    assert res["search_id"] == sid and res["status"] == "completed" and res["fdr_family_size"] == 8
    assert len(res["rows"]) == 8 and "not investment advice" in res["disclaimer"] and res["note"]
    assert sid in [s["search_id"] for s in client.get("/api/searches").json()["searches"]]
    assert sid not in [r.get("run_id") for r in client.get("/api/runs").json()["runs"]]  # 실행 목록과 분리
    assert client.get(f"/api/runs/{sid}").status_code == 404


def test_unknown_search(client):
    for r in (client.get("/api/searches/nope"), client.get("/api/searches/nope/result"),
              client.post("/api/searches/nope/cancel")):
        assert r.status_code == 404 and r.json()["code"] == "search_not_found"


def test_busy_slot_shared_and_cancel_keeps_record(client):
    gate = threading.Event()
    jobs = client.app.state.rl.jobs
    real = jobs._prep
    jobs._prep = lambda: (gate.wait(10), real())[1]  # 탐색을 시작 직후 붙잡아 둔다
    try:
        r = client.post("/api/searches", json=BODY)
        sid = r.json()["search_id"]
        busy = client.post("/api/runs", json={"strategies": [{"name": "x", "patterns": ["breakout_20d"]}]})
        assert busy.status_code == 409 and busy.json()["code"] == "busy"
        assert busy.json()["detail"] == {"run_id": sid, "kind": "search", "id": sid}
        busy2 = client.post("/api/searches", json=BODY)
        assert busy2.status_code == 409 and busy2.json()["detail"] == {"kind": "search", "id": sid}
        assert client.get(f"/api/searches/{sid}/result").status_code == 409
        assert client.post(f"/api/searches/{sid}/cancel").status_code == 202
    finally:
        gate.set()
        jobs._prep = real
    snap = jobs.get_search(sid)
    import time
    end = time.time() + 60
    while snap["status"] not in ("completed", "cancelled", "failed") and time.time() < end:
        time.sleep(0.05)
        snap = jobs.get_search(sid)
    assert snap["status"] == "cancelled"
    res = client.get(f"/api/searches/{sid}/result").json()
    assert res["status"] == "cancelled" and res["counts"]["processed"] < res["counts"]["candidates"]
    assert client.post(f"/api/searches/{sid}/cancel").status_code == 409  # 이미 끝남


# ---------------------------------------------------------------- 조합 저장
STRAT = {"name": "b", "patterns": ["rsi_rebound"], "exit": {"stop_loss_pct": -5, "max_hold_days": 10}}


def test_preset_crud(client):
    r = client.post("/api/presets", json={"name": "RSI 반등", "strategy": STRAT})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["revision"] == 1 and p["strategy"]["exit"]["take_profit_pct"] == 20 and p["source"]["kind"] == "manual"
    pid = p["id"]
    assert [x["id"] for x in client.get("/api/presets").json()["presets"]] == [pid]
    assert client.get(f"/api/presets/{pid}").json() == p
    u = client.put(f"/api/presets/{pid}", json={"revision": 1, "name": "RSI 반등 v2"})
    assert u.status_code == 200 and u.json()["revision"] == 2
    stale = client.put(f"/api/presets/{pid}", json={"revision": 1, "name": "옛 창"})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    dup = client.post("/api/presets", json={"name": "rsi 반등 V2", "strategy": STRAT})
    assert dup.status_code == 409 and dup.json()["code"] == "name_conflict"
    assert client.delete(f"/api/presets/{pid}").status_code == 204
    gone = client.get(f"/api/presets/{pid}")
    assert gone.status_code == 404 and gone.json()["code"] == "preset_not_found"


@pytest.mark.parametrize("body,field", [
    ({"name": "x"}, "strategy"),
    ({"name": "x", "strategy": STRAT, "from_search": {"search_id": "s", "candidate_id": "c001"}}, "strategy"),
    ({"name": "", "strategy": STRAT}, "name"),
    ({"name": "x", "strategy": {**STRAT, "exit": {"max_hold_days": 999}}}, "strategy.exit.max_hold_days"),
    ({"name": "x", "strategy": {**STRAT, "patterns": ["nope"]}}, "strategy.patterns[0]"),
])
def test_preset_validation(client, body, field):
    r = client.post("/api/presets", json=body)
    assert r.status_code == 422 and r.json()["code"] == "validation_failed"
    assert field in r.json()["detail"]["fields"], r.json()["detail"]["fields"]


def test_preset_from_search_and_rerun(client):
    sid, _ = search_to_end(client)
    row = client.get(f"/api/searches/{sid}/result").json()["rows"][0]
    r = client.post("/api/presets", json={"name": "탐색 결과", "from_search": {"search_id": sid,
                                                                          "candidate_id": row["id"]}})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["source"]["search_id"] == sid and p["source"]["candidate_id"] == row["id"]
    run = client.post("/api/runs", json={"strategies": [p["strategy"]]})  # 저장한 조합을 그대로 정방향 실행
    assert run.status_code == 202, run.text
    for sid2, cid, code in (("nope", "c001", "search_not_found"), (sid, "c999", "candidate_not_found")):
        r = client.post("/api/presets", json={"name": f"x{cid}{sid2[:3]}",
                                              "from_search": {"search_id": sid2, "candidate_id": cid}})
        assert r.status_code == 404 and r.json()["code"] == code


# ---------------------------------------------------------------- P1-4 패턴 수치
def test_pattern_params_in_meta_and_options(client):
    m = client.get("/api/meta").json()
    assert m["pattern_params"]["rsi_rebound"]["threshold"] == {"default": 30, "min": 10, "max": 50}
    assert m["pattern_params"]["breakout_vol"]["volume_mult"]["default"] == 2.0
    o = client.get("/api/searches/options").json()
    assert o["pattern_axes"]["rsi_rebound"]["threshold"] == [20, 25, 30, 35, 40]


def test_run_with_pattern_params(client):
    body = {"strategies": [{"name": "pp", "patterns": ["rsi_rebound"], "pattern_params": {"rsi_rebound": {"threshold": 25}}}]}
    r = client.post("/api/runs", json=body)
    assert r.status_code == 202, r.text
    bad = {"strategies": [{"name": "pp", "patterns": ["rsi_rebound"], "pattern_params": {"rsi_rebound": {"threshold": 90}}}]}
    r = client.post("/api/runs", json=bad)
    assert r.status_code == 422 and "strategies[0].pattern_params.rsi_rebound.threshold" in r.json()["detail"]["fields"]
    bad2 = {"strategies": [{"name": "pp", "patterns": ["rsi_rebound"], "pattern_params": {"rsi_rebound": {"foo": 10}}}]}
    r = client.post("/api/runs", json=bad2)
    assert r.status_code == 422 and "strategies[0].pattern_params.rsi_rebound.foo" in r.json()["detail"]["fields"]
    bad3 = {"strategies": [{"name": "pp", "patterns": ["ma_cross_5_20"],
                            "pattern_params": {"ma_cross_5_20": {"fast": 30, "slow": 20}}}]}
    r = client.post("/api/runs", json=bad3)
    assert r.status_code == 422 and "strategies[0].pattern_params.ma_cross_5_20.fast" in r.json()["detail"]["fields"]


def test_search_preview_with_pattern_axis(client):
    body = {**BODY, "axes": {**BODY["axes"], "pattern_params": {"rsi_rebound": {"threshold": [25, 35]}}}}
    r = client.post("/api/searches/preview", json=body)
    assert r.status_code == 200 and r.json()["candidates"] == 7 * 2  # (bo 1 + rsi 2 + and·or 2·2) × 보유일 2
    bad = {**BODY, "axes": {**BODY["axes"], "pattern_params": {"rsi_rebound": {"threshold": [27]}}}}
    r = client.post("/api/searches/preview", json=bad)
    assert r.status_code == 422 and "axes.pattern_params.rsi_rebound.threshold" in r.json()["detail"]["fields"]


def test_preset_with_pattern_params(client):
    r = client.post("/api/presets", json={"name": "수치", "strategy": {"patterns": ["breakout_vol"],
                                                                     "pattern_params": {"breakout_vol": {"volume_mult": 1.5}}}})
    assert r.status_code == 201, r.text
    assert r.json()["strategy"]["pattern_params"] == {"breakout_vol": {"volume_mult": 1.5, "lookback": 20}}
