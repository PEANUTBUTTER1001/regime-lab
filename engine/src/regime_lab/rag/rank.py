"""역색인·BM25·MMR (P3-11).

미래 정보 누설 경로가 둘이다: ① 보이는 문서(select.py 가 막음) ② 점수 통계. BM25 의 N·df·평균 길이는
말뭉치 통계라 전체 문서로 계산하면 as_of 뒤 문서가 늘 때 과거 시점 순위가 바뀐다. 그래서 역색인은 한 번만
만들고, 통계는 질의마다 as_of 에 보이는 행(visible) 안에서만 센다.

제목이 비슷한 기사 묶기도 같은 이유로 미리 전체 문서로 하지 않고, 질의 때 보이는 후보만으로 한다
(나중에 들어온 기사가 과거 시점의 묶음을 바꾸지 않게).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Index:
    postings: dict[str, list[tuple[int, int]]]  # 토큰 → [(행, 빈도)] (행 오름차순)
    lengths: list[int]                           # 행별 토큰 수
    title_tokens: list[frozenset[str]]           # MMR 중복 판정용 제목 토큰 집합


@dataclass(frozen=True)
class Hit:
    row: int
    score: float
    matched: tuple[str, ...]  # 문서에 실제로 있던 질의 토큰 (검색 근거)
    members: tuple[int, ...] = ()  # 대표 행과 제목이 비슷해 묶인 다른 행 (순위 순)


def build_index(texts: list[list[str]], titles: list[list[str]]) -> Index:
    postings: dict[str, list[tuple[int, int]]] = {}
    for i, toks in enumerate(texts):
        for t, c in sorted(Counter(toks).items()):
            postings.setdefault(t, []).append((i, c))
    return Index(postings, [len(t) for t in texts], [frozenset(t) for t in titles])


def bm25(index: Index, q_tokens: list[str], visible: set[int], k1: float, b: float) -> dict[int, tuple[float, list[str]]]:
    """visible 안에서만 통계를 낸 점수 {행: (점수, 맞은 토큰)}."""
    n = len(visible)
    if n == 0:
        return {}
    avgdl = sum(index.lengths[i] for i in visible) / n or 1.0
    out: dict[int, tuple[float, list[str]]] = {}
    for t in sorted(set(q_tokens)):  # 토큰 순서를 고정해 부동소수 합을 재현 가능하게
        plist = [(i, tf) for i, tf in index.postings.get(t, ()) if i in visible]
        if not plist:
            continue
        idf = math.log(1 + (n - len(plist) + 0.5) / (len(plist) + 0.5))
        for i, tf in plist:
            s = idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * index.lengths[i] / avgdl))
            score, matched = out.get(i, (0.0, []))
            out[i] = (score + s, matched + [t])
    return out


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def mmr(cands: list[int], rel: dict[int, float], token_sets: list[frozenset[str]], lam: float, k: int) -> list[int]:
    """관련도는 높고 이미 고른 것과는 먼 행을 하나씩 고른다. cands 순서가 동점 순서다."""
    picked: list[int] = []
    pool = list(cands)
    while pool and len(picked) < k:
        def gain(i: int) -> float:
            dup = max((jaccard(token_sets[i], token_sets[p]) for p in picked), default=0.0)
            return lam * rel[i] - (1 - lam) * dup
        best = max(pool, key=gain)
        picked.append(best)
        pool.remove(best)
    return picked


def group_similar(rows: list[int], token_sets: list[frozenset[str]], exclude: set[str], threshold: float) -> list[list[int]]:
    """순위 순서대로 훑으며, 앞선 묶음의 대표(첫 행)와 제목 자카드가 threshold 이상이면 그 묶음에 넣는다.

    질의 토큰(exclude)은 뺀다 — 결과는 모두 질의 글자를 공유하므로 그대로 재면 전부 한 묶음이 된다.
    글자 겹침만 보므로 같은 사건이라는 판정이 아니라 '제목이 비슷함'이다.
    """
    groups: list[list[int]] = []
    for i in rows:
        a = token_sets[i] - exclude
        for g in groups:
            if jaccard(a, token_sets[g[0]] - exclude) >= threshold:
                g.append(i)
                break
        else:
            groups.append([i])
    return groups


def search(index: Index, keys: list[tuple], q_tokens: list[str], visible: set[int], *, k1: float, b: float,
           top_k: int, candidate_k: int, mmr_lambda: float | None, min_score: float,
           group_threshold: float | None = None, group_candidate_k: int = 100) -> list[Hit]:
    """BM25 순위 (+ 선택: 비슷한 제목 묶기, 또는 MMR). 상위 점수가 min_score 이하이면 근거 없음([]).

    동점은 keys(doc_id, version) 순. group_threshold 가 있으면 BM25 상위 group_candidate_k 개를 묶어
    묶음 대표 top_k 개를 돌려주고, 이때 MMR 은 쓰지 않는다.
    """
    scored = bm25(index, q_tokens, visible, k1, b)
    if not scored:
        return []
    order = sorted(scored, key=lambda i: (-scored[i][0], keys[i]))
    if scored[order[0]][0] <= min_score:
        return []
    if group_threshold is not None:
        groups = group_similar(order[:max(group_candidate_k, top_k)], index.title_tokens, set(q_tokens), group_threshold)
        return [Hit(g[0], scored[g[0]][0], tuple(scored[g[0]][1]), tuple(g[1:])) for g in groups[:top_k]]
    if mmr_lambda is not None:
        cands = order[:max(candidate_k, top_k)]
        top = scored[cands[0]][0]
        order = mmr(cands, {i: scored[i][0] / top for i in cands}, index.title_tokens, mmr_lambda, top_k)
    return [Hit(i, scored[i][0], tuple(scored[i][1])) for i in order[:top_k]]
