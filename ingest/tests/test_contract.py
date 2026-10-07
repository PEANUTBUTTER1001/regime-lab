"""docs/P3_수집_계약.md 수용 테스트 (T6·T7·T10·T11·T12 와 §2·§3·§5 경계). 네트워크·키 없음."""

import io
import json
import urllib.error
from datetime import date, datetime, timedelta

import pytest
from fake_dart import FakeDart, make_items

from regime_ingest.collect import collect
from regime_ingest.config import check_store_path, load_config
from regime_ingest.dedup import Seen, classify, select_as_of
from regime_ingest.normalize import normalize
from regime_ingest.ports import DartError, Incomplete, RateLimited
from regime_ingest.sources.opendart import OpenDartList, http_fetch
from regime_ingest.store import Store
from regime_ingest.timing import KST

T0 = datetime(2026, 10, 7, 9, 0, tzinfo=KST)


@pytest.fixture
def cfg():
    return {**load_config()["opendart"], "page_count": 7, "request_interval_sec": 0.0, "backoff_base_sec": 0.0}


def _client(fetch, cfg, **kw):
    return OpenDartList(fetch, {**cfg, **kw}, sleep=lambda s: None)


def _run(root, fetch, cfg, start, end, mode="backfill", t=T0, run_id="r1"):
    clock = iter(t + timedelta(seconds=i) for i in range(1, 10**6))
    return collect(Store(root), _client(fetch, cfg), cfg, start, end, mode=mode, now=lambda: next(clock), run_id=run_id)


def _norm(item, **kw):
    return normalize(item, first_seen_at=kw.pop("seen", T0), backfilled=kw.pop("backfilled", True), ingest_run_id="r",
                     viewer_url="u", license_scope="metadata_only", **kw)


ITEM = make_items(date(2021, 1, 4), date(2021, 1, 4), per_day=1)[0]


# ---------------------------------------------------------------- §3 식별자·창·시장
@pytest.mark.parametrize("bad", [{"rcept_no": "2021010400000"}, {"rcept_no": "202101040000011"},
                                 {"corp_code": "1234567"}, {"corp_code": "0000000A"}, {"rcept_dt": "20210230"}])
def test_identifier_formats(bad):
    with pytest.raises(ValueError):
        _norm({**ITEM, **bad})


def test_row_must_match_query_window_and_market():
    w = (date(2021, 1, 1), date(2021, 1, 31))
    assert _norm(ITEM, window=w, corp_cls="Y")["rcept_no"] == ITEM["rcept_no"]
    with pytest.raises(ValueError):
        _norm(ITEM, window=(date(2021, 2, 1), date(2021, 2, 28)), corp_cls="Y")
    with pytest.raises(ValueError):
        _norm(ITEM, window=w, corp_cls="K")


def test_content_hash_covers_meaning_fields_only():
    a = _norm(ITEM)
    assert _norm(ITEM, seen=T0 + timedelta(days=3))["content_hash"] == a["content_hash"]  # 조회 시각 무관
    for k, v in {"corp_code": "00000009", "corp_cls": "K", "corp_name": "다른회사"}.items():
        assert _norm({**ITEM, k: v})["content_hash"] != a["content_hash"], k


def test_changed_version_never_before_previous_version():
    """§4-3·4: 시계가 뒤로 가도 새 버전 available_at 은 직전 버전보다 앞서지 않는다."""
    d = _norm({**ITEM, "rm": "정"}, seen=T0 - timedelta(days=2))
    _, row = classify(d, {d["doc_id"]: Seen(1, "old", T0.isoformat())})
    assert row["available_at"] == T0.isoformat() and row["backfilled"] is False


# ---------------------------------------------------------------- §4 as_of (T7)
def test_select_as_of_observed_vs_historical_assumed(tmp_path, cfg):
    """2021-01 공시를 2026-10-07 에 소급 → historical_assumed 로는 2021-01 에 보이지만 observed 로는 안 보인다."""
    items = make_items(date(2021, 1, 4), date(2021, 1, 8))
    _run(tmp_path, FakeDart(items), cfg, date(2021, 1, 1), date(2021, 1, 31))
    docs = Store(tmp_path).read_docs("opendart")
    t = datetime(2021, 1, 6, 12, tzinfo=KST)
    hist = select_as_of(docs, t, "historical_assumed")
    assert {d["published_at"] for d in hist} == {"2021-01-04", "2021-01-05"}  # 접수일 다음 날 0시부터
    assert select_as_of(docs, t, "observed") == []  # 실제로는 2026-10-07 에 처음 봄
    assert len(select_as_of(docs, T0 + timedelta(hours=1), "observed")) == len(items)
    with pytest.raises(ValueError):
        select_as_of(docs, t.replace(tzinfo=None))


def test_select_as_of_picks_latest_allowed_version(tmp_path, cfg):
    items = make_items(date(2026, 10, 7), date(2026, 10, 7), per_day=1)
    _run(tmp_path, FakeDart(items), cfg, date(2026, 10, 7), date(2026, 10, 7), mode="forward")
    later = T0 + timedelta(hours=5)
    _run(tmp_path, FakeDart([{**items[0], "rm": "정"}]), cfg, date(2026, 10, 7), date(2026, 10, 7),
         mode="forward", t=later, run_id="r2")
    docs = Store(tmp_path).read_docs("opendart")
    assert [d["version"] for d in select_as_of(docs, T0 + timedelta(hours=1))] == [1]
    assert [d["version"] for d in select_as_of(docs, later + timedelta(minutes=1))] == [2]


def test_forward_then_backfill_keeps_version_and_first_seen(tmp_path, cfg):
    """T6: 순방향으로 본 공시를 소급으로 다시 받아도 문서 수·version·first_seen_at·available_at 불변."""
    items = make_items(date(2026, 10, 6), date(2026, 10, 6))
    _run(tmp_path, FakeDart(items), cfg, date(2026, 10, 6), date(2026, 10, 6), mode="forward")
    before = {d["doc_id"]: (d["version"], d["first_seen_at"], d["available_at"]) for d in Store(tmp_path).read_docs("opendart")}
    _run(tmp_path, FakeDart(items), cfg, date(2026, 10, 6), date(2026, 10, 6), t=T0 + timedelta(days=1), run_id="r2")
    after = {d["doc_id"]: (d["version"], d["first_seen_at"], d["available_at"]) for d in Store(tmp_path).read_docs("opendart")}
    assert after == before


# ---------------------------------------------------------------- §5 응답 검증 (T10)
def _ok(page, total_page, total_count, items):
    return {"status": "000", "page_no": page, "total_page": total_page, "total_count": total_count, "list": items}


@pytest.mark.parametrize("body,exc", [
    ({"status": "000"}, DartError),  # list·페이지 필드 없음
    ({"status": "000", "page_no": 1, "total_page": 1, "total_count": 1, "list": "x"}, DartError),
    ({"status": "000", "page_no": 2, "total_page": 1, "total_count": 1, "list": []}, DartError),  # page_no 불일치
    (_ok(1, 1, 3, [ITEM]), Incomplete),  # total_count 와 받은 건수 다름
    (_ok(1, 1, 2, [ITEM, ITEM]), Incomplete),  # 고유 접수번호 수 다름
])
def test_malformed_000_responses_fail(cfg, body, exc):
    with pytest.raises(exc):
        list(_client(lambda p: body, cfg).pages(date(2021, 1, 1), date(2021, 1, 31), "Y"))


def test_total_changes_between_pages_is_incomplete(cfg):
    pages = {1: _ok(1, 2, 2, [ITEM]), 2: _ok(2, 3, 3, [{**ITEM, "rcept_no": "20210104000002"}])}
    with pytest.raises(Incomplete):
        list(_client(lambda p: pages[p["page_no"]], cfg).pages(date(2021, 1, 1), date(2021, 1, 31), "Y"))
    with pytest.raises(Incomplete):  # 둘째 쪽에서 013
        list(_client(lambda p: pages[1] if p["page_no"] == 1 else {"status": "013"}, cfg)
             .pages(date(2021, 1, 1), date(2021, 1, 31), "Y"))


def test_request_params_include_all_report_types(cfg):
    fake = FakeDart([])
    list(_client(fake, cfg).pages(date(2021, 1, 1), date(2021, 1, 31), "K"))
    assert fake.calls[0]["last_reprt_at"] == "N" and fake.calls[0]["page_count"] == 7


def test_incomplete_window_is_gap_not_collected(tmp_path, cfg):
    items = make_items(date(2021, 1, 1), date(2021, 1, 31))

    def flaky(p):
        b = FakeDart(items)(p)
        return {**b, "total_count": b.get("total_count", 0) + 1} if b.get("status") == "000" else b

    st = _run(tmp_path, flaky, cfg, date(2021, 1, 1), date(2021, 1, 31))
    assert st.status == "partial" and "건수 불일치" in st.message
    store = Store(tmp_path)
    assert set(store.coverage("opendart").values()) == {"gap"} and store.read_docs("opendart") == []
    assert store.cursor("opendart", "backfill") is None


def test_server_message_not_recorded(tmp_path, cfg):
    secretish = "키 SECRET-123 이 유효하지 않습니다"
    st = _run(tmp_path, lambda p: {"status": "010", "message": secretish}, cfg, date(2021, 1, 1), date(2021, 1, 31))
    assert st.status == "failed" and "SECRET-123" not in st.message and "010" in st.message


# ---------------------------------------------------------------- HTTP 경계 (T12)
class _Opener:
    def __init__(self, exc=None, body=None):
        self.exc, self.body = exc, body

    def open(self, req, timeout):
        if self.exc:
            raise self.exc
        return io.BytesIO(json.dumps(self.body).encode())


def _http(monkeypatch, opener, key="SECRET-KEY-1"):
    import urllib.request

    seen = {}

    def build(*handlers):
        seen["handlers"] = handlers
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build)
    return http_fetch("https://opendart.example/api/list.json", key, "ua", 1), seen


def test_redirect_is_refused_and_not_followed(monkeypatch):
    err = urllib.error.HTTPError("https://opendart.example/x?crtfc_key=SECRET-KEY-1", 302, "Found", {}, None)
    fetch, seen = _http(monkeypatch, _Opener(exc=err))
    with pytest.raises(DartError) as e:
        fetch({})
    assert "SECRET-KEY-1" not in str(e.value) and "http302" in str(e.value)
    from regime_ingest.sources.opendart import _NoRedirect

    assert any(isinstance(h, type) and issubclass(h, _NoRedirect) for h in seen["handlers"])
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example/") is None


def test_http_429_is_rate_limited(monkeypatch, cfg):
    fetch, _ = _http(monkeypatch, _Opener(exc=urllib.error.HTTPError("u", 429, "Too Many", {}, None)))
    with pytest.raises(RateLimited):
        _client(fetch, cfg)._call({})


# ---------------------------------------------------------------- §2·§5 저장 경로·manifest·skipped
def test_store_path_must_not_overlap_repo_or_raw_data(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    for bad in (repo, repo / "data" / "x", repo / "ext_store", tmp_path):  # 저장소 안·원본 안·저장소를 포함하는 상위
        with pytest.raises(ValueError):
            check_store_path(bad, repo)
    ok = tmp_path / "regime-lab_ext_store"
    assert check_store_path(ok, repo) == ok.resolve()


def test_manifest_mismatch_fails_not_skipped(tmp_path, cfg):
    _run(tmp_path, FakeDart([]), cfg, date(2021, 1, 1), date(2021, 1, 31))
    m = tmp_path / "docs" / "source=opendart" / "_manifest.json"
    assert json.loads(m.read_text(encoding="utf-8"))["schema_version"] == 1
    m.write_text(json.dumps({"schema_version": 99, "policy_version": "x"}), encoding="utf-8")
    with pytest.raises(RuntimeError):  # 경로·manifest 오류는 skipped 로 숨기지 않는다
        _run(tmp_path, FakeDart([]), cfg, date(2021, 2, 1), date(2021, 2, 28), run_id="r2")
    assert not (tmp_path / "locks" / "opendart.lock").exists()  # 잠금은 풀림


def test_coverage_history_is_append_only(tmp_path, cfg):
    items = make_items(date(2026, 10, 7), date(2026, 10, 7), per_day=1)
    _run(tmp_path, FakeDart(items), cfg, date(2026, 10, 7), date(2026, 10, 7), mode="forward")
    _run(tmp_path, FakeDart(items), cfg, date(2026, 10, 7), date(2026, 10, 7), t=T0 + timedelta(hours=12), run_id="r2")
    log = Store(tmp_path).db.execute("SELECT state, observed_at FROM coverage_log WHERE corp_cls='Y' ORDER BY rowid").fetchall()
    assert [s for s, _ in log] == ["forward", "collected"] and log[0][1] < log[1][1]
