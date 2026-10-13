"""P3-11 근거 검색 유스케이스: 색인 캐시 키, data_unavailable, 검색 응답, 평가 분할 잠금."""

from datetime import timedelta

import pytest
from rag_synth import T0, make_docs, write_store

from regime_lab import retrieval
from regime_lab.config import load_config
from regime_lab.data.loader import DocsStore
from regime_lab.rag.rank import build_index
from regime_lab.rag.text import tokenize, doc_text


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


def test_cached_index_matches_built(tmp_path, cfg):
    """캐시에서 연 색인(정수 배열 postings)이 새로 만든 색인과 같고, 검색 결과도 같다."""
    store = _store(tmp_path)
    a, b = retrieval.open_index(store, cfg), retrieval.open_index(store, cfg)
    assert b.from_cache and a.index == b.index
    q, t = "한빛반도체 자기주식", (T0 + timedelta(days=1)).isoformat()
    assert retrieval.search(a, cfg, q, t) == retrieval.search(b, cfg, q, t)


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


def _memory_index(docs):
    fields = ("title", "corp_name")
    index = build_index((tokenize(doc_text(d, fields)) for d in docs),
                        (tokenize(d["title"]) for d in docs))
    return retrieval.EvidenceIndex(docs, [(d["doc_id"], d["version"]) for d in docs],
                                   index, fields, "test", "test", {}, False)


def test_latest_search_selects_before_limit_and_is_cut_invariant(cfg):
    from rag_synth import make_doc
    # 관련도 후보 한도보다 많은 오래된 고득점 문서 뒤에 낮은 점수의 최신 문서를 둔다.
    count = cfg["rag"]["group_candidate_k"] + 1
    docs = [make_doc(i, "", "반도체", "", T0, published_at="2026-10-01") for i in range(count)]
    latest = make_doc(count, "", "반도체 설비 투자 신규 공장 확장 소식", "", T0,
                      published_at="2026-10-07T01:00:00Z", time_precision="datetime")
    docs.append(latest)
    before = retrieval.search(_memory_index(docs), cfg, "반도체", T0, k=1, sort="published_desc")
    assert before["items"][0]["doc_id"] == latest["doc_id"]
    assert retrieval.search(_memory_index(docs), cfg, "반도체", T0, k=1)["items"][0]["doc_id"] != latest["doc_id"]
    future = make_doc(count + 1, "", "반도체", "", T0 + timedelta(hours=1),
                      published_at="2026-10-08T00:00:00Z")
    after = retrieval.search(_memory_index(list(reversed(docs + [future]))), cfg,
                              "반도체", T0, k=1, sort="published_desc")
    assert before["items"] == after["items"]


def test_latest_search_timezone_missing_dates_and_browse(cfg):
    from rag_synth import make_doc
    values = ["2026-10-07T08:30:00+09:00", "2026-10-07T00:00:00Z", "2026-10-07", None, "bad"]
    docs = [make_doc(i, "", "반도체", "", T0, published_at=value) for i, value in enumerate(values)]
    cfg = {**cfg, "rag": {**cfg["rag"], "group_threshold": None}}
    ei = _memory_index(docs)
    expected = [docs[i]["doc_id"] for i in (1, 0, 2, 4, 3)]
    assert [d["doc_id"] for d in retrieval.search(ei, cfg, "반도체", T0, sort="published_desc")["items"]] == expected
    assert [d["doc_id"] for d in retrieval.browse(ei, cfg, T0)["items"]] == expected
    with pytest.raises(retrieval.RetrievalError) as e:
        retrieval.search(ei, cfg, "반도체", T0, sort="unknown")
    assert "sort" in e.value.detail["fields"]


def test_latest_group_representative_is_newest(cfg):
    from rag_synth import make_doc
    docs = [make_doc(i, "", "반도체 생산 설비 확장 발표", "", T0,
                     published_at=f"2026-10-0{i + 1}") for i in range(3)]
    items = retrieval.search(_memory_index(docs), cfg, "반도체", T0, sort="published_desc")["items"]
    assert len(items) == 1 and items[0]["doc_id"] == docs[2]["doc_id"]
    assert [s["doc_id"] for s in items[0]["similar"]] == [docs[1]["doc_id"], docs[0]["doc_id"]]
