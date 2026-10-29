import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from event_radar.__main__ import main
from event_radar.collectors.luma import LumaClient, Response, discovery_page_event_ids
from event_radar.config import ConfigError, Source, load_config
from event_radar.smoke import run_smoke, smoke_config

NOW = datetime(2026, 10, 28, tzinfo=UTC)
FIXTURES = Path("tests/fixtures")


def transport(url, timeout):
    parsed = urlsplit(url)
    if parsed.hostname == "luma.com":
        page = (
            (FIXTURES / "city.html")
            .read_text()
            .replace(
                '"events":[]',
                '"events":[{"event":{"api_id":"evt-one"}},{"event":{"api_id":"evt-page-only"}}]',
            )
        )
        return Response(200, {}, page.encode())
    if parsed.path == "/event/get":
        event_id = parse_qs(parsed.query)["event_api_id"][0]
        return Response(
            200,
            {},
            json.dumps(
                {
                    "event": {"api_id": event_id},
                    "description_mirror": {"type": "doc", "content": []},
                }
            ).encode(),
        )
    return Response(200, {}, (FIXTURES / "feed-last.json").read_bytes())


def test_smoke_limits_comparison_and_no_event_payload(tmp_path):
    config = smoke_config(
        replace(load_config("config.example.yaml"), sources=(Source("sf", "city"),))
    )
    summary = run_smoke(config, client=LumaClient(config.requests, transport=transport), now=NOW)
    assert summary["healthy"]
    assert summary["sources"][0]["page_comparison"] == {
        "page_ids": 2,
        "feed_ids": 2,
        "overlap": 1,
        "page_only": 1,
        "feed_only": 1,
    }
    assert summary["descriptions"]["empty"] == 2
    assert summary["requests"] == 4
    serialized = json.dumps(summary)
    assert "Example robotics" not in serialized and "evt-page-only" not in serialized
    assert "before" not in summary  # No relevance filtering is applied.


def test_cli_smoke_and_unhealthy_status(tmp_path, capsys):
    config_file = tmp_path / "config.yaml"
    import yaml

    data = yaml.safe_load(Path("config.example.yaml").read_text())
    data["sources"] = [{"slug": "sf", "kind": "city"}]
    config_file.write_text(yaml.safe_dump(data))
    factory = lambda settings: LumaClient(settings, transport=transport)
    assert (
        main(
            ["--smoke-check", "--config", str(config_file), "--output", str(tmp_path)],
            client_factory=factory,
            now=NOW,
        )
        == 0
    )
    assert (tmp_path / "smoke.json").exists()
    assert not (tmp_path / "digest.html").exists()

    def broken(settings):
        def fetch(url, timeout):
            if "/event/get" in url:
                return Response(404, {}, b"")
            return transport(url, timeout)

        return LumaClient(settings, transport=fetch)

    assert (
        main(
            ["--smoke-check", "--config", str(config_file), "--output", str(tmp_path)],
            client_factory=broken,
            now=NOW,
        )
        == 5
    )
    assert '"healthy": false' in capsys.readouterr().out


def test_bounded_settings_and_changed_page_schema():
    config = smoke_config(load_config("config.example.yaml"))
    assert config.requests.total_requests == 15
    assert config.requests.pages_per_source == 2
    assert config.requests.detail_requests == 3
    with pytest.raises(ConfigError):
        smoke_config(replace(config, sources=(Source("ai", "category"),)))
    from event_radar.collectors.luma import CollectionError

    with pytest.raises(CollectionError):
        discovery_page_event_ids('<script id="__NEXT_DATA__">{}</script>')
