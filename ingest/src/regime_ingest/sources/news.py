"""P3-8 pure response decoding; no HTTP, keys, article-body fetch or persistence."""
from datetime import datetime
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET


def _date(value: str) -> str:
    try:
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError
        return result.isoformat()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("invalid publisher timestamp") from None


def parse_naver(body: dict, *, expected_start: int, requested_display: int) -> dict:
    """Reject malformed pages instead of turning API errors into zero articles."""
    if (type(expected_start) is not int or not 1 <= expected_start <= 1000 or
            type(requested_display) is not int or not 1 <= requested_display <= 100):
        raise ValueError("invalid requested page")
    if not isinstance(body, dict) or "errorCode" in body:
        raise ValueError("news API response failed")
    for key in ("total", "start", "display"):
        if type(body.get(key)) is not int or body[key] < 0:
            raise ValueError("invalid page metadata")
    items = body.get("items")
    if (not isinstance(items, list) or body["start"] != expected_start or
            len(items) != body["display"] or len(items) > requested_display or
            len(items) > max(0, body["total"] - expected_start + 1) or
            (not items and body["total"] >= expected_start)):
        raise ValueError("inconsistent search page")
    out, invalid = [], 0
    for row in items:
        try:
            if not isinstance(row, dict) or not isinstance(row.get("title"), str):
                raise ValueError
            url = row.get("originallink") or row["link"]
            if not isinstance(url, str) or not url.strip():
                raise ValueError
            out.append(dict(title=row["title"], original_url=url, summary=row.get("description") or "",
                            published_at=_date(row["pubDate"]), published_at_basis="naver_provided"))
        except (KeyError, ValueError, TypeError):
            invalid += 1
    return {"items": out, "invalid": invalid, "status": "partial" if invalid else "ok",
            "more_results": body["total"] > expected_start - 1 + len(items)}


def parse_feed(payload: bytes, *, max_bytes: int, max_items: int) -> dict:
    """RSS 2.0 and Atom, bounded bytes/items, no DTD/entity or remote resolution."""
    if (type(max_bytes) is not int or type(max_items) is not int or max_bytes < 1 or max_items < 1 or
            not isinstance(payload, bytes) or len(payload) > max_bytes):
        raise ValueError("feed size limit")
    # Only UTF-8 is supported here; reject alternate encodings before XML expansion.
    try:
        text = payload.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("DTD/entity not accepted")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError):
        raise ValueError("invalid UTF-8 XML feed") from None
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == "rss" and root.find("channel") is not None:
        rows, kind = root.findall("./channel/item"), "rss"
    elif root.tag == atom + "feed":
        rows, kind = root.findall(atom + "entry"), "atom"
    else:
        raise ValueError("unsupported feed")
    out, invalid = [], 0
    for row in rows[:max_items]:
        try:
            if kind == "rss":
                title, url, date = (row.findtext(k) for k in ("title", "link", "pubDate"))
                summary = row.findtext("description") or ""
            else:
                title = row.findtext(atom + "title")
                links = [e.get("href") for e in row.findall(atom + "link") if e.get("rel", "alternate") == "alternate"]
                url = links[0] if links else None
                date = row.findtext(atom + "published") or row.findtext(atom + "updated")
                summary = row.findtext(atom + "summary") or ""
            if not title or not url:
                raise ValueError
            out.append(dict(title=title, original_url=url, summary=summary,
                            published_at=_date(date), published_at_basis="feed_pubdate"))
        except ValueError:
            invalid += 1
    truncated = max(0, len(rows) - max_items)
    return {"items": out, "invalid": invalid, "truncated": truncated,
            "status": "partial" if invalid or truncated else "ok"}
