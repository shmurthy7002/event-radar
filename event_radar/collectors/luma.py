"""Undocumented, credential-free Luma web interfaces. All source details live here."""

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from event_radar.config import Requests

PAGE_BASE = "https://luma.com/"
API_BASE = "https://api.lu.ma/"
MAX_RESPONSE_BYTES = 8_000_000


class CollectionError(RuntimeError):
    """Public discovery could not be completed."""


class BudgetExhausted(CollectionError):
    """Shared HTTP attempt budget exhausted."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_transport(url: str, timeout: float) -> Response:
    """No cookie jar, auth handlers, credentials, or automatic redirects."""
    request = Request(
        url, headers={"User-Agent": "event-radar/0.1", "Accept": "application/json,text/html"}
    )
    opener = build_opener(_NoRedirects())
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as exc:
        response = exc
    with response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise CollectionError("response exceeds size limit")
        return Response(response.code, dict(response.headers.items()), body)


class LumaClient:
    def __init__(
        self,
        settings: Requests,
        *,
        transport: Callable[[str, float], Response] = public_transport,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.settings = settings
        self.transport = transport
        self.sleep = sleep
        self.now = now
        self.attempts = 0

    def _delay(self, response: Response | None, attempt: int) -> float:
        fallback = min(2**attempt, self.settings.max_backoff_seconds)
        header = (
            next((v for k, v in response.headers.items() if k.lower() == "retry-after"), None)
            if response
            else None
        )
        if header is None:
            return fallback
        try:
            delay = float(header)
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    return fallback
                delay = (date - self.now()).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return fallback
        if not math.isfinite(delay):
            return fallback
        return min(max(0, delay), self.settings.max_backoff_seconds)

    def _get(self, url: str) -> bytes:
        for attempt in range(self.settings.retries + 1):
            if self.attempts >= self.settings.total_requests:
                raise BudgetExhausted("total HTTP attempt budget exhausted")
            self.attempts += 1
            response = None
            try:
                response = self.transport(url, self.settings.timeout_seconds)
            except (TimeoutError, URLError, OSError):
                error = "public HTTP transport failed"
            else:
                if 200 <= response.status < 300:
                    return response.body
                if response.status not in (408, 425, 429, 500, 502, 503, 504):
                    raise CollectionError(f"public HTTP permanent status {response.status}")
                error = f"public HTTP transient status {response.status}"
            if attempt == self.settings.retries:
                raise CollectionError(f"{error}; retries exhausted") from None
            # Do not wait when another request cannot be attempted.
            if self.attempts >= self.settings.total_requests:
                raise BudgetExhausted("total HTTP attempt budget exhausted")
            self.sleep(self._delay(response, attempt))
        raise AssertionError("unreachable")

    def page(self, slug: str) -> str:
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]+", slug):
            raise CollectionError("invalid public discovery slug")
        try:
            return self._get(PAGE_BASE + slug).decode("utf-8")
        except UnicodeError:
            raise CollectionError("public page is not valid UTF-8") from None

    def discovery(self, params: Mapping[str, str]) -> dict:
        body = self._get(API_BASE + "discover/get-paginated-events?" + urlencode(params))
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise CollectionError("public endpoint returned invalid JSON") from None
        if not isinstance(value, dict):
            raise CollectionError("public endpoint returned non-object JSON")
        return value
