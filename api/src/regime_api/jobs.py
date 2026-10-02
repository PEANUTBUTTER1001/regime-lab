"""실행 작업 관리자 (FR-X2·X3, C2, E4).

- 프로세스 내 백그라운드 스레드 1개. 실행 중 새 요청은 409 busy + 현재 작업 id·종류 (대기열 없음).
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

    def snapshot(self) -> dict:
        stages = SEARCH_STAGES if self.kind == "search" else STAGES
        snap = self.ctx.snapshot() if self.ctx else {"stage": None, "stages": stages, "processed": 0, "total": 0,
                                                     "elapsed_sec": 0.0}
        if self.elapsed_final is not None:
            snap["elapsed_sec"] = self.elapsed_final
        head = ({"search_id": self.run_id, "name": self.strategies[0]} if self.kind == "search"
                else {"run_id": self.run_id, "strategies": self.strategies})
        return {**head, "status": self.status, "created_at": self.created_at, "finished_at": self.finished_at,
                "error": self.error, **snap}


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

    @property
    def current(self) -> Job | None:
        with self._lock:
            return self._current

    # ------------------------------------------------------------------ 실행·취소
    def _check_free(self) -> None:
        if self._current is not None and self._current.status not in TERMINAL:
            raise Busy(self._current.run_id, self._current.kind)

    def submit(self, strategies: list[Strategy]) -> Job:
        from regime_lab.config import config_hash
        from regime_lab.data.loader import input_file_hashes

        with self._lock:
            self._check_free()
            run_id = make_run_id(strategies, self.cfg, config_hash(input_file_hashes(self.paths.store))[:8])
            while (self.runs_dir / run_id).exists() or run_id in self._jobs:
                time.sleep(1.0)  # run_id 는 초 단위 시각을 포함한다
                run_id = make_run_id(strategies, self.cfg, config_hash(input_file_hashes(self.paths.store))[:8])
            job = Job(run_id, [s.name for s in strategies])
            job.ctx = _JobContext(job, self._write)
            self._jobs[run_id] = job
            self._current = job
        self._write(job)
        threading.Thread(target=self._run, args=(job, strategies), name=f"run-{run_id}", daemon=True).start()
        return job

    def _run(self, job: Job, strategies: list[Strategy]) -> None:
        job.status = "running"
        self._write(job)
        try:
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
    def submit_search(self, req: SearchRequest) -> Job:
        from regime_lab.config import config_hash
        from regime_lab.data.loader import input_file_hashes

        with self._lock:
            self._check_free()
            data_ver = config_hash(input_file_hashes(self.paths.store))[:8]
            sid = make_search_id(req, self.cfg, data_ver)
            while (self.search_dir / sid).exists() or sid in self._jobs:
                time.sleep(1.0)  # search_id 는 초 단위 시각을 포함한다
                sid = make_search_id(req, self.cfg, data_ver)
            job = Job(sid, [req.name], kind="search")
            job.ctx = _JobContext(job, self._write, SEARCH_STAGES)
            self._jobs[sid] = job
            self._current = job
        self._write(job)
        threading.Thread(target=self._run_search, args=(job, req), name=f"search-{sid}", daemon=True).start()
        return job

    def _run_search(self, job: Job, req: SearchRequest) -> None:
        job.status = "running"
        self._write(job)
        try:
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
        if job is None or job.status in TERMINAL or job.ctx is None:
            return False
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
