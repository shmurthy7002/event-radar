import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from event_radar.__main__ import main
from event_radar.collectors.luma import LumaClient, Response, TotalCollectionFailure
from event_radar.config import Source, load_config
from event_radar.delivery import DeliveryError, FakeProvider
from event_radar.models import Event, Observation
from event_radar.runner import PostSendStateError, run_delivery
from event_radar.state import State, load_state, save_state

NOW = datetime(2026, 10, 29, tzinfo=UTC)


def event(event_id="evt-test", title="Robotics", start=None):
    return Event(
        event_id,
        "https://luma.com/" + event_id,
        title,
        start or NOW + timedelta(days=2),
        (Observation("sf", NOW),),
        latitude=37.8715,
        longitude=-122.273,
    )


def client(config, events=(), *, fail=False):
    def transport(url, timeout):
        if fail:
            return Response(404, {}, b"")
        if "luma.com/sf" in url:
            body = {
                "props": {
                    "pageProps": {
                        "initialData": {
                            "kind": "discover-place",
                            "data": {"place": {"api_id": "discplace-test", "slug": "sf"}},
                        }
                    }
                }
            }
        else:
            body = {
                "entries": [
                    {
                        "event": {
                            "api_id": e.id,
                            "name": e.title,
                            "url": e.url,
                            "start_at": e.start.isoformat(),
                            "coordinate": {"latitude": e.latitude, "longitude": e.longitude},
                        }
                    }
                    for e in events
                ],
                "has_more": False,
            }
        encoded = json.dumps(body)
        if "luma.com/sf" in url:
            encoded = '<script id="__NEXT_DATA__">' + encoded + "</script>"
        return Response(200, {}, encoded.encode())

    return LumaClient(config.requests, transport=transport)


@pytest.fixture
def setup(tmp_path):
    config = load_config("config.example.yaml")
    config = replace(
        config,
        sources=(Source("sf", "city"),),
        requests=replace(config.requests, detail_requests=0),
    )
    path = tmp_path / "state.json"
    save_state(path, State(), initialize=True)
    return config, path, FakeProvider()


def run(setup, events=(), **kwargs):
    config, path, provider = setup
    return run_delivery(
        config, path, provider, client=client(config, events), now=NOW, send_enabled=True, **kwargs
    )


def test_acceptance_marks_only_included_and_omissions_stay_pending(setup):
    config, path, provider = setup
    setup = (replace(config, digest_length=1), path, provider)
    result = run(setup, [event("evt-a"), event("evt-b")])
    assert result.included == 1 and result.pending == 1
    state = load_state(path)
    assert state.events["evt-a"].emailed_at == NOW
    assert state.events["evt-b"].emailed_at is None
    assert next(iter(state.batches.values())).provider_message_id == "fake-1"
    assert run(setup).included == 1
    assert run(setup).outcome == "empty"
    assert len(provider.calls) == 2


def test_provider_rejection_keeps_pending(setup):
    _config, path, provider = setup
    provider.failure = DeliveryError("rejected", "provider rejected")
    with pytest.raises(DeliveryError):
        run(setup, [event()])
    state = load_state(path)
    assert state.events["evt-test"].emailed_at is None
    assert next(iter(state.batches.values())).outcome == "rejected"
    provider.failure = None
    assert run(setup).included == 1


def test_ambiguous_retries_frozen_payload_and_key(setup):
    _config, path, provider = setup
    provider.failure = DeliveryError("ambiguous", "unknown acceptance")
    with pytest.raises(DeliveryError):
        run(setup, [event()])
    original = provider.calls[0]
    provider.failure = None
    assert run(setup).included == 1
    assert provider.calls[1] == original
    assert "example.test" not in path.read_text()


def test_acceptance_before_failed_save_uses_same_idempotency_key(setup, monkeypatch):
    from event_radar import runner

    real_save = runner.save_state

    def save(path, state, **kwargs):
        if any(b.outcome == "accepted" for b in state.batches.values()):
            from event_radar.state import StateError

            raise StateError("disk failed")
        real_save(path, state, **kwargs)

    monkeypatch.setattr(runner, "save_state", save)
    with pytest.raises(PostSendStateError):
        run(setup, [event()])
    assert len(setup[2].accepted) == 1
    monkeypatch.setattr(runner, "save_state", real_save)
    assert run(setup).included == 1
    assert len(setup[2].accepted) == 1


def test_empty_default_optional_summary_and_collection_failure(setup):
    config, path, provider = setup
    assert run(setup).outcome == "empty"
    assert not provider.calls
    assert run(setup, nothing_new=True).outcome == "accepted"
    assert run(setup, nothing_new=True).outcome == "empty"
    old = path.read_bytes()
    with pytest.raises(TotalCollectionFailure):
        run_delivery(
            config,
            path,
            provider,
            client=client(config, fail=True),
            now=NOW,
            send_enabled=True,
            nothing_new=True,
        )
    assert path.read_bytes() == old and len(provider.calls) == 1


def test_explicit_send_required_and_cli_safe_default(setup, tmp_path, monkeypatch, capsys):
    config, path, provider = setup
    with pytest.raises(DeliveryError, match="explicit"):
        run_delivery(config, path, provider, now=NOW)
    monkeypatch.setenv("EVENT_RADAR_EMAIL_API_KEY", "fake-secret")
    monkeypatch.setenv("EVENT_RADAR_EMAIL_FROM", "sender@example.test")
    monkeypatch.setenv("EVENT_RADAR_EMAIL_TO", "recipient@example.test")
    factory = lambda _: client(config, [event()])
    args = [
        "--config",
        "config.example.yaml",
        "--state",
        str(path),
        "--output",
        str(tmp_path / "preview"),
    ]
    initial = path.read_bytes()
    assert (
        main(
            args,
            client_factory=factory,
            provider_factory=lambda _: pytest.fail("provider in preview"),
            now=NOW,
        )
        == 0
    )
    assert path.read_bytes() == initial
    assert (
        main(
            args + ["--send"], client_factory=factory, provider_factory=lambda _: provider, now=NOW
        )
        == 0
    )
    assert "fake-secret" not in capsys.readouterr().out
