"""실행 작업 관리자 (FR-X2·X3, C2, E4).

- 프로세스 내 계산 스레드 1개. 실행·탐색은 선착순 대기열을 공유한다 (X13, F1b).
- 작업 종류: run(정방향 실행)과 search(역방향 탐색, P1-7). 두 종류가 같은 슬롯을 쓴다(동시에 1개).
  탐색 상태는 runs/searches/.status/ 에 따로 두어 실행 목록과 섞이지 않는다.
- 탐색 취소는 그때까지 처리한 후보를 cancelled 기록으로 남긴다(실행 취소는 결과를 남기지 않음).
- 상태는 메모리에 두고, 단계가 바뀔 때마다 runs/.status/<run_id>.json 에 기록한다 (새로고침 복구, NFR-13).
- 서버 재시작 시 running/queued 상태로 남은 작업은 failed(server_restarted) 로 바꾼다.
- 취소는 협조적이다: 엔진이 단계·종목 루프·무작위 반복 사이에서 확인하며, 취소 시 결과 폴더를 남기지 않는다.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from regime_lab.context import SEARCH_STAGES, STAGES, RecordingContext, RunCancelled
from regime_lab.runs import Strategy, make_run_id, run_and_save
from regime_lab.search import SearchRequest, make_search_id, run_and_save_search, searches_dir

TERMINAL = ("completed", "failed", "cancelled")


class Busy(Exception):
    def __init__(self, run_id: str, kind: str = "run"):
        self.run_id, self.kind = run_id, kind


class QueueFull(Exception):
    pass


class _JobContext(RecordingContext):
    def __init__(self, job: "Job", on_change, stage_names=STAGES):
        super().__init__(stage_names=stage_names)
        self.job, self.on_change = job, on_change

    def stage(self, name, detail=None):
        super().stage(name, detail)
        self.on_change(self.job)


@dataclass
class Job:
    run_id: str
    strategies: list[str]
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None
    error: dict | None = None
    ctx: RecordingContext | None = None
    elapsed_final: float | None = None
    kind: str = "run"
    requester: str = ""
    queue_position: int | None = 0

    def snapshot(self) -> dict:
        stages = SEARCH_STAGES if self.kind == "search" else STAGES
        snap = self.ctx.snapshot() if self.ctx else {"stage": None, "stages": stages, "processed": 0, "total": 0,
                                                     "elapsed_sec": 0.0}
        if self.elapsed_final is not None:
            snap["elapsed_sec"] = self.elapsed_final
        head = ({"search_id": self.run_id, "name": self.strategies[0]} if self.kind == "search"
                else {"run_id": self.run_id, "strategies": self.strategies})
        return {**head, "status": self.status, "created_at": self.created_at, "finished_at": self.finished_at,
                "error": self.error, "queue_position": self.queue_position if self.status == "queued" else None, **snap}


class JobManager:
    def __init__(self, runs_dir: Path, prep_getter, cfg: dict, paths, tickers=None):
        self.runs_dir = runs_dir
        self.status_dir = runs_dir / ".status"
        self.status_dir.mkdir(parents=True, exist_ok=True)
        self.search_dir = searches_dir(paths)
        self.search_status_dir = self.search_dir / ".status"
        self.search_status_dir.mkdir(parents=True, exist_ok=True)
        self._prep = prep_getter
        self.cfg, self.paths, self.tickers = cfg, paths, tickers
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._current: Job | None = None
        self._pending: deque[tuple[Job, object]] = deque()
        self.queue_max = cfg["jobs"]["queue_max"]
        self.per_user = cfg["jobs"]["per_user"]
        if (type(self.queue_max) is not int or self.queue_max < 0
                or type(self.per_user) is not int or self.per_user < 1):
            raise ValueError("jobs.queue_max must be a nonnegative integer; jobs.per_user must be a positive integer")
        self._recover()

    # ------------------------------------------------------------------ 상태 파일
    def _write(self, job: Job) -> None:
        path = (self.search_status_dir if job.kind == "search" else self.status_dir) / f"{job.run_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.snapshot(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _recover(self) -> None:
        for f in [*self.status_dir.glob("*.json"), *self.search_status_dir.glob("*.json")]:
            try:
                snap = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if snap.get("status") not in TERMINAL:
                snap.update(status="failed", finished_at=datetime.now().isoformat(timespec="seconds"),
                            queue_position=None,
                            error={"code": "server_restarted", "message": "The server restarted while this run was in progress."})
                f.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        for d in [*self.runs_dir.glob(".tmp_*"), *self.search_dir.glob(".tmp_*")]:  # 중단된 작업의 임시 폴더 정리
            import shutil

            shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------------ 조회
    def get(self, run_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(run_id)
            if job and job.kind == "run":
                return job.snapshot()
        f = self.status_dir / f"{run_id}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        if (self.runs_dir / run_id / "result.json").exists():  # CLI 로 만든 결과
            return {"run_id": run_id, "status": "completed", "stage": "save", "stages": STAGES,
                    "processed": 0, "total": 0, "error": None}
        return None

    def list(self, limit: int = 50) -> list[dict]:
        seen, out = set(), []
        for f in sorted(self.status_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            snap = self.get(f.stem)
            if snap:
                out.append(snap)
                seen.add(f.stem)
        for d in sorted((p for p in self.runs_dir.iterdir()
                         if p.is_dir() and not p.name.startswith(".") and p != self.search_dir),
                        key=lambda p: p.stat().st_mtime, reverse=True):
            if d.name not in seen and (d / "result.json").exists():
                out.append(self.get(d.name))
        return out[:limit]

    # ------------------------------------------------------------------ 삭제 (되살릴 수 있게 runs/.deleted/ 로 옮긴다, 10-13 결정 E20)
    def delete(self, run_id: str) -> str:
        """끝난 실행(완료·취소·실패)의 결과 폴더와 상태 파일을 runs/.deleted/<run_id>/ 로 옮긴다. 옮긴 위치를 돌려준다.

        KeyError: 없는 실행 · RuntimeError(status): 실행 중·대기 중 · OSError: 옮기지 못함(원래 자리 그대로)
        """
        import shutil

        if not run_id or run_id.startswith(".") or "/" in run_id or "\\" in run_id or run_id == self.search_dir.name:
            raise KeyError(run_id)
        with self._lock:
            snap = self.get_locked(run_id)
            if snap is None:
                raise KeyError(run_id)
            if snap.get("status") not in TERMINAL:
                raise RuntimeError(snap.get("status"))
            trash = self.runs_dir / ".deleted"
            trash.mkdir(exist_ok=True)
            dest = trash / run_id
            if dest.exists():
                dest = trash / f"{run_id}_{datetime.now():%Y%m%dT%H%M%S}"
            src = self.runs_dir / run_id
            status = self.status_dir / f"{run_id}.json"
            moved = False
            if src.exists():
                shutil.move(str(src), str(dest))
                moved = True
            else:
                dest.mkdir()
            try:
                if status.exists():
                    shutil.move(str(status), str(dest / "_status.json"))
            except OSError:
                if moved:  # 반쯤 옮긴 상태로 남기지 않는다
                    shutil.move(str(dest), str(src))
                else:
                    shutil.rmtree(dest, ignore_errors=True)
                raise
            job = self._jobs.pop(run_id, None)
            if job is not None and self._current is job:
                self._current = None
        return dest.relative_to(self.runs_dir).as_posix()

    def get_locked(self, run_id: str) -> dict | None:
        """get() 과 같지만 이미 _lock 을 잡은 상태에서 부른다."""
        job = self._jobs.get(run_id)
        if job and job.kind == "run":
            return job.snapshot()
        f = self.status_dir / f"{run_id}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        if (self.runs_dir / run_id / "result.json").exists():
            return {"run_id": run_id, "status": "completed"}
        return None

    @property
    def current(self) -> Job | None:
        with self._lock:
            return self._current

    # ------------------------------------------------------------------ 실행·취소
    def _check_capacity(self, requester: str) -> None:
        own = [j for j in self._jobs.values() if j.requester == requester and j.status not in TERMINAL]
        if len(own) >= self.per_user:
            raise Busy(own[0].run_id, own[0].kind)
        if self._current is not None and len(self._pending) >= self.queue_max:
            raise QueueFull()

    def _positions(self) -> None:
        for i, (job, _) in enumerate(self._pending, 1):
            job.queue_position = i
            self._write(job)

    def _enqueue(self, job: Job, payload) -> None:
        # Caller holds _lock; persist acceptance before making the job runnable.
        job.queue_position = len(self._pending) + 1 if self._current is not None else 0
        self._write(job)
        self._jobs[job.run_id] = job
        if self._current is None:
            self._current = job
            threading.Thread(target=self._work, args=(job, payload), name="analysis-worker", daemon=True).start()
        else:
            self._pending.append((job, payload))

    def _work(self, job: Job, payload) -> None:
        while True:
            try:
                with self._lock:
                    if job.status != "cancelled":
                        job.ctx = _JobContext(job, self._write, SEARCH_STAGES if job.kind == "search" else STAGES)
                        job.status = "running"
                        job.queue_position = None
                if job.status != "cancelled":
                    if job.kind == "search":
                        self._run_search(job, payload)
                    else:
                        self._run(job, payload)
            finally:
                with self._lock:
                    if not self._pending:
                        self._current = None
                        return
                    job, payload = self._pending.popleft()
                    self._current = job
                    job.queue_position = 0
                    self._write(job)
                    self._positions()

    def _data_version(self) -> str:
        """원본 + 워밍업 파일 지문 (NFR-10). 실행·탐색 id 와 기록의 data_version 과 같은 기준."""
        from regime_lab.config import config_hash
        from regime_lab.data.loader import input_file_hashes, warmup_file_hashes

        return config_hash({**input_file_hashes(self.paths.store), **warmup_file_hashes(self.paths.cache)})[:8]

    def submit(self, strategies: list[Strategy], requester: str = "") -> Job:
        with self._lock:
            self._check_capacity(requester)
            run_id = make_run_id(strategies, self.cfg, self._data_version())
            while ((self.runs_dir / run_id).exists() or (self.status_dir / f"{run_id}.json").exists()
                   or run_id in self._jobs):
                time.sleep(1.0)  # run_id 는 초 단위 시각을 포함한다
                run_id = make_run_id(strategies, self.cfg, self._data_version())
            job = Job(run_id, [s.name for s in strategies], requester=requester)
            self._enqueue(job, strategies)
        return job

    def _run(self, job: Job, strategies: list[Strategy]) -> None:
        try:
            self._write(job)
            run_and_save(strategies, self._prep(), self.cfg, self.paths, self.tickers, run_id=job.run_id, ctx=job.ctx)
            job.status = "completed"
        except RunCancelled:
            job.status = "cancelled"
            job.error = {"code": "cancelled", "message": "The run was cancelled by the user. No result was generated."}
        except Exception as e:  # noqa: BLE001 — 실패 사유를 상태로 전달
            job.status = "failed"
            job.error = {"code": "run_failed", "message": f"{type(e).__name__}: {e}",
                         "trace": traceback.format_exc(limit=3)}
        finally:
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            job.elapsed_final = job.ctx.snapshot()["elapsed_sec"] if job.ctx else None
            self._write(job)

    # ------------------------------------------------------------------ 탐색 (P1-7)
    def submit_search(self, req: SearchRequest, requester: str = "") -> Job:
        with self._lock:
            self._check_capacity(requester)
            data_ver = self._data_version()
            sid = make_search_id(req, self.cfg, data_ver)
            while ((self.search_dir / sid).exists() or (self.search_status_dir / f"{sid}.json").exists()
                   or sid in self._jobs):
                time.sleep(1.0)  # search_id 는 초 단위 시각을 포함한다
                sid = make_search_id(req, self.cfg, data_ver)
            job = Job(sid, [req.name], kind="search", requester=requester)
            self._enqueue(job, req)
        return job

    def _run_search(self, job: Job, req: SearchRequest) -> None:
        try:
            self._write(job)
            out = run_and_save_search(req, self._prep(), self.cfg, self.paths, search_id=job.run_id, ctx=job.ctx)
            job.status = json.loads((out / "meta.json").read_text(encoding="utf-8"))["status"]  # completed·cancelled
            if job.status == "cancelled":
                job.error = {"code": "cancelled",
                             "message": "The search was cancelled. Candidates processed so far were recorded."}
        except Exception as e:  # noqa: BLE001 — 실패 사유를 상태로 전달
            job.status = "failed"
            job.error = {"code": "search_failed", "message": f"{type(e).__name__}: {e}",
                         "trace": traceback.format_exc(limit=3)}
        finally:
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            job.elapsed_final = job.ctx.snapshot()["elapsed_sec"] if job.ctx else None
            self._write(job)

    def get_search(self, search_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(search_id)
            if job and job.kind == "search":
                return job.snapshot()
        f = self.search_status_dir / f"{search_id}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return None

    def list_searches(self, limit: int = 50) -> list[dict]:
        files = sorted(self.search_status_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [s for s in (self.get_search(f.stem) for f in files[:limit]) if s]

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(run_id)
            if job is None or job.status in TERMINAL:
                return False
            if job.status == "queued":
                self._pending = deque((j, p) for j, p in self._pending if j is not job)
                job.status = "cancelled"
                job.finished_at = datetime.now().isoformat(timespec="seconds")
                job.elapsed_final = 0.0
                job.error = {"code": "cancelled_before_start", "message": "Cancelled before execution. No result was generated."}
                self._write(job)
                self._positions()
            else:
                job.ctx.cancel()
            return True

    def wait(self, run_id: str, timeout: float = 120.0) -> dict:
        """테스트·CLI 용: 종료 상태까지 기다린다."""
        end = time.time() + timeout
        while time.time() < end:
            snap = self.get(run_id)
            if snap and snap["status"] in TERMINAL:
                return snap
            time.sleep(0.05)
        raise TimeoutError(run_id)
