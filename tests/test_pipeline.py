import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from event_radar.__main__ import main
from event_radar.collectors.luma import LumaClient, Response
from event_radar.config import Source, load_config
from event_radar.models import DescriptionStatus
from event_radar.pipeline import build_preview, write_preview

NOW = datetime(2026, 10, 28, tzinfo=UTC)
FIXTURES = Path("tests/fixtures")


def transport(url, timeout):
    parsed = urlsplit(url)
    if parsed.hostname == "luma.com":
        body = (FIXTURES / "city.html").read_bytes()
    elif parsed.path == "/event/get":
        event_id = parse_qs(parsed.query)["event_api_id"][0]
        payload = json.loads((FIXTURES / "detail.json").read_text())
        payload["event"]["api_id"] = event_id
        body = json.dumps(payload).encode()
    else:
        payload = json.loads((FIXTURES / "feed-first.json").read_text())
        payload["has_more"] = False
        payload["entries"][0]["event"].update(start_at="2026-10-29T10:00:00-07:00", end_at=None)
        body = json.dumps(payload).encode()
    return Response(200, {}, body)


def factory(settings):
    return LumaClient(settings, transport=transport, sleep=lambda _: None)


def test_full_preview_pipeline_offline(tmp_path):
    config = replace(load_config("config.example.yaml"), sources=(Source("sf", "city"),))
    preview = build_preview(config, client=factory(config.requests), now=NOW)
    write_preview(preview, tmp_path)
    data = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert data["selected_ids"] == ["evt-one"]
    assert data["matches"][0]["event"]["description"] == "Build robots\nLearn RL"
    assert data["coverage"]["filter_exclusions"] == {"unknown_location": 1}
    assert data["coverage"]["descriptions"]["content"] == 1
    assert data["coverage"]["requests"] == 3
    assert "skipped malformed" in (tmp_path / "digest.txt").read_text(encoding="utf-8")
    assert "PDT" in (tmp_path / "digest.html").read_text(encoding="utf-8")
    assert {p.name for p in tmp_path.iterdir()} == {"results.json", "digest.html", "digest.txt"}


@pytest.mark.parametrize("description", ["Build robots", ""])
def test_pending_preview_reuses_completed_descriptions(description):
    config = replace(load_config("config.example.yaml"), sources=(Source("sf", "city"),))
    initial = build_preview(config, client=factory(config.requests), now=NOW)
    cached = replace(
        initial.candidate_events[0],
        title="Old title",
        description=description,
        description_status=DescriptionStatus.CONTENT if description else DescriptionStatus.EMPTY,
    )
    preview = build_preview(
        config, client=factory(config.requests), now=NOW, previous_events=[cached]
    )
    current = preview.candidate_events[0]
    assert current.title != cached.title
    assert current.description == description
    assert current.description_status == cached.description_status
    assert preview.summary["descriptions"]["requests"] == 0
    assert preview.summary["requests"] == 2
    assert not preview.digest.data["matches"][0]["title_only"]


def test_cli_success_default_preview_and_overrides(tmp_path, capsys, monkeypatch):
    # Sensitive provider settings must not affect read-only operation or output.
    monkeypatch.setenv("EVENT_RADAR_EMAIL_API_KEY", "never-log-secret")
    assert (
        main(
            ["--config", "config.example.yaml", "--output", str(tmp_path)],
            client_factory=factory,
            now=NOW,
        )
        == 0
    )
    console = capsys.readouterr().out
    assert "PARTIAL" in console and "Warning:" in console
    assert "never-log-secret" not in console
    assert (tmp_path / "digest.html").exists()
    assert not (tmp_path / "state.json").exists()


def test_cli_failure_exit_codes(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.yaml")], client_factory=factory, now=NOW) == 2

    def failed(settings):
        return LumaClient(replace(settings, retries=0), transport=lambda *_: Response(503, {}, b""))

    assert (
        main(
            ["--config", "config.example.yaml", "--output", str(tmp_path)],
            client_factory=failed,
            now=NOW,
        )
        == 3
    )
    assert not (tmp_path / "digest.html").exists()
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    assert (
        main(
            ["--config", "config.example.yaml", "--output", str(blocked)],
            client_factory=factory,
            now=NOW,
        )
        == 4
    )
    errors = capsys.readouterr().err
    assert (
        "Configuration error" in errors
        and "Collection failed" in errors
        and "Output error" in errors
    )


def test_truncation_warnings_in_artifacts_and_console(tmp_path, capsys):
    config = load_config("config.example.yaml")

    def truncated(settings):
        def fetch(url, timeout):
            value = transport(url, timeout)
            if "/discover/" in url:
                data = json.loads(value.body)
                data.update(has_more=True, next_cursor="repeated")
                return Response(200, {}, json.dumps(data).encode())
            return value

        return LumaClient(config.requests, transport=fetch)

    assert (
        main(
            ["--config", "config.example.yaml", "--output", str(tmp_path)],
            client_factory=truncated,
            now=NOW,
        )
        == 0
    )
    assert "repeated pagination cursor" in capsys.readouterr().out
    assert "repeated pagination cursor" in (tmp_path / "digest.html").read_text(encoding="utf-8")
