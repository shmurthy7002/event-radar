"""Read-only preview orchestration; no state or provider dependencies."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from event_radar.collectors.luma import LumaClient, collect, enrich
from event_radar.config import Config
from event_radar.digest import Digest, render_digest
from event_radar.models import Event, retain_cached_description, utc_timestamp
from event_radar.relevance import filter_events, rank_events


@dataclass(frozen=True)
class Preview:
    digest: Digest
    summary: dict
    observed_events: list[Event]
    candidate_events: list[Event]


def build_preview(
    config: Config,
    *,
    client: LumaClient | None = None,
    now: datetime | None = None,
    previous_events: list[Event] | None = None,
    suppressed_ids: set[str] | None = None,
) -> Preview:
    clock = utc_timestamp(now if now is not None else datetime.now(UTC))
    client = client or LumaClient(config.requests)
    collection = collect(config, client, now=clock)
    candidates = {event.id: event for event in previous_events or []}
    for event in collection.events:
        candidates[event.id] = retain_cached_description(event, candidates.get(event.id))
    filtered = filter_events(list(candidates.values()), config, now=clock)
    pending = [event for event in filtered.events if event.id not in (suppressed_ids or set())]
    enrichment = enrich(pending, client, detail_budget=config.requests.detail_requests)
    ranking = rank_events(enrichment.events, config)
    warnings = []
    for source in collection.sources:
        warnings.extend(
            f"{source.source}: {message}" for message in source.warnings + source.errors
        )
    warnings.extend(enrichment.warnings)
    warnings.extend(w for event in collection.events for w in event.warnings)
    warnings = list(dict.fromkeys(warnings))
    summary = {
        "partial": collection.partial or bool(enrichment.warnings),
        "sources": [asdict(s) for s in collection.sources],
        "requests": client.attempts,
        "request_budget": config.requests.total_requests,
        "page_budget_per_source": config.requests.pages_per_source,
        "collected": len(collection.events),
        "duplicates": collection.duplicate_count,
        "eligible_before_ranking": len(filtered.events),
        "filter_exclusions": filtered.counts,
        "excluded_events": {**filtered.excluded, **ranking.excluded},
        "distances_km": filtered.distances_km,
        "matches": len(ranking.matches),
        "descriptions": {
            key: value for key, value in asdict(enrichment).items() if key != "events"
        },
        "description_population": "date/location/availability eligible events",
        "warnings": warnings,
    }
    digest = render_digest(
        ranking.matches, limit=config.digest_length, now=clock, warnings=warnings, coverage=summary
    )
    enriched = {event.id: event for event in enrichment.events}
    return Preview(
        digest,
        summary,
        [enriched.get(event.id, candidates[event.id]) for event in collection.events],
        enrichment.events,
    )


def write_preview(preview: Preview, directory: str | Path) -> None:
    """UTF-8 artifacts; callers must treat any write failure as an unsuccessful run."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "digest.html").write_text(preview.digest.html, encoding="utf-8")
    (directory / "digest.txt").write_text(preview.digest.text, encoding="utf-8")
    (directory / "results.json").write_text(
        json.dumps(preview.digest.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
