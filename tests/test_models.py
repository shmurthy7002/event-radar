import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from event_radar.models import (
    Availability,
    DescriptionStatus,
    Event,
    Observation,
    RecordError,
    merge_events,
    registration_url,
)


@pytest.fixture
def records():
    raw = json.loads(Path("tests/fixtures/events.json").read_text())
    return [
        Event(**item, provenance=(Observation(source, datetime(2026, 10, day, tzinfo=UTC)),))
        for item, source, day in zip(raw, ("sf", "ai"), (28, 29), strict=True)
    ]


def test_utc_and_merge_precedence(records):
    first, second = records
    assert first.start.hour == 8
    assert second.start.hour == 9
    merged = merge_events(records)
    assert merge_events(list(reversed(records))) == merged
    event = merged[0]
    assert event.title == "Updated robotics event"
    assert event.city == "Berkeley"
    assert event.venue == "Example Hall"
    assert event.url == "https://luma.com/example"
    assert len(event.provenance) == 2
    assert event.warnings
    assert json.loads(json.dumps(event.to_dict()))["start"].endswith("+00:00")


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://lu.ma/x",
        "https://lu.ma.evil.test/x",
        "https://evil@lu.ma/x",
        "https://lu.ma:444/x",
        "https://lu.ma/x\\bad",
        "https://lu.ma/%0ax",
        "https://lu.ma/",
        "https://lu.ma/x\n",
    ],
)
def test_unsafe_urls(url):
    with pytest.raises(RecordError):
        registration_url(url)


@pytest.mark.parametrize(
    "key,value",
    [
        ("id", ""),
        ("title", None),
        ("start", "2026-11-01T01:30:00"),
        ("start", "bad"),
        ("end", "2020-01-01T00:00:00Z"),
        ("source_timezone", "bad/timezone"),
        ("latitude", 91),
        ("latitude", float("nan")),
        ("provenance", ()),
        ("description", "content without fetch status"),
        ("availability", "invalid"),
    ],
)
def test_malformed_records(records, key, value):
    with pytest.raises(RecordError):
        replace(records[0], **{key: value})


def test_description_states_and_optional_coordinates(records):
    first = records[0]
    assert first.latitude is None
    content = replace(first, description="Robotics", description_status=DescriptionStatus.CONTENT)
    failed = replace(records[1], description_status=DescriptionStatus.FAILED)
    assert merge_events([content, failed])[0].description == "Robotics"
    empty = replace(records[1], description="", description_status=DescriptionStatus.EMPTY)
    assert merge_events([content, empty])[0].description == ""
    assert merge_events([first, failed])[0].description_status == DescriptionStatus.FAILED
    assert replace(first, availability=Availability.WAITLIST).availability == "waitlist"
    with pytest.raises(RecordError):
        replace(first, latitude=37.8)


def test_duplicate_ties_and_different_ids(records):
    a = records[0]
    b = replace(a, title="Another title")
    assert merge_events([a, b, a]) == merge_events([b, a])
    assert len(merge_events([a, replace(a, id="evt-other")])) == 2
