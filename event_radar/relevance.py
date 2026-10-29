"""Deterministic date, distance, availability, and interest rules."""

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from event_radar.config import Config
from event_radar.models import Availability, Event, utc_timestamp


def distance_km(
    latitude: float, longitude: float, other_latitude: float, other_longitude: float
) -> float:
    """Great-circle distance using mean Earth radius (6371.0088 km)."""
    lat1, lat2 = math.radians(latitude), math.radians(other_latitude)
    dlat, dlon = lat2 - lat1, math.radians(other_longitude - longitude)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, a))))


@dataclass(frozen=True)
class FilterResult:
    events: list[Event]
    excluded: dict[str, str]
    counts: dict[str, int]
    distances_km: dict[str, float | None]


def filter_events(events: list[Event], config: Config, *, now: datetime) -> FilterResult:
    """Starts in [now, now+horizon), distance <= radius; first exclusion wins."""
    now = utc_timestamp(now)
    until = now + timedelta(days=config.horizon_days)
    accepted, excluded, distances = [], {}, {}
    for event in sorted(events, key=lambda e: (e.start, e.id)):
        reason = None
        if event.start < now:
            reason = "past_start"
        elif event.start >= until:
            reason = "beyond_horizon"
        else:
            distance = (
                None
                if event.latitude is None
                else distance_km(config.latitude, config.longitude, event.latitude, event.longitude)
            )
            distances[event.id] = distance
            if distance is None and config.unknown_location == "exclude":
                reason = "unknown_location"
            elif distance is not None and distance > config.radius_km:
                reason = "outside_radius"
            elif config.exclude_sold_out and event.availability == Availability.SOLD_OUT:
                reason = "sold_out"
            elif config.exclude_waitlist and event.availability == Availability.WAITLIST:
                reason = "waitlist"
        if reason:
            excluded[event.id] = reason
        else:
            accepted.append(event)
    return FilterResult(
        accepted, excluded, dict(sorted(Counter(excluded.values()).items())), distances
    )
