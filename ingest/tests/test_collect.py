"""수집 유스케이스·어댑터·저장소 (가짜 서버, 네트워크 없음): 멱등·커서 재개·공백·버전·절단 불변·키 비노출."""

from datetime import date, datetime, timedelta

import pytest
from fake_dart import FakeDart, make_items

from regime_ingest.collect import collect
from regime_ingest.config import load_config
from regime_ingest.sources.opendart import DartError, OpenDartList, http_fetch, mask
from regime_ingest.store import Store
from regime_ingest.timing import KST

RUN_FIELDS = ("first_seen_at", "ingest_run_id")


@pytest.fixture
def cfg():
    return {**load_config()["opendart"], "page_count": 7, "request_interval_sec": 0.0, "backoff_base_sec": 0.0}


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 7, 9, 0, tzinfo=KST)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def _run(store, fake, cfg, start, end, mode="backfill", run_id="r1", **kw):
    client = OpenDartList(fake, {**cfg, **kw}, sleep=lambda s: None)
    return collect(store, client, {**cfg, **kw}, start, end, mode=mode, now=Clock(), run_id=run_id)


def _stable(docs):
    return sorted(({k: v for k, v in d.items() if k not in RUN_FIELDS} for d in docs), key=lambda d: (d["doc_id"], d["version"]))


def test_backfill_collects_all_pages_and_windows(tmp_path, cfg):
    items = make_items(date(2021, 1, 1), date(2021, 3, 31))
    fake = FakeDart(items)
    st = _run(Store(tmp_path), fake, cfg, date(2021, 1, 1), date(2021, 3, 31))
    assert st.status == "ok" and st.new == len(items) and st.received == len(items)
    docs = Store(tmp_path).read_docs("opendart")
    assert len(docs) == len(items) and {d["doc_id"] for d in docs} == {f"opendart:{i['rcept_no']}" for i in items}
    assert all(d["backfilled"] and d["license_scope"] == "metadata_only" for d in docs)
    # 1개월 창 × (Y·K) — 창 하나도 3개월을 넘지 않는다
    assert {(c["bgn_de"][:6], c["end_de"][:6]) for c in fake.calls} == {("202101", "202101"), ("202102", "202102"),
                                                                         ("202103", "202103")}
    assert max(int(c["page_no"]) for c in fake.calls) > 1  # 여러 페이지
    cov = Store(tmp_path).coverage("opendart")
    assert len(cov) == 90 * 2 and set(cov.values()) == {"collected"}
    assert (tmp_path / "raw" / "opendart").exists() and (tmp_path / "state.sqlite").exists()


def test_rerun_same_window_is_idempotent(tmp_path, cfg):
    items = make_items(date(2021, 1, 1), date(2021, 1, 31))
    _run(Store(tmp_path), FakeDart(items), cfg, date(2021, 1, 1), date(2021, 1, 31))
    before = _stable(Store(tmp_path).read_docs("opendart"))
    store = Store(tmp_path)
    store.set_cursor("opendart", "backfill", "2020-12-31")  # 커서를 되돌려 같은 창을 다시 받게 함
    store.commit()
    st = _run(store, FakeDart(items), cfg, date(2021, 1, 1), date(2021, 1, 31), run_id="r2")
    assert st.new == 0 and st.duplicate == len(items) and st.changed == 0
    assert _stable(Store(tmp_path).read_docs("opendart")) == before


def test_changed_content_adds_new_version(tmp_path, cfg):
    items = make_items(date(2021, 1, 1), date(2021, 1, 5))
    _run(Store(tmp_path), FakeDart(items), cfg, date(2021, 1, 1), date(2021, 1, 5))
    items2 = [dict(i) for i in items]
    items2[0]["rm"] = "정"
    st = _run(Store(tmp_path), FakeDart(items2), cfg, date(2021, 1, 1), date(2021, 1, 5), mode="forward", run_id="r2")
    assert st.changed == 1 and st.new == 0
    rows = [d for d in Store(tmp_path).read_docs("opendart") if d["rcept_no"] == items[0]["rcept_no"]]
    assert sorted(d["version"] for d in rows) == [1, 2]  # 옛 버전은 그대로 남는다


def test_rate_limit_marks_gap_and_resumes_from_cursor(tmp_path, cfg):
    items = make_items(date(2021, 1, 1), date(2021, 3, 31))
    full = FakeDart(items)
    _run(Store(tmp_path / "ref"), full, cfg, date(2021, 1, 1), date(2021, 3, 31))
    calls_jan = sum(1 for c in full.calls if c["bgn_de"].startswith("202101"))

    fake = FakeDart(items, fail={calls_jan + 2: {"status": "020", "message": "요청 제한을 초과하였습니다."}})
    st = _run(Store(tmp_path / "s"), fake, cfg, date(2021, 1, 1), date(2021, 3, 31))
    assert st.status == "partial" and "020" in st.message
    store = Store(tmp_path / "s")
    assert store.cursor("opendart", "backfill") == "2021-01-31"  # 1월만 확정
    cov = store.coverage("opendart")
    assert cov[("2021-02-10", "Y")] == "gap" and cov[("2021-01-10", "K")] == "collected"
    assert ("2021-03-10", "Y") not in cov  # 시도 안 한 구간은 기록 없음 (not_scheduled)
    assert all(d["published_at"] < "2021-02" for d in store.read_docs("opendart"))  # 끊긴 창은 확정 안 함

    st2 = _run(store, FakeDart(items), cfg, date(2021, 1, 1), date(2021, 3, 31), run_id="r2")
    assert st2.status == "ok"
    assert _stable(Store(tmp_path / "s").read_docs("opendart")) == _stable(Store(tmp_path / "ref").read_docs("opendart"))
    assert set(Store(tmp_path / "s").coverage("opendart").values()) == {"collected"}  # 공백이 채워짐


def test_daily_request_limit_stops_with_partial(tmp_path, cfg):
    items = make_items(date(2021, 1, 1), date(2021, 2, 28))
    st = _run(Store(tmp_path), FakeDart(items), cfg, date(2021, 1, 1), date(2021, 2, 28), daily_request_limit=5)
    assert st.status == "partial" and st.requests == 5
    store = Store(tmp_path)
    assert store.requests_on("opendart", "2026-10-07") == 5
    st2 = _run(store, FakeDart(items), cfg, date(2021, 1, 1), date(2021, 2, 28), run_id="r2", daily_request_limit=5)
    assert st2.status == "partial" and st2.requests == 0  # 같은 날은 더 요청하지 않음


def test_transient_errors_retry_then_permanent_error_fails(tmp_path, cfg):
    items = make_items(date(2021, 1, 1), date(2021, 1, 3))
    fake = FakeDart(items, fail={1: {"status": "800", "message": "점검"}, 2: OSError("timeout")})
    st = _run(Store(tmp_path / "a"), fake, cfg, date(2021, 1, 1), date(2021, 1, 3))
    assert st.status == "ok" and st.new == len(items)  # 일시 오류 2번 뒤 성공

    bad = FakeDart(items, fail={1: {"status": "010", "message": "등록되지 않은 키입니다."}})
    st = _run(Store(tmp_path / "b"), bad, cfg, date(2021, 1, 1), date(2021, 1, 3))
    assert st.status == "failed" and len(bad.calls) == 1  # 키 오류는 재시도하지 않음
    with pytest.raises(DartError):
        OpenDartList(FakeDart(items, fail={i: {"status": "900", "message": "x"} for i in range(1, 10)}),
                     cfg, sleep=lambda s: None)._call({})


def test_no_data_window_is_collected_not_gap(tmp_path, cfg):
    st = _run(Store(tmp_path), FakeDart([]), cfg, date(2021, 1, 1), date(2021, 1, 31))
    assert st.status == "ok" and st.new == 0
    assert set(Store(tmp_path).coverage("opendart").values()) == {"collected"}  # 진짜 0건 ≠ 공백


def test_bad_items_are_counted_not_stored(tmp_path, cfg):
    items = make_items(date(2021, 1, 1), date(2021, 1, 2))
    items[0] = {**items[0], "rcept_no": "A" + items[0]["rcept_no"]}  # 형식이 깨진 접수번호
    st = _run(Store(tmp_path), FakeDart(items), cfg, date(2021, 1, 1), date(2021, 1, 2))
    assert st.errors == 1 and st.status == "partial" and st.new == len(items) - 1


def test_lock_prevents_concurrent_run(tmp_path, cfg):
    store = Store(tmp_path)
    store.lock("opendart")
    fake = FakeDart(make_items(date(2021, 1, 1), date(2021, 1, 2)))
    st = _run(store, fake, cfg, date(2021, 1, 1), date(2021, 1, 2))
    assert st.status == "skipped" and fake.calls == []


@pytest.mark.parametrize("cut", ["2021-02-15", "2021-03-31", "2021-05-01"])
def test_truncation_invariance(tmp_path, cfg, cut):
    """나중 구간을 더 수집해도 시각 T 이전에 쓸 수 있던 자료(available_at ≤ T)는 그대로다."""
    items = make_items(date(2021, 1, 1), date(2021, 6, 30))
    t = datetime.fromisoformat(cut).replace(tzinfo=KST)
    _run(Store(tmp_path / "short"), FakeDart(items), cfg, date(2021, 1, 1), date.fromisoformat(cut))
    _run(Store(tmp_path / "long"), FakeDart(items), cfg, date(2021, 1, 1), date(2021, 6, 30))

    def usable(root):
        return _stable([d for d in Store(root).read_docs("opendart") if datetime.fromisoformat(d["available_at"]) <= t])

    a, b = usable(tmp_path / "short"), usable(tmp_path / "long")
    assert a == b and len(a) > 0
    assert all(d["published_at"] < cut for d in a)  # 당일 접수분은 다음 날 0시부터 사용


def test_amendment_links_across_runs(tmp_path, cfg):
    base = make_items(date(2021, 1, 4), date(2021, 1, 4), per_day=1)
    orig = {**base[0], "report_nm": "사업보고서 (2020.12)"}
    amend = {**orig, "report_nm": "[기재정정]사업보고서 (2020.12)", "rcept_no": "20210205000001", "rcept_dt": "20210205"}
    _run(Store(tmp_path), FakeDart([orig]), cfg, date(2021, 1, 1), date(2021, 1, 31))
    _run(Store(tmp_path), FakeDart([orig, amend]), cfg, date(2021, 1, 1), date(2021, 2, 28), run_id="r2")
    docs = {d["rcept_no"]: d for d in Store(tmp_path).read_docs("opendart")}
    assert docs["20210205000001"]["amends_doc_id"] == f"opendart:{orig['rcept_no']}"
    assert docs["20210205000001"]["is_amendment"] and len(docs) == 2


def test_api_key_never_leaks(monkeypatch):
    key = "SECRET-KEY-1234567890"
    assert mask(f"https://x/list.json?crtfc_key={key}&a=1", key) == "https://x/list.json?crtfc_key=***&a=1"
    import urllib.request

    def boom(req, timeout):
        raise OSError(f"HTTP Error 500 for url {req.full_url}")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    fetch = http_fetch("https://opendart.example/api/list.json", key, "ua", 1)
    with pytest.raises(OSError) as e:
        fetch({"page_no": 1})
    assert key not in str(e.value) and "***" in str(e.value)
