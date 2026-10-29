import json
from dataclasses import replace
from datetime import timedelta

import pytest

from event_radar.__main__ import main
from event_radar.config import Source, load_config
from event_radar.delivery import DeliveryError, FakeProvider
from event_radar.runner import reconcile_batch, run_delivery
from event_radar.state import State, StateError, load_state, save_state, state_lock
from tests.test_runner import NOW, client, event


@pytest.fixture
def context(tmp_path):
    config = load_config("config.example.yaml")
    config = replace(
        config,
        sources=(Source("sf", "city"),),
        requests=replace(config.requests, detail_requests=0),
    )
    path = tmp_path / "state.json"
    save_state(path, State(), initialize=True)
    return config, path, FakeProvider()


def deliver(context, events=(), *, now=NOW):
    config, path, provider = context
    return run_delivery(
        config, path, provider, client=client(config, events), now=now, send_enabled=True
    )


def test_seen_event_becomes_relevant_and_cached_metadata_rechecked(context):
    config, path, provider = context
    assert deliver(context, [event(title="Music gathering")]).outcome == "empty"
    assert load_state(path).events["evt-test"].emailed_at is None
    config = replace(config, interests={"music": ("music",)})
    assert deliver((config, path, provider), now=NOW + timedelta(hours=1)).included == 1
    assert (
        deliver(
            (config, path, provider),
            [event(title="Edited music gathering")],
            now=NOW + timedelta(hours=2),
        ).outcome
        == "empty"
    )
    state = load_state(path)
    assert state.events["evt-test"].event.title == "Edited music gathering"
    assert state.events["evt-test"].first_seen_at == NOW
    assert len(provider.accepted) == 1


def test_rejected_event_expiring_before_next_run_is_not_sent(context):
    _config, path, provider = context
    provider.failure = DeliveryError("rejected", "rejected")
    with pytest.raises(DeliveryError):
        deliver(context, [event(start=NOW + timedelta(hours=1))])
    provider.failure = None
    assert deliver(context, now=NOW + timedelta(hours=2)).outcome == "empty"
    assert len(provider.calls) == 1
    assert load_state(path).events["evt-test"].emailed_at is None


@pytest.mark.parametrize("point", ["before_send", "after_acceptance"])
def test_crash_before_or_after_provider_replays_one_frozen_request(context, point):
    _config, path, provider = context
    original_send = provider.send
    calls = []

    def crashing_send(message, *, idempotency_key):
        calls.append((message, idempotency_key))
        if point == "after_acceptance":
            original_send(message, idempotency_key=idempotency_key)
        raise SystemExit("simulated process interruption")

    provider.send = crashing_send
    with pytest.raises(SystemExit):
        deliver(context, [event()])
    state = load_state(path)
    assert state.events["evt-test"].emailed_at is None
    assert next(iter(state.batches.values())).outcome == "prepared"
    provider.send = original_send
    assert deliver(context, now=NOW + timedelta(hours=1)).included == 1
    assert provider.calls[-1] == calls[0]
    assert len(provider.accepted) == 1
    assert deliver(context, now=NOW + timedelta(hours=2)).outcome == "empty"


def test_state_failure_before_send_blocks_contact_and_preserves_prior_file(context, monkeypatch):
    import event_radar.state as store

    _config, path, provider = context
    old = path.read_bytes()

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(store.os, "fsync", fail)
    with pytest.raises(StateError):
        deliver(context, [event()])
    assert not provider.calls and path.read_bytes() == old
    assert not list(path.parent.glob("*.tmp"))


def test_prepared_intent_save_failure_blocks_provider(context, monkeypatch):
    from event_radar import runner

    real = runner.save_state

    def save(path, state, **kwargs):
        if state.batches:
            raise StateError("prepared save failed")
        real(path, state, **kwargs)

    monkeypatch.setattr(runner, "save_state", save)
    with pytest.raises(StateError):
        deliver(context, [event()])
    assert not context[2].calls
    assert load_state(context[1]).events["evt-test"].emailed_at is None


@pytest.mark.parametrize("hours", [24, 25])
def test_expired_provider_idempotency_window_requires_reconciliation(context, hours):
    _config, path, provider = context
    provider.failure = DeliveryError("ambiguous", "unknown")
    with pytest.raises(DeliveryError):
        deliver(context, [event()])
    provider.failure = None
    provider.accepted.clear()  # Provider no longer retains its idempotency memory.
    with pytest.raises(DeliveryError, match="outside the idempotency window"):
        deliver(context, now=NOW + timedelta(hours=hours))
    assert len(provider.calls) == 1 and not provider.accepted
    key = next(iter(load_state(path).batches))
    reconcile_batch(
        path, key, now=NOW + timedelta(hours=hours), accepted_message_id="confirmed-provider-id"
    )
    assert deliver(context, now=NOW + timedelta(hours=hours, minutes=1)).outcome == "empty"
    assert len(provider.calls) == 1


@pytest.mark.parametrize("change", ["scope", "expiry", "relevance"])
def test_ambiguous_retry_guards_prevent_changed_or_expired_payload(context, change):
    config, path, provider = context
    provider.failure = DeliveryError("ambiguous", "unknown")
    with pytest.raises(DeliveryError):
        deliver(context, [event(start=NOW + timedelta(hours=1))])
    provider.failure = None
    now = NOW + timedelta(minutes=1)
    if change == "scope":
        provider.scope = "a" * 64
    elif change == "expiry":
        now = NOW + timedelta(hours=2)
    else:
        config = replace(config, negative_keywords=("robotics",))
    with pytest.raises(DeliveryError, match="reconcile"):
        deliver((config, path, provider), now=now)
    assert len(provider.calls) == 1


def test_confirmed_not_accepted_releases_pending_for_fresh_batch(context):
    _config, path, provider = context
    provider.failure = DeliveryError("ambiguous", "unknown")
    with pytest.raises(DeliveryError):
        deliver(context, [event()])
    old = next(iter(load_state(path).batches))
    reconcile_batch(path, old, now=NOW + timedelta(hours=25), confirmed_not_accepted=True)
    provider.failure = None
    assert deliver(context, now=NOW + timedelta(hours=25)).included == 1
    assert provider.calls[-1][1] != old


def test_lock_contention_missing_state_and_corruption_block_before_provider(context):
    _config, path, provider = context
    with state_lock(path), pytest.raises(StateError, match="locked"):
        deliver(context)
    path.write_text("invalid json")
    with pytest.raises(StateError):
        deliver(context)
    path.unlink()
    with pytest.raises(StateError):
        deliver(context)
    assert not provider.calls


def test_tampered_pending_payload_is_not_retried(context):
    _config, path, provider = context
    provider.failure = DeliveryError("ambiguous", "unknown")
    with pytest.raises(DeliveryError):
        deliver(context, [event()])
    data = json.loads(path.read_text())
    next(iter(data["batches"].values()))["html"] += "edited"
    path.write_text(json.dumps(data))
    with pytest.raises(StateError):
        deliver(context)
    assert len(provider.calls) == 1


def test_reconciliation_cli_and_pending_preview_are_offline_or_read_only(context, tmp_path):
    config, path, provider = context
    provider.failure = DeliveryError("ambiguous", "unknown")
    with pytest.raises(DeliveryError):
        deliver(context, [event()])
    key = next(iter(load_state(path).batches))
    assert (
        main(
            ["--state", str(path), "--batch-id", key, "--reconcile-accepted", "provider-confirmed"],
            now=NOW,
        )
        == 0
    )
    before = path.read_bytes()
    assert (
        main(
            [
                "--pending-preview",
                "--state",
                str(path),
                "--config",
                "config.example.yaml",
                "--output",
                str(tmp_path / "preview"),
            ],
            client_factory=lambda _: client(config),
            now=NOW,
        )
        == 0
    )
    assert json.loads((tmp_path / "preview" / "results.json").read_text())["selected_ids"] == []
    assert path.read_bytes() == before
    with pytest.raises(StateError):
        reconcile_batch(path, key, now=NOW, confirmed_not_accepted=True)


def test_acknowledged_state_suppresses_after_provider_key_expiry(context):
    assert deliver(context, [event()]).included == 1
    context[2].accepted.clear()
    assert deliver(context, now=NOW + timedelta(hours=25)).outcome == "empty"
    assert len(context[2].calls) == 1


def test_crash_after_successful_state_commit_does_not_resend(context, monkeypatch):
    from event_radar import runner

    real = runner.save_state

    def save(path, state, **kwargs):
        real(path, state, **kwargs)
        if any(b.outcome == "accepted" for b in state.batches.values()):
            raise SystemExit("crash after commit")

    monkeypatch.setattr(runner, "save_state", save)
    with pytest.raises(SystemExit):
        deliver(context, [event()])
    monkeypatch.setattr(runner, "save_state", real)
    assert deliver(context).outcome == "empty"
    assert len(context[2].calls) == 1


def test_inconsistent_accepted_history_is_rejected(context):
    assert deliver(context, [event()]).included == 1
    path = context[1]
    data = json.loads(path.read_text())
    data["events"]["evt-test"]["emailed_at"] = None
    path.write_text(json.dumps(data))
    with pytest.raises(StateError):
        deliver(context)
    assert len(context[2].calls) == 1


@pytest.mark.parametrize("outcome,exit_code", [("rejected", 7), ("ambiguous", 7), ("post_save", 8)])
def test_cli_reports_delivery_failures_without_secrets(
    context, monkeypatch, capsys, outcome, exit_code
):
    from event_radar import runner

    config, path, provider = context
    monkeypatch.setenv("EVENT_RADAR_EMAIL_API_KEY", "private-test-key")
    monkeypatch.setenv("EVENT_RADAR_EMAIL_FROM", "sender@example.test")
    monkeypatch.setenv("EVENT_RADAR_EMAIL_TO", "recipient@example.test")
    if outcome == "post_save":
        real = runner.save_state

        def save(path, state, **kwargs):
            if any(b.outcome == "accepted" for b in state.batches.values()):
                raise StateError("injected persistence failure")
            real(path, state, **kwargs)

        monkeypatch.setattr(runner, "save_state", save)
    else:
        provider.failure = DeliveryError(outcome, "safe provider diagnostic")
    assert (
        main(
            ["--send", "--state", str(path), "--config", "config.example.yaml"],
            client_factory=lambda _: client(config, [event()]),
            provider_factory=lambda _: provider,
            now=NOW,
        )
        == exit_code
    )
    console = capsys.readouterr()
    assert "private-test-key" not in console.err + console.out
    assert "example.test" not in console.err + console.out
    assert load_state(path).events["evt-test"].emailed_at is None
