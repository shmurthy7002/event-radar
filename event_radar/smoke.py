"""Explicit, bounded live diagnostics without event/person payload artifacts."""

from dataclasses import asdict, replace
from datetime import datetime

from event_radar.collectors.luma import LumaClient, collect, enrich
from event_radar.config import Config, ConfigError, Source


def smoke_config(config: Config) -> Config:
    allowed = (Source("sf", "city"), Source("ai", "category"), Source("tech", "category"))
    if Source("sf", "city") not in config.sources or any(s not in allowed for s in config.sources):
        raise ConfigError("smoke check requires SF city and supports only SF/AI/tech sources")
    return replace(
        config,
        requests=replace(
            config.requests,
            pages_per_source=min(2, config.requests.pages_per_source),
            total_requests=min(15, config.requests.total_requests),
            detail_requests=min(3, config.requests.detail_requests),
            retries=min(1, config.requests.retries),
            timeout_seconds=min(10, config.requests.timeout_seconds),
            max_backoff_seconds=min(3, config.requests.max_backoff_seconds),
        ),
    )


def run_smoke(config: Config, *, client: LumaClient, now: datetime | None = None) -> dict:
    collection = collect(config, client, now=now, compare_page_ids=True)
    descriptions = enrich(collection.events, client, detail_budget=config.requests.detail_requests)
    city = next(s for s in collection.sources if s.source == "sf")
    healthy = (
        all(s.usable_pages and not s.errors and not s.malformed for s in collection.sources)
        and city.page_comparison is not None
        and not descriptions.failed
    )
    return {
        "healthy": bool(healthy),
        "partial": collection.partial or bool(descriptions.warnings),
        "sources": [asdict(s) for s in collection.sources],
        "collected": len(collection.events),
        "duplicates": collection.duplicate_count,
        "requests": client.attempts,
        "budgets": asdict(config.requests),
        "descriptions": {
            "content": descriptions.content,
            "empty": descriptions.empty,
            "failed": descriptions.failed,
            "not_fetched": descriptions.not_fetched,
            "requests": descriptions.requests,
        },
        "warnings": list(
            dict.fromkeys(
                [w for s in collection.sources for w in s.warnings + s.errors]
                + descriptions.warnings
            )
        ),
        "coverage_note": "SF page/feed comparison precedes relevance filtering. Differences reflect this bounded sample, not exhaustive discovery. Explicit coordinates do not prove the source is free of location bias.",
    }
