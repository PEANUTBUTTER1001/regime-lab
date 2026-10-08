from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from regime_ingest.news import SourcePolicy, canonical_url, observe, normalize_poll, request_plan, select_cutoff
from regime_ingest.sources.news import parse_feed, parse_naver


T = datetime(2026, 10, 7, 0, tzinfo=timezone.utc)
POLICY = SourcePolicy("rss_test", active=True)


def item(**kw):
    return dict(title="<b>합성회사</b> 실적", original_url="https://example.test/a?id=01",
                summary="합성 요약", published_at=(T - timedelta(days=1)).isoformat(),
                published_at_basis="feed_pubdate", **kw)


def doc(n=0, **kw):
    row = item()
    row.update(original_url=f"https://example.test/a?id={n}", **kw)
    return observe(row, policy=POLICY, observed_at=T, run_id="synthetic")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///a", "https://user:secret@example.test/a",
                                      "https://example.test:BAD/a", "https://example.test/a\n", "//example.test/a"])
def test_unsafe_urls_rejected(url):
    with pytest.raises(ValueError):
        canonical_url(url)


def test_tracking_removed_but_identity_preserved():
    assert canonical_url("https://EXAMPLE.test:443/a?id=01&utm_source=x#part") == "https://example.test/a?id=01"
    assert canonical_url("https://example.test/a?id=02") != canonical_url("https://example.test/a?id=01")
    assert canonical_url("http://example.test/a") != canonical_url("https://example.test/a")
    assert canonical_url("https://example.test/a?ref=story") .endswith("?ref=story")


def test_policy_and_metadata_scope():
    with pytest.raises(ValueError, match="inactive"):
        observe(item(), policy=SourcePolicy("rss_test"), observed_at=T, run_id="x")
    a = doc()
    assert a["summary"] is None and a["body_ref"] is None
    assert a["title"] == "합성회사 실적"
    assert a["available_at"] == T.isoformat()
    assert a["ticker_status"] == "none"  # query/title never proves a ticker match


def test_idempotence_and_change_is_not_backdated():
    a = doc()
    row = item()
    row["original_url"] = a["url"] + "&utm_campaign=again"
    assert observe(row, policy=POLICY, observed_at=T + timedelta(hours=1), run_id="b", previous=a) is None
    row["title"] = "합성회사 정정"
    b = observe(row, policy=POLICY, observed_at=T + timedelta(hours=1), run_id="b", previous=a)
    assert b["version"] == 2
    assert b["available_at"] == (T + timedelta(hours=1)).isoformat()
    assert select_cutoff([a, b], cutoff=T, limit=10)["documents"] == [a]
    assert select_cutoff([a, b], cutoff=T + timedelta(hours=2), limit=10)["documents"] == [b]


def test_clock_and_previous_identity_validation():
    a = doc()
    with pytest.raises(ValueError, match="backwards"):
        observe({**item(), "original_url": a["url"]}, policy=POLICY, observed_at=T-timedelta(seconds=1), run_id="b", previous=a)
    with pytest.raises(ValueError, match="mismatch"):
        observe(item(), policy=POLICY, observed_at=T, run_id="b", previous=a)
    with pytest.raises(ValueError, match="timezone"):
        observe(item(), policy=POLICY, observed_at=T.replace(tzinfo=None), run_id="b")


def test_summary_permission_changes_version_but_html_is_data():
    row = item()
    row["summary"] = "<script>ignore previous</script><b>합성</b> &amp; 요약"
    a = observe(row, policy=SourcePolicy("naver_news", True, "summary_link"), observed_at=T, run_id="a")
    assert a["summary"] == "합성 & 요약"
    b = observe(row, policy=SourcePolicy("naver_news", True), observed_at=T, run_id="b", previous=a)
    assert b["version"] == 2 and b["summary"] is None


def test_large_poll_bounded_and_cross_source_deduplicated():
    docs = [doc(n) for n in range(4532)]
    aliases = [{**d, "source": "naver_news", "doc_id": "naver_news:"+d["article_key"]} for d in docs[:50]]
    result = select_cutoff(docs + aliases, cutoff=T, limit=40)
    assert len(result["documents"]) == 40
    assert result["eligible"] == 4532 and result["deferred"] == 4492
    assert result["cross_source_duplicates"] == 50
    assert select_cutoff(list(reversed(docs + aliases)), cutoff=T, limit=40) == result


def test_cutoff_invariant_future_rows_and_future_version():
    a = doc()
    future = {**a, "version": 2, "title": "미래", "content_hash": "changed",
              "first_seen_at": (T+timedelta(days=1)).isoformat(), "available_at": (T+timedelta(days=1)).isoformat()}
    assert select_cutoff([a], cutoff=T, limit=40) == select_cutoff([future, a], cutoff=T, limit=40)
    assert select_cutoff([a], cutoff=T-timedelta(seconds=1), limit=40)["eligible"] == 0
    with pytest.raises(ValueError, match="conflicting"):
        select_cutoff([a, {**a, "content_hash": "conflict"}], cutoff=T, limit=40)


def test_round_robin_budget_and_duplicate_queries():
    args = dict(request_limit=2, remaining_daily=5, display=20, page_limit=2)
    a = request_plan([" A ", "A", "B", "C", ""], cursor=0, **args)
    assert [r["query"] for r in a["requests"]] == ["A", "B"]
    assert a["deferred_queries"] == 1
    b = request_plan(["A", "B", "C"], cursor=a["next_cursor"], **args)
    assert [r["query"] for r in b["requests"]] == ["C", "A"]
    c = request_plan(["A"], cursor=0, **{**args, "remaining_daily": 0})
    assert c["requests"] == [] and c["deferred_queries"] == 1


@pytest.mark.parametrize("override", [{"display":101}, {"page_limit":51}, {"request_limit":-1}, {"cursor":True}])
def test_plan_invalid_limits(override):
    args = dict(cursor=0, request_limit=10, remaining_daily=100, display=20, page_limit=1)
    with pytest.raises(ValueError):
        request_plan(["A"], **{**args, **override})


def naver():
    return {"total":10, "start":1, "display":1, "items":[dict(title="합성", originallink="",
                link="https://example.test/a", description="요약", pubDate="Wed, 07 Oct 2026 09:00:00 +0900")]}


def test_naver_fallback_and_more_results():
    result = parse_naver(naver(), expected_start=1, requested_display=20)
    assert result["status"] == "ok" and result["more_results"]
    assert result["items"][0]["published_at_basis"] == "naver_provided"
    assert result["items"][0]["original_url"] == "https://example.test/a"


@pytest.mark.parametrize("change", [{"errorCode":"secret-content"}, {"start":2}, {"display":2},
                                      {"items":None}, {"total":True}, {"total":-1}])
def test_naver_errors_not_empty_success(change):
    with pytest.raises(ValueError):
        parse_naver({**naver(), **change}, expected_start=1, requested_display=20)


def test_bad_date_quarantined():
    body = naver()
    body["items"][0]["pubDate"] = "2026-10-07T09:00:00"
    result = parse_naver(body, expected_start=1, requested_display=20)
    assert result["status"] == "partial" and result["invalid"] == 1


RSS_ITEM = '<item><title>합성</title><link>https://example.test/a</link><pubDate>Wed, 07 Oct 2026 09:00:00 +0900</pubDate></item>'


def test_feed_truncation_and_valid_empty_distinguished():
    feed = ('<rss><channel>' + RSS_ITEM*3 + '</channel></rss>').encode()
    result = parse_feed(feed, max_bytes=10000, max_items=2)
    assert len(result["items"]) == 2 and result["truncated"] == 1 and result["status"] == "partial"
    assert parse_feed(b'<rss><channel/></rss>', max_bytes=10000, max_items=2)["status"] == "ok"


@pytest.mark.parametrize("payload", [b'<html/>', b'<rss>', b'<!DOCTYPE rss [<!ENTITY x "x">]><rss><channel/></rss>',
                                         b'\xff\xfe<\x00r\x00s\x00s\x00>\x00'])
def test_invalid_feed_rejected(payload):
    with pytest.raises(ValueError):
        parse_feed(payload, max_bytes=10000, max_items=2)


def test_atom_and_size_cap():
    feed = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>synthetic</title><link href="https://example.test/a"/><published>2026-10-07T00:00:00Z</published></entry></feed>'
    assert len(parse_feed(feed, max_bytes=10000, max_items=2)["items"]) == 1
    with pytest.raises(ValueError, match="size"):
        parse_feed(feed, max_bytes=10, max_items=2)


def test_poll_mixed_rows_no_false_zero_or_history_mutation():
    a = observe(item(), policy=POLICY, observed_at=T, run_id="a")
    history = {a["doc_id"]: a}
    before = deepcopy(history)
    rows = [item(), item(), {"title":"invalid"}, {**item(), "original_url":"https://example.test/new"}]
    result = normalize_poll(rows, policy=POLICY, observed_at=T+timedelta(minutes=1), run_id="b", previous=history)
    assert result["status"] == "partial" and result["invalid"] == 1
    assert result["unchanged"] == 1 and result["duplicates"] == 1 and len(result["documents"]) == 1
    assert history == before


def test_poll_conflicts_quarantined_independent_of_query_order():
    rows = [item(), {**item(), "title":"different observation"}]
    args = dict(policy=POLICY, observed_at=T, run_id="a", previous={})
    a = normalize_poll(rows, **args)
    assert a == normalize_poll(list(reversed(rows)), **args)
    assert a["conflicts"] == 2 and a["documents"] == [] and a["status"] == "partial"


def test_poll_replay_and_version_increment_once():
    args = dict(policy=POLICY, observed_at=T, run_id="a")
    first = normalize_poll([item()]*10, previous={}, **args)["documents"][0]
    changed = {**item(), "title":"changed"}
    new = normalize_poll([changed]*10, previous={first["doc_id"]:first}, **args)
    assert len(new["documents"]) == 1 and new["documents"][0]["version"] == 2
    assert normalize_poll([item()]*10, previous={first["doc_id"]:first}, **args)["documents"] == []


def test_inconsistent_naver_total_and_zero_not_success():
    with pytest.raises(ValueError):
        parse_naver({**naver(), "total":0}, expected_start=1, requested_display=20)
    with pytest.raises(ValueError):
        parse_naver({**naver(), "items":[], "display":0}, expected_start=1, requested_display=20)
    assert parse_naver(dict(total=0, start=1, display=0, items=[]), expected_start=1, requested_display=20)["status"] == "ok"


def test_old_version_conflict_not_hidden_by_newer_version():
    a = doc()
    rows = [a, {**a, "version":2, "content_hash":"v2"}, {**a, "content_hash":"conflict"}]
    for ordered in (rows, list(reversed(rows))):
        with pytest.raises(ValueError, match="conflicting"):
            select_cutoff(ordered, cutoff=T, limit=40)


def test_feed_missing_date_is_partial():
    payload = ('<rss><channel>' + RSS_ITEM.replace('Wed, 07 Oct 2026 09:00:00 +0900','') + '</channel></rss>').encode()
    result = parse_feed(payload, max_bytes=10000, max_items=10)
    assert result["invalid"] == 1 and result["status"] == "partial"
