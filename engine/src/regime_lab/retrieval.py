"""근거 검색 유스케이스 (P3-11, SRS FR-N5): 색인 만들기·불러오기, as_of 검색, 평가셋 채점.

API·CLI 의 공개 진입점(AGENTS.md R5). 문서·캐시 I/O 는 인자로 받은 저장소(store, 인프라 data/docs.py 의
DocsStore)가 맡고, 시계도 호출자가 넘긴다 — 이 파일은 파일·시계에 직접 닿지 않는다(R1·R2).
제품 색인 필드는 설정 rag.index_fields(제목·회사명)만 쓴다. 다른 필드는 CLI 의 개인 실험에서만 바꾼다.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from regime_lab.rag.metrics import abstention, ndcg_at_k, percentile, reciprocal_rank, unique_clusters_at_k
from regime_lab.rag.rank import Index, build_index
from regime_lab.rag.rank import search as rank_search
from regime_lab.rag.select import AS_OF_MODES, aware, select_as_of, with_tickers
from regime_lab.rag.text import doc_text, tokenize

INDEX_VERSION = 1  # 토큰화·순위·시점 선택 규칙이나 캐시 형식을 바꾸면 올린다 (AGENTS.md §4)
SPLITS = ("dev", "test")
_DOC_FIELDS = ("doc_id", "version", "source", "title", "corp_name", "url", "published_at", "time_precision",
               "first_seen_at", "available_at")
_SIMILAR_FIELDS = ("doc_id", "title", "source", "url", "published_at", "time_precision", "available_at")
_TICKER_LEN = 6


class RetrievalError(Exception):
    """code: data_unavailable · validation_failed · test_split_locked"""

    def __init__(self, code: str, message: str, detail=None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail


@dataclass(frozen=True)
class EvidenceIndex:
    docs: list[dict]
    keys: list[tuple]
    index: Index
    fields: tuple[str, ...]
    key: str
    docs_fingerprint: str
    manifests: dict
    from_cache: bool


def _hash(obj) -> str:
    """config.config_hash 와 같은 방식 (인프라 import 를 피하려 여기 둔다)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def index_key(rag_cfg: dict, fields: list[str], docs_fingerprint: str) -> str:
    return _hash({"index_version": INDEX_VERSION, "rag": {**rag_cfg, "index_fields": list(fields)},
                  "docs": docs_fingerprint})


def open_index(store, cfg: dict, fields: list[str] | None = None, use_cache: bool = True) -> EvidenceIndex:
    """색인을 캐시에서 불러오거나 새로 만든다. 문서가 없으면 빈 색인이 아니라 data_unavailable 오류.

    store: manifests()·files()·fingerprint(files)·read(files)·read_cache(key)·write_cache(key, payload) 를 가진
    문서 저장소 (구현은 data/docs.py 의 DocsStore 하나, 조립·진입이 만들어 넘긴다).
    """
    fields = list(fields or cfg["rag"]["index_fields"])
    try:
        manifests = store.manifests()
        files = store.files()
        fp = store.fingerprint(files)
    except FileNotFoundError as e:
        raise RetrievalError("data_unavailable", "External documents are not available.", {"error": str(e)}) from None
    except ValueError as e:
        raise RetrievalError("data_unavailable", "External documents use an unsupported schema.",
                             {"error": str(e)}) from None
    key = index_key(cfg["rag"], fields, fp)
    cached = store.read_cache(key) if use_cache else None
    if cached is not None:
        docs = cached["docs"]
        index = Index({t: [tuple(x) for x in p] for t, p in cached["postings"].items()}, cached["lengths"],
                      [frozenset(t) for t in cached["title_tokens"]])
    else:
        docs = store.read(files)
        if not docs:
            raise RetrievalError("data_unavailable", "External documents are not available.", {"error": "0 rows"})
        index = build_index([tokenize(doc_text(d, fields)) for d in docs], [tokenize(d.get("title")) for d in docs])
        if use_cache:
            store.write_cache(key, {"key": key, "docs": docs, "lengths": index.lengths,
                                    "postings": {t: [list(x) for x in p] for t, p in index.postings.items()},
                                    "title_tokens": [sorted(t) for t in index.title_tokens]})
    keys = [(d["doc_id"], d["version"]) for d in docs]
    return EvidenceIndex(docs, keys, index, tuple(fields), key, fp, manifests, cached is not None)


def _validate(cfg: dict, q, as_of, mode, tickers, k) -> tuple[str, datetime, str, list[str], int]:
    r, errors = cfg["rag"], {}
    q = q.strip() if isinstance(q, str) else ""
    if not q or len(q) > r["max_query_len"]:
        errors["q"] = f"1~{r['max_query_len']}자 질의가 필요합니다"
    t = None
    try:
        t = aware(as_of)
    except (TypeError, ValueError):
        errors["as_of"] = "시간대가 포함된 ISO 8601 시각이 필요합니다 (예: 2026-10-07T09:00:00+09:00)"
    if mode not in AS_OF_MODES:
        errors["mode"] = f"{AS_OF_MODES} 중 하나"
    tickers = [x.strip() for x in (tickers or []) if x and x.strip()]
    if any(len(x) != _TICKER_LEN or not x.isalnum() for x in tickers):
        errors["tickers"] = "종목코드는 6자리 영숫자입니다"
    k = r["top_k"] if k is None else k
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= r["max_k"]:
        errors["k"] = f"1~{r['max_k']}"
    if errors:
        raise RetrievalError("validation_failed", "Check the input values.", {"fields": errors})
    return q, t, mode, tickers, k


def search(ei: EvidenceIndex, cfg: dict, q: str, as_of, mode: str = "observed", tickers=None, k: int | None = None) -> dict:
    """as_of 에 이용 가능했던 문서 중 관련 근거. 근거가 없으면 status=no_evidence 와 빈 목록.

    묶기를 켜면(rag.group_threshold) 결과는 묶음 대표이고, 제목이 비슷한 다른 기사는 similar 에 담는다.
    """
    q, t, mode, tickers, k = _validate(cfg, q, as_of, mode, tickers, k)
    r = cfg["rag"]
    rows = with_tickers(ei.docs, select_as_of(ei.docs, t, mode), tickers)
    hits = rank_search(ei.index, ei.keys, tokenize(q), set(rows), k1=r["k1"], b=r["b"], top_k=k,
                       candidate_k=r["candidate_k"], mmr_lambda=r["mmr_lambda"], min_score=r["min_score"],
                       group_threshold=r["group_threshold"], group_candidate_k=r["group_candidate_k"])
    items = []
    for h in hits:
        d = ei.docs[h.row]
        similar = [{f: ei.docs[m].get(f) for f in _SIMILAR_FIELDS} for m in h.members]
        items.append({f: d.get(f) for f in _DOC_FIELDS}
                     | {"score": round(h.score, 6), "matched_tokens": list(h.matched),
                        "similar_count": len(similar), "similar": similar})
    return {"status": "ok" if items else "no_evidence", "items": items,
            "index": {"index_version": INDEX_VERSION, "docs_fingerprint": ei.docs_fingerprint, "fields": list(ei.fields),
                      "total_docs": len(ei.docs), "visible_docs": len(rows), "mode": mode, "as_of": t.isoformat(),
                      "grouping": r["group_threshold"]}}


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _summary(ms: dict[str, list[float]]) -> dict[str, float | None]:
    return {("mrr" if m == "rr" else m): _mean(v) for m, v in ms.items()}


def evaluate(ei: EvidenceIndex, cfg: dict, gold: list[dict], split: str, *, allow_test: bool = False,
             clock: Callable[[], float] | None = None) -> dict:
    """평가셋 채점. test 분할은 allow_test=True(CLI --split test 명시)일 때만. clock 을 주면 질의 지연(ms)도 잰다."""
    if split not in SPLITS:
        raise RetrievalError("validation_failed", "Check the input values.", {"fields": {"split": f"{SPLITS} 중 하나"}})
    if split == "test" and not allow_test:
        raise RetrievalError("test_split_locked", "The test split is scored only when requested explicitly.")
    k = cfg["rag"]["top_k"]
    lookup = {**{d["url"]: d["doc_id"] for d in ei.docs if d.get("url")}, **{d["doc_id"]: d["doc_id"] for d in ei.docs}}
    warnings, per_query, latencies, abst_cases = [], [], [], []
    by_type: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    overall: dict[str, list[float]] = defaultdict(list)
    for item in (g for g in gold if g.get("split") == split):
        grades, clusters = {}, {}
        for rel in item.get("relevant", []):
            ident = rel.get("url") or rel.get("doc_id")
            did = lookup.get(ident)
            if did is None:
                warnings.append(f"{item['id']}: 정답 {ident} 가 문서에 없음 (찾지 못한 정답으로 센다)")
                did = f"_missing:{ident}"
            grades[did] = float(rel.get("grade", 1))
        for ident, cluster in (item.get("clusters") or {}).items():
            clusters[lookup.get(ident, ident)] = cluster
        t0 = clock() if clock else None
        res = search(ei, cfg, item["query"], item["as_of"], item.get("mode", "observed"), item.get("tickers"), k)
        if clock:
            latencies.append((clock() - t0) * 1000)
        # 묶음 하나 = 순위 하나. 정답이 묶음 안(비슷한 기사)에 있어도 그 순위에서 찾은 것으로 본다
        groups = [[x["doc_id"]] + [s["doc_id"] for s in x["similar"]] for x in res["items"]]
        ranked = [next((m for m in g if m in grades), g[0]) for g in groups]
        covered = {m for g in groups for m in g}
        abstained = res["status"] == "no_evidence"
        abst_cases.append((bool(grades), abstained))
        row = {"id": item["id"], "type": item.get("type", "untyped"), "ranked": ranked, "no_evidence": abstained}
        if grades:
            rel_set = set(grades)
            row |= {"recall": len(covered & rel_set) / len(rel_set), "rr": reciprocal_rank(ranked, rel_set),
                    "ndcg": ndcg_at_k(ranked, grades, k)}
            if clusters:
                row["unique_clusters"] = unique_clusters_at_k(ranked, clusters, k)
            for m in ("recall", "rr", "ndcg", "unique_clusters"):
                if m in row:
                    overall[m].append(row[m])
                    by_type[row["type"]][m].append(row[m])
        per_query.append(row)
    return {"split": split, "k": k, "n": len(per_query), "overall": _summary(overall),
            "by_type": {t: _summary(ms) for t, ms in sorted(by_type.items())},
            "abstention": abstention(abst_cases),
            "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95)} if latencies else None,
            "index": {"index_version": INDEX_VERSION, "docs_fingerprint": ei.docs_fingerprint, "fields": list(ei.fields),
                      "total_docs": len(ei.docs)},
            "warnings": warnings, "per_query": per_query}
