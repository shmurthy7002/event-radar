import json
from dataclasses import replace
from datetime import timedelta

import pytest

from event_radar.config import Source, load_config
from event_radar.delivery import DeliveryError, FakeProvider
from event_radar.pages import PublicationError
from event_radar.runner import PostSendStateError, reconcile_batch, run_delivery
from event_radar.state import State, StateError, load_state, save_state
from tests.test_pages import BASE
from tests.test_runner import NOW, client, event


class Publisher:
    base_url = BASE

    def __init__(self):
        self.snapshots = []
        self.fail = False

    def publish(self, snapshot):
        self.snapshots.append(snapshot)
        if self.fail:
            raise PublicationError("deployment/verification failure")


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
    return config, path, FakeProvider(), Publisher()


def deliver(context, events=(), *, now=NOW, **kwargs):
    config, path, provider, publisher = context
    return run_delivery(
        config,
        path,
        provider,
        publisher=publisher,
        client=client(config, events),
        now=now,
        send_enabled=True,
        **kwargs,
    )


@pytest.mark.parametrize("count", [0, 7, 30, 45])
def test_selection_cap_acknowledgement_pending_and_suppression(context, count):
    items = [event(f"evt-{i:02}") for i in range(count)]
    publisher, provider = context[3], context[2]
    result = deliver(context, list(reversed(items)))
    assert result.included == min(count, 30)
    assert result.pending == max(count - 30, 0)
    state = load_state(context[1])
    acknowledged = {key for key, entry in state.events.items() if entry.emailed_at is not None}
    assert acknowledged == {e.id for e in items[:30]}
    if not count:
        assert not publisher.snapshots and not provider.calls
        return
    batch = next(iter(state.batches.values()))
    assert set(batch.event_ids) == acknowledged
    assert batch.snapshot == publisher.snapshots[0]
    assert batch.snapshot.url in provider.calls[0][0].text
    assert "Robotics" not in provider.calls[0][0].html + provider.calls[0][0].text
    assert "Public discovery is incomplete" in batch.snapshot.html
    if count > 30:
        assert deliver(context, now=NOW + timedelta(minutes=1)).included == count - 30
        assert publisher.snapshots[0].url != publisher.snapshots[1].url
    assert deliver(context, now=NOW + timedelta(minutes=2)).outcome == "empty"


def test_explicit_empty_summary_publishes_once(context):
    assert deliver(context, nothing_new=True).included == 0
    assert len(context[3].snapshots) == len(context[2].calls) == 1
    assert deliver(context, nothing_new=True).outcome == "empty"
    assert len(context[2].calls) == 1


def test_prepared_intent_precedes_publication_and_failure_never_acknowledges(context):
    _config, path, provider, publisher = context
    publisher.fail = True
    checkpoints = []

    def checkpoint(state):
        checkpoints.append(state)

    with pytest.raises(PublicationError):
        deliver(context, [event()], checkpoint=checkpoint)
    prepared = next(iter(checkpoints[-1].batches.values()))
    assert prepared.snapshot == publisher.snapshots[0]
    assert prepared.outcome == "prepared" and prepared.html and prepared.text
    assert not provider.calls and load_state(path).events["evt-test"].emailed_at is None
    publisher.fail = False
    assert deliver(context, now=NOW + timedelta(minutes=1)).included == 1
    assert publisher.snapshots[0] == publisher.snapshots[1]


@pytest.mark.parametrize("failure", ["rejected", "ambiguous", "interrupted", "post-save"])
def test_publication_then_email_failure_and_frozen_retry(context, monkeypatch, failure):
    from event_radar import runner

    _config, path, provider, publisher = context
    real_send, real_save = provider.send, runner.save_state
    if failure in ("rejected", "ambiguous"):
        provider.failure = DeliveryError(failure, "injected failure")
    elif failure == "interrupted":

        def crash(*args, **kwargs):
            real_send(*args, **kwargs)
            raise SystemExit("crashed after provider acceptance")

        provider.send = crash
    else:

        def save(path, state, **kwargs):
            if any(b.outcome == "accepted" for b in state.batches.values()):
                raise StateError("injected post-send failure")
            return real_save(path, state, **kwargs)

        monkeypatch.setattr(runner, "save_state", save)
    error = (
        DeliveryError
        if failure in ("rejected", "ambiguous")
        else (SystemExit if failure == "interrupted" else PostSendStateError)
    )
    with pytest.raises(error):
        deliver(context, [event()])
    state = load_state(path)
    assert state.events["evt-test"].emailed_at is None
    original = publisher.snapshots[0]
    assert next(iter(state.batches.values())).snapshot == original
    first_call = provider.calls[0] if provider.calls else None
    provider.failure, provider.send = None, real_send
    monkeypatch.setattr(runner, "save_state", real_save)
    assert (
        deliver(
            context, [event(title="Edited robotics title")], now=NOW + timedelta(minutes=1)
        ).included
        == 1
    )
    if failure != "rejected":
        assert publisher.snapshots[1] == original
        assert provider.calls[-1] == first_call
        assert len(provider.accepted) == 1
    else:
        # Definite non-acceptance releases eligibility; a fresh identity is allowed.
        assert publisher.snapshots[1].path != original.path


def test_snapshot_integrity_and_expired_batch_reconciliation(context):
    _, path, provider, publisher = context
    publisher.fail = True
    with pytest.raises(PublicationError):
        deliver(context, [event()])
    original = path.read_text()
    data = json.loads(original)
    next(iter(data["batches"].values()))["snapshot"]["html"] += "tampered"
    path.write_text(json.dumps(data))
    with pytest.raises(StateError):
        deliver(context)
    path.write_text(original)
    publisher.fail = False
    with pytest.raises(DeliveryError, match="outside the idempotency window"):
        deliver(context, now=NOW + timedelta(hours=25))
    assert not provider.calls
    key = next(iter(load_state(path).batches))
    reconcile_batch(path, key, now=NOW + timedelta(hours=25), confirmed_not_accepted=True)
    assert deliver(context, now=NOW + timedelta(hours=25)).included == 1


def test_inline_prepared_batch_stays_recoverable_during_migration(context):
    config, path, provider, publisher = context
    provider.failure = DeliveryError("ambiguous", "injected")
    with pytest.raises(DeliveryError):
        run_delivery(
            config, path, provider, client=client(config, [event()]), now=NOW, send_enabled=True
        )
    original = provider.calls[0]
    assert next(iter(load_state(path).batches.values())).snapshot is None
    provider.failure = None
    assert deliver(context, now=NOW + timedelta(minutes=1)).included == 1
    assert provider.calls[-1] == original
    assert not publisher.snapshots


def test_remote_intent_failure_blocks_publication_and_acceptance_failure_is_recoverable(context):
    _, path, provider, publisher = context

    def fail_intent(state):
        if state.batches:
            raise StateError("remote intent checkpoint failed")

    with pytest.raises(StateError):
        deliver(context, [event()], checkpoint=fail_intent)
    assert not publisher.snapshots and not provider.calls
    remote_states = []

    def fail_acknowledgement(state):
        if any(b.outcome == "accepted" for b in state.batches.values()):
            raise StateError("remote acknowledgement failed")
        remote_states.append(state)

    with pytest.raises(PostSendStateError):
        deliver(context, checkpoint=fail_acknowledgement)
    assert load_state(path).events["evt-test"].emailed_at is not None
    # Simulate the next ephemeral runner retrieving the last successful checkpoint.
    save_state(path, remote_states[-1])
    assert deliver(context, now=NOW + timedelta(minutes=1)).included == 1
    assert len(provider.accepted) == 1
    assert publisher.snapshots[0] == publisher.snapshots[1]


def test_total_collection_failure_does_not_publish_empty_summary(context):
    from event_radar.collectors.luma import TotalCollectionFailure

    config, path, provider, publisher = context
    with pytest.raises(TotalCollectionFailure):
        run_delivery(
            config,
            path,
            provider,
            publisher=publisher,
            client=client(config, fail=True),
            now=NOW,
            send_enabled=True,
            nothing_new=True,
        )
    assert not publisher.snapshots and not provider.calls
