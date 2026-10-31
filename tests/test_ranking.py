from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from event_radar.config import load_config
from event_radar.models import DescriptionStatus, Event, Observation
from event_radar.relevance import rank_events

NOW = datetime(2026, 10, 28, tzinfo=UTC)


@pytest.fixture
def config():
    return replace(
        load_config("config.example.yaml"),
        interests={"research": dict.fromkeys(("ai", "rl", "ml", "machine learning"), 3)},
        negative_keywords=("sales pitch",),
    )


def event(title, description=None, **kwargs):
    return Event(
        "evt-test",
        "https://luma.com/test",
        title,
        NOW,
        (Observation("sf", NOW),),
        description=description,
        description_status=DescriptionStatus.CONTENT
        if description
        else DescriptionStatus.NOT_FETCHED,
        **kwargs,
    )


def test_synonyms_count_once_and_title_beats_description(config):
    ranked = rank_events([event("AI / ML / machine learning", "AI and RL")], config).matches[0]
    assert ranked.score == 3
    assert ranked.contributions[0].title_terms == ("ai", "machine learning", "ml")
    assert ranked.contributions[0].description_terms == ("ai", "rl")
    assert not ranked.title_only
    assert rank_events([event("Workshop", "AI")], config).matches[0].score == 1


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Trail mail pearl XML", False),
        ("AI, RL & ML", True),
        ("An ai-based workshop", True),
        ("email", False),
        ("RAIL", False),
        ("機AI器", False),
    ],
)
def test_word_boundaries(config, title, expected):
    result = rank_events([event(title)], config)
    assert bool(result.matches) == expected


def test_negative_in_either_field(config):
    for item in (event("AI sales pitch"), event("AI", "A SALES   PITCH")):
        result = rank_events([item], config)
        assert not result.matches
        assert result.excluded[item.id] == "negative_keyword"
        assert result.evaluations[0].negative_terms == ("sales pitch",)


def test_overlap_across_groups_and_threshold(config):
    config = replace(
        config,
        interests={"ai": {"ai": 3}, "safety": {"ai safety": 3, "safety": 3}},
        minimum_score=6,
    )
    result = rank_events([event("AI safety")], config)
    assert result.matches[0].score == 6
    assert len(result.matches[0].contributions) == 2
    assert not rank_events([event("AI safety")], replace(config, minimum_score=6.1)).matches


def test_stable_order_title_only_and_failed(config):
    a = replace(event("AI"), id="a")
    b = replace(a, id="b")
    later = replace(a, id="later", start=NOW + timedelta(days=1))
    result = rank_events([later, b, a], config)
    assert [r.event.id for r in result.matches] == ["a", "b", "later"]
    assert result.matches[0].title_only
    failed = replace(a, description_status=DescriptionStatus.FAILED)
    assert rank_events([failed], config).matches[0].title_only
    empty = replace(a, description="", description_status=DescriptionStatus.EMPTY)
    assert not rank_events([empty], config).matches[0].title_only


def test_independent_points_group_maximum_and_explanation(config):
    config = replace(config, interests={"robotics": {"embodied ai": 8, "ai": 2, "robots": 4}})
    strong = replace(event("Embodied AI and AI AI", "robots and embodied AI"), id="strong")
    weak = replace(event("AI", "embodied AI"), id="weak")
    description = replace(event("Workshop", "robots embodied AI AI AI"), id="description")
    result = rank_events([description, weak, strong], config)
    assert [(r.event.id, r.score) for r in result.matches] == [
        ("strong", 8),
        ("weak", 2),
        ("description", 1),
    ]
    c = result.matches[0].contributions[0]
    assert c.title_terms == ("ai", "embodied ai")
    assert c.description_terms == ("ai", "embodied ai", "robots")
    assert c.scoring_terms == ("embodied ai",)
    assert c.points == 8


def test_description_one_per_group_cross_group_addition_and_title_ties(config):
    config = replace(config, interests={"research": {"ai": 8, "ml": 8}, "safety": {"ai safety": 2}})
    ranked = rank_events([event("Workshop", "AI ML AI safety")], config).matches[0]
    assert ranked.score == 2
    ranked = rank_events([event("AI ML AI safety", "AI ML AI safety")], config).matches[0]
    assert ranked.score == 10
    assert ranked.contributions[0].scoring_terms == ("ai", "ml")


def test_order_independent_of_config_and_input_order(config):
    config = replace(config, interests={"safety": {"ai safety": 2}, "research": {"ai": 8, "ml": 8}})
    events = [replace(event("AI ML AI safety"), id=key) for key in ("b", "a")]
    expected = rank_events(events, config)
    reversed_config = replace(
        config,
        interests={
            g: dict(reversed(list(t.items()))) for g, t in reversed(list(config.interests.items()))
        },
    )
    assert rank_events(list(reversed(events)), reversed_config) == expected
