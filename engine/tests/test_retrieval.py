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


def _gold(**changes):
    return {"id": "q1", "query": "대양중공업 수주", "as_of": (T0 + timedelta(days=1)).isoformat(),
            "split": "dev", "relevant": [], **changes}


def test_zero_grade_is_not_a_relevant_answer(tmp_path, cfg):
    ei = retrieval.open_index(_store(tmp_path), cfg)
    zero = {"doc_id": make_docs()[2]["doc_id"], "grade": 0}
    out = retrieval.evaluate(ei, cfg, [_gold(relevant=[zero])], "dev")
    assert out["overall"] == {}
    assert out["abstention"] == {"no_answer_abstain": 0.0, "false_abstain": None}
    out = retrieval.evaluate(ei, cfg, [_gold(relevant=[zero, {"doc_id": "missing", "grade": 1}])], "dev")
    assert out["overall"] == {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    assert len(out["warnings"]) == 1


@pytest.mark.parametrize("gold", [
    {}, [None], [_gold(), _gold()], [_gold(id="")], [_gold(split="typo")],
    [_gold(query="")], [_gold(as_of="2026-10-07T09:00:00")],
    [_gold(tickers="005930")], [_gold(tickers=[None])],
    [_gold(relevant=None)], [_gold(relevant=[{}])], [_gold(relevant=["doc"])],
    *[[_gold(relevant=[{"doc_id": "doc", "grade": grade}])] for grade in (-1, float("nan"), float("inf"), True, "1")],
    [_gold(clusters=[])], [_gold(clusters={"doc": []})], [], [_gold(split="test")],
])
def test_invalid_gold_is_reported_without_scoring(tmp_path, cfg, gold):
    ei = retrieval.open_index(_store(tmp_path), cfg)
    with pytest.raises(retrieval.RetrievalError) as exc:
        retrieval.evaluate(ei, cfg, gold, "dev")
    assert exc.value.code == "validation_failed"
    assert exc.value.detail["fields"]


def test_url_and_doc_id_aliases_have_order_independent_grades(tmp_path, cfg):
    ei = retrieval.open_index(_store(tmp_path), cfg)
    doc = make_docs()[2]
    refs = [{"url": doc["url"], "grade": 1}, {"doc_id": doc["doc_id"], "grade": 0}]
    a = retrieval.evaluate(ei, cfg, [_gold(relevant=refs)], "dev")
    b = retrieval.evaluate(ei, cfg, [_gold(relevant=refs[::-1])], "dev")
    assert a == b
    assert a["overall"] == {"recall": 1.0, "mrr": 1.0, "ndcg": 1.0}


def test_cli_gold_file_error_is_concise(tmp_path, cfg, capsys):
    from argparse import Namespace
    from regime_lab.cli import _rag

    root = write_store(tmp_path / "ext", make_docs())
    path = tmp_path / "gold.json"
    args = Namespace(cmd="rag-eval", docs=root, fields=None, no_cache=True, gold=path, split="dev")
    paths = Namespace(ext_store=root, cache=tmp_path / "cache")
    for payload in (None, b'{bad json', b'\xff'):
        if payload is not None:
            path.write_bytes(payload)
        assert _rag(args, cfg, paths) == 2
        captured = capsys.readouterr()
        assert "validation_failed" in captured.err and "Traceback" not in captured.err


def test_gold_validation_finishes_before_search_and_dev_does_not_read_test_answers(tmp_path, cfg, monkeypatch):
    ei = retrieval.open_index(_store(tmp_path), cfg)
    test_question = {"id": "held-out", "split": "test", "relevant": "not-read-in-dev"}
    assert retrieval.evaluate(ei, cfg, [_gold(), test_question], "dev")["n"] == 1
    with pytest.raises(retrieval.RetrievalError):
        retrieval.evaluate(ei, cfg, [_gold(), test_question], "test", allow_test=True)
    monkeypatch.setattr(retrieval, "search", lambda *a, **kw: pytest.fail("검색 전에 전체 선택 분할 검증 필요"))
    with pytest.raises(retrieval.RetrievalError):
        retrieval.evaluate(ei, cfg, [_gold(), _gold(id="bad", relevant=None)], "dev")


@pytest.mark.parametrize("split", ["dev", "test"])
def test_rag_eval_cli_synthetic_end_to_end(tmp_path, cfg, capsys, monkeypatch, split):
    import json
    from argparse import Namespace
    from regime_lab import cli

    root = write_store(tmp_path / "ext", make_docs())
    path = tmp_path / "gold.json"
    path.write_text(json.dumps([_gold(split=split, relevant=[{"doc_id": make_docs()[2]["doc_id"]}]),
                               _gold(id="absent", split=split, query="없는회사")]), encoding="utf-8")
    monkeypatch.setattr(cli, "load_paths", lambda: Namespace(ext_store=root, cache=tmp_path / "cache"))
    assert cli.main(["rag-eval", "--gold", str(path), "--split", split]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out.split("\n", 1)[1])
    assert out["split"] == split and out["n"] == 2
    assert out["overall"] == {"recall": 1.0, "mrr": 1.0, "ndcg": 1.0}
    assert out["abstention"] == {"no_answer_abstain": 1.0, "false_abstain": 0.0}
    assert set(out["latency_ms"]) == {"p50", "p95"} and not captured.err


@pytest.mark.parametrize("mode", ["observed", "historical_assumed"])
def test_future_versions_preserve_search_and_reopening_refreshes_cache(tmp_path, cfg, mode):
    from rag_synth import make_doc

    docs = [{**d, "status": "active", "is_amendment": False, "amends_candidate_doc_id": None}
            for d in make_docs()]
    t = T0 + timedelta(days=1)
    store = _store(tmp_path, docs)
    old = retrieval.open_index(store, cfg)
    before = retrieval.search(old, cfg, "한빛반도체 자기주식", t, mode)
    revised = {**docs[0], "version": 2, "title": "한빛반도체 신규 보고",
               "first_seen_at": (t + timedelta(hours=1)).isoformat(),
               "available_at": (t + timedelta(hours=1)).isoformat()}
    amendment = {**make_doc(99, "한빛반도체", "[기재정정] 자기주식", "005930", t + timedelta(hours=2)),
                 "status": "active", "is_amendment": True, "amends_candidate_doc_id": docs[0]["doc_id"]}
    write_store(store.ext_store, [revised, amendment, *reversed(docs)])
    fresh = retrieval.open_index(store, cfg)
    cached = retrieval.open_index(store, cfg)
    uncached = retrieval.open_index(store, cfg, use_cache=False)
    assert fresh.key != old.key and not fresh.from_cache and cached.from_cache
    after = retrieval.search(fresh, cfg, "한빛반도체 자기주식", t, mode)
    assert before["items"] == after["items"] and before["status"] == after["status"]
    assert before["index"]["visible_docs"] == after["index"]["visible_docs"]
    assert after == retrieval.search(cached, cfg, "한빛반도체 자기주식", t, mode)
    assert after == retrieval.search(uncached, cfg, "한빛반도체 자기주식", t, mode)
    later = t + timedelta(hours=3)
    rows = retrieval.browse(fresh, cfg, later, mode=mode)["items"]
    assert {r["doc_id"]: r["version"] for r in rows}[docs[0]["doc_id"]] == 2
    assert amendment["doc_id"] in {r["doc_id"] for r in rows}


def test_deleted_rows_and_physical_removal_invalidate_reopened_index(tmp_path, cfg):
    docs = [{**d, "status": "active"} for d in make_docs()]
    store = _store(tmp_path, docs)
    old = retrieval.open_index(store, cfg)
    docs[2]["status"] = "deleted"
    write_store(store.ext_store, docs)
    deleted = retrieval.open_index(store, cfg)
    assert deleted.key != old.key
    assert retrieval.search(deleted, cfg, "대양중공업 수주", T0 + timedelta(days=1))["status"] == "no_evidence"
    write_store(store.ext_store, [d for d in docs if d["status"] != "deleted"])
    removed = retrieval.open_index(store, cfg)
    assert removed.key != deleted.key
    assert retrieval.search(removed, cfg, "대양중공업 수주", T0 + timedelta(days=1))["status"] == "no_evidence"


def test_company_particles_complete_conditions_and_similar_members(tmp_path, cfg):
    from rag_synth import make_doc
    rows = [("대한전선", "대한전선 합성 계약 공시", "001440"),
            ("한전기술", "한전기술 합성 계약 공시", "052690"),
            ("", "전선 합성 계약 공시", ""),
            ("삼성전자", "삼성전자 자사주 매입 합성 공시", "005930"),
            ("", "삼성전자는 자사주 매입 합성 공시", ""),
            ("", "삼성전자 자사주 매입 화성기지 합성 공시", ""),
            ("", "화성 사업 기지 합성 공시", ""),
            ("", "없는 자료 검증 98% 합성 공시", ""),
            ("", "없는회사검증ZXQ987 합성 공시", ""),
            ("", "SK하이닉스 양산 합성 공시", "")]
    docs = [make_doc(i, corp, title, code, T0) for i, (corp, title, code) in enumerate(rows)]
    ei = retrieval.open_index(_store(tmp_path, docs), cfg)

    def found(q, **kw):
        r = retrieval.search(ei, cfg, q, T0, **kw)
        return {d["doc_id"] for x in r["items"] for d in [x, *x["similar"]]}

    for q in ("대한전선", "대한전선이", "대한전선은"):
        assert found(q) == {docs[0]["doc_id"]}
    for q in ("삼성전자 자사주 매입", "삼성전자가 자사주 매입", "삼성전자를 자사주 매입"):
        assert found(q) == {d["doc_id"] for d in docs[3:6]}
    assert found("삼성전자가 자사주 매입 화성기지") == {docs[5]["doc_id"]}
    assert found("없는회사검증ZXQ987") == {docs[8]["doc_id"]}
    assert found("SK하이닉스가 양산") == {docs[9]["doc_id"]}
    assert not found("대한전선", tickers=["052690"])
    assert not found("qzxvplmtrw") and not found("·!")
    assert not found("삼전") and not found("하닉")


def test_no_evidence_for_missing_complete_unit_and_numeric_fragment(tmp_path, cfg):
    from rag_synth import make_doc
    docs = [make_doc(0, "", "삼성전자 자사주 매입", "", T0),
            make_doc(1, "", "화성 성기 기지", "", T0),
            make_doc(2, "", "없는 회사 검증 98%", "", T0)]
    ei = retrieval.open_index(_store(tmp_path, docs), cfg)
    for q in ("삼성전자가 자사주 매입 화성기지", "화성기지", "없는회사검증ZXQ987"):
        assert retrieval.search(ei, cfg, q, T0)["status"] == "no_evidence"


def test_complete_candidate_after_first_hundred_fragment_matches(tmp_path, cfg):
    from rag_synth import make_doc
    docs = [make_doc(i, "", "대한 한전 전선", "", T0) for i in range(120)]
    docs.append(make_doc(120, "", "대한전선 합성 계약 공시 매우 긴 보충 자료 " * 3, "", T0))
    ei = retrieval.open_index(_store(tmp_path, docs), cfg)
    out = retrieval.search(ei, cfg, "대한전선", T0)
    assert len(out["items"]) == 1 and out["items"][0]["doc_id"] == docs[-1]["doc_id"]
    assert out["items"][0]["similar"] == []


@pytest.mark.parametrize("mode", ["observed", "historical_assumed"])
def test_particle_results_cut_invariant_with_future_versions_and_permutation(tmp_path, cfg, mode):
    from rag_synth import make_doc
    docs = [make_doc(0, "", "SK하이닉스 양산 HBM3E", "", T0),
            make_doc(1, "", "SK하이닉스 양산 HBM3E 보충", "", T0)]
    q, t = "SK하이닉스가 HBM3E", T0 + timedelta(hours=1)
    before = retrieval.search(retrieval.open_index(_store(tmp_path / "before", docs), cfg), cfg, q, t, mode)
    assert before["status"] == "ok"
    future = [make_doc(0, "", "새로운 제목", "", t + timedelta(seconds=1), version=2),
              *[make_doc(i, "", "SK하이닉스가 HBM3E 미래 기사", "", t + timedelta(seconds=1)) for i in range(2, 52)]]
    for n, rows in enumerate((docs + future, list(reversed(docs + future)))):
        ei = retrieval.open_index(_store(tmp_path / str(n), rows), cfg)
        after = retrieval.search(ei, cfg, q, t, mode)
        assert after["items"] == before["items"]
        assert after["index"]["visible_docs"] == before["index"]["visible_docs"]


def test_version_three_skips_old_cache_and_particle_config_changes_key(tmp_path, cfg):
    store = _store(tmp_path)
    a = retrieval.open_index(store, cfg)
    old_key = retrieval._hash({"index_version": 2, "rag": cfg["rag"], "docs": a.docs_fingerprint})
    assert a.key != old_key and retrieval.INDEX_VERSION == 3
    store.write_cache(old_key, {"not": "a usable index"})
    uncached = retrieval.open_index(store, cfg, use_cache=False)
    cached = retrieval.open_index(store, cfg)
    assert cached.from_cache and cached.index == uncached.index
    q = "한빛반도체가 자기주식"
    assert retrieval.search(cached, cfg, q, T0) == retrieval.search(uncached, cfg, q, T0)
    assert retrieval.search(cached, cfg, q, T0)["status"] == "ok"
    changed = {**cfg, "rag": {**cfg["rag"], "query_particles": []}}
    assert retrieval.open_index(store, changed).key != a.key
    assert retrieval.search(cached, changed, q, T0)["status"] == "no_evidence"
