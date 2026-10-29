from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from event_radar.config import load_config
from event_radar.models import Availability, Event, Observation, RecordError
from event_radar.relevance import distance_km, filter_events

NOW = datetime(2026, 10, 28, tzinfo=UTC)


@pytest.fixture
def config():
    return load_config("config.example.yaml")


@pytest.fixture
def event(config):
    return Event(
        "evt-test",
        "https://luma.com/example",
        "Robotics",
        NOW,
        (Observation("sf", NOW),),
        latitude=config.latitude,
        longitude=config.longitude,
    )


def test_date_boundaries(config, event):
    horizon = NOW + timedelta(days=config.horizon_days)
    events = [
        replace(event, id=str(i), start=start)
        for i, start in enumerate(
            (
                NOW - timedelta(microseconds=1),
                NOW,
                horizon - timedelta(microseconds=1),
                horizon,
                horizon + timedelta(days=1),
            )
        )
    ]
    result = filter_events(events, config, now=NOW)
    assert [e.id for e in result.events] == ["1", "2"]
    assert result.counts == {"beyond_horizon": 2, "past_start": 1}
    with pytest.raises(RecordError):
        filter_events(events, config, now=NOW.replace(tzinfo=None))


def test_radius_inclusive_boundary(config, event):
    far = replace(event, latitude=37.7749, longitude=-122.4194)
    radius = distance_km(config.latitude, config.longitude, far.latitude, far.longitude)
    assert 10 < radius < 30
    assert filter_events([far], replace(config, radius_km=radius), now=NOW).events
    assert filter_events([far], replace(config, radius_km=radius - 0.000001), now=NOW).counts == {
        "outside_radius": 1
    }
    assert filter_events([event], replace(config, radius_km=0), now=NOW).events
    assert distance_km(0, 0, 0, 180) == pytest.approx(20015.1144, rel=1e-6)


def test_unknown_location(config, event):
    hidden = replace(event, latitude=None, longitude=None)
    assert filter_events([hidden], config, now=NOW).counts == {"unknown_location": 1}
    result = filter_events([hidden], replace(config, unknown_location="include"), now=NOW)
    assert result.events == [hidden]
    assert result.distances_km[hidden.id] is None


@pytest.mark.parametrize(
    "status,setting,reason",
    [
        (Availability.SOLD_OUT, "exclude_sold_out", "sold_out"),
        (Availability.WAITLIST, "exclude_waitlist", "waitlist"),
    ],
)
def test_availability_explicit_policy(config, event, status, setting, reason):
    event = replace(event, availability=status)
    assert filter_events([event], config, now=NOW).events[0].availability == status
    assert filter_events([event], replace(config, **{setting: True}), now=NOW).counts == {reason: 1}


def test_first_exclusion_and_stable_order(config, event):
    past_hidden = replace(event, start=NOW - timedelta(days=1), latitude=None, longitude=None)
    assert filter_events([past_hidden], config, now=NOW).counts == {"past_start": 1}
    a, b = replace(event, id="a"), replace(event, id="b")
    assert filter_events([b, a], config, now=NOW).events == [a, b]
