"""Fixed-endpoint Naver transport; no redirect, bounded response, sanitized failures."""
import json
from datetime import datetime
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.parse
import urllib.request

from regime_ingest.ports import NewsRequestError

ENDPOINT = "https://openapi.naver.com/v1/search/news.json"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def retry_delay(value: str | None, now: datetime) -> float:
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        try:
            return max(0, (parsedate_to_datetime(value) - now).total_seconds())
        except (ValueError, TypeError, OverflowError):
            # An unknown delay is not permission to immediately retry a rate limit.
            return float("inf")


def make_fetch(client_id: str, client_secret: str, *, timeout: float, max_bytes: int,
               user_agent: str, now):
    if not client_id or not client_secret or timeout <= 0 or max_bytes < 1:
        raise ValueError("invalid transport configuration")
    opener = urllib.request.build_opener(_NoRedirect)

    def fetch(params: dict) -> dict:
        query = urllib.parse.urlencode({k: params[k] for k in ("query", "start", "display", "sort")})
        request = urllib.request.Request(ENDPOINT + "?" + query, headers={
            "X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret, "User-Agent": user_agent})
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise NewsRequestError("response_too_large")
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise NewsRequestError("invalid_json")
            return result
        except urllib.error.HTTPError as exc:
            delay = retry_delay(exc.headers.get("Retry-After"), now()) if exc.headers else 0
            raise NewsRequestError(f"http_{exc.code}", retryable=exc.code == 429 or 500 <= exc.code <= 599,
                                   retry_after=delay) from None
        except (UnicodeError, json.JSONDecodeError):
            raise NewsRequestError("invalid_json") from None
        except OSError:
            raise NewsRequestError("network", retryable=True) from None
    return fetch
