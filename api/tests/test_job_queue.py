"""X13: 실제 작업 관리자·HTTP 계약을 원본·계정·외부 요청 없이 검사한다."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from regime_api.jobs import Busy, JobManager, QueueFull
from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab.config import Paths, load_config
from test_auth_contract import auth_app, signin

RUN = {"strategies": [{"name": "first", "patterns": ["breakout_20d"]}]}
SEARCH = {"name": "second", "target_win_rate": 0.45, "min_trades": 30,
          "axes": {"patterns": ["breakout_20d"], "max_hold_days": [5]}}


@pytest.fixture
def runner(monkeypatch):
    gate, started = threading.Event(), threading.Event()
    calls = []
    active = [0, 0]

    def compute(name, ctx):
        active[0] += 1
        active[1] = max(active)
        calls.append(name)
        try:
            if len(calls) == 1:
                started.set()
                assert gate.wait(10), "test gate timed out"
            ctx.check_cancel()
            if name == "fail":
                raise ValueError("synthetic failure")
        finally:
            active[0] -= 1

    def run(strategies, prep, cfg, paths, tickers, *, run_id, ctx):
        compute(strategies[0].name, ctx)

    def search(req, prep, cfg, paths, *, search_id, ctx):
        compute(req.name, ctx)
        out = paths.runs / "searches" / search_id
        out.mkdir(parents=True)
        (out / "meta.json").write_text('{"status":"completed"}', encoding="utf-8")
        return out

    monkeypatch.setattr("regime_api.jobs.run_and_save", run)
    monkeypatch.setattr("regime_api.jobs.run_and_save_search", search)
    monkeypatch.setattr(JobManager, "_data_version", lambda self: "synthetic")
    yield SimpleNamespace(gate=gate, started=started, calls=calls, active=active)
    gate.set()


@pytest.fixture
def queue_app(tmp_path, runner):
    paths = Paths(tmp_path / "store", tmp_path / "dump", tmp_path / "cache", tmp_path / "runs")
    app = create_app(Settings(paths=paths), prep=object(), rag=object(), serve_web=False)
    app.state.rl.jobs.queue_max = 2
    yield app


def wait_search(jobs, sid):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        snap = jobs.get_search(sid)
        if snap["status"] in ("completed", "cancelled", "failed"):
            return snap
        time.sleep(0.01)
    raise AssertionError("search timed out")


def test_http_fifo_shared_queue_failure_and_positions(queue_app, runner):
    jobs = queue_app.state.rl.jobs
    with (TestClient(queue_app, client=("127.0.0.1", 1)) as a,
          TestClient(queue_app, client=("127.0.0.2", 1)) as b,
          TestClient(queue_app, client=("127.0.0.3", 1)) as c,
          TestClient(queue_app, client=("127.0.0.4", 1)) as d):
        first = a.post("/api/runs", json={"strategies": [{"name": "fail", "patterns": ["breakout_20d"]}]})
        assert first.status_code == 202 and first.json()["status"] == "queued"
        assert runner.started.wait(2)
        rid = first.json()["run_id"]
        second = b.post("/api/searches", json=SEARCH)
        third = c.post("/api/runs", json=RUN)
        assert second.status_code == third.status_code == 202
        sid, rid3 = second.json()["search_id"], third.json()["run_id"]
        assert second.json()["queue_position"] == 1 and third.json()["queue_position"] == 2
        assert jobs.get_search(sid)["elapsed_sec"] == 0
        assert jobs.get(rid3)["queue_position"] == 2
        dup = b.post("/api/runs", json=RUN)
        assert dup.status_code == 409 and dup.json()["code"] == "user_busy"
        assert dup.json()["detail"]["id"] == sid
        full = d.post("/api/searches", json=SEARCH)
        assert full.status_code == 429 and full.json() == {
            "code": "queue_full", "message": "The job queue is full. Try again after a job finishes.",
            "detail": None, "retryable": True}
        assert runner.calls == ["fail"]
        runner.gate.set()
        assert jobs.wait(rid)["status"] == "failed"
        assert wait_search(jobs, sid)["status"] == "completed"
        assert jobs.wait(rid3)["status"] == "completed"
        assert runner.calls == ["fail", "second", "first"] and runner.active[1] == 1
        assert jobs.get(rid3)["queue_position"] is None
        spec = a.get("/openapi.json").json()
        assert "queue_position" in spec["components"]["schemas"]["RunAccepted"]["properties"]


def test_waiting_cancel_never_executes_or_creates_result(queue_app, runner):
    jobs = queue_app.state.rl.jobs
    with (TestClient(queue_app, client=("127.0.0.1", 1)) as a,
          TestClient(queue_app, client=("127.0.0.2", 1)) as b,
          TestClient(queue_app, client=("127.0.0.3", 1)) as c):
        rid = a.post("/api/runs", json=RUN).json()["run_id"]
        assert runner.started.wait(2)
        sid = b.post("/api/searches", json=SEARCH).json()["search_id"]
        rid3 = c.post("/api/runs", json=RUN).json()["run_id"]
        assert b.post(f"/api/searches/{sid}/cancel").status_code == 202
        snap = jobs.get_search(sid)
        assert snap["status"] == "cancelled" and snap["error"]["code"] == "cancelled_before_start"
        assert snap["elapsed_sec"] == 0 and snap["queue_position"] is None
        assert not (jobs.search_dir / sid).exists()
        assert b.get(f"/api/searches/{sid}/result").json()["code"] == "search_cancelled"
        assert jobs.get(rid3)["queue_position"] == 1
        disk = json.loads((jobs.status_dir / f"{rid3}.json").read_text(encoding="utf-8"))
        assert disk["queue_position"] == 1
        assert c.post(f"/api/runs/{rid3}/cancel").status_code == 202
        assert not (jobs.runs_dir / rid3).exists()
        # Cancelling frees this requester's slot immediately; the engine never sees the removed jobs.
        replacement = b.post("/api/searches", json={**SEARCH, "name": "replacement"})
        assert replacement.status_code == 202 and replacement.json()["queue_position"] == 1
        runner.gate.set()
        assert jobs.wait(rid)["status"] == "completed"
        assert wait_search(jobs, replacement.json()["search_id"])["status"] == "completed"
        assert runner.calls == ["first", "replacement"]


def test_authenticated_queue_is_private_and_limited_by_user_id(auth_app, runner):
    state = auth_app.state.rl
    state.jobs = JobManager(state.paths.runs, state.require_prep, state.cfg, state.paths)
    with TestClient(auth_app) as a, TestClient(auth_app) as b, TestClient(auth_app) as same_user:
        ha, hb, ha2 = signin(a, "a"), signin(b, "b"), signin(same_user, "a")
        rid = a.post("/api/runs", json=RUN, headers=ha).json()["run_id"]
        assert runner.started.wait(2)
        response = b.post("/api/searches", json=SEARCH, headers=hb)
        assert response.status_code == 202, response.text
        sid = response.json()["search_id"]
        assert response.json()["queue_position"] == 1
        own = same_user.post("/api/searches", json=SEARCH, headers=ha2)
        assert own.status_code == 409 and own.json()["code"] == "user_busy"
        assert own.json()["detail"]["id"] == rid
        for client, headers, foreign, collection in ((a, ha, sid, "searches"), (b, hb, rid, "runs")):
            assert client.get(f"/api/{collection}/{foreign}").status_code == 404
            assert client.post(f"/api/{collection}/{foreign}/cancel", headers=headers).status_code == 404
            assert client.get(f"/api/{collection}").json()[collection] == []
        assert "requester" not in b.get(f"/api/searches/{sid}").text
        assert b.post(f"/api/searches/{sid}/cancel", headers=hb).status_code == 202
        runner.gate.set()
        assert state.jobs.wait(rid)["status"] == "completed"


def test_concurrent_acceptance_respects_capacity(queue_app, runner):
    jobs = queue_app.state.rl.jobs
    from regime_lab.runs import Strategy
    strategy = [Strategy.from_dict(RUN["strategies"][0])]
    first = jobs.submit(strategy, requester="first")
    assert runner.started.wait(2)
    barrier = threading.Barrier(8)

    def submit(i):
        barrier.wait(5)
        try:
            return jobs.submit(strategy, requester=str(i))
        except QueueFull:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = [j for j in pool.map(submit, range(8)) if j is not None]
    assert len(accepted) == 2
    assert sorted(jobs.get(j.run_id)["queue_position"] for j in accepted) == [1, 2]
    with pytest.raises(Busy):
        jobs.submit(strategy, requester=accepted[0].requester)
    for job in accepted:
        assert jobs.cancel(job.run_id)
    runner.gate.set()
    assert jobs.wait(first.run_id)["status"] == "completed"


def test_restart_fails_queued_and_running_without_replaying(tmp_path):
    paths = Paths(tmp_path / "store", tmp_path / "dump", tmp_path / "cache", tmp_path / "runs")
    for folder, rid, status in ((paths.runs / ".status", "r", "running"),
                                (paths.runs / "searches" / ".status", "s", "queued")):
        folder.mkdir(parents=True)
        (folder / f"{rid}.json").write_text(json.dumps({"status": status, "queue_position": 2}), encoding="utf-8")
    jobs = JobManager(paths.runs, lambda: pytest.fail("must not replay"), load_config(), paths)
    for snap in (jobs.get("r"), jobs.get_search("s")):
        assert snap["status"] == "failed" and snap["error"]["code"] == "server_restarted"
        assert snap["queue_position"] is None
    assert jobs.current is None
