"""Validated non-sensitive YAML and separately loaded environment settings."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """Configuration cannot safely be used."""


@dataclass(frozen=True)
class Source:
    slug: str
    kind: str


@dataclass(frozen=True)
class Requests:
    timeout_seconds: float = 15
    retries: int = 2
    max_backoff_seconds: float = 30
    pages_per_source: int = 5
    total_requests: int = 50
    detail_requests: int = 20


@dataclass(frozen=True)
class Config:
    sources: tuple[Source, ...]
    horizon_days: int
    latitude: float
    longitude: float
    radius_km: float
    unknown_location: str
    exclude_sold_out: bool
    exclude_waitlist: bool
    requests: Requests
    digest_length: int
    interests: dict[str, tuple[str, ...]]
    title_weight: float
    description_weight: float
    negative_keywords: tuple[str, ...]
    minimum_score: float


@dataclass(frozen=True)
class EmailSettings:
    api_key: str | None = field(default=None, repr=False)
    sender: str | None = field(default=None, repr=False)
    recipients: tuple[str, ...] = field(default=(), repr=False)


def load_email_settings(environ: Mapping[str, str] | None = None) -> EmailSettings:
    """Explicit mapping overrides the process environment; never echo secret values."""
    env = os.environ if environ is None else environ
    return EmailSettings(
        api_key=env.get("EVENT_RADAR_EMAIL_API_KEY") or None,
        sender=env.get("EVENT_RADAR_EMAIL_FROM") or None,
        recipients=tuple(
            x.strip() for x in env.get("EVENT_RADAR_EMAIL_TO", "").split(",") if x.strip()
        ),
    )


def _mapping(value, allowed, label):
    if not isinstance(value, dict) or any(key not in allowed for key in value):
        raise ConfigError(f"{label}: expected a mapping with keys {', '.join(allowed)}")
    return value


def _number(value, label, low, high, integer=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (integer and not isinstance(value, int))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ConfigError(
            f"{label}: expected {'integer' if integer else 'number'} in [{low}, {high}]"
        )
    return value


def _terms(value, label, allow_empty=False):
    if (
        not isinstance(value, list)
        or (not value and not allow_empty)
        or any(not isinstance(x, str) or not x.strip() for x in value)
    ):
        raise ConfigError(f"{label}: expected a list of nonempty strings")
    return tuple(dict.fromkeys(x.strip().casefold() for x in value))


def parse_config(data) -> Config:
    keys = (
        "sources",
        "horizon_days",
        "origin",
        "radius_km",
        "unknown_location",
        "exclude_sold_out",
        "exclude_waitlist",
        "requests",
        "digest_length",
        "interests",
        "title_weight",
        "description_weight",
        "negative_keywords",
        "minimum_score",
    )
    data = _mapping(data, keys, "configuration")
    if any(key not in data for key in keys):
        raise ConfigError(
            "configuration: missing required settings; start with config.example.yaml"
        )
    if not isinstance(data["sources"], list) or not data["sources"]:
        raise ConfigError("sources: expected a nonempty list")
    sources = []
    for entry in data["sources"]:
        entry = _mapping(entry, ("slug", "kind"), "sources")
        slug, kind = entry.get("slug"), entry.get("kind")
        if (
            not isinstance(slug, str)
            or not slug
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slug)
        ):
            raise ConfigError("sources.slug: use a lowercase Luma discovery slug")
        if kind not in ("city", "category"):
            raise ConfigError("sources.kind: expected city or category")
        sources.append(Source(slug, kind))
    if len(set(sources)) != len(sources) or len(sources) > 20:
        raise ConfigError("sources: use at most 20 unique sources")
    origin = _mapping(data["origin"], ("latitude", "longitude"), "origin")
    latitude = _number(origin.get("latitude"), "origin.latitude", -90, 90)
    longitude = _number(origin.get("longitude"), "origin.longitude", -180, 180)
    request_keys = tuple(Requests.__dataclass_fields__)
    req = _mapping(data["requests"], request_keys, "requests")
    limits = {
        "timeout_seconds": (0.1, 120, False),
        "retries": (0, 5, True),
        "max_backoff_seconds": (0, 120, False),
        "pages_per_source": (1, 100, True),
        "total_requests": (1, 1000, True),
        "detail_requests": (0, 500, True),
    }
    requests = Requests(
        **{
            key: _number(req.get(key, getattr(Requests(), key)), f"requests.{key}", *limits[key])
            for key in request_keys
        }
    )
    if requests.detail_requests > requests.total_requests:
        raise ConfigError("requests.detail_requests: cannot exceed total_requests")
    if data["unknown_location"] not in ("exclude", "include"):
        raise ConfigError("unknown_location: expected exclude or include")
    for key in ("exclude_sold_out", "exclude_waitlist"):
        if not isinstance(data[key], bool):
            raise ConfigError(f"{key}: expected boolean")
    interests = data["interests"]
    if not isinstance(interests, dict) or not interests:
        raise ConfigError("interests: expected nonempty group mapping")
    if any(not isinstance(k, str) or not k.strip() for k in interests):
        raise ConfigError("interests: group names must be nonempty strings")
    title = _number(data["title_weight"], "title_weight", 0, 1000)
    description = _number(data["description_weight"], "description_weight", 0, 1000)
    if title <= description:
        raise ConfigError("title_weight: must exceed description_weight")
    return Config(
        tuple(sources),
        _number(data["horizon_days"], "horizon_days", 1, 366, True),
        latitude,
        longitude,
        _number(data["radius_km"], "radius_km", 0, 1000),
        data["unknown_location"],
        data["exclude_sold_out"],
        data["exclude_waitlist"],
        requests,
        _number(data["digest_length"], "digest_length", 1, 100, True),
        {k: _terms(v, f"interests.{k}") for k, v in interests.items()},
        title,
        description,
        _terms(data["negative_keywords"], "negative_keywords", allow_empty=True),
        _number(data["minimum_score"], "minimum_score", 0, 1000000),
    )


def load_config(path: str | Path) -> Config:
    try:
        text = Path(path).read_text(encoding="utf-8")
        if len(text) > 100_000:
            raise ConfigError("configuration: exceeds 100 KB limit")
        return parse_config(yaml.safe_load(text))
    except (OSError, yaml.YAMLError, RecursionError) as exc:
        raise ConfigError("configuration: cannot read valid YAML; check file and syntax") from exc
