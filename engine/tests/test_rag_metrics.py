"""P3-11 평가 지표 손계산."""

import math

import pytest

from regime_lab.rag.metrics import abstention, ndcg_at_k, percentile, recall_at_k, reciprocal_rank, unique_clusters_at_k


def test_recall_and_reciprocal_rank():
    ranked = ["a", "b", "c", "d"]
    assert recall_at_k(ranked, {"b", "z"}, 2) == 0.5
    assert reciprocal_rank(ranked, {"c"}) == 1 / 3 and reciprocal_rank(ranked, {"z"}) == 0.0
    with pytest.raises(ValueError):
        recall_at_k(ranked, set(), 5)


def test_ndcg_graded():
    got = ndcg_at_k(["b", "a"], {"a": 2, "b": 1}, 2)
    dcg = (2 ** 1 - 1) / math.log2(2) + (2 ** 2 - 1) / math.log2(3)
    idcg = (2 ** 2 - 1) / math.log2(2) + (2 ** 1 - 1) / math.log2(3)
    assert abs(got - dcg / idcg) < 1e-12
    assert ndcg_at_k(["a"], {}, 5) == 0.0


def test_unique_clusters_counts_unlabeled_as_own():
    assert unique_clusters_at_k(["a", "b", "c", "d"], {"a": "e1", "b": "e1", "c": "e2"}, 4) == 3


def test_abstention_rates():
    got = abstention([(False, True), (False, False), (True, True), (True, False), (True, False)])
    assert got == {"no_answer_abstain": 0.5, "false_abstain": 1 / 3}
    assert abstention([(True, False)])["no_answer_abstain"] is None


def test_percentile_nearest_rank():
    v = [5.0, 1.0, 3.0, 2.0, 4.0]
    assert percentile(v, 50) == 3.0 and percentile(v, 95) == 5.0 and percentile([], 50) is None
