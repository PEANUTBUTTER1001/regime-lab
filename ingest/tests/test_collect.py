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


def _run(store, fake, cfg, start, end, mode="backfill", run_id="r1", refetch=False, **kw):
    client = OpenDartList(fake, {**cfg, **kw}, sleep=lambda s: None)
    return collect(store, client, {**cfg, **kw}, start, end, mode=mode, now=Clock(), run_id=run_id, refetch=refetch)


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
    skip = FakeDart(items)
    st = _run(Store(tmp_path), skip, cfg, date(2021, 1, 1), date(2021, 1, 31), run_id="r2")
    assert st.skipped_windows == 1 and skip.calls == [] and st.status == "ok"  # 이미 collected → 요청 0
    st = _run(Store(tmp_path), FakeDart(items), cfg, date(2021, 1, 1), date(2021, 1, 31), run_id="r3", refetch=True)
    assert st.new == 0 and st.duplicate == len(items) and st.changed == 0
    assert _stable(Store(tmp_path).read_docs("opendart")) == before


def test_earlier_uncollected_range_is_not_skipped(tmp_path, cfg):
    """codex 리뷰: 2월만 받은 뒤 1~2월을 요청하면 1월을 받아야 한다 (예전: 전역 커서가 2월 말이라 조용히 누락)."""
    items = make_items(date(2021, 1, 1), date(2021, 2, 28))
    _run(Store(tmp_path), FakeDart(items), cfg, date(2021, 2, 1), date(2021, 2, 28))
    fake = FakeDart(items)
    st = _run(Store(tmp_path), fake, cfg, date(2021, 1, 1), date(2021, 2, 28), run_id="r2")
    assert st.status == "ok" and st.skipped_windows == 1 and st.new == sum(1 for i in items if i["rcept_dt"] < "202102")
    assert {c["bgn_de"][:6] for c in fake.calls} == {"202101"}  # 2월은 요청하지 않음
    cov = Store(tmp_path).coverage("opendart")
    assert len(cov) == 59 * 2 and set(cov.values()) == {"collected"}
    assert Store(tmp_path).cursor("opendart", "backfill") == "2021-02-28"


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


def test_retries_respect_daily_limit(cfg):
    """리뷰 #21-4: 재시도도 하루 상한 안에서만 (상한 1이면 900 고정 응답에도 요청은 1회)."""
    fake = FakeDart([], fail={i: {"status": "900", "message": "x"} for i in range(1, 10)})
    client = OpenDartList(fake, {**cfg, "daily_request_limit": 1}, sleep=lambda s: None)
    with pytest.raises(DartError):
        client._call({})
    assert len(fake.calls) == 1 and client.requests == 1


def test_changed_backfill_version_not_backdated(tmp_path, cfg):
    """리뷰 #21-1 (수집 흐름): 소급으로 다시 받은 변경 버전은 과거 as_of 에 보이지 않는다."""
    items = make_items(date(2026, 1, 1), date(2026, 1, 3))
    _run(Store(tmp_path), FakeDart(items), cfg, date(2026, 1, 1), date(2026, 1, 3))
    changed = [dict(i) for i in items]
    changed[0]["rm"] = "정"
    _run(Store(tmp_path), FakeDart(changed), cfg, date(2026, 1, 1), date(2026, 1, 3), run_id="r2", refetch=True)
    rows = {d["version"]: d for d in Store(tmp_path).read_docs("opendart") if d["rcept_no"] == items[0]["rcept_no"]}
    assert rows[1]["available_at"] == "2026-01-02T00:00:00+09:00"
    assert rows[2]["available_at"] == rows[2]["first_seen_at"] > "2026-10-07"  # 수집 시각(합성 시계 2026-10-07)


def test_amendment_before_later_collected_original(tmp_path, cfg):
    """리뷰 #21-2 (수집 흐름): 3월을 먼저 받고 2월 정정 공시를 넣어도 3월 공시를 후보로 잡지 않는다."""
    corp = {"corp_code": "00000001", "corp_name": "합성회사1", "stock_code": "000001", "corp_cls": "Y", "flr_nm": "x", "rm": ""}
    later = {**corp, "report_nm": "사업보고서 (2020.12)", "rcept_no": "20210301000001", "rcept_dt": "20210301"}
    amend = {**corp, "report_nm": "[기재정정]사업보고서 (2020.12)", "rcept_no": "20210201000001", "rcept_dt": "20210201"}
    _run(Store(tmp_path), FakeDart([later]), cfg, date(2021, 3, 1), date(2021, 3, 31))
    store = Store(tmp_path)
    _run(store, FakeDart([later, amend]), cfg, date(2021, 2, 1), date(2021, 2, 28), mode="forward", run_id="r2")
    docs = {d["rcept_no"]: d for d in Store(tmp_path).read_docs("opendart")}
    assert docs["20210201000001"]["amends_candidate_doc_id"] is None


def test_crash_between_parquet_and_commit_recovers(tmp_path, cfg, monkeypatch):
    """리뷰 추가: 문서 파일을 쓴 뒤 commit 전에 죽으면 tmp 만 남고, 다음 실행에서 지워져 중복 없이 다시 받는다."""
    items = make_items(date(2021, 1, 1), date(2021, 1, 5))
    store = Store(tmp_path)
    real = store.mark_seen

    def die(docs):
        raise KeyboardInterrupt("강제 종료")

    monkeypatch.setattr(store, "mark_seen", die)
    with pytest.raises(KeyboardInterrupt):
        _run(store, FakeDart(items), cfg, date(2021, 1, 1), date(2021, 1, 5))
    monkeypatch.setattr(store, "mark_seen", real)
    assert not list((tmp_path / "docs").rglob("*.parquet"))  # 확정 파일 없음
    store.db.close()
    (tmp_path / "locks" / "opendart.lock").unlink(missing_ok=True)
    st = _run(Store(tmp_path), FakeDart(items), cfg, date(2021, 1, 1), date(2021, 1, 5), run_id="r2")
    assert st.new == len(items)
    assert len(Store(tmp_path).read_docs("opendart")) == len(items)  # 중복 없음
    assert not list((tmp_path / "docs").rglob("*.tmp"))


def test_crash_after_commit_before_rename_is_published_on_open(tmp_path, cfg, monkeypatch):
    items = make_items(date(2021, 1, 1), date(2021, 1, 5))
    store = Store(tmp_path)
    monkeypatch.setattr(store, "publish", lambda paths: None)  # commit 뒤 이름 바꾸기 전에 죽음
    _run(store, FakeDart(items), cfg, date(2021, 1, 1), date(2021, 1, 5))
    assert list((tmp_path / "docs").rglob("*.tmp")) and not list((tmp_path / "docs").rglob("*.parquet"))
    assert len(Store(tmp_path).read_docs("opendart")) == len(items)  # 다시 열면 확정
    assert not list((tmp_path / "docs").rglob("*.tmp"))


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


def test_bad_items_keep_window_partial_and_cursor(tmp_path, cfg):
    """리뷰 #21-3: 잘못된 행이 있는 창은 collected 가 아니고 커서도 넘기지 않는다 → 다음 실행이 다시 받는다."""
    items = make_items(date(2021, 1, 1), date(2021, 2, 28))
    bad = [dict(i) for i in items]
    bad[3] = {**bad[3], "report_nm": None}  # 1월 창에 잘못된 행
    st = _run(Store(tmp_path), FakeDart(bad), cfg, date(2021, 1, 1), date(2021, 2, 28))
    assert st.errors == 1 and st.status == "partial" and st.new == len(items) - 1
    store = Store(tmp_path)
    assert store.cursor("opendart", "backfill") is None  # 1월이 불완전 → 2월이 완료여도 커서 안 넘김
    cov = store.coverage("opendart")
    assert cov[("2021-01-10", "Y")] == "partial" and cov[("2021-02-10", "Y")] == "collected"
    st2 = _run(store, FakeDart(items), cfg, date(2021, 1, 1), date(2021, 2, 28), run_id="r2")  # 고쳐진 응답
    jan = sum(1 for i in items if i["rcept_dt"] < "202102")
    assert st2.status == "ok" and st2.new == 1 and st2.duplicate == jan - 1 and st2.skipped_windows == 1  # 2월은 건너뜀
    store = Store(tmp_path)
    assert store.cursor("opendart", "backfill") == "2021-02-28"
    assert set(store.coverage("opendart").values()) == {"collected"}


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
    assert docs["20210205000001"]["amends_candidate_doc_id"] == f"opendart:{orig['rcept_no']}"
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
