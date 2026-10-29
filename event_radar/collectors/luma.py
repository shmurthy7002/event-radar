"""Undocumented, credential-free Luma web interfaces. All source details live here."""

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from event_radar.config import Config, Requests, Source
from event_radar.models import (
    Availability,
    DescriptionStatus,
    Event,
    Observation,
    RecordError,
    merge_events,
    utc_timestamp,
)

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

    def _get(self, url: str, *, attempt_limit: int | None = None) -> bytes:
        stop_at = (
            self.attempts + attempt_limit
            if attempt_limit is not None
            else self.settings.total_requests
        )
        for attempt in range(self.settings.retries + 1):
            if self.attempts >= min(stop_at, self.settings.total_requests):
                raise BudgetExhausted("HTTP attempt budget exhausted")
            self.attempts += 1
            response = None
            try:
                response = self.transport(url, self.settings.timeout_seconds)
            except (TimeoutError, URLError, OSError, HTTPException):
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
            if self.attempts >= min(stop_at, self.settings.total_requests):
                raise BudgetExhausted("HTTP attempt budget exhausted")
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
        return self._json("discover/get-paginated-events", params)

    def detail(self, event_id: str, *, attempt_limit: int) -> dict:
        return self._json("event/get", {"event_api_id": event_id}, attempt_limit=attempt_limit)

    def _json(
        self, path: str, params: Mapping[str, str], *, attempt_limit: int | None = None
    ) -> dict:
        body = self._get(API_BASE + path + "?" + urlencode(params), attempt_limit=attempt_limit)
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise CollectionError("public endpoint returned invalid JSON") from None
        if not isinstance(value, dict):
            raise CollectionError("public endpoint returned non-object JSON")
        return value


class _PageDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False


def discovery_identifier(page: str, source: Source) -> str:
    """Parse only known metadata paths; unrelated IDs on a page are never guessed."""
    parser = _PageDataParser()
    try:
        parser.feed(page)
        initial = json.loads("".join(parser.parts))["props"]["pageProps"]["initialData"]
        kind, key = (
            ("discover-place", "place") if source.kind == "city" else ("category", "category")
        )
        if initial["kind"] != kind:
            raise ValueError
        metadata = initial["data"][key]
        identifier = metadata["api_id"]
        prefix = "discplace-" if source.kind == "city" else "cat-"
        if (
            metadata["slug"] != source.slug
            or not isinstance(identifier, str)
            or not re.fullmatch(prefix + r"[A-Za-z0-9_-]+", identifier)
        ):
            raise ValueError
        return identifier
    except (ValueError, KeyError, TypeError, RecursionError):
        raise CollectionError(
            "public discovery metadata schema changed or identifier missing"
        ) from None


def normalize_record(record, source: str, collected_at: datetime) -> Event:
    """Map the observed public feed schema into source-independent Event fields."""
    if not isinstance(record, dict) or not isinstance(record.get("event"), dict):
        raise RecordError("event: expected nested object")
    raw = record["event"]
    warnings = []

    def optional_text(value, label):
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            warnings.append(f"discarded malformed {label}")
            return None
        return value.strip() or None

    address = raw.get("geo_address_info") or {}
    if not isinstance(address, dict):
        warnings.append("discarded malformed address")
        address = {}
    coordinate = raw.get("coordinate")
    latitude = longitude = None
    if coordinate is not None:
        if isinstance(coordinate, dict):
            lat, lon = coordinate.get("latitude"), coordinate.get("longitude")
            if (
                all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon))
                and -90 <= lat <= 90
                and -180 <= lon <= 180
            ):
                latitude, longitude = lat, lon
            else:
                warnings.append("discarded malformed coordinates")
        else:
            warnings.append("discarded malformed coordinates")
    end = None
    if raw.get("end_at") is not None:
        try:
            end = utc_timestamp(raw["end_at"])
            if end < utc_timestamp(raw.get("start_at")):
                raise RecordError("end before start")
        except RecordError:
            warnings.append("discarded malformed end timestamp")
            end = None
    timezone = optional_text(raw.get("timezone"), "source timezone")
    if timezone:
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            warnings.append("discarded unknown source timezone")
            timezone = None
    slug_or_url = raw.get("url")
    if isinstance(slug_or_url, str) and re.fullmatch(r"[A-Za-z0-9_-]+", slug_or_url):
        slug_or_url = PAGE_BASE + slug_or_url
    availability = {
        "open": Availability.AVAILABLE,
        "sold-out": Availability.SOLD_OUT,
        "waitlist": Availability.WAITLIST,
        "closed": Availability.CLOSED,
    }.get(
        record.get("registration_availability")
        if isinstance(record.get("registration_availability"), str)
        else None,
        Availability.UNKNOWN,
    )
    if availability == Availability.UNKNOWN and record.get("registration_availability") is not None:
        warnings.append("unrecognized registration availability")
    if record.get("api_id") is not None and record["api_id"] != raw.get("api_id"):
        warnings.append("listing ID differs from event ID; event ID used")
    return Event(
        id=raw.get("api_id"),
        title=raw.get("name"),
        url=slug_or_url,
        start=raw.get("start_at"),
        end=end,
        source_timezone=timezone,
        city=optional_text(address.get("city"), "city"),
        neighborhood=optional_text(address.get("sublocality"), "neighborhood"),
        venue=optional_text(address.get("address"), "venue"),
        latitude=latitude,
        longitude=longitude,
        availability=availability,
        provenance=(Observation(source, collected_at),),
        warnings=tuple(warnings),
    )


@dataclass
class SourceReport:
    source: str
    identifier: str | None = None
    pages: int = 0
    records: int = 0
    normalized: int = 0
    malformed: int = 0
    usable_pages: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CollectionResult:
    events: list[Event]
    sources: list[SourceReport]
    duplicate_count: int
    requests: int

    @property
    def partial(self) -> bool:
        return any(s.truncated or s.errors or s.warnings or s.malformed for s in self.sources)


class TotalCollectionFailure(CollectionError):
    def __init__(self, result: CollectionResult):
        super().__init__("all discovery sources failed; see per-source errors")
        self.result = result


def collect(
    config: Config, client: LumaClient | None = None, *, now: datetime | None = None
) -> CollectionResult:
    """Collect every fetched record, without date/rank filtering or delivery side effects."""
    client = client or LumaClient(config.requests)
    collected_at = utc_timestamp(now if now is not None else datetime.now(UTC))
    reports, events = [], []
    initial_attempts = client.attempts
    for source in config.sources:
        report = SourceReport(source.slug)
        reports.append(report)
        try:
            report.identifier = discovery_identifier(client.page(source.slug), source)
            params = {
                "discover_place_api_id"
                if source.kind == "city"
                else "discover_category_api_id": report.identifier,
                "latitude": str(config.latitude),
                "longitude": str(config.longitude),
                "pagination_limit": "20",
            }
            # Match the category page's own request and avoid IP-selected location.
            if source.kind == "category":
                params["slug"] = source.slug
            cursors = set()
            for page_number in range(config.requests.pages_per_source):
                payload = client.discovery(params)
                report.pages += 1
                entries, more = payload.get("entries"), payload.get("has_more")
                if not isinstance(entries, list) or not isinstance(more, bool):
                    raise CollectionError(
                        "discovery page schema changed: entries/has_more required"
                    )
                report.records += len(entries)
                parsed = 0
                for record in entries:
                    try:
                        event = normalize_record(record, source.slug, collected_at)
                    except RecordError as exc:
                        report.malformed += 1
                        report.warnings.append(f"skipped malformed record: {exc}")
                    else:
                        events.append(event)
                        report.normalized += 1
                        parsed += 1
                        report.warnings.extend(event.warnings)
                if entries and not parsed:
                    raise CollectionError("discovery page has no parseable event records")
                report.usable_pages += 1
                if not more:
                    break
                cursor = payload.get("next_cursor")
                if not isinstance(cursor, str) or not cursor:
                    raise CollectionError("more results advertised but next cursor missing")
                if cursor in cursors:
                    raise CollectionError("repeated pagination cursor")
                cursors.add(cursor)
                if page_number + 1 == config.requests.pages_per_source:
                    report.truncated = True
                    report.warnings.append("page budget exhausted; more results remain")
                else:
                    params["pagination_cursor"] = cursor
        except CollectionError as exc:
            report.errors.append(str(exc))
            report.truncated = True
    merged = merge_events(events)
    result = CollectionResult(
        merged, reports, len(events) - len(merged), client.attempts - initial_attempts
    )
    if not any(s.usable_pages for s in reports):
        raise TotalCollectionFailure(result)
    return result


def description_text(value) -> str:
    """Extract text from the observed rich-text tree without interpreting markup."""
    count = 0

    def walk(node, depth=0):
        nonlocal count
        count += 1
        if (
            depth > 100
            or count > 10000
            or not isinstance(node, dict)
            or not isinstance(node.get("type"), str)
        ):
            raise CollectionError("malformed rich-text description")
        if node["type"] == "text":
            if not isinstance(node.get("text"), str):
                raise CollectionError("malformed description text node")
            return node["text"]
        children = node.get("content", [])
        if not isinstance(children, list):
            raise CollectionError("malformed description content")
        text = "".join(walk(child, depth + 1) for child in children)
        if node["type"] in ("paragraph", "heading", "listItem", "hardBreak"):
            text += "\n"
        return text

    return "\n".join(line.strip() for line in walk(value).splitlines() if line.strip())


@dataclass
class EnrichmentResult:
    events: list[Event]
    requests: int
    content: int
    empty: int
    failed: int
    not_fetched: int
    warnings: list[str]


def enrich(events: list[Event], client: LumaClient, *, detail_budget: int) -> EnrichmentResult:
    """Soonest start, then ID; retries consume both detail and global budgets."""
    records = {event.id: event for event in merge_events(events)}
    initial = client.attempts
    warnings = []
    for event in sorted(records.values(), key=lambda e: (e.start, e.id)):
        if event.description_status in (DescriptionStatus.CONTENT, DescriptionStatus.EMPTY):
            continue
        remaining = detail_budget - (client.attempts - initial)
        if remaining <= 0 or client.attempts >= client.settings.total_requests:
            warnings.append("description request budget exhausted; coverage reduced")
            break
        try:
            payload = client.detail(event.id, attempt_limit=remaining)
            if (
                not isinstance(payload.get("event"), dict)
                or payload["event"].get("api_id") != event.id
            ):
                raise CollectionError("detail event ID missing or mismatched")
            if "description_mirror" not in payload:
                raise CollectionError("description missing from detail response")
            text = description_text(payload["description_mirror"])
        except CollectionError as exc:
            warning = f"description enrichment failed: {exc}"
            warnings.append(warning)
            records[event.id] = replace(
                event,
                description=None,
                description_status=DescriptionStatus.FAILED,
                warnings=event.warnings + (warning,),
            )
        else:
            records[event.id] = replace(
                event,
                description=text,
                description_status=DescriptionStatus.CONTENT if text else DescriptionStatus.EMPTY,
            )
    result = sorted(records.values(), key=lambda e: e.id)
    counts = {
        status: sum(e.description_status == status for e in result) for status in DescriptionStatus
    }
    return EnrichmentResult(
        result,
        client.attempts - initial,
        counts[DescriptionStatus.CONTENT],
        counts[DescriptionStatus.EMPTY],
        counts[DescriptionStatus.FAILED],
        counts[DescriptionStatus.NOT_FETCHED],
        warnings,
    )
