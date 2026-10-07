"""조립: 시계·환경변수·HTTP·저장소를 만들어 유스케이스에 넘긴다 (R2·R3)."""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from datetime import date, datetime

from regime_ingest.collect import RunStats, collect
from regime_ingest.config import load_config, load_news_config, load_store_path
from regime_ingest.news_collect import collect_news, validate_config
from regime_ingest.sources.news import parse_naver
from regime_ingest.sources.naver_http import make_fetch
from regime_ingest.sources.opendart import OpenDartList, http_fetch
from regime_ingest.store import Store, NEWS_SCHEMA, NEWS_MANIFEST
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


def today() -> date:
    return _now().date()  # KST 기준 오늘 (OS 시간대와 무관)


def backfill(start: date, end: date | None = None, refetch: bool = False) -> RunStats:
    end = end or today()
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
    day = today()
    try:
        return collect(store, client, cfg, day, day, mode="forward", now=_now, run_id=_run_id())
    finally:
        store.close()


def status(limit: int = 10) -> dict:
    store = Store(load_store_path())
    try:
        cov = store.coverage("opendart")
        return {"store": str(store.root), "backfill_cursor": store.cursor("opendart", "backfill"),
                "manifest": (store.root / "docs" / "source=opendart" / "_manifest.json").exists(),
                "coverage": {s: sum(1 for v in cov.values() if v == s)
                             for s in ("collected", "forward", "partial", "gap")},
                "runs": store.runs("opendart", limit)}
    finally:
        store.close()


def news_forward(config_path: Path | None = None) -> dict:
    try:
        cfg = load_news_config(config_path)
        policy = validate_config(cfg)
    except (KeyError, TypeError, ValueError):
        return {"source": "naver_news", "status": "failed", "message": "invalid_configuration"}
    if not policy.active:
        return {"source": "naver_news", "status": "disabled", "message": "source_inactive"}
    policy.check()
    client_id = os.environ.get("NAVER_CLIENT_ID", "").strip()
    client_secret = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return {"source": "naver_news", "status": "failed", "message": "credentials_missing"}
    fetch = make_fetch(client_id, client_secret, timeout=cfg["timeout_sec"],
                       max_bytes=cfg["response_max_bytes"], user_agent=cfg["user_agent"], now=_now)
    manifest = {**NEWS_MANIFEST, "license_scope": policy.license_scope}
    store = Store(load_store_path(), formats={"naver_news": (NEWS_SCHEMA, manifest)})
    try:
        return collect_news(store, fetch, parse_naver, cfg, now=_now, sleep=time.sleep, run_id=_run_id())
    finally:
        store.close()


def news_status(limit: int = 10) -> dict:
    root = load_store_path()
    if not (root / "state.sqlite").exists():
        return {"source": "naver_news", "status": "not_initialized", "runs": []}
    store = Store(root, formats={"naver_news": (NEWS_SCHEMA, NEWS_MANIFEST)})
    try:
        return {"source": "naver_news", "cursor": store.cursor("naver_news", "news_query"),
                "requests_today": store.requests_on("naver_news", today().isoformat()),
                "runs": store.runs("naver_news", limit)}
    finally:
        store.close()
