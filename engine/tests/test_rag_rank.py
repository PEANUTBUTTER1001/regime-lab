"""P3-11 BM25·MMR 와 절단 불변. 핵심 테스트(CI): as_of 뒤 문서가 늘어도 과거 시점 순위·점수가 그대로여야 한다."""

import math
from datetime import timedelta

from rag_synth import T0, make_doc, make_docs

from regime_lab.rag.rank import bm25, build_index, search
from regime_lab.rag.select import select_as_of
from regime_lab.rag.text import doc_text, tokenize

FIELDS = ["title", "corp_name"]
P = dict(k1=1.5, b=0.75, top_k=5, candidate_k=20, mmr_lambda=None, min_score=0.0)


def _index(docs):
    texts = [tokenize(doc_text(d, FIELDS)) for d in docs]
    return build_index(texts, [tokenize(d["title"]) for d in docs]), [(d["doc_id"], d["version"]) for d in docs]


def _run(docs, q, as_of, **kw):
    index, keys = _index(docs)
    visible = set(select_as_of(docs, as_of))
    return [(docs[h.row]["doc_id"], h.score, h.matched) for h in search(index, keys, tokenize(q), visible, **{**P, **kw})]


def test_bm25_matches_hand_calculation():
    index = build_index([["aa", "bb"], ["aa"], ["cc", "cc", "dd"]], [[], [], []])
    got = bm25(index, ["aa"], {0, 1, 2}, k1=1.5, b=0.75)
    idf = math.log(1 + (3 - 2 + 0.5) / (2 + 0.5))      # N=3, df=2
    avgdl = 2.0                                          # (2 + 1 + 3) / 3
    assert abs(got[0][0] - idf * 2.5 / (1 + 1.5 * (0.25 + 0.75 * 2 / avgdl))) < 1e-9
    assert abs(got[1][0] - idf * 2.5 / (1 + 1.5 * (0.25 + 0.75 * 1 / avgdl))) < 1e-9
    assert 2 not in got and got[0][1] == ["aa"]


def test_future_docs_do_not_change_past_search():
    """절단 불변: as_of 뒤에 질의어가 든 문서 50건을 더해도 순위와 점수가 비트 단위로 같다."""
    docs = make_docs()
    as_of = T0 + timedelta(hours=2, minutes=30)  # 앞의 3건만 보임
    before = _run(docs, "한빛반도체 자기주식", as_of)
    assert before and {d for d, _, _ in before} <= {docs[0]["doc_id"], docs[1]["doc_id"]}
    future = docs + [make_doc(100 + n, "한빛반도체", "자기주식취득결정 자기주식 소각", "005930", as_of + timedelta(minutes=1))
                     for n in range(50)]
    assert _run(future, "한빛반도체 자기주식", as_of) == before
    assert _run(list(reversed(future)), "한빛반도체 자기주식", as_of) == before  # 파일·행 순서와 무관


def test_full_corpus_statistics_would_leak():
    """대조군: 통계를 전체 문서로 내면 같은 문서의 점수가 바뀐다 — 위 테스트가 실제로 누설을 잡는다는 확인."""
    docs = make_docs()
    as_of = T0 + timedelta(hours=2, minutes=30)
    future = docs + [make_doc(100 + n, "한빛반도체", "자기주식취득결정", "005930", as_of + timedelta(minutes=1))
                     for n in range(50)]
    index, _ = _index(future)
    q = tokenize("한빛반도체 자기주식")
    visible = set(select_as_of(future, as_of))
    asof_stats = bm25(index, q, visible, 1.5, 0.75)
    full_stats = {i: v for i, v in bm25(index, q, set(range(len(future))), 1.5, 0.75).items() if i in visible}
    assert asof_stats.keys() == full_stats.keys()
    assert any(abs(asof_stats[i][0] - full_stats[i][0]) > 1e-6 for i in asof_stats)


def test_no_evidence_when_nothing_matches_or_nothing_visible():
    docs = make_docs()
    assert _run(docs, "전혀없는질의어", T0 + timedelta(days=1)) == []
    assert _run(docs, "한빛반도체", T0 - timedelta(minutes=1)) == []          # 보이는 문서 0건
    assert _run(docs, "한빛반도체", T0 + timedelta(days=1), min_score=1e9) == []  # 문턱값 미만


def test_ties_break_by_doc_id_and_matched_tokens_reported():
    docs = [make_doc(2, "같은회사", "같은제목", "", T0), make_doc(1, "같은회사", "같은제목", "", T0)]
    got = _run(docs, "같은제목", T0)
    assert [d for d, _, _ in got] == sorted(d["doc_id"] for d in docs)
    assert got[0][1] == got[1][1] and got[0][2] == ("같은", "은제", "제목")


def test_mmr_lambda_one_equals_bm25_and_low_lambda_diversifies():
    docs = [make_doc(0, "한빛반도체", "자기주식취득결정", "", T0), make_doc(1, "한빛반도체", "자기주식취득결정 공시", "", T0),
            make_doc(2, "한빛반도체", "임원ㆍ주요주주 특정증권 소유상황", "", T0)]
    q, as_of = "한빛반도체 자기주식", T0 + timedelta(hours=1)
    plain = [d for d, _, _ in _run(docs, q, as_of)]
    assert [d for d, _, _ in _run(docs, q, as_of, mmr_lambda=1.0)] == plain
    diverse = [d for d, _, _ in _run(docs, q, as_of, mmr_lambda=0.1, top_k=2)]
    assert diverse[1] == docs[2]["doc_id"]  # 거의 같은 제목 대신 다른 공시


SAME_EVENT = [("삼성전자", "삼성전자, 자사주 계획물량 모두 매입"), ("삼성전자", "삼성전자, 자사주 계획 물량 모두 매입…수급 우려"),
              ("삼성전자", "삼성전자 15조원 자사주 매입, 다음주 6일 종료"), ("한빛반도체", "한빛반도체 자사주 매입 결정 공시")]


def test_grouping_merges_similar_titles_ignoring_query_tokens():
    docs = [make_doc(i, c, t, "", T0) for i, (c, t) in enumerate(SAME_EVENT)]
    got = _run_hits(docs, "삼성전자 자사주 매입", T0, group_threshold=0.3)
    reps = [docs[h.row]["title"] for h in got]
    first = got[0]
    assert {docs[m]["title"] for m in first.members} == {SAME_EVENT[1][1]}  # '계획 물량 모두' 가 겹침
    assert SAME_EVENT[2][1] in reps and SAME_EVENT[3][1] in reps          # 다른 내용은 따로 남음
    assert len(got) + sum(len(h.members) for h in got) == len(docs)
    # 질의 글자까지 넣고 재면 전부 비슷해 보이는 문제를 막는지: 문턱을 낮춰도 질의 글자만 겹치는 제목은 안 묶임
    assert all(len(h.members) == 0 for h in _run_hits(docs[2:], "삼성전자 자사주 매입", T0, group_threshold=0.9))


def test_grouping_is_cut_invariant():
    """묶음도 as_of 에 보이는 후보로만 만든다: 미래의 비슷한 기사 50건이 과거 묶음·대표를 바꾸지 않는다."""
    docs = [make_doc(i, c, t, "", T0) for i, (c, t) in enumerate(SAME_EVENT)]
    as_of = T0 + timedelta(minutes=30)
    before = [(h.row, h.score, h.members) for h in _run_hits(docs, "삼성전자 자사주 매입", as_of, group_threshold=0.3)]
    future = docs + [make_doc(100 + n, "삼성전자", f"삼성전자 자사주 매입 15조원 종료 다음주 {n}", "", as_of + timedelta(minutes=1))
                     for n in range(50)]
    after = [(h.row, h.score, h.members) for h in _run_hits(future, "삼성전자 자사주 매입", as_of, group_threshold=0.3)]
    assert after == before


def _run_hits(docs, q, as_of, **kw):
    index, keys = _index(docs)
    return search(index, keys, tokenize(q), set(select_as_of(docs, as_of)), **{**P, **kw})
