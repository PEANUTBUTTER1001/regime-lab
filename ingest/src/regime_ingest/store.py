"""수집 저장소 (인프라, plan/03-04 §7.3·§7.4).

<ext_store>/
  raw/<source>/<YYYY-MM-DD>/<run_id>.jsonl.gz     원본 응답(목록 메타데이터), 추가만
  docs/source=<source>/date=<YYYY-MM>/part-<run_id>.parquet   정규화 문서, 추가만 (버전은 새 행)
  state.sqlite                                     ingest_runs · coverage · cursors · seen_keys · report_history · doc_files
  locks/<source>.lock                              같은 출처 동시 실행 방지

문서 파일과 SQLite 상태를 함께 확정하는 순서 (중간에 죽어도 중복·부분 파일이 남지 않게):
  1) docs 를 `*.parquet.tmp` 로 쓴다  2) seen_keys 등과 함께 doc_files 에 최종 이름을 넣고 commit
  3) tmp → 최종 이름으로 바꾼다. 남은 tmp 정리(recover)는 **같은 출처의 배타 잠금을 잡은 뒤에만** 한다
     (lock() 안에서): doc_files 에 있으면(commit 됨) 이름을 바꾸고, 없으면(commit 전 실패) 지운다.
     읽기(read_docs·status)는 파일을 바꾸지 않는다 — *.parquet 와, commit 됐지만 아직 이름을 못 바꾼 tmp 만 읽는다.
     그래서 다른 프로세스가 저장하는 중에 읽어도 그쪽 tmp 를 지우지 않는다 (hchee99-codex 리뷰).
"""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from regime_ingest.dedup import Seen

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_runs (run_id TEXT PRIMARY KEY, source TEXT, mode TEXT, window_from TEXT,
  window_to TEXT, started_at TEXT, finished_at TEXT, requests INTEGER, received INTEGER, new INTEGER,
  duplicate INTEGER, changed INTEGER, errors INTEGER, status TEXT, message TEXT);
-- coverage.state: collected(소급으로 그 날 전체) · forward(순방향으로 일부) · partial(잘못된 행) · gap(실패)
CREATE TABLE IF NOT EXISTS coverage (source TEXT, day TEXT, corp_cls TEXT, state TEXT, run_id TEXT,
  PRIMARY KEY (source, day, corp_cls));
CREATE TABLE IF NOT EXISTS cursors (source TEXT, mode TEXT, value TEXT, PRIMARY KEY (source, mode));
CREATE TABLE IF NOT EXISTS seen_keys (doc_id TEXT PRIMARY KEY, version INTEGER, content_hash TEXT);
CREATE TABLE IF NOT EXISTS report_history (corp_code TEXT, report_base TEXT, rcept_no TEXT, doc_id TEXT,
  PRIMARY KEY (corp_code, report_base, rcept_no));
CREATE TABLE IF NOT EXISTS doc_files (path TEXT PRIMARY KEY, run_id TEXT);
CREATE TABLE IF NOT EXISTS request_days (source TEXT, day TEXT, requests INTEGER, PRIMARY KEY (source, day));
"""

TICKER = pa.struct([("code", pa.string()), ("method", pa.string()), ("evidence", pa.string()),
                    ("confidence", pa.float64())])
DOC_SCHEMA = pa.schema([
    ("doc_id", pa.string()), ("version", pa.int64()), ("source", pa.string()), ("source_type", pa.string()),
    ("title", pa.string()), ("summary", pa.string()), ("body_ref", pa.string()), ("url", pa.string()),
    ("original_url", pa.string()), ("published_at", pa.string()), ("published_at_basis", pa.string()),
    ("time_precision", pa.string()), ("modified_at", pa.string()), ("first_seen_at", pa.string()),
    ("available_at", pa.string()), ("backfilled", pa.bool_()), ("tickers", pa.list_(TICKER)),
    ("ticker_status", pa.string()), ("stock_code_raw", pa.string()), ("content_hash", pa.string()), ("cluster_id", pa.string()),
    ("license_scope", pa.string()), ("ingest_run_id", pa.string()), ("rcept_no", pa.string()),
    ("corp_code", pa.string()), ("corp_name", pa.string()), ("corp_cls", pa.string()), ("flr_nm", pa.string()),
    ("rm", pa.string()), ("report_tags", pa.list_(pa.string())), ("report_base", pa.string()),
    ("is_amendment", pa.bool_()), ("amends_candidate_doc_id", pa.string()), ("amends_basis", pa.string()),
])


class SourceLocked(RuntimeError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "state.sqlite")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # ---------------------------------------------------------------- 잠금
    def lock(self, source: str):
        d = self.root / "locks"
        d.mkdir(exist_ok=True)
        p = d / f"{source}.lock"
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SourceLocked(f"{source} 수집이 이미 실행 중 (잠금 파일 {p})") from None
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        self.recover()  # 잠금을 잡은 쪽만 남은 staging 을 정리한다
        return p

    @staticmethod
    def unlock(p: Path):
        p.unlink(missing_ok=True)

    # ---------------------------------------------------------------- 원본·문서
    def save_raw(self, source: str, day: str, run_id: str, records: list[dict]):
        d = self.root / "raw" / source / day
        d.mkdir(parents=True, exist_ok=True)
        with gzip.open(d / f"{run_id}.jsonl.gz", "at", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def stage_docs(self, source: str, run_id: str, docs: list[dict]) -> list[Path]:
        """1단계: 달별 tmp 파일을 쓰고 doc_files 에 최종 이름을 넣는다 (commit 은 호출자). 최종 경로 목록을 돌려준다."""
        by_month = defaultdict(list)
        for d in docs:
            by_month[d["published_at"][:7]].append(d)
        final = []
        for month, rows in sorted(by_month.items()):
            d = self.root / "docs" / f"source={source}" / f"date={month}"
            d.mkdir(parents=True, exist_ok=True)
            p, i = d / f"part-{run_id}.parquet", 1
            while p.exists() or self._tmp(p).exists():  # 같은 실행이 같은 달을 여러 번 쓰면 조각을 늘린다
                p, i = d / f"part-{run_id}-{i}.parquet", i + 1
            pq.write_table(pa.Table.from_pylist(rows, schema=DOC_SCHEMA), self._tmp(p))
            self.db.execute("INSERT INTO doc_files VALUES (?, ?)", (str(p.relative_to(self.root)), run_id))
            final.append(p)
        return final

    def publish(self, paths: list[Path]):
        """3단계 (commit 뒤): tmp → 최종 이름."""
        for p in paths:
            os.replace(self._tmp(p), p)

    def discard(self, paths: list[Path]):
        """commit 전 실패: tmp 를 지운다 (rollback 과 짝)."""
        for p in paths:
            self._tmp(p).unlink(missing_ok=True)

    def recover(self):
        committed = {r[0] for r in self.db.execute("SELECT path FROM doc_files")}
        for t in (self.root / "docs").rglob("*.parquet.tmp") if (self.root / "docs").exists() else []:
            final = t.with_suffix("")
            if str(final.relative_to(self.root)) in committed:
                os.replace(t, final)
            else:
                t.unlink()

    @staticmethod
    def _tmp(p: Path) -> Path:
        return p.with_suffix(p.suffix + ".tmp")

    def read_docs(self, source: str) -> list[dict]:
        """읽기 전용: 최종 파일 + commit 됐지만 이름을 아직 못 바꾼 tmp. 진행 중(미커밋) tmp 는 보지 않는다."""
        base = self.root / "docs" / f"source={source}"
        if not base.exists():
            return []
        committed = {r[0] for r in self.db.execute("SELECT path FROM doc_files")}
        files = sorted(base.rglob("*.parquet"))
        files += sorted(t for t in base.rglob("*.parquet.tmp")
                        if str(t.with_suffix("").relative_to(self.root)) in committed and not t.with_suffix("").exists())
        return [r for f in files for r in pq.read_table(f, schema=DOC_SCHEMA).to_pylist()]

    # ---------------------------------------------------------------- 상태
    def seen(self, doc_ids: list[str]) -> dict[str, Seen]:
        out = {}
        for i in range(0, len(doc_ids), 500):
            chunk = doc_ids[i:i + 500]
            q = f"SELECT doc_id, version, content_hash FROM seen_keys WHERE doc_id IN ({','.join('?' * len(chunk))})"
            out.update({k: Seen(v, h) for k, v, h in self.db.execute(q, chunk)})
        return out

    def mark_seen(self, docs: list[dict]):
        self.db.executemany("INSERT OR REPLACE INTO seen_keys VALUES (?, ?, ?)",
                            [(d["doc_id"], d["version"], d["content_hash"]) for d in docs])

    def report_history(self, keys: set[tuple[str, str]]) -> dict[tuple[str, str], list[tuple[str, str]]]:
        out: dict = defaultdict(list)
        for c, b in keys:
            for r, d in self.db.execute("SELECT rcept_no, doc_id FROM report_history WHERE corp_code = ? AND "
                                        "report_base = ?", (c, b)):
                out[(c, b)].append((r, d))
        return dict(out)

    def add_report_history(self, rows: list[tuple[tuple[str, str], str, str]]):
        self.db.executemany("INSERT OR IGNORE INTO report_history VALUES (?, ?, ?, ?)",
                            [(c, b, r, d) for (c, b), r, d in rows])

    def set_coverage(self, source: str, days: list[str], corp_cls: str, state: str, run_id: str):
        self.db.executemany("INSERT OR REPLACE INTO coverage VALUES (?, ?, ?, ?, ?)",
                            [(source, d, corp_cls, state, run_id) for d in days])

    def coverage(self, source: str) -> dict[tuple[str, str], str]:
        return {(d, c): s for d, c, s in self.db.execute(
            "SELECT day, corp_cls, state FROM coverage WHERE source = ?", (source,))}

    def cursor(self, source: str, mode: str) -> str | None:
        r = self.db.execute("SELECT value FROM cursors WHERE source = ? AND mode = ?", (source, mode)).fetchone()
        return r[0] if r else None

    def set_cursor(self, source: str, mode: str, value: str):
        self.db.execute("INSERT OR REPLACE INTO cursors VALUES (?, ?, ?)", (source, mode, value))

    def requests_on(self, source: str, day: str) -> int:
        r = self.db.execute("SELECT requests FROM request_days WHERE source = ? AND day = ?", (source, day)).fetchone()
        return r[0] if r else 0

    def set_requests(self, source: str, day: str, n: int):
        self.db.execute("INSERT OR REPLACE INTO request_days VALUES (?, ?, ?)", (source, day, n))

    def save_run(self, run: dict):
        cols = ("run_id", "source", "mode", "window_from", "window_to", "started_at", "finished_at", "requests",
                "received", "new", "duplicate", "changed", "errors", "status", "message")
        self.db.execute(f"INSERT OR REPLACE INTO ingest_runs VALUES ({','.join('?' * len(cols))})",
                        [run.get(c) for c in cols])

    def runs(self, source: str, limit: int = 10) -> list[dict]:
        cur = self.db.execute("SELECT * FROM ingest_runs WHERE source = ? ORDER BY started_at DESC LIMIT ?",
                              (source, limit))
        names = [c[0] for c in cur.description]
        return [dict(zip(names, r)) for r in cur]

    def commit(self):
        self.db.commit()
