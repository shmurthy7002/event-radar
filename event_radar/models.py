"""Source-independent event records, validation, and deterministic merging."""

import json
import math
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class RecordError(ValueError):
    """Required event data is missing, malformed, or unsafe."""


class DescriptionStatus(StrEnum):
    NOT_FETCHED = "not_fetched"
    EMPTY = "empty"
    CONTENT = "content"
    FAILED = "failed"


class Availability(StrEnum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    SOLD_OUT = "sold_out"
    WAITLIST = "waitlist"
    CLOSED = "closed"


def utc_timestamp(value: str | datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise RecordError("timestamp: an explicit UTC offset is required")
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, OverflowError) as exc:
        raise RecordError("timestamp: expected an ISO timestamp with explicit offset") from exc


def registration_url(value: str) -> str:
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        raise RecordError("registration URL: expected a public HTTPS Luma URL")
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.hostname not in ("lu.ma", "luma.com", "www.lu.ma", "www.luma.com")
            or url.username is not None
            or url.password is not None
            or url.port not in (None, 443)
            or not re.fullmatch(r"/[A-Za-z0-9_-]+/?", url.path)
        ):
            raise ValueError
    except ValueError as exc:
        raise RecordError("registration URL: expected a public HTTPS Luma event URL") from exc
    return "https://luma.com" + url.path.rstrip("/")


def _optional_text(value, label):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise RecordError(f"{label}: expected nonempty text or null")


@dataclass(frozen=True, order=True)
class Observation:
    source: str
    collected_at: datetime

    def __post_init__(self):
        if not isinstance(self.source, str) or not self.source.strip():
            raise RecordError("provenance: source is required")
        object.__setattr__(self, "collected_at", utc_timestamp(self.collected_at))


@dataclass(frozen=True)
class Event:
    id: str
    url: str
    title: str
    start: datetime
    provenance: tuple[Observation, ...]
    end: datetime | None = None
    source_timezone: str | None = None
    description: str | None = None
    description_status: DescriptionStatus = DescriptionStatus.NOT_FETCHED
    city: str | None = None
    neighborhood: str | None = None
    venue: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    availability: Availability = Availability.UNKNOWN
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        for label in ("id", "title"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise RecordError(f"{label}: nonempty text required")
        object.__setattr__(self, "url", registration_url(self.url))
        object.__setattr__(self, "start", utc_timestamp(self.start))
        if self.end is not None:
            object.__setattr__(self, "end", utc_timestamp(self.end))
            if self.end < self.start:
                raise RecordError("end: must not precede start")
        if self.source_timezone is not None:
            try:
                ZoneInfo(self.source_timezone)
            except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
                raise RecordError("source_timezone: unknown IANA timezone") from exc
        for label in ("city", "neighborhood", "venue"):
            _optional_text(getattr(self, label), label)
        if (self.latitude is None) != (self.longitude is None):
            raise RecordError("coordinates: latitude and longitude must both be present or absent")
        for label, limit in (("latitude", 90), ("longitude", 180)):
            value = getattr(self, label)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not -limit <= value <= limit
                or not math.isfinite(value)
            ):
                raise RecordError(f"{label}: invalid coordinate")
        try:
            object.__setattr__(
                self, "description_status", DescriptionStatus(self.description_status)
            )
            object.__setattr__(self, "availability", Availability(self.availability))
        except ValueError as exc:
            raise RecordError("unknown description or availability status") from exc
        expected = {
            DescriptionStatus.NOT_FETCHED: self.description is None,
            DescriptionStatus.FAILED: self.description is None,
            DescriptionStatus.EMPTY: self.description == "",
            DescriptionStatus.CONTENT: isinstance(self.description, str)
            and bool(self.description.strip()),
        }
        if not expected[self.description_status]:
            raise RecordError("description: inconsistent with description_status")
        if not self.provenance or any(not isinstance(p, Observation) for p in self.provenance):
            raise RecordError("provenance: at least one observation required")
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))
        if any(not isinstance(w, str) for w in self.warnings):
            raise RecordError("warnings: expected strings")
        object.__setattr__(self, "warnings", tuple(sorted(set(self.warnings))))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat() if self.end else None
        data["warnings"] = list(self.warnings)
        data["provenance"] = [
            {"source": p.source, "collected_at": p.collected_at.isoformat()}
            for p in self.provenance
        ]
        return data


def merge_events(events: list[Event]) -> list[Event]:
    """Latest nonmissing metadata wins; ties use canonical JSON, independent of input order.

    Completed description fetches outrank failed/unattempted fetches. Among
    completed fetches the latest wins, including an explicitly empty description.
    """
    groups: dict[str, list[Event]] = {}
    for event in events:
        groups.setdefault(event.id, []).append(event)
    merged = []
    for event_id in sorted(groups):
        records = sorted(
            groups[event_id],
            key=lambda e: (
                max(p.collected_at for p in e.provenance),
                json.dumps(e.to_dict(), sort_keys=True),
            ),
            reverse=True,
        )
        best = records[0]
        updates = {}
        for label in ("end", "source_timezone", "city", "neighborhood", "venue"):
            updates[label] = next(
                (getattr(e, label) for e in records if getattr(e, label) is not None), None
            )
        # Coordinates are selected together, never combined from different records.
        located = next((e for e in records if e.latitude is not None), best)
        updates.update(latitude=located.latitude, longitude=located.longitude)
        updates["availability"] = next(
            (e.availability for e in records if e.availability != Availability.UNKNOWN),
            Availability.UNKNOWN,
        )
        described = next(
            (
                e
                for e in records
                if e.description_status in (DescriptionStatus.CONTENT, DescriptionStatus.EMPTY)
            ),
            None,
        )
        described = described or next(
            (e for e in records if e.description_status == DescriptionStatus.FAILED), best
        )
        updates.update(
            description=described.description, description_status=described.description_status
        )
        updates["provenance"] = tuple(p for e in records for p in e.provenance)
        warnings = {w for e in records for w in e.warnings}
        if len({(e.title, e.start, e.url) for e in records}) > 1:
            warnings.add("conflicting required metadata; latest observation wins")
        updates["warnings"] = tuple(warnings)
        # An older end may no longer apply after a changed start.
        if updates["end"] is not None and updates["end"] < best.start:
            updates["end"] = None
            updates["warnings"] += ("discarded end before updated start",)
        merged.append(replace(best, **updates))
    return merged
