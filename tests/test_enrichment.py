import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from event_radar.collectors.luma import (
    CollectionError,
    LumaClient,
    Response,
    description_text,
    enrich,
    normalize_record,
)
from event_radar.config import Requests
from event_radar.models import DescriptionStatus


def event():
    raw = json.loads(Path("tests/fixtures/feed-first.json").read_text())["entries"][0]
    return normalize_record(raw, "sf", datetime(2026, 10, 28, tzinfo=UTC))


def test_nested_text():
    payload = json.loads(Path("tests/fixtures/detail.json").read_text())
    assert description_text(payload["description_mirror"]) == "Build robots\nLearn RL"
    assert description_text({"type": "doc", "content": []}) == ""


@pytest.mark.parametrize(
    "value",
    [
        None,
        "text",
        {},
        {"type": "unknown"},
        {"type": "doc", "content": {}},
        {"type": "text", "text": 42},
    ],
)
def test_malformed_description(value):
    with pytest.raises(CollectionError):
        description_text(value)


def test_priority_budget_and_empty_status():
    calls = []

    def transport(url, timeout):
        calls.append(url)
        return Response(
            200,
            {},
            b'{"event":{"api_id":"evt-sooner"},"description_mirror":{"type":"doc","content":[]}}',
        )

    later = event()
    sooner = replace(later, id="evt-sooner", start=later.start - timedelta(days=1), end=None)
    client = LumaClient(Requests(), transport=transport)
    result = enrich([later, sooner, sooner], client, detail_budget=1)
    assert len(calls) == 1
    assert "evt-sooner" in calls[0]
    assert result.empty == 1 and result.not_fetched == 1
    assert result.warnings
    assert next(e for e in result.events if e.id == sooner.id).description == ""


def test_retry_budget_and_failure_preserves_record():
    calls = []

    def transport(url, timeout):
        calls.append(url)
        return Response(503, {}, b"")

    client = LumaClient(Requests(), transport=transport, sleep=lambda _: None)
    result = enrich([event()], client, detail_budget=2)
    assert len(calls) == result.requests == 2
    assert result.failed == 1
    assert result.events[0].title == event().title
    assert result.events[0].description is None


@pytest.mark.parametrize(
    "payload",
    [
        {"event": {"api_id": "wrong"}},
        {"event": {"api_id": "evt-one"}},
        {"event": {"api_id": "evt-one"}, "description_mirror": None},
    ],
)
def test_bad_details_are_failed_not_empty(payload):
    client = LumaClient(
        Requests(), transport=lambda *_: Response(200, {}, json.dumps(payload).encode())
    )
    assert enrich([event()], client, detail_budget=1).failed == 1


def test_zero_budget_completed_content_and_global_limit():
    client = LumaClient(Requests(), transport=lambda *_: pytest.fail("unexpected network"))
    assert enrich([event()], client, detail_budget=0).not_fetched == 1
    done = replace(event(), description="done", description_status=DescriptionStatus.CONTENT)
    assert enrich([done], client, detail_budget=5).content == 1
    client.attempts = client.settings.total_requests
    assert enrich([event()], client, detail_budget=5).warnings
