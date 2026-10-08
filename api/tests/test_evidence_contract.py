"""P3-11 근거 검색 API 계약 테스트 (SRS FR-N5). 원본 시세·실제 수집 자료 없이 합성 공시 문서로 돈다."""

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
ITEM_KEYS = {"doc_id", "version", "source", "title", "corp_name", "url", "published_at", "time_precision",
             "first_seen_at", "available_at", "score", "matched_tokens", "similar_count", "similar"}
SIMILAR_KEYS = {"doc_id", "title", "source", "url", "published_at", "time_precision", "available_at"}
INDEX_KEYS = {"index_version", "docs_fingerprint", "fields", "total_docs", "visible_docs", "mode", "as_of", "grouping"}
AS_OF = (T0 + timedelta(days=1)).isoformat()


@pytest.fixture(scope="module")
def market():
    return make_market(load_config(), n_tickers=5, seed=3)


def _app(tmp_path, market, ext_store):
    paths = Paths(tmp_path / "store", tmp_path / "dump.sql", tmp_path / "cache", tmp_path / "runs", ext_store)
    return create_app(Settings(paths=paths), prep=market, serve_web=False)


@pytest.fixture()
def client(tmp_path, market):
    app = _app(tmp_path, market, write_store(tmp_path / "ext", make_docs()))
    app.state.rl.load_rag()  # 백그라운드 적재를 기다리지 않고 바로 불러온다
    with TestClient(app) as c:
        yield c


def test_search_ok_contract(client):
    r = client.get("/api/evidence/search", params={"q": "한빛반도체 자기주식", "as_of": AS_OF})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["disclaimer"]
    assert set(body["index"]) == INDEX_KEYS and body["index"]["mode"] == "observed"
    assert body["items"] and all(set(it) == ITEM_KEYS for it in body["items"])
    assert body["items"][0]["corp_name"] == "한빛반도체" and body["items"][0]["matched_tokens"]
    assert all(it["similar_count"] == len(it["similar"]) and all(set(s) == SIMILAR_KEYS for s in it["similar"])
               for it in body["items"])


def test_tickers_and_k(client):
    r = client.get("/api/evidence/search", params={"q": "공시 계약 수주 실적", "as_of": AS_OF, "tickers": "010140", "k": 1})
    items = r.json()["items"]
    assert r.status_code == 200 and len(items) == 1 and items[0]["corp_name"] == "대양중공업"


def test_no_evidence_is_200_with_empty_items(client):
    r = client.get("/api/evidence/search", params={"q": "한빛반도체", "as_of": (T0 - timedelta(hours=1)).isoformat()})
    assert r.status_code == 200 and r.json()["status"] == "no_evidence" and r.json()["items"] == []


@pytest.mark.parametrize("params,field", [
    ({"q": "", "as_of": AS_OF}, "q"),
    ({"q": "x" * 201, "as_of": AS_OF}, "q"),
    ({"q": "한빛", "as_of": "2026-10-08T09:00:00"}, "as_of"),
    ({"q": "한빛", "as_of": AS_OF, "mode": "published"}, "mode"),
    ({"q": "한빛", "as_of": AS_OF, "tickers": "12"}, "tickers"),
    ({"q": "한빛", "as_of": AS_OF, "k": 99}, "k"),
])
def test_validation_422(client, params, field):
    r = client.get("/api/evidence/search", params=params)
    assert r.status_code == 422 and set(r.json()) == ERROR_KEYS
    assert r.json()["code"] == "validation_failed" and field in r.json()["detail"]["fields"]


def test_data_unavailable_503_not_empty(tmp_path, market):
    app = _app(tmp_path, market, None)
    app.state.rl.load_rag()
    with TestClient(app) as c:
        r = c.get("/api/evidence/search", params={"q": "한빛", "as_of": AS_OF})
    assert r.status_code == 503 and r.json()["code"] == "data_unavailable" and r.json()["retryable"] is False


def test_warming_up_503(tmp_path, market):
    app = _app(tmp_path, market, write_store(tmp_path / "ext", make_docs()))
    c = TestClient(app)  # with 없이: 시작 이벤트(백그라운드 적재)를 돌리지 않는다
    r = c.get("/api/evidence/search", params={"q": "한빛", "as_of": AS_OF})
    assert r.status_code == 503 and r.json()["code"] == "warming_up" and r.json()["retryable"] is True
