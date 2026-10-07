"""수집 저장소 (인프라, plan/03-04 §7.3·§7.4).

<ext_store>/
  raw/<source>/<YYYY-MM-DD>/<run_id>.jsonl.gz     원본 응답(목록 메타데이터), 추가만
  docs/source=<source>/date=<YYYY-MM>/part-<run_id>.parquet   정규화 문서, 추가만 (버전은 새 행)
  state.sqlite                                     ingest_runs · coverage · cursors · seen_keys · report_chain
  locks/<source>.lock                              같은 출처 동시 실행 방지
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
CREATE TABLE IF NOT EXISTS coverage (source TEXT, day TEXT, corp_cls TEXT, state TEXT, run_id TEXT,
  PRIMARY KEY (source, day, corp_cls));
CREATE TABLE IF NOT EXISTS cursors (source TEXT, mode TEXT, value TEXT, PRIMARY KEY (source, mode));
CREATE TABLE IF NOT EXISTS seen_keys (doc_id TEXT PRIMARY KEY, version INTEGER, content_hash TEXT);
CREATE TABLE IF NOT EXISTS report_chain (corp_code TEXT, report_base TEXT, doc_id TEXT,
  PRIMARY KEY (corp_code, report_base));
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
    ("ticker_status", pa.string()), ("content_hash", pa.string()), ("cluster_id", pa.string()),
    ("license_scope", pa.string()), ("ingest_run_id", pa.string()), ("rcept_no", pa.string()),
    ("corp_code", pa.string()), ("corp_name", pa.string()), ("corp_cls", pa.string()), ("flr_nm", pa.string()),
    ("rm", pa.string()), ("report_tags", pa.list_(pa.string())), ("report_base", pa.string()),
    ("is_amendment", pa.bool_()), ("amends_doc_id", pa.string()),
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

    def save_docs(self, source: str, run_id: str, docs: list[dict]):
        by_month = defaultdict(list)
        for d in docs:
            by_month[d["published_at"][:7]].append(d)
        for month, rows in by_month.items():
            d = self.root / "docs" / f"source={source}" / f"date={month}"
            d.mkdir(parents=True, exist_ok=True)
            p, i = d / f"part-{run_id}.parquet", 1
            while p.exists():  # 같은 실행이 같은 달을 여러 번 쓰면 조각을 늘린다 (덮어쓰지 않음)
                p, i = d / f"part-{run_id}-{i}.parquet", i + 1
            pq.write_table(pa.Table.from_pylist(rows, schema=DOC_SCHEMA), p)

    def read_docs(self, source: str) -> list[dict]:
        base = self.root / "docs" / f"source={source}"
        files = sorted(base.rglob("*.parquet")) if base.exists() else []
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

    def report_chain(self) -> dict[tuple[str, str], str]:
        return {(c, b): d for c, b, d in self.db.execute("SELECT corp_code, report_base, doc_id FROM report_chain")}

    def save_report_chain(self, chain: dict[tuple[str, str], str]):
        self.db.executemany("INSERT OR REPLACE INTO report_chain VALUES (?, ?, ?)",
                            [(c, b, d) for (c, b), d in chain.items()])

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
