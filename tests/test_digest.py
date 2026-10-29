import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from event_radar.config import load_config
from event_radar.digest import local_time, render_digest
from event_radar.models import DescriptionStatus, Event, Observation, RecordError
from event_radar.relevance import rank_events

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def matches():
    event = Event(
        "evt-test",
        "https://luma.com/example",
        'Robotics <script>alert("bad")</script>',
        NOW,
        (Observation("sf", NOW),),
        city="Berkeley & SF",
        description="Learn <b>RL</b>\nwith robots",
        description_status=DescriptionStatus.CONTENT,
    )
    return rank_events([event], load_config("config.example.yaml")).matches


def test_escaping_and_inspectable_json():
    digest = render_digest(
        matches(), limit=10, now=NOW, warnings=["Partial <img src=x>"], coverage={"partial": True}
    )
    assert "<script>" not in digest.html and "<img " not in digest.html
    assert "&lt;script&gt;" in digest.html and "&amp;" in digest.html
    assert "Learn &lt;b&gt;RL&lt;/b&gt; with robots" in digest.html
    assert "Learn <b>RL</b> with robots" in digest.text
    assert 'href="https://luma.com/example"' in digest.html
    data = json.loads(json.dumps(digest.data))
    assert data["coverage"]["partial"]
    assert data["matches"][0]["contributions"]
    assert data["matches"][0]["event"]["provenance"]


def test_limit_stable_selection_and_omissions():
    a = matches()[0]
    b = replace(a, event=replace(a.event, id="evt-z"))
    first = render_digest([b, a], limit=1, now=NOW)
    second = render_digest([a, b], limit=1, now=NOW)
    assert first == second
    assert first.selected_ids == ("evt-test",)
    assert first.omitted_ids == ("evt-z",)
    assert not first.data["matches"][1]["included"]


def test_dst_fall_back_distinguished():
    assert "01:30 AM PDT (UTC-0700)" in local_time(datetime(2026, 11, 1, 8, 30, tzinfo=UTC))
    assert "01:30 AM PST (UTC-0800)" in local_time(datetime(2026, 11, 1, 9, 30, tzinfo=UTC))
    assert "03:30 AM PDT" in local_time(datetime(2026, 3, 8, 10, 30, tzinfo=UTC))


def test_empty_partial_and_missing_fields():
    empty = render_digest([], limit=1, now=NOW, warnings=["Partial discovery"])
    assert "No matching events in this collected sample" in empty.text
    assert "Partial discovery" in empty.html
    a = matches()[0]
    a = replace(
        a,
        event=replace(
            a.event, city=None, description=None, description_status=DescriptionStatus.NOT_FETCHED
        ),
        title_only=True,
    )
    digest = render_digest([a], limit=1, now=NOW)
    assert "Location unavailable" in digest.text
    assert "Description not fetched" in digest.text
    assert "title-only scoring" in digest.html


def test_links_revalidated():
    a = matches()[0]
    object.__setattr__(a.event, "url", "javascript:alert(1)")
    with pytest.raises(RecordError):
        render_digest([a], limit=1, now=NOW)
