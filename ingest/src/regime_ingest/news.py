"""P3-8 news domain: bounded plans, metadata versions and cutoff selection; no I/O.

Request execution/persistence are deliberately left to the collector composition.
News metadata is untrusted input, never an instruction or an LLM permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit, urlunsplit


def aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone required")
    return value.astimezone(timezone.utc)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("text required")
    parser = _Text()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def canonical_url(value: str) -> str:
    """Strip known tracking only; keep article IDs, query order, scheme and path.

    Unknown parameters may identify articles. No redirect or remote resolution.
    """
    if not isinstance(value, str) or re.search(r"[\x00-\x20\x7f]", value):
        raise ValueError("invalid article URL")
    try:
        parts = urlsplit(unescape(value))
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        if port and (parts.scheme, port) not in {("https", 443), ("http", 80)}:
            host += f":{port}"
    except (ValueError, UnicodeError):
        raise ValueError("invalid article URL") from None
    keep = []
    for segment in parts.query.split("&"):
        key = segment.split("=", 1)[0].lower()
        if segment and not (key.startswith("utm_") or key in {"fbclid", "gclid"}):
            keep.append(segment)
    return urlunsplit((parts.scheme, host, parts.path or "/", "&".join(keep), ""))


@dataclass(frozen=True)
class SourcePolicy:
    source: str
    active: bool = False
    license_scope: str = "metadata_only"

    def check(self):
        if not self.active:
            raise ValueError("source inactive")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.source):
            raise ValueError("invalid source identifier")
        if self.license_scope not in {"metadata_only", "summary_link"}:
            raise ValueError("unsupported news scope")


def observe(item: dict, *, policy: SourcePolicy, observed_at: datetime, run_id: str,
            previous: dict | None = None) -> dict | None:
    """Normalized item -> new version, or None for an unchanged observation.

    Version times come from observation, never backdated to publisher timestamps.
    A publisher timestamp change is metadata correction, not evidence of past availability.
    """
    policy.check()
    now = aware(observed_at)
    title = plain_text(item["title"])
    if not title:
        raise ValueError("empty title")
    url = canonical_url(item["original_url"])
    published = aware(datetime.fromisoformat(item["published_at"]))
    basis = item["published_at_basis"]
    if basis not in {"naver_provided", "feed_pubdate"}:
        raise ValueError("unsupported timestamp basis")
    summary = plain_text(item.get("summary") or "") if policy.license_scope == "summary_link" else None
    article_key = sha256(url.encode()).hexdigest()
    doc_id = f"{policy.source}:{article_key}"
    semantic = dict(title=title, summary=summary, url=url, published_at=published.isoformat(),
                    published_at_basis=basis, license_scope=policy.license_scope)
    digest = sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    version = 1
    if previous is not None:
        if previous["doc_id"] != doc_id:
            raise ValueError("previous document mismatch")
        if now < aware(datetime.fromisoformat(previous["first_seen_at"])):
            raise ValueError("observation clock moved backwards")
        if previous["content_hash"] == digest:
            return None
        now = max(now, aware(datetime.fromisoformat(previous["available_at"])))
        version = previous["version"] + 1
    return {**semantic, "doc_id": doc_id, "article_key": article_key, "version": version,
            "source": policy.source, "source_type": "news", "original_url": url,
            "first_seen_at": aware(observed_at).isoformat(), "available_at": now.isoformat(),
            "modified_at": None, "time_precision": "second", "backfilled": False,
            "content_hash": digest, "body_ref": None, "tickers": [], "ticker_status": "none",
            "cluster_id": None, "ingest_run_id": run_id}


def request_plan(queries: list[str], *, cursor: int, request_limit: int, remaining_daily: int,
                 display: int, page_limit: int) -> dict:
    """Round robin one page per query before second pages; all bounds supplied by config.

    Searches are snapshots of the newest results, not historical coverage. The caller
    persists cursor and daily usage under a lock; retries also consume that budget.
    """
    values = (cursor, request_limit, remaining_daily, display, page_limit)
    if any(type(v) is not int for v in values) or min(cursor, request_limit, remaining_daily) < 0:
        raise ValueError("invalid request limits")
    if not 1 <= display <= 100 or page_limit < 1 or (page_limit - 1) * display + 1 > 1000:
        raise ValueError("invalid search page range")
    unique = list(dict.fromkeys(q.strip() for q in queries if isinstance(q, str) and q.strip()))
    if not unique:
        return {"requests": [], "next_cursor": 0, "deferred_queries": 0}
    rotated = unique[cursor % len(unique):] + unique[:cursor % len(unique)]
    cap = min(request_limit, remaining_daily)
    requests = []
    for page in range(page_limit):
        for query in rotated:
            if len(requests) >= cap:
                break
            requests.append(dict(query=query, start=page * display + 1, display=display, sort="date"))
        if len(requests) >= cap:
            break
    served = min(len(requests), len(unique))
    return {"requests": requests, "next_cursor": (cursor + served) % len(unique),
            "deferred_queries": len(unique) - served}


def normalize_poll(items: list[dict], *, policy: SourcePolicy, observed_at: datetime,
                   run_id: str, previous: dict[str, dict]) -> dict:
    """Quarantine invalid/conflicting rows; never mutate caller history.

    Repeated identical URLs from multiple search queries produce one version. Two
    different contents for one URL in a poll have no reliable order, so neither wins.
    Persist returned documents atomically before advancing any request cursor.
    """
    policy.check()
    aware(observed_at)
    groups: dict[str, list[dict]] = {}
    invalid = 0
    for row in items:
        try:
            candidate = observe(row, policy=policy, observed_at=observed_at, run_id=run_id)
            groups.setdefault(candidate["doc_id"], []).append(candidate)
        except (KeyError, TypeError, ValueError):
            invalid += 1
    documents, unchanged, duplicates, conflicts = [], 0, 0, 0
    for key, rows in sorted(groups.items()):
        if len({r["content_hash"] for r in rows}) != 1:
            conflicts += len(rows)
            continue
        duplicates += len(rows) - 1
        candidate = rows[0]
        old = previous.get(key)
        if old is not None:
            if aware(observed_at) < aware(datetime.fromisoformat(old["first_seen_at"])):
                invalid += len(rows)
                continue
            if candidate["content_hash"] == old["content_hash"]:
                unchanged += 1
                continue
            candidate = {**candidate, "version": old["version"] + 1,
                         "available_at": max(aware(observed_at), aware(datetime.fromisoformat(old["available_at"]))).isoformat()}
        documents.append(candidate)
    return {"documents": documents, "invalid": invalid, "conflicts": conflicts,
            "duplicates": duplicates, "unchanged": unchanged,
            "status": "partial" if invalid or conflicts else "ok"}


def select_cutoff(docs: list[dict], *, cutoff: datetime, limit: int) -> dict:
    """Observed-only latest versions, cross-source URL dedup, then bounded output.

    Same titles at different URLs remain distinct (no unproven event clustering).
    This returns selection statistics, never collection coverage or permission to send to LLM.
    """
    cutoff = aware(cutoff)
    if type(limit) is not int or limit < 0:
        raise ValueError("invalid selection limit")
    latest, versions = {}, {}
    for doc in docs:
        if (aware(datetime.fromisoformat(doc["available_at"])) > cutoff or
                aware(datetime.fromisoformat(doc["first_seen_at"])) > cutoff):
            continue
        key = doc["doc_id"]
        version_key = (key, doc["version"])
        if version_key in versions and versions[version_key] != doc["content_hash"]:
            raise ValueError("conflicting document version")
        versions[version_key] = doc["content_hash"]
        prev = latest.get(key)
        if prev is None or doc["version"] > prev["version"]:
            latest[key] = doc
    ordered = sorted(latest.values(), key=lambda d: (-aware(datetime.fromisoformat(d["available_at"])).timestamp(), d["doc_id"]))
    articles = {}
    for doc in ordered:
        articles.setdefault(doc["article_key"], doc)
    eligible = list(articles.values())
    return {"documents": eligible[:limit], "eligible": len(eligible),
            "cross_source_duplicates": len(latest) - len(eligible),
            "deferred": max(0, len(eligible) - limit), "cutoff": cutoff.isoformat(), "mode": "observed"}
