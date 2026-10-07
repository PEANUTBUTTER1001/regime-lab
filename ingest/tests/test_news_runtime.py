import io
import json
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.request

import pytest

from regime_ingest import app, cli
from regime_ingest.config import load_news_config
from regime_ingest.news import SourcePolicy, observe
from regime_ingest.news_collect import collect_news
from regime_ingest.ports import NewsRequestError
from regime_ingest.sources.naver_http import make_fetch, retry_delay, _NoRedirect
from regime_ingest.sources.news import parse_naver
from regime_ingest.store import Store, NEWS_SCHEMA, NEWS_MANIFEST, MANIFEST

T = datetime(2026, 10, 7, 0, tzinfo=timezone.utc)
FORMATS = {"naver_news": (NEWS_SCHEMA, {**NEWS_MANIFEST, "license_scope": "metadata_only"})}


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path, formats=FORMATS)
    yield value
    value.close()


@pytest.fixture
def cfg():
    return {**load_news_config(), "active": True, "queries": ["합성"], "request_interval_sec": 0,
            "backoff_base_sec": 0, "max_retries": 1}


def response(title="합성 뉴스", total=1):
    return {"start": 1, "display": 1, "total": total, "items": [{"title": title,
            "description": "보관하지 않을 합성 요약", "originallink": "https://example.test/a",
            "link": "https://example.test/a", "pubDate": "Wed, 07 Oct 2026 08:00:00 +0900"}]}


def run(store, cfg, fetch=None, *, run_id="synthetic", now=lambda: T, sleep=lambda _: None):
    return collect_news(store, fetch or (lambda _: response()), parse_naver, cfg,
                        now=now, sleep=sleep, run_id=run_id)


def test_restart_dedup_and_metadata_only(store, cfg):
    assert run(store, cfg)["new"] == 1
    assert run(store, cfg, run_id="again")["duplicate"] == 1
    rows = store.read_docs("naver_news")
    assert len(rows) == 1 and rows[0]["article_key"] and rows[0]["summary"] is None
    assert not (store.root / "raw").exists()  # response descriptions are not persisted
    assert store.requests_on("naver_news", "2026-10-07") == 2
    manifest = json.loads((store.root / "docs/source=naver_news/_manifest.json").read_text())
    assert manifest["policy_version"] != MANIFEST["policy_version"]


def test_new_version_roundtrip_preserves_past(store, cfg):
    run(store, cfg)
    run(store, cfg, lambda _: response("정정 제목"), run_id="changed", now=lambda: T+timedelta(hours=1))
    rows = sorted(store.read_docs("naver_news"), key=lambda d: d["version"])
    assert [d["version"] for d in rows] == [1, 2]
    assert rows[0]["first_seen_at"] == T.isoformat()
    assert rows[1]["available_at"] == (T+timedelta(hours=1)).isoformat()


def test_quota_survives_failure_and_new_connection(store, cfg):
    def fail(_):
        raise NewsRequestError("network", retryable=True)
    one = {**cfg, "daily_request_limit":1}
    assert run(store, one, fail)["requests"] == 1
    other = Store(store.root, formats=FORMATS)
    try:
        assert run(other, one, lambda _: pytest.fail("quota bypass"), run_id="restart")["requests"] == 0
        assert other.requests_on("naver_news", "2026-10-07") == 1
    finally:
        other.close()


def test_retry_after_honored_and_counted(store, cfg):
    attempts, waits = [], []
    def fetch(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise NewsRequestError("http_429", retryable=True, retry_after=12)
        return response()
    result = run(store, cfg, fetch, sleep=waits.append)
    assert result["status"] == "ok" and result["requests"] == 2
    assert 12 in waits


def test_long_retry_after_stops_without_early_retry(store, cfg):
    def fetch(_):
        raise NewsRequestError("http_429", retryable=True, retry_after=3600)
    waits = []
    result = run(store, cfg, fetch, sleep=waits.append)
    assert result["status"] == "partial" and result["requests"] == 1 and not waits


def test_per_run_budget_counts_retries(store, cfg):
    def fetch(_):
        raise NewsRequestError("network", retryable=True)
    result = run(store, {**cfg, "request_limit": 1}, fetch)
    assert result["requests"] == 1 and result["message"] == "request_limit"
    assert store.cursor("naver_news", "news_query") is None


def test_invalid_rows_hold_cursor_and_mark_partial(store, cfg):
    def fetch(_):
        body = response()
        body["items"][0]["pubDate"] = "bad"
        return body
    result = run(store, {**cfg, "queries":["a","b"], "request_limit":1}, fetch)
    assert result["status"] == "partial" and result["errors"] == 1
    assert store.cursor("naver_news", "news_query") is None
    assert store.coverage("naver_news")[("2026-10-07", "news")] == "partial"


def test_bad_response_is_gap_not_normal_empty(store, cfg):
    result = run(store, cfg, lambda _: {"errorCode":"secret server content"})
    assert result["status"] == "failed" and result["message"] == "invalid_response"
    assert store.coverage("naver_news")[("2026-10-07", "news")] == "gap"


def test_sampling_more_results_is_partial_and_rotates_queries(store, cfg):
    queries = []
    def fetch(params):
        queries.append(params["query"])
        return response(total=9999)
    capped = {**cfg, "queries":["a","b"], "request_limit":1}
    assert run(store, capped, fetch)["status"] == "partial"
    run(store, capped, fetch, run_id="r2")
    assert queries == ["a", "b"]


def test_midnight_request_accounting_uses_dispatch_day(store, cfg):
    before = T.replace(hour=14, minute=59, second=59)
    clock = [before]
    def sleep(_):
        clock[0] = before + timedelta(seconds=2)
    result = run(store, {**cfg, "queries":["a","b"]}, now=lambda:clock[0], sleep=sleep)
    assert result["requests"] == 2
    assert store.requests_on("naver_news", "2026-10-07") == 1
    assert store.requests_on("naver_news", "2026-10-08") == 1


def test_inactive_cli_never_builds_transport_or_store(monkeypatch, capsys):
    monkeypatch.setattr(app, "make_fetch", lambda *a, **k:pytest.fail("transport called"))
    monkeypatch.setattr(app, "Store", lambda *a, **k:pytest.fail("store called"))
    assert cli.main(["run", "--source", "naver_news"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "disabled"


def test_parallel_source_recovery_never_deletes_other_staging(store):
    second = Store(store.root, formats=FORMATS)
    lock = store.lock("opendart")
    try:
        paths = store.stage_docs("opendart", "writer", [{"doc_id":"opendart:synthetic", "published_at":"2026-10-07"}])
        assert store._tmp(paths[0]).exists()
        news_lock = second.lock("naver_news")
        try:
            assert store._tmp(paths[0]).exists()
        finally:
            second.unlock(news_lock)
        store.rollback()
        store.discard(paths)
    finally:
        store.unlock(lock)
        second.close()


def test_publish_failure_is_recovered_without_duplicate(store, cfg, monkeypatch):
    original = store.publish
    monkeypatch.setattr(store, "publish", lambda _: (_ for _ in ()).throw(OSError("synthetic rename failure")))
    with pytest.raises(OSError):
        run(store, cfg)
    assert len(store.read_docs("naver_news")) == 1  # committed staging is already visible
    monkeypatch.setattr(store, "publish", original)
    result = run(store, cfg, run_id="recover")
    assert result["duplicate"] == 1 and len(store.read_docs("naver_news")) == 1
    assert not list((store.root / "docs").rglob("*.tmp"))


def test_precommit_failure_rolls_back_but_spends_request(store, cfg, monkeypatch):
    original = store.mark_seen
    monkeypatch.setattr(store, "mark_seen", lambda _: (_ for _ in ()).throw(OSError("synthetic write failure")))
    with pytest.raises(OSError):
        run(store, cfg)
    assert store.read_docs("naver_news") == [] and store.cursor("naver_news", "news_query") is None
    assert store.requests_on("naver_news", "2026-10-07") == 1
    monkeypatch.setattr(store, "mark_seen", original)
    assert run(store, cfg, run_id="retry")["new"] == 1


class Opener:
    def __init__(self, payload=b'{}', error=None):
        self.payload, self.error, self.request = payload, error, None
    def open(self, request, timeout):
        self.request = request
        if self.error:
            raise self.error
        return io.BytesIO(self.payload)


def transport(monkeypatch, opener, max_bytes=1000):
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args:opener)
    return make_fetch("synthetic-id", "synthetic-secret", timeout=1, max_bytes=max_bytes,
                      user_agent="test", now=lambda:T)


PARAMS = dict(query="합성", display=20, start=1, sort="date")


def test_credentials_headers_fixed_endpoint_only(monkeypatch):
    opener = Opener()
    transport(monkeypatch, opener)(PARAMS)
    assert opener.request.full_url.startswith("https://openapi.naver.com/v1/search/news.json?")
    assert "synthetic-secret" not in opener.request.full_url
    assert opener.request.get_header("X-naver-client-secret") == "synthetic-secret"
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.test") is None


@pytest.mark.parametrize("status,retryable", [(301,False),(401,False),(403,False),(429,True),(503,True)])
def test_transport_status_and_secret_redaction(monkeypatch, status, retryable):
    error = urllib.error.HTTPError("https://example.test/synthetic-secret",status,"synthetic-secret",{"Retry-After":"12"},None)
    with pytest.raises(NewsRequestError) as got:
        transport(monkeypatch, Opener(error=error))(PARAMS)
    assert got.value.retryable == retryable and got.value.retry_after == 12
    assert "synthetic-secret" not in str(got.value)


@pytest.mark.parametrize("payload,max_bytes,code", [(b'x'*20,10,"response_too_large"),(b'no-json',100,"invalid_json"),(b'[]',100,"invalid_json")])
def test_transport_bounded_and_malformed(monkeypatch,payload,max_bytes,code):
    with pytest.raises(NewsRequestError, match=code):
        transport(monkeypatch,Opener(payload),max_bytes)(PARAMS)


def test_retry_after_date_and_invalid():
    assert retry_delay("Wed, 07 Oct 2026 00:00:20 GMT", T) == 20
    assert retry_delay("unknown", T) == float("inf")


def test_cli_active_roundtrip_without_network(monkeypatch, cfg, tmp_path, capsys):
    monkeypatch.setattr(app, "load_news_config", lambda _:cfg)
    monkeypatch.setattr(app, "load_store_path", lambda:tmp_path)
    monkeypatch.setattr(app, "make_fetch", lambda *a, **kw:lambda _:response())
    monkeypatch.setattr(app, "_now", lambda:T)
    monkeypatch.setenv("NAVER_CLIENT_ID", "synthetic-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "synthetic-secret")
    assert cli.main(["run", "--source", "naver_news"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["new"] == 1 and "synthetic-secret" not in output
    assert cli.main(["status", "--source", "naver_news"]) == 0
    assert json.loads(capsys.readouterr().out)["requests_today"] == 1


def test_missing_credentials_no_store(monkeypatch, cfg):
    monkeypatch.setattr(app, "load_news_config", lambda _:cfg)
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(app, "Store", lambda *a, **k:pytest.fail("store called"))
    assert app.news_forward()["message"] == "credentials_missing"


def test_bad_config_reports_safe_failure(tmp_path, capsys):
    path = tmp_path / "broken.yaml"
    path.write_text('active: [ synthetic-secret', encoding="utf-8")
    assert cli.main(["run", "--source", "naver_news", "--news-config", str(path)]) == 1
    output = capsys.readouterr().out
    assert "synthetic-secret" not in output
    assert json.loads(output)["message"] == "invalid_configuration"


def test_clock_regression_does_not_reset_daily_quota(store, cfg):
    times = iter([T, T, T-timedelta(days=1), T])
    result = run(store, cfg, lambda _:pytest.fail("request after clock regression"), now=lambda:next(times))
    assert result["message"] == "clock_regression" and result["requests"] == 0
