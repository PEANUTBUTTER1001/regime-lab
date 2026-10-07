"""Bounded Naver poll use case. All I/O/clock/sleep supplied by composition/ports."""
from __future__ import annotations

from datetime import datetime
from collections.abc import Callable
import math

from regime_ingest.news import SourcePolicy, aware, normalize_poll, request_plan
from regime_ingest.ports import NewsRequestError, NewsStore, SourceLocked
from regime_ingest.timing import KST


def validate_config(cfg: dict) -> SourcePolicy:
    policy = SourcePolicy("naver_news", cfg["active"], cfg["license_scope"])
    if type(cfg["active"]) is not bool:
        raise ValueError("active must be boolean")
    if policy.active:
        policy.check()
    if not isinstance(cfg.get("user_agent"), str) or not cfg["user_agent"].strip() or any(c in cfg["user_agent"] for c in "\r\n"):
        raise ValueError("invalid user agent")
    for key in ("request_limit", "daily_request_limit", "display", "page_limit", "max_retries", "response_max_bytes"):
        if type(cfg[key]) is not int or cfg[key] < (0 if key == "max_retries" else 1):
            raise ValueError("invalid news limit: " + key)
    for key in ("request_interval_sec", "backoff_base_sec", "max_retry_wait_sec", "timeout_sec"):
        if type(cfg[key]) not in (int, float) or not math.isfinite(cfg[key]) or cfg[key] < 0:
            raise ValueError("invalid news duration: " + key)
    if cfg["timeout_sec"] == 0 or not isinstance(cfg["queries"], list) or any(not isinstance(q, str) for q in cfg["queries"]):
        raise ValueError("invalid news configuration")
    request_plan(cfg["queries"], cursor=0, request_limit=cfg["request_limit"],
                 remaining_daily=cfg["daily_request_limit"], display=cfg["display"], page_limit=cfg["page_limit"])
    return policy


def collect_news(store: NewsStore, fetch: Callable, decode: Callable, cfg: dict, *,
                 now: Callable[[], datetime], sleep: Callable[[float], None], run_id: str) -> dict:
    policy = validate_config(cfg)
    policy.check()  # before lock, quota or network
    started = aware(now())
    stats = dict(run_id=run_id, source=policy.source, mode="forward", window_from=started.isoformat(),
                 window_to=started.isoformat(), started_at=started.isoformat(), requests=0, received=0,
                 new=0, duplicate=0, changed=0, errors=0, status="ok", message="")
    try:
        lock = store.lock(policy.source)
    except SourceLocked:
        return {**stats, "status": "skipped", "message": "source_locked"}
    staged, committed = [], False
    try:
        cursor = int(store.cursor(policy.source, "news_query") or "0")
        day = aware(now()).astimezone(KST).date().isoformat()
        used = store.requests_on(policy.source, day)
        plan = request_plan(cfg["queries"], cursor=cursor, request_limit=cfg["request_limit"],
                            remaining_daily=max(0, cfg["daily_request_limit"] - used),
                            display=cfg["display"], page_limit=cfg["page_limit"])
        history = {}
        for doc in store.read_docs(policy.source):
            if doc["doc_id"] not in history or doc["version"] > history[doc["doc_id"]]["version"]:
                history[doc["doc_id"]] = doc
        items, last_time, more, complete = [], None, {}, True
        for params in plan["requests"]:
            for attempt in range(cfg["max_retries"] + 1):
                if stats["requests"]:
                    sleep(cfg["request_interval_sec"])
                current = aware(now())
                if current < started or (last_time is not None and current < last_time):
                    raise NewsRequestError("clock_regression")
                day = current.astimezone(KST).date().isoformat()
                used = store.requests_on(policy.source, day)
                if used >= cfg["daily_request_limit"] or stats["requests"] >= cfg["request_limit"]:
                    raise NewsRequestError("request_limit")
                # Reserve BEFORE dispatch: crash/network failures still spend quota.
                store.set_requests(policy.source, day, used + 1)
                store.commit()
                stats["requests"] += 1
                last_time = current
                try:
                    response = fetch(params)
                    page = decode(response, expected_start=params["start"], requested_display=params["display"])
                    stats["received"] += len(page["items"]) + page["invalid"]
                    stats["errors"] += page["invalid"]
                    items.extend(page["items"])
                    more[params["query"]] = page["more_results"]
                    break
                except ValueError:
                    raise NewsRequestError("invalid_response") from None
                except NewsRequestError as exc:
                    wait = max(exc.retry_after, cfg["backoff_base_sec"] * 2 ** attempt)
                    if not exc.retryable or attempt >= cfg["max_retries"] or wait > cfg["max_retry_wait_sec"]:
                        raise
                    # Never sleep/retry beyond the remaining per-run/day request budget.
                    if stats["requests"] >= cfg["request_limit"] or used + 1 >= cfg["daily_request_limit"]:
                        raise NewsRequestError("request_limit") from None
                    sleep(wait)
        if not plan["requests"]:
            complete = False
            stats["message"] = "no_queries" if not any(q.strip() for q in cfg["queries"]) else "request_limit"
        observed = aware(now())
        if observed < started or (last_time is not None and observed < last_time):
            raise NewsRequestError("clock_regression")
        normalized = normalize_poll(items, policy=policy, observed_at=observed, run_id=run_id, previous=history)
        stats["errors"] += normalized["invalid"] + normalized["conflicts"]
        stats["duplicate"] = normalized["duplicates"] + normalized["unchanged"]
        docs = normalized["documents"]
        stats["new"] = sum(d["version"] == 1 for d in docs)
        stats["changed"] = len(docs) - stats["new"]
        complete = complete and not stats["errors"]
        partial = not complete or bool(plan["deferred_queries"]) or any(more.values())
        stats["status"] = "partial" if partial else "ok"
        if partial and not stats["message"]:
            stats["message"] = "invalid_rows" if stats["errors"] else "bounded_search"
        staged = store.stage_docs(policy.source, run_id, docs)
        store.mark_seen(docs)
        # Selection sampling is not a complete historical coverage claim.
        state = "partial" if partial else "forward"
        store.set_coverage(policy.source, [observed.astimezone(KST).date().isoformat()], "news", state,
                           run_id, observed.isoformat())
        if complete:
            store.set_cursor(policy.source, "news_query", str(plan["next_cursor"]))
        stats["finished_at"] = observed.isoformat()
        store.save_run(stats)
        store.commit()
        committed = True
        store.publish(staged)
        return stats
    except NewsRequestError as exc:
        store.rollback()
        store.discard(staged)
        ended = aware(now())
        stats.update(status="partial" if exc.code == "request_limit" or exc.retryable else "failed",
                     message=exc.code, errors=stats["errors"] + 1, finished_at=ended.isoformat())
        store.set_coverage(policy.source, [ended.astimezone(KST).date().isoformat()], "news", "gap", run_id,
                           ended.isoformat())
        store.save_run(stats)
        store.commit()
        return stats
    except BaseException:
        if not committed:
            store.rollback()
            store.discard(staged)
        # After commit, keep staging for source-scoped recovery on next run.
        raise
    finally:
        store.unlock(lock)
