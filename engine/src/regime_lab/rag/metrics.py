"""검색 평가 지표 (P3-11). ranked 는 순위대로 정렬한 정답 식별자(url 또는 doc_id) 목록."""

from __future__ import annotations

import math


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        raise ValueError("정답이 없는 질문은 recall 을 계산하지 않는다 (근거 없음 지표로 본다)")
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    return next((1 / r for r, x in enumerate(ranked, 1) if x in relevant), 0.0)


def ndcg_at_k(ranked: list[str], grades: dict[str, float], k: int) -> float:
    """등급 관련도. DCG = Σ (2^g − 1) / log2(순위 + 1), 이상적 순서로 나눈다."""
    dcg = sum((2 ** grades.get(x, 0) - 1) / math.log2(r + 1) for r, x in enumerate(ranked[:k], 1))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(r + 1) for r, g in enumerate(ideal, 1))
    return dcg / idcg if idcg else 0.0


def unique_clusters_at_k(ranked: list[str], clusters: dict[str, str], k: int) -> int:
    """상위 k 안의 서로 다른 사건 수. 사건 표시가 없는 결과는 각자 다른 사건으로 센다."""
    return len({clusters.get(x, f"_self:{x}") for x in ranked[:k]})


def abstention(cases: list[tuple[bool, bool]]) -> dict[str, float | None]:
    """cases: (정답이 있는 질문인가, 근거 없음을 돌려줬는가).

    no_answer_abstain: 답 없는 질문에서 근거 없음을 돌려준 비율(높을수록 좋음)
    false_abstain: 답 있는 질문을 근거 없음으로 처리한 비율(낮을수록 좋음)
    """
    no_ans = [abst for has, abst in cases if not has]
    has_ans = [abst for has, abst in cases if has]
    return {"no_answer_abstain": sum(no_ans) / len(no_ans) if no_ans else None,
            "false_abstain": sum(has_ans) / len(has_ans) if has_ans else None}


def percentile(values: list[float], p: float) -> float | None:
    """가장 가까운 순위 방식 백분위 (p: 0~100)."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]
