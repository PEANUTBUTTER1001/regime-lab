"""P3-11 근거 검색 유스케이스: 색인 캐시 키, data_unavailable, 검색 응답, 평가 분할 잠금."""

from datetime import timedelta

import pytest
from rag_synth import T0, make_docs, write_store

from regime_lab import retrieval
from regime_lab.config import load_config
from regime_lab.data.loader import DocsStore


@pytest.fixture()
def cfg():
    return load_config()


def _store(tmp_path, docs=None):
    return DocsStore(write_store(tmp_path / "ext", docs or make_docs()), tmp_path / "cache")


def test_missing_store_is_unavailable_not_empty(tmp_path, cfg):
    with pytest.raises(retrieval.RetrievalError) as e:
        retrieval.open_index(DocsStore(None, tmp_path), cfg)
    assert e.value.code == "data_unavailable"
    with pytest.raises(retrieval.RetrievalError):
        retrieval.open_index(DocsStore(write_store(tmp_path / "bad", make_docs(), schema_version=9), tmp_path), cfg)


def test_cache_key_stable_and_changes_with_inputs(tmp_path, cfg):
    store = _store(tmp_path)
    a = retrieval.open_index(store, cfg)
    b = retrieval.open_index(store, cfg)
    assert a.key == b.key and not a.from_cache and b.from_cache
    assert retrieval.open_index(store, {**cfg, "rag": {**cfg["rag"], "k1": 1.2}}).key != a.key
    assert retrieval.open_index(store, cfg, fields=["title"]).key != a.key


def test_search_response_fields_and_validation(tmp_path, cfg):
    ei = retrieval.open_index(_store(tmp_path), cfg)
    res = retrieval.search(ei, cfg, "한빛반도체 자기주식", (T0 + timedelta(days=1)).isoformat())
    assert res["status"] == "ok" and res["items"][0]["corp_name"] == "한빛반도체"
    assert {"doc_id", "version", "url", "available_at", "score", "matched_tokens", "similar_count", "similar"} <= res["items"][0].keys()
    assert res["index"]["grouping"] == cfg["rag"]["group_threshold"]
    assert retrieval.search(ei, cfg, "한빛반도체", T0 - timedelta(hours=1))["status"] == "no_evidence"
    with pytest.raises(retrieval.RetrievalError) as e:
        retrieval.search(ei, cfg, "", "2026-10-07T09:00:00", mode="x", tickers=["12"], k=99)
    assert set(e.value.detail["fields"]) == {"q", "as_of", "mode", "tickers", "k"}


def test_evaluate_and_test_split_lock(tmp_path, cfg):
    ei = retrieval.open_index(_store(tmp_path), cfg)
    docs = make_docs()
    as_of = (T0 + timedelta(days=1)).isoformat()
    gold = [{"id": "q1", "type": "종목명", "query": "대양중공업 수주", "as_of": as_of, "split": "dev",
             "relevant": [{"url": docs[2]["url"]}]},
            {"id": "q2", "type": "답없음", "query": "없는회사", "as_of": as_of, "split": "dev", "relevant": []},
            {"id": "q3", "type": "종목명", "query": "새벽바이오", "as_of": as_of, "split": "test",
             "relevant": [{"doc_id": docs[4]["doc_id"]}]}]
    out = retrieval.evaluate(ei, cfg, gold, "dev")
    assert out["n"] == 2 and out["overall"]["recall"] == 1.0 and out["overall"]["mrr"] == 1.0
    assert out["abstention"] == {"no_answer_abstain": 1.0, "false_abstain": 0.0}
    with pytest.raises(retrieval.RetrievalError) as e:
        retrieval.evaluate(ei, cfg, gold, "test")
    assert e.value.code == "test_split_locked"
    assert retrieval.evaluate(ei, cfg, gold, "test", allow_test=True)["n"] == 1


def test_evaluate_counts_answer_hidden_in_similar_group(tmp_path, cfg):
    """정답 기사가 대표가 아니라 '비슷한 기사' 묶음 안에 있어도 그 순위에서 찾은 것으로 센다."""
    from rag_synth import make_doc
    docs = [make_doc(0, "삼성전자", "삼성전자, 자사주 계획물량 모두 매입", "", T0),
            make_doc(1, "삼성전자", "삼성전자, 자사주 계획 물량 모두 매입…수급 우려", "", T0)]
    ei = retrieval.open_index(_store(tmp_path, docs), cfg)
    as_of = (T0 + timedelta(hours=1)).isoformat()
    res = retrieval.search(ei, cfg, "삼성전자 자사주 매입", as_of)
    assert len(res["items"]) == 1 and res["items"][0]["similar_count"] == 1
    hidden = res["items"][0]["similar"][0]["url"]
    gold = [{"id": "g", "query": "삼성전자 자사주 매입", "as_of": as_of, "split": "dev", "relevant": [{"url": hidden}]}]
    out = retrieval.evaluate(ei, cfg, gold, "dev")
    assert out["overall"]["recall"] == 1.0 and out["overall"]["mrr"] == 1.0
