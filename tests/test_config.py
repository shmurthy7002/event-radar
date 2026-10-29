from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from event_radar.config import ConfigError, load_config, load_email_settings, parse_config


@pytest.fixture
def data():
    return yaml.safe_load(Path("config.example.yaml").read_text())


def test_example_defaults(data):
    config = parse_config(data)
    assert config.unknown_location == "exclude"
    assert [s.slug for s in config.sources] == ["sf", "ai", "tech"]
    assert config.interests["research"][:2] == ("rl", "ml")
    assert load_config("config.example.yaml") == config


@pytest.mark.parametrize(
    "key,value",
    [
        ("horizon_days", True),
        ("radius_km", -1),
        ("digest_length", 0),
        ("minimum_score", float("nan")),
        ("exclude_waitlist", "false"),
        ("unknown_location", "maybe"),
        ("title_weight", 1),
        ("interests", {}),
        ("negative_keywords", "bad"),
        ("credentials", "secret"),
    ],
)
def test_invalid_top_level(data, key, value):
    data[key] = value
    with pytest.raises(ConfigError):
        parse_config(data)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("origin", "latitude", 91),
        ("origin", "longitude", -181),
        ("requests", "retries", 6),
        ("requests", "total_requests", 0),
        ("requests", "pages_per_source", 1.5),
        ("requests", "detail_requests", 51),
        ("requests", "timeout_seconds", False),
        ("requests", "unexpected", 2),
    ],
)
def test_invalid_nested(data, section, key, value):
    data[section][key] = value
    with pytest.raises(ConfigError):
        parse_config(data)


def test_missing_and_wrong_types(data):
    for invalid in (None, [], {"sources": []}):
        with pytest.raises(ConfigError):
            parse_config(invalid)
    for sources in ([{"slug": "../sf", "kind": "city"}], data["sources"] * 2):
        changed = deepcopy(data)
        changed["sources"] = sources
        with pytest.raises(ConfigError):
            parse_config(changed)


def test_environment_precedence_and_no_secret_repr(monkeypatch):
    monkeypatch.setenv("EVENT_RADAR_EMAIL_API_KEY", "process-secret")
    assert load_email_settings().api_key == "process-secret"
    settings = load_email_settings(
        {
            "EVENT_RADAR_EMAIL_API_KEY": "override",
            "EVENT_RADAR_EMAIL_TO": "a@example.test, b@example.test",
        }
    )
    assert settings.api_key == "override"
    assert settings.recipients == ("a@example.test", "b@example.test")
    assert "override" not in repr(settings)
    assert "example.test" not in repr(settings)
    assert load_email_settings({}).api_key is None


def test_invalid_file(tmp_path):
    file = tmp_path / "config.yaml"
    file.write_text("sources: [")
    with pytest.raises(ConfigError, match="valid YAML"):
        load_config(file)
