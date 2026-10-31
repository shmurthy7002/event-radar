"""Deterministic date, distance, availability, and interest rules."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from event_radar.config import Config
from event_radar.models import Availability, DescriptionStatus, Event, utc_timestamp


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


def matched_terms(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    folded = text.casefold()
    return tuple(
        sorted(
            {
                term
                for term in terms
                if re.search(
                    r"(?<!\w)"
                    + r"\s+".join(re.escape(part) for part in term.casefold().split())
                    + r"(?!\w)",
                    folded,
                )
            }
        )
    )


@dataclass(frozen=True)
class Contribution:
    group: str
    title_terms: tuple[str, ...]
    description_terms: tuple[str, ...]
    points: float
    scoring_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedEvent:
    event: Event
    score: float
    contributions: tuple[Contribution, ...]
    negative_terms: tuple[str, ...]
    title_only: bool


@dataclass(frozen=True)
class RankingResult:
    matches: list[RankedEvent]
    evaluations: list[RankedEvent]
    excluded: dict[str, str]


def rank_events(events: list[Event], config: Config) -> RankingResult:
    """Sum group maxima: strongest title keyword, otherwise one description point."""
    evaluations, matches, excluded = [], [], {}
    for event in sorted(events, key=lambda e: e.id):
        contributions = []
        description = event.description or ""
        for group, terms in sorted(config.interests.items()):
            title_terms = matched_terms(event.title, tuple(terms))
            description_terms = matched_terms(description, tuple(terms))
            if title_terms or description_terms:
                points = max(terms[term] for term in title_terms) if title_terms else 1
                scoring_terms = (
                    tuple(term for term in title_terms if terms[term] == points)
                    if title_terms
                    else description_terms
                )
                contributions.append(
                    Contribution(
                        group,
                        title_terms,
                        description_terms,
                        points,
                        scoring_terms,
                    )
                )
        negative = tuple(
            sorted(
                set(matched_terms(event.title, config.negative_keywords))
                | set(matched_terms(description, config.negative_keywords))
            )
        )
        ranked = RankedEvent(
            event,
            sum(c.points for c in contributions),
            tuple(contributions),
            negative,
            event.description_status in (DescriptionStatus.NOT_FETCHED, DescriptionStatus.FAILED),
        )
        evaluations.append(ranked)
        if negative:
            excluded[event.id] = "negative_keyword"
        elif ranked.score < config.minimum_score:
            excluded[event.id] = "below_minimum_score"
        else:
            matches.append(ranked)
    matches.sort(key=lambda r: (-r.score, r.event.start, r.event.id))
    return RankingResult(matches, evaluations, excluded)
