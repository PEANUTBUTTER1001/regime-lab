"""P3-13 자료 보관함·자료 상세 API 계약 테스트 (SRS FR-N7). 원본·실제 수집 자료 없이 합성 공시 문서로 돈다."""

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab.config import Paths, load_config

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine" / "tests"))
from rag_synth import T0, make_docs, write_store  # noqa: E402
from synth import make_market  # noqa: E402

ERROR_KEYS = {"code", "message", "detail", "retryable"}
LIST_KEYS = {"status", "items", "total", "page", "page_size", "pages", "facets", "index", "disclaimer"}
ITEM_KEYS = {"doc_id", "version", "source", "source_type", "title", "corp_name", "url", "published_at", "time_precision",
             "first_seen_at", "available_at", "license_scope", "ticker_status", "tickers", "statuses",
             "versions_visible", "has_amendment_candidate"}
DETAIL_KEYS = {"doc", "versions", "amends_candidate", "amended_by_candidates", "content_policy", "as_of", "mode",
               "disclaimer"}
AS_OF = (T0 + timedelta(days=1)).isoformat()
EXTRA = {"is_amendment": False, "ticker_status": "resolved", "amends_candidate_doc_id": None, "backfilled": False,
         "content_hash": "h"}


def _docs():
    docs = [{**EXTRA, **d} for d in make_docs()]
    docs[3] = {**docs[3], "is_amendment": True, "amends_candidate_doc_id": docs[2]["doc_id"]}
    v2 = {**docs[0], "version": 2, "content_hash": "h2", "first_seen_at": (T0 + timedelta(hours=5)).isoformat(),
          "available_at": (T0 + timedelta(hours=5)).isoformat()}
    return docs + [v2]


@pytest.fixture(scope="module")
def market():
    return make_market(load_config(), n_tickers=5, seed=3)


def _app(tmp_path, market, ext_store):
    paths = Paths(tmp_path / "store", tmp_path / "dump.sql", tmp_path / "cache", tmp_path / "runs", ext_store)
    return create_app(Settings(paths=paths), prep=market, serve_web=False)


@pytest.fixture()
def client(tmp_path, market):
    app = _app(tmp_path, market, write_store(tmp_path / "ext", _docs()))
    app.state.rl.load_rag()
    with TestClient(app) as c:
        yield c


def test_list_contract(client):
    r = client.get("/api/evidence/documents", params={"as_of": AS_OF})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == LIST_KEYS and body["status"] == "ok" and body["disclaimer"]
    assert body["total"] == 5 and all(set(it) == ITEM_KEYS for it in body["items"])
    assert body["facets"]["status"]["amendment"] == 1 and body["facets"]["status"]["revised"] == 1
    assert body["index"]["mode"] == "observed" and body["index"]["as_of"] == AS_OF


def test_list_filters_and_paging(client):
    r = client.get("/api/evidence/documents", params={"as_of": AS_OF, "tickers": "005930", "status": "revised"})
    assert [x["doc_id"] for x in r.json()["items"]] == ["opendart:20261007000000"]
    p = client.get("/api/evidence/documents", params={"as_of": AS_OF, "page": 2, "page_size": 2}).json()
    assert (p["page"], p["pages"], len(p["items"])) == (2, 3, 2)
    early = client.get("/api/evidence/documents", params={"as_of": (T0 - timedelta(hours=1)).isoformat()})
    assert early.status_code == 200 and early.json()["status"] == "no_documents" and early.json()["items"] == []


@pytest.mark.parametrize("params,field", [
    ({"as_of": "2026-10-08T09:00:00"}, "as_of"), ({"as_of": AS_OF, "mode": "x"}, "mode"),
    ({"as_of": AS_OF, "source_type": "sns"}, "source_type"), ({"as_of": AS_OF, "start": "20261001"}, "start"),
    ({"as_of": AS_OF, "status": "deleted"}, "status"), ({"as_of": AS_OF, "page_size": 999}, "page_size"),
    ({"as_of": AS_OF, "tickers": "12"}, "tickers"),
])
def test_list_validation_422(client, params, field):
    r = client.get("/api/evidence/documents", params=params)
    assert r.status_code == 422 and set(r.json()) == ERROR_KEYS
    assert r.json()["code"] == "validation_failed" and field in r.json()["detail"]["fields"]


def test_detail_contract_and_time_rules(client):
    r = client.get("/api/evidence/documents/opendart:20261007000000", params={"as_of": AS_OF})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == DETAIL_KEYS and [v["version"] for v in body["versions"]] == [1, 2]
    assert body["content_policy"] == {"license_scope": "metadata_only", "summary_shown": False, "body_shown": False}
    early = client.get("/api/evidence/documents/opendart:20261007000000",
                       params={"as_of": (T0 + timedelta(hours=1)).isoformat()}).json()
    assert [v["version"] for v in early["versions"]] == [1]  # 미래 버전은 보이지 않는다
    am = client.get("/api/evidence/documents/opendart:20261007000003", params={"as_of": AS_OF}).json()
    assert am["amends_candidate"]["doc_id"] == "opendart:20261007000002" and am["amends_candidate"]["visible"]


def test_detail_404_and_422(client):
    r = client.get("/api/evidence/documents/opendart:20261007000004", params={"as_of": (T0 + timedelta(hours=1)).isoformat()})
    assert r.status_code == 404 and r.json()["code"] == "not_available_at_as_of" and set(r.json()) == ERROR_KEYS
    r = client.get("/api/evidence/documents/opendart:99999999999999", params={"as_of": AS_OF})
    assert r.status_code == 404 and r.json()["code"] == "document_not_found"
    r = client.get("/api/evidence/documents/OPENDART:x", params={"as_of": AS_OF})
    assert r.status_code == 422 and "doc_id" in r.json()["detail"]["fields"]


def test_unavailable_and_warming_up(tmp_path, market):
    app = _app(tmp_path, market, None)
    app.state.rl.load_rag()
    with TestClient(app) as c:
        r = c.get("/api/evidence/documents", params={"as_of": AS_OF})
    assert r.status_code == 503 and r.json()["code"] == "data_unavailable"
    app2 = _app(tmp_path / "w", market, write_store(tmp_path / "ext2", _docs()))
    r = TestClient(app2).get("/api/evidence/documents", params={"as_of": AS_OF})
    assert r.status_code == 503 and r.json()["code"] == "warming_up" and r.json()["retryable"] is True


COVERAGE_KEYS = {"status", "sources", "as_of", "mode", "start", "end", "list_limit", "disclaimer"}
SOURCE_KEYS = {"counts", "gap_days", "partial_days", "first_day", "last_day", "markets"}


def _write_coverage(root):
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = root / "coverage" / "source=opendart"
    d.mkdir(parents=True, exist_ok=True)
    rows = [("2026-10-06", "Y", "gap", "2026-10-06T23:00:00+09:00"), ("2026-10-07", "Y", "forward", "2026-10-07T09:05:00+09:00")]
    pq.write_table(pa.Table.from_pylist([{"seq": i + 1, "source": "opendart", "day": a, "corp_cls": b, "state": c, "run_id": "r",
                                          "observed_at": o} for i, (a, b, c, o) in enumerate(rows)]), d / "coverage_log.parquet")


def test_coverage_contract(tmp_path, market):
    ext = write_store(tmp_path / "ext", _docs())
    _write_coverage(ext)
    app = _app(tmp_path, market, ext)
    with TestClient(app) as c:
        r = c.get("/api/evidence/coverage", params={"as_of": AS_OF})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == COVERAGE_KEYS and body["status"] == "ok"
        od = body["sources"]["opendart"]
        assert set(od) == SOURCE_KEYS and od["gap_days"] == ["2026-10-06"] and od["counts"]["forward"] == 1
        r = c.get("/api/evidence/coverage", params={"as_of": AS_OF, "start": "x"})
        assert r.status_code == 422 and "start" in r.json()["detail"]["fields"]
        none = c.get("/api/evidence/coverage", params={"as_of": "2026-10-01T00:00:00+09:00"}).json()
        assert none["status"] == "no_coverage" and none["sources"] == {}


def test_coverage_unavailable_without_ext_store(tmp_path, market):
    with TestClient(_app(tmp_path, market, None)) as c:
        r = c.get("/api/evidence/coverage", params={"as_of": AS_OF})
    assert r.status_code == 503 and r.json()["code"] == "data_unavailable"
