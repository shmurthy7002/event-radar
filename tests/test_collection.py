import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from event_radar.collectors.luma import (
    CollectionError,
    LumaClient,
    Response,
    TotalCollectionFailure,
    collect,
    discovery_identifier,
    normalize_record,
)
from event_radar.config import Source, load_config

FIXTURES = Path("tests/fixtures")
NOW = datetime(2026, 10, 28, tzinfo=UTC)


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def config():
    config = load_config("config.example.yaml")
    return replace(config, sources=(Source("sf", "city"),))


def metadata(slug):
    if slug == "sf":
        return (FIXTURES / "city.html").read_text()
    return (
        (FIXTURES / "category.html")
        .read_text()
        .replace('"ai"', f'"{slug}"')
        .replace("cat-ai", f"cat-{slug}")
    )


def mock_client(config, pages, bad_sources=()):
    calls = []

    def transport(url, timeout):
        calls.append(url)
        parsed = urlsplit(url)
        if parsed.hostname == "luma.com":
            slug = parsed.path[1:]
            return Response(503 if slug in bad_sources else 200, {}, metadata(slug).encode())
        query = parse_qs(parsed.query)
        slug = query.get("slug", ["sf"])[0]
        cursor = query.get("pagination_cursor", [None])[0]
        payload = pages[(slug, cursor)]
        return Response(200, {}, json.dumps(payload).encode())

    return LumaClient(
        replace(config.requests, retries=0), transport=transport, sleep=lambda _: None
    ), calls


def test_identifiers_and_schema_changes():
    assert discovery_identifier(metadata("sf"), Source("sf", "city")) == "discplace-example"
    assert discovery_identifier(metadata("ai"), Source("ai", "category")) == "cat-ai"
    for page in (
        "<html>no data</html>",
        "<script id='__NEXT_DATA__'>[]</script>",
        metadata("tech"),
        metadata("ai").replace('"category":', '"renamed":'),
    ):
        with pytest.raises(CollectionError, match="metadata schema"):
            discovery_identifier(page, Source("ai", "category"))


def test_pagination_all_records_and_merge_across_feeds(config):
    config = replace(
        config, sources=(Source("sf", "city"), Source("ai", "category"), Source("tech", "category"))
    )
    pages = {
        (s, cursor): fixture(name)
        for s in ("sf", "ai", "tech")
        for cursor, name in ((None, "feed-first.json"), ("opaque+/=first", "feed-last.json"))
    }
    client, calls = mock_client(config, pages)
    result = collect(config, client, now=NOW)
    assert {e.id for e in result.events} == {"evt-one", "evt-two", "evt-three"}
    assert result.duplicate_count == 9
    assert result.requests == 9
    assert result.partial  # Malformed records are visibly skipped.
    assert all(s.pages == 2 and s.records == 5 and s.malformed == 1 for s in result.sources)
    assert all(len(e.provenance) == 3 for e in result.events)
    queries = [parse_qs(urlsplit(url).query) for url in calls if "?" in url]
    assert any(q.get("pagination_cursor") == ["opaque+/=first"] for q in queries)
    assert all(q["latitude"] == [str(config.latitude)] for q in queries)
    assert any(
        q.get("discover_category_api_id") == ["cat-tech"] and q["slug"] == ["tech"] for q in queries
    )
    assert result.events[0].provenance[0].collected_at == NOW


@pytest.mark.parametrize("cursor", [None, "", 12])
def test_missing_cursor_is_partial(config, cursor):
    page = fixture("feed-first.json")
    page["next_cursor"] = cursor
    client, calls = mock_client(config, {("sf", None): page})
    result = collect(config, client, now=NOW)
    assert result.partial
    assert result.sources[0].truncated
    assert "cursor missing" in result.sources[0].errors[0]
    assert len(calls) == 2


def test_repeated_cursor(config):
    page = fixture("feed-first.json")
    client, calls = mock_client(config, {("sf", None): page, ("sf", "opaque+/=first"): page})
    result = collect(config, client, now=NOW)
    assert "repeated" in result.sources[0].errors[0]
    assert len(calls) == 3


def test_page_budget(config):
    config = replace(config, requests=replace(config.requests, pages_per_source=1))
    client, calls = mock_client(config, {("sf", None): fixture("feed-first.json")})
    result = collect(config, client, now=NOW)
    assert result.sources[0].truncated
    assert any("page budget" in w for w in result.sources[0].warnings)
    assert len(calls) == 2


def test_total_request_budget(config):
    config = replace(
        config,
        sources=(Source("sf", "city"), Source("ai", "category")),
        requests=replace(config.requests, total_requests=2),
    )
    client, calls = mock_client(config, {("sf", None): fixture("feed-first.json")})
    result = collect(config, client, now=NOW)
    assert result.partial
    assert len(calls) == 2
    assert all("budget exhausted" in s.errors[0] for s in result.sources)


def test_partial_and_total_source_failure(config):
    config = replace(config, sources=(Source("sf", "city"), Source("ai", "category")))
    client, _ = mock_client(config, {("ai", None): fixture("feed-last.json")}, bad_sources=("sf",))
    result = collect(config, client, now=NOW)
    assert result.partial
    assert len(result.events) == 2
    client, _ = mock_client(config, {}, bad_sources=("sf", "ai"))
    with pytest.raises(TotalCollectionFailure) as exc:
        collect(config, client, now=NOW)
    assert len(exc.value.result.sources) == 2
    assert all(s.errors for s in exc.value.result.sources)


@pytest.mark.parametrize(
    "payload", [{}, {"entries": [], "has_more": "true"}, {"entries": [None, {}], "has_more": False}]
)
def test_changed_schema_or_all_malformed_is_total_failure(config, payload):
    client, _ = mock_client(config, {("sf", None): payload})
    with pytest.raises(TotalCollectionFailure):
        collect(config, client, now=NOW)


def test_healthy_empty_is_success(config):
    client, _ = mock_client(config, {("sf", None): {"entries": [], "has_more": False}})
    result = collect(config, client, now=NOW)
    assert result.events == []
    assert not result.partial


def test_schema_change_after_valid_page_preserves_events(config):
    client, _ = mock_client(
        config, {("sf", None): fixture("feed-first.json"), ("sf", "opaque+/=first"): {}}
    )
    result = collect(config, client, now=NOW)
    assert len(result.events) == 2
    assert result.partial
    assert result.sources[0].pages == 2


def test_normalization_optional_fields_and_status():
    record = fixture("feed-first.json")["entries"][0]
    event = normalize_record(record, "sf", NOW)
    assert event.venue == "Example Hall"
    assert event.latitude == 37.87
    assert event.description_status == "not_fetched"
    assert event.availability == "available"
    record["event"].update(coordinate={"latitude": "bad"}, timezone="invalid", end_at="invalid")
    record["registration_availability"] = {"changed": "schema"}
    event = normalize_record(record, "sf", NOW)
    assert event.latitude is None
    assert event.end is None
    assert event.source_timezone is None
    assert event.availability == "unknown"
    assert len(event.warnings) == 4


@pytest.mark.parametrize("latitude", [10**1000, float("nan"), float("inf"), True])
def test_malformed_optional_coordinates_do_not_abort_collection(latitude):
    record = fixture("feed-first.json")["entries"][0]
    record["event"]["coordinate"]["latitude"] = latitude
    event = normalize_record(record, "sf", NOW)
    assert event.latitude is None
    assert "discarded malformed coordinates" in event.warnings
