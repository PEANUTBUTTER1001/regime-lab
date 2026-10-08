"""P3-13 자료 보관함·자료 상세 (SRS FR-N7): 시점별 보이는 버전, 필터·개수·쪽 나누기, 절단 불변, 정정 후보, 표시 범위."""

import random
from datetime import timedelta

import pytest
from rag_synth import T0, make_doc, make_docs, write_store

from regime_lab import retrieval
from regime_lab.config import load_config
from regime_lab.data.loader import DocsStore
from regime_lab.rag.select import select_as_of

BASE = {"is_amendment": False, "ticker_status": "resolved", "amends_candidate_doc_id": None, "backfilled": False,
        "content_hash": "h", "status": "active"}


@pytest.fixture()
def cfg():
    return load_config()


def _norm(docs):
    """parquet 은 첫 행으로 열을 정하므로 모든 행에 같은 키를 채운다."""
    out = [{**BASE, **d} for d in docs]
    if any(d["ticker_status"] == "resolved" and not d["tickers"] for d in out):
        out = [{**d, "ticker_status": "none"} if not d["tickers"] else d for d in out]
    return out


def _ei(tmp_path, docs, cfg):
    by_src = {}
    for d in _norm(docs):
        by_src.setdefault(d["source"], []).append(d)
    root = tmp_path / "ext"
    for src, rows in by_src.items():
        write_store(root, rows, source=src)
    return retrieval.open_index(DocsStore(root, tmp_path / "cache"), cfg, use_cache=False)


def _corpus():
    docs = make_docs()  # 5건, T0 부터 1시간 간격 (모두 first_seen = available)
    docs[3] = {**docs[3], "is_amendment": True, "title": "[기재정정]영업실적등에대한전망",
               "amends_candidate_doc_id": docs[2]["doc_id"]}
    docs[4] = {**docs[4], "tickers": [], "ticker_status": "none"}
    # docs[0] 의 내용 변경 버전: T0+5h 에 처음 봄
    v2 = {**docs[0], "version": 2, "title": docs[0]["title"] + " (정정)", "first_seen_at": (T0 + timedelta(hours=5)).isoformat(),
          "available_at": (T0 + timedelta(hours=5)).isoformat(), "content_hash": "h2"}
    # 소급분: 접수일 다음 날 0시부터 쓸 수 있다고 가정하지만 실제로는 T0+6h 에 처음 봄
    back = make_doc(9, "옛날전자", "사업보고서 (2025.12)", "000660", T0 + timedelta(hours=6),
                    available=T0 - timedelta(days=200), published_at=(T0 - timedelta(days=201)).date().isoformat(),
                    backfilled=True)
    news = make_doc(20, "", "한빛반도체 자사주 매입 기사", "005930", T0 + timedelta(hours=2), source="naver_news",
                    source_type="news", doc_id="naver_news:abc123", time_precision="minute",
                    published_at="2026-10-06T23:30:00+00:00", license_scope="summary_link", summary="요약 한 줄")
    return docs + [v2, back, news]


AFTER = (T0 + timedelta(days=1)).isoformat()


def test_browse_lists_latest_visible_version_sorted(tmp_path, cfg):
    ei = _ei(tmp_path, _corpus(), cfg)
    res = retrieval.browse(ei, cfg, AFTER)
    assert res["status"] == "ok" and res["total"] == 7  # doc_id 7개 (docs 5 + 소급 1 + 뉴스 1)
    ids = [x["doc_id"] for x in res["items"]]
    assert len(set(ids)) == len(ids)
    first = next(x for x in res["items"] if x["doc_id"] == "opendart:20261007000000")
    assert first["version"] == 2 and first["versions_visible"] == 2 and "revised" in first["statuses"]
    dates = [(x["published_at"][:10] if x["time_precision"] == "date_only" else "2026-10-07") for x in res["items"]]
    assert dates == sorted(dates, reverse=True)
    assert res["items"][-1]["doc_id"] == "opendart:20261007000009"  # 가장 오래된 게시일(소급분)이 끝
    assert {"doc_id", "title", "url", "source_type", "license_scope", "tickers", "statuses", "versions_visible",
            "has_amendment_candidate"} <= res["items"][0].keys()
    assert res["facets"]["source_type"] == {"disclosure": 6, "news": 1}
    assert res["facets"]["status"] == {"amendment": 1, "revised": 1, "unlinked": 1}


def test_future_documents_and_versions_are_hidden(tmp_path, cfg):
    ei = _ei(tmp_path, _corpus(), cfg)
    t = (T0 + timedelta(hours=2, minutes=30)).isoformat()
    res = retrieval.browse(ei, cfg, t)
    ids = {x["doc_id"] for x in res["items"]}
    assert ids == {"opendart:20261007000000", "opendart:20261007000001", "opendart:20261007000002", "naver_news:abc123"}
    first = next(x for x in res["items"] if x["doc_id"] == "opendart:20261007000000")
    assert first["version"] == 1 and first["versions_visible"] == 1  # v2 는 T0+5h 부터
    assert retrieval.browse(ei, cfg, (T0 - timedelta(hours=1)).isoformat())["status"] == "no_documents"


def test_observed_vs_historical_assumed(tmp_path, cfg):
    ei = _ei(tmp_path, _corpus(), cfg)
    t = (T0 + timedelta(hours=1)).isoformat()  # 소급분은 available 은 과거지만 first_seen 은 T0+6h
    obs = {x["doc_id"] for x in retrieval.browse(ei, cfg, t)["items"]}
    hist = {x["doc_id"] for x in retrieval.browse(ei, cfg, t, mode="historical_assumed")["items"]}
    assert "opendart:20261007000009" not in obs and "opendart:20261007000009" in hist
    assert hist - obs == {"opendart:20261007000009"}


def test_filters_facets_and_paging(tmp_path, cfg):
    ei = _ei(tmp_path, _corpus(), cfg)
    news = retrieval.browse(ei, cfg, AFTER, source_type="news")
    assert [x["doc_id"] for x in news["items"]] == ["naver_news:abc123"]
    assert news["facets"]["source_type"] == {"disclosure": 6, "news": 1}  # 유형 필터 전 개수
    assert {x["doc_id"] for x in retrieval.browse(ei, cfg, AFTER, status="amendment")["items"]} == {"opendart:20261007000003"}
    assert {x["doc_id"] for x in retrieval.browse(ei, cfg, AFTER, status="unlinked")["items"]} == {"opendart:20261007000004"}
    tick = retrieval.browse(ei, cfg, AFTER, tickers=["005930"])
    assert {x["doc_id"] for x in tick["items"]} == {"opendart:20261007000000", "opendart:20261007000001", "naver_news:abc123"}
    old = retrieval.browse(ei, cfg, AFTER, mode="historical_assumed", end="2026-06-30")  # 소급분 게시일 2026-03-20
    assert [x["doc_id"] for x in old["items"]] == ["opendart:20261007000009"]
    # 뉴스 게시 시각은 UTC 2026-10-06 23:30 = KST 2026-10-07 → 10-07 하루 필터에 들어간다
    day = retrieval.browse(ei, cfg, AFTER, start="2026-10-07", end="2026-10-07", source_type="news")
    assert day["total"] == 1
    p1 = retrieval.browse(ei, cfg, AFTER, page=1, page_size=3)
    p3 = retrieval.browse(ei, cfg, AFTER, page=3, page_size=3)
    assert (p1["pages"], len(p1["items"]), len(p3["items"]), p3["total"]) == (3, 3, 1, 7)
    allp = [x["doc_id"] for p in (1, 2, 3) for x in retrieval.browse(ei, cfg, AFTER, page=p, page_size=3)["items"]]
    assert allp == [x["doc_id"] for x in retrieval.browse(ei, cfg, AFTER, page_size=200)["items"]]


@pytest.mark.parametrize("kw,field", [({"as_of": "2026-10-07T09:00:00"}, "as_of"), ({"mode": "x"}, "mode"),
                                      ({"source_type": "sns"}, "source_type"), ({"start": "2026-13-01"}, "start"),
                                      ({"start": "2026-10-08", "end": "2026-10-01"}, "end"), ({"tickers": ["5930"]}, "tickers"),
                                      ({"status": "deleted"}, "status"), ({"page": 0}, "page"), ({"page_size": 201}, "page_size")])
def test_browse_validation(tmp_path, cfg, kw, field):
    ei = _ei(tmp_path, make_docs(), cfg)
    args = {"as_of": AFTER, **kw}
    with pytest.raises(retrieval.RetrievalError) as e:
        retrieval.browse(ei, cfg, **args)
    assert e.value.code == "validation_failed" and field in e.value.detail["fields"]


def test_visibility_matches_select_as_of(tmp_path, cfg):
    """보관함과 근거 검색이 같은 시점 규칙을 쓰는지: 무작위 시각 20개에서 보이는 (doc_id, version) 이 같다."""
    corpus = _corpus()
    ei = _ei(tmp_path, corpus, cfg)
    rng = random.Random(1)
    for _ in range(20):
        t = T0 + timedelta(minutes=rng.randint(-120, 60 * 30))
        for mode in ("observed", "historical_assumed"):
            got = {(x["doc_id"], x["version"]) for x in retrieval.browse(ei, cfg, t.isoformat(), mode, page_size=200)["items"]}
            want = {(ei.docs[i]["doc_id"], ei.docs[i]["version"]) for i in select_as_of(ei.docs, t, mode)}
            assert got == want


@pytest.mark.parametrize("hours", [0.5, 2.5, 5.5, 30])
def test_truncation_invariance(tmp_path, cfg, hours):
    """T 이후에 처음 본 자료(버전)를 지워도 T 시점 보관함 목록·상세는 같다."""
    t = T0 + timedelta(hours=hours)
    corpus = _corpus()
    past = [d for d in corpus if d["first_seen_at"] <= t.isoformat()]
    a, b = _ei(tmp_path / "a", corpus, cfg), _ei(tmp_path / "b", past, cfg)
    ra = retrieval.browse(a, cfg, t.isoformat(), page_size=200)
    rb = retrieval.browse(b, cfg, t.isoformat(), page_size=200)
    assert ra["items"] == rb["items"] and ra["facets"] == rb["facets"] and ra["total"] == rb["total"]
    for x in ra["items"]:
        da, db = retrieval.document(a, cfg, x["doc_id"], t.isoformat()), retrieval.document(b, cfg, x["doc_id"], t.isoformat())
        assert da == db


def test_document_versions_candidates_and_policy(tmp_path, cfg):
    ei = _ei(tmp_path, _corpus(), cfg)
    d = retrieval.document(ei, cfg, "opendart:20261007000000", AFTER)
    assert [v["version"] for v in d["versions"]] == [1, 2] and d["doc"]["version"] == 2
    early = retrieval.document(ei, cfg, "opendart:20261007000000", (T0 + timedelta(hours=1)).isoformat())
    assert [v["version"] for v in early["versions"]] == [1]
    am = retrieval.document(ei, cfg, "opendart:20261007000003", AFTER)
    assert am["amends_candidate"]["doc_id"] == "opendart:20261007000002" and am["amends_candidate"]["visible"]
    assert am["doc"]["is_amendment"] and "amendment" in am["doc"]["statuses"]
    orig = retrieval.document(ei, cfg, "opendart:20261007000002", AFTER)
    assert [x["doc_id"] for x in orig["amended_by_candidates"]] == ["opendart:20261007000003"]
    # 원공시 쪽에서 정정 후보는 그 정정 공시가 보이기 시작한 뒤에만 나온다 (T0+3h 관측)
    before = retrieval.document(ei, cfg, "opendart:20261007000002", (T0 + timedelta(hours=2, minutes=30)).isoformat())
    assert before["amended_by_candidates"] == []
    # 보관 범위: metadata_only 는 요약을 숨기고, summary_link 는 보인다. 본문은 언제나 내보내지 않는다
    assert d["content_policy"] == {"license_scope": "metadata_only", "summary_shown": False, "body_shown": False}
    assert d["doc"]["summary"] is None and "body_ref" not in d["doc"]
    nw = retrieval.document(ei, cfg, "naver_news:abc123", AFTER)
    assert nw["doc"]["summary"] == "요약 한 줄" and nw["content_policy"]["summary_shown"]


def test_document_hidden_until_available_and_errors(tmp_path, cfg):
    docs = _corpus()
    docs.append({**docs[3], "doc_id": "opendart:20261007000077", "amends_candidate_doc_id": "opendart:20261007000004",
                 "first_seen_at": (T0 + timedelta(hours=8)).isoformat(), "available_at": (T0 + timedelta(hours=8)).isoformat()})
    ei = _ei(tmp_path, docs, cfg)
    with pytest.raises(retrieval.RetrievalError) as e:
        retrieval.document(ei, cfg, "opendart:20261007000077", (T0 + timedelta(hours=7)).isoformat())
    assert e.value.code == "not_available_at_as_of"
    with pytest.raises(retrieval.RetrievalError) as e:
        retrieval.document(ei, cfg, "opendart:99999999999999", AFTER)
    assert e.value.code == "not_found"
    for bad in ("../etc", "OPENDART:1", "opendart:", "opendart:a b"):
        with pytest.raises(retrieval.RetrievalError) as e:
            retrieval.document(ei, cfg, bad, AFTER)
        assert e.value.code == "validation_failed"
    # 후보 원공시가 그 시점에 아직 없으면 visible=False 로만 알리고 내용은 비운다
    late = {**docs[3], "doc_id": "opendart:20261007000088", "amends_candidate_doc_id": "opendart:20261007000077"}
    ei2 = _ei(tmp_path / "x", docs + [late], cfg)
    c = retrieval.document(ei2, cfg, "opendart:20261007000088", (T0 + timedelta(hours=7)).isoformat())["amends_candidate"]
    assert c == {"doc_id": "opendart:20261007000077", "visible": False}
