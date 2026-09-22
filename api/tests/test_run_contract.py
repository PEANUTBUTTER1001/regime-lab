"""test_run_contract (FR-X2~X6): run_id 일관성, 상태·취소·결과, 입력 단위, 오류 응답 형식."""

import json
import threading

import pytest
from conftest import STRAT, run_to_end

ERROR_KEYS = {"code", "message", "detail", "retryable"}


def test_meta(client):
    m = client.get("/api/meta").json()
    assert m["status"] == "ready" and m["data_as_of"] == "2026-09-18"
    assert {p["name"] for p in m["patterns"]} == {"ma_cross_5_20", "breakout_20d", "breakout_vol", "rsi_rebound",
                                                  "bb_lower_recover"}
    assert m["exit_defaults"] == {"stop_loss_pct": -8, "take_profit_pct": 20, "max_hold_days": 20,
                                  "trailing_stop_pct": None}
    assert m["min_avg_value_krw"] == 500_000_000 and m["execution"]["round_trip_cost_pct"] == 0.30
    assert len(m["stages"]) == 7 and "not investment advice" in m["disclaimer"]


def test_openapi_contract_available(client):
    spec = client.get("/openapi.json").json()
    for path in ("/api/runs", "/api/runs/{run_id}", "/api/runs/{run_id}/cancel", "/api/runs/{run_id}/result",
                 "/api/runs/{run_id}/stocks/{ticker}", "/api/runs/{run_id}/report", "/api/universe/preview",
                 "/api/briefing", "/api/meta"):
        assert path in spec["paths"], path


def test_run_lifecycle_same_run_id(client):
    run_id, snap = run_to_end(client)
    assert snap["status"] == "completed" and snap["run_id"] == run_id
    assert snap["stage"] == "save" and snap["stages"][-1] == "save"
    st = client.get(f"/api/runs/{run_id}").json()
    assert st["run_id"] == run_id and st["status"] == "completed" and st["elapsed_sec"] > 0
    res = client.get(f"/api/runs/{run_id}/result").json()
    assert res["run_id"] == run_id and res["status"] == "completed" and res["fdr_family_size"] == 1
    s0 = res["strategies"][0]
    assert s0["summary"]["trades"] == sum(b["count"] for b in s0["pnl_histogram"])
    assert "metric_definitions" in res
    tk = s0["tickers"][0]["ticker"]
    d = client.get(f"/api/runs/{run_id}/stocks/{tk}").json()
    assert d["run_id"] == run_id and d["ticker"] == tk and len(d["trades"]) == s0["tickers"][0]["trades"] + \
        sum(1 for t in d["trades"] if t["excluded"])
    listed = [r["run_id"] for r in client.get("/api/runs").json()["runs"]]
    assert run_id in listed


def test_compare_run(client):
    body = {"strategies": [STRAT, {"name": "bv", "patterns": ["breakout_vol"], "markets": ["KOSPI"]}]}
    run_id, snap = run_to_end(client, body)
    res = client.get(f"/api/runs/{run_id}/result").json()
    assert res["fdr_family_size"] == 2 and [s["strategy"] for s in res["strategies"]] == ["bo", "bv"]
    d = client.get(f"/api/runs/{run_id}/stocks/005930", params={"strategy": "bv"})
    assert d.status_code == 200 and d.json()["strategy"] == "bv"


@pytest.mark.parametrize("strategy,field", [
    ({"name": "x", "patterns": ["breakout_20d"], "period": {"start": "2021/01/04", "end": "2022-01-03"}},
     "strategies[0].period.start"),
    ({"name": "x", "patterns": ["breakout_20d"], "period": {"start": "2022-01-04", "end": "2021-01-04"}},
     "strategies[0].period"),
    ({"name": "x", "patterns": ["breakout_20d"], "min_avg_value_krw": 100000000}, "strategies[0].min_avg_value_krw"),
    ({"name": "x", "patterns": ["breakout_20d"], "min_avg_value_krw": "500000000"}, "strategies[0].min_avg_value_krw"),
    ({"name": "x", "patterns": ["breakout_20d"], "exit": {"stop_loss_pct": "-8"}}, "strategies[0].exit.stop_loss_pct"),
    ({"name": "x", "patterns": ["breakout_20d"], "exit": {"max_hold_days": 20.5}}, "strategies[0].exit.max_hold_days"),
    ({"name": "x", "patterns": ["breakout_20d"], "exit": {"max_hold_days": 0}}, "strategies[0].exit.max_hold_days"),
    ({"name": "x", "patterns": ["breakout_20d"], "exit": {"trailing_stop_pct": 5}},
     "strategies[0].exit.trailing_stop_pct"),
    ({"name": "x", "patterns": ["unknown"]}, "strategies[0].patterns[0]"),
    ({"name": "x", "patterns": ["breakout_20d"], "params": {"volume_mult": 1}}, "strategies[0].params"),
])
def test_input_validation_errors(client, strategy, field):
    r = client.post("/api/runs", json={"strategies": [strategy]})
    assert r.status_code == 422, r.text
    b = r.json()
    assert set(b) == ERROR_KEYS and b["code"] == "validation_failed"
    assert field in b["detail"]["fields"], b["detail"]["fields"]


def test_duplicate_names_and_limits(client):
    r = client.post("/api/runs", json={"strategies": [STRAT, STRAT]})
    assert r.status_code == 422 and "strategies" in r.json()["detail"]["fields"]
    r = client.post("/api/runs", json={"strategies": []})
    assert r.status_code == 422
    r = client.post("/api/runs", json={"strategies": [{**STRAT, "name": f"s{i}"} for i in range(6)]})
    assert r.status_code == 422


def test_not_found_and_not_ready(client):
    for url in ("/api/runs/nope", "/api/runs/nope/result", "/api/runs/nope/stocks/005930"):
        r = client.get(url)
        assert r.status_code == 404 and set(r.json()) == ERROR_KEYS and r.json()["code"] == "run_not_found"
    r = client.post("/api/runs/nope/cancel")
    assert r.status_code == 404


def test_busy_and_cancel(client):
    gate = threading.Event()
    jobs = client.app.state.rl.jobs
    real = jobs._prep

    def slow_prep():  # 첫 단계 진입 전에 멈춰 실행 중 상태를 만든다
        gate.wait(10)
        return real()

    jobs._prep = slow_prep
    r1 = client.post("/api/runs", json={"strategies": [STRAT]})
    run_id = r1.json()["run_id"]
    r2 = client.post("/api/runs", json={"strategies": [{**STRAT, "name": "other"}]})
    assert r2.status_code == 409 and r2.json()["code"] == "busy" and r2.json()["detail"]["run_id"] == run_id
    assert r2.json()["retryable"] is True
    c = client.post(f"/api/runs/{run_id}/cancel")
    assert c.status_code == 202 and c.json()["accepted"]
    gate.set()
    snap = jobs.wait(run_id)
    assert snap["status"] == "cancelled" and snap["error"]["code"] == "cancelled"
    r = client.get(f"/api/runs/{run_id}/result")
    assert r.status_code == 409 and r.json()["code"] == "run_cancelled"
    assert not (client.app.state.rl.paths.runs / run_id).exists()
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 409
    jobs._prep = real


def test_restart_marks_running_as_failed(make_client, client):
    status_dir = client.app.state.rl.paths.runs / ".status"
    (status_dir / "ghost.json").write_text(json.dumps({"run_id": "ghost", "status": "running"}), encoding="utf-8")
    with make_client() as c2:
        snap = c2.get("/api/runs/ghost").json()
        assert snap["status"] == "failed" and snap["error"]["code"] == "server_restarted"


def test_warming_up(make_client):
    with make_client(prep=None) as c:
        c.app.state.rl.status = "warming_up"
        c.app.state.rl.prep = None
        r = c.post("/api/runs", json={"strategies": [STRAT]})
        if r.status_code == 503:  # 백그라운드 적재가 아직 끝나지 않았을 때
            assert r.json()["code"] == "warming_up" and r.json()["retryable"]


def test_universe_preview(client):
    all_ = client.post("/api/universe/preview", json={}).json()
    k = client.post("/api/universe/preview", json={"markets": ["KOSDAQ"], "min_avg_value_krw": 2_000_000_000,
                                                    "period": {"start": "2023-01-02", "end": "2023-12-28"}}).json()
    assert 0 < k["eligible_tickers"] < all_["eligible_tickers"] <= 30
    assert k["trading_days"] > 200
    bad = client.post("/api/universe/preview", json={"min_avg_value_krw": 1})
    assert bad.status_code == 422 and "min_avg_value_krw" in bad.json()["detail"]["fields"]
