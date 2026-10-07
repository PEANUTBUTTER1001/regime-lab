"""조립: 시계·환경변수·HTTP·저장소를 만들어 유스케이스에 넘긴다 (R2·R3)."""

from __future__ import annotations

import os
import secrets
from datetime import date, datetime

from regime_ingest.collect import RunStats, collect
from regime_ingest.config import load_config, load_store_path
from regime_ingest.sources.opendart import OpenDartList, http_fetch
from regime_ingest.store import Store
from regime_ingest.timing import KST


def _now() -> datetime:
    return datetime.now(KST)


def _run_id() -> str:
    return _now().strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)


def _client() -> tuple[OpenDartList, dict]:
    cfg = load_config()["opendart"]
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        raise SystemExit("환경변수 DART_API_KEY 가 없습니다 (키는 파일·채팅에 쓰지 않고 환경변수로만 설정)")
    return OpenDartList(http_fetch(cfg["base_url"], key, cfg["user_agent"], float(cfg["timeout_sec"])), cfg), cfg


def backfill(start: date, end: date, refetch: bool = False) -> RunStats:
    client, cfg = _client()
    store = Store(load_store_path())
    try:
        return collect(store, client, cfg, start, end, mode="backfill", now=_now, run_id=_run_id(),
                       refetch=refetch)
    finally:
        store.close()


def forward() -> RunStats:
    client, cfg = _client()
    store = Store(load_store_path())
    today = _now().date()
    try:
        return collect(store, client, cfg, today, today, mode="forward", now=_now, run_id=_run_id())
    finally:
        store.close()


def status(limit: int = 10) -> dict:
    store = Store(load_store_path())
    try:
        cov = store.coverage("opendart")
        return {"store": str(store.root), "backfill_cursor": store.cursor("opendart", "backfill"),
                "coverage": {s: sum(1 for v in cov.values() if v == s)
                             for s in ("collected", "forward", "partial", "gap")},
                "runs": store.runs("opendart", limit)}
    finally:
        store.close()
