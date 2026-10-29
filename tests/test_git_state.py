import subprocess
from dataclasses import replace

import pytest

from event_radar.config import Source, load_config
from event_radar.delivery import FakeProvider
from event_radar.git_state import GitStateError, GitStateStore
from event_radar.runner import PostSendStateError, run_delivery
from event_radar.state import State, observe, save_state
from tests.test_runner import NOW, client, event


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(path)], capture_output=True, check=True)
    return path


def test_explicit_bootstrap_and_fresh_checkout(remote, tmp_path):
    store = GitStateStore(tmp_path / "cache", str(remote))
    with pytest.raises(GitStateError, match="missing"):
        store.fetch()
    store.initialize()
    with pytest.raises(GitStateError, match="already exists"):
        store.initialize()
    state = observe(store.fetch(), [event()], now=NOW)
    store.publish(state)
    fresh = GitStateStore(tmp_path / "fresh-cache", str(remote))
    assert fresh.fetch() == state
    assert fresh.publish(state) == fresh.revision


def test_concurrent_snapshots_conflict_without_overwriting(remote, tmp_path):
    first = GitStateStore(tmp_path / "first", str(remote))
    first.initialize()
    a, b = first.fetch(), None
    second = GitStateStore(tmp_path / "second", str(remote))
    b = second.fetch()
    first.publish(observe(a, [event("evt-a")], now=NOW))
    with pytest.raises(GitStateError, match="stale/conflicting"):
        second.publish(observe(b, [event("evt-b")], now=NOW))
    assert set(second.fetch().events) == {"evt-a"}


def test_fetch_errors_and_untrusted_cache(tmp_path):
    store = GitStateStore(tmp_path / "cache", str(tmp_path / "missing.git"))
    with pytest.raises(GitStateError):
        store.fetch()
    with pytest.raises(GitStateError):
        store.publish(State())
    with pytest.raises(GitStateError):
        GitStateStore(tmp_path, str(tmp_path / "missing.git"))


def test_racing_push_is_rejected_without_force(remote, tmp_path, monkeypatch):
    first = GitStateStore(tmp_path / "first", str(remote))
    first.initialize()
    first.fetch()
    second = GitStateStore(tmp_path / "second", str(remote))
    state = second.fetch()
    original = first._run

    def race(args, **kwargs):
        if args[0] == "push":
            second.publish(observe(state, [event("evt-winner")], now=NOW))
        return original(args, **kwargs)

    monkeypatch.setattr(first, "_run", race)
    with pytest.raises(GitStateError):
        first.publish(observe(State(), [event("evt-loser")], now=NOW))
    assert set(second.fetch().events) == {"evt-winner"}


def test_unexpected_branch_deletion_is_not_reinitialized(remote, tmp_path):
    store = GitStateStore(tmp_path / "cache", str(remote))
    store.initialize()
    state = store.fetch()
    subprocess.run(
        ["git", "--git-dir", str(remote), "update-ref", "-d", f"refs/heads/{store.branch}"],
        capture_output=True,
        check=True,
    )
    with pytest.raises(GitStateError):
        store.publish(state)
    with pytest.raises(GitStateError, match="missing"):
        store.fetch()


def test_corrupt_remote_state_rejected(remote, tmp_path):
    store = GitStateStore(tmp_path / "cache", str(remote))
    store.initialize()
    blob = store._run(["hash-object", "-w", "--stdin"], data=b"corrupt").stdout.decode().strip()
    tree = (
        store._run(["mktree"], data=f"100644 blob {blob}\tstate.json\n".encode())
        .stdout.decode()
        .strip()
    )
    commit = (
        store._run(["commit-tree", tree, "-p", store.revision], data=b"corruption fixture\n")
        .stdout.decode()
        .strip()
    )
    store._run(["push", "origin", f"{commit}:refs/heads/{store.branch}"])
    with pytest.raises(GitStateError, match="corrupt"):
        store.fetch()


def test_remote_failure_after_acceptance_retains_prepared_intent(remote, tmp_path):
    store = GitStateStore(tmp_path / "cache", str(remote))
    store.initialize()
    state = store.fetch()
    local = tmp_path / "local.json"
    save_state(local, state, initialize=True)
    config = load_config("config.example.yaml")
    config = replace(
        config,
        sources=(Source("sf", "city"),),
        requests=replace(config.requests, detail_requests=0),
    )
    provider = FakeProvider()

    def checkpoint(state):
        if any(b.outcome == "accepted" for b in state.batches.values()):
            raise GitStateError("injected remote push failure")
        store.publish(state)

    with pytest.raises(PostSendStateError):
        run_delivery(
            config,
            local,
            provider,
            client=client(config, [event()]),
            now=NOW,
            send_enabled=True,
            checkpoint=checkpoint,
        )
    fresh = GitStateStore(tmp_path / "fresh", str(remote))
    recovered = fresh.fetch()
    assert next(iter(recovered.batches.values())).outcome == "prepared"
    save_state(local, recovered)
    result = run_delivery(
        config,
        local,
        provider,
        client=client(config),
        now=NOW,
        send_enabled=True,
        checkpoint=fresh.publish,
    )
    assert result.included == 1 and len(provider.accepted) == 1
    assert fresh.fetch().events["evt-test"].emailed_at == NOW
