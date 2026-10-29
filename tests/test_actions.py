import json
from pathlib import Path

import pytest
import yaml

from event_radar.actions import bounded_settings, main
from event_radar.git_state import GitStateError
from event_radar.state import State


@pytest.fixture(autouse=True)
def clear_gates(monkeypatch):
    for key in ("EVENT_RADAR_DELIVERY_ENABLED", "EVENT_RADAR_ROLLOUT_VERIFIED", "GITHUB_ACTIONS"):
        monkeypatch.delenv(key, raising=False)


class Store:
    def __init__(self, *args, **kwargs):
        self.fetched = False

    def initialize(self):
        self.initialized = True

    def fetch(self):
        self.fetched = True
        return State()

    def publish(self, state):
        self.published = state


def arguments(tmp_path, mode):
    return [
        mode,
        "--directory",
        str(tmp_path / "work"),
        "--reports",
        str(tmp_path / "reports"),
        "--remote",
        "test-remote",
    ]


def test_preview_fetches_state_and_never_publishes(tmp_path):
    store = Store()

    def invoke(args, **kwargs):
        assert store.fetched and kwargs["checkpoint"] is None
        assert "--pending-preview" in args and "--diagnostics-only" in args
        assert (tmp_path / "work" / "state.json").is_file()
        return 0

    assert (
        main(
            arguments(tmp_path, "preview"),
            store_factory=lambda *_args, **_kwargs: store,
            invoke=invoke,
        )
        == 0
    )


@pytest.mark.parametrize("scheduled", [False, True])
def test_disabled_send_never_accesses_store_or_provider(tmp_path, scheduled):
    args = arguments(tmp_path, "send") + (
        ["--scheduled"] if scheduled else ["--confirmation", "SEND_CONFIGURED_RECIPIENT"]
    )
    assert (
        main(
            args,
            store_factory=lambda *_args, **_kwargs: pytest.fail("store accessed"),
            invoke=lambda *_args, **_kwargs: pytest.fail("provider accessed"),
        )
        == 7
    )
    assert (
        json.loads((tmp_path / "reports" / "workflow.json").read_text())["outcome"]
        == "delivery_gated"
    )


def test_send_requires_verified_schedule_and_wires_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_RADAR_DELIVERY_ENABLED", "true")
    args = arguments(tmp_path, "send") + ["--scheduled"]
    assert main(args) == 7
    monkeypatch.setenv("EVENT_RADAR_ROLLOUT_VERIFIED", "true")
    store = Store()

    def invoke(args, **kwargs):
        assert "--send" in args and store.fetched
        kwargs["checkpoint"](State())
        return 0

    assert main(args, store_factory=lambda *_args, **_kwargs: store, invoke=invoke) == 0
    assert store.published == State()


def test_bootstrap_explicit_and_state_failure_blocks_invoke(tmp_path):
    args = arguments(tmp_path, "initialize")
    assert main(args) == 2
    assert main(args + ["--confirmation", "INITIALIZE_EMPTY_STATE"], store_factory=Store) == 0

    class Broken(Store):
        def fetch(self):
            raise GitStateError("bad state")

    assert (
        main(
            arguments(tmp_path, "preview"),
            store_factory=Broken,
            invoke=lambda *_args, **_kwargs: pytest.fail("invoked after failed state"),
        )
        == 6
    )


def test_smoke_samples_both_origins_without_state(tmp_path):
    origins = []

    def invoke(args):
        config = Path(args[args.index("--config") + 1])
        origins.append(yaml.safe_load(config.read_text())["origin"])
        return 0

    assert (
        main(
            arguments(tmp_path, "smoke"),
            store_factory=lambda *_args, **_kwargs: pytest.fail("smoke touched state"),
            invoke=invoke,
        )
        == 0
    )
    assert [origin["latitude"] for origin in origins] == [37.8715, 37.7749]
    assert bounded_settings("config.example.yaml")["requests"]["total_requests"] == 40
    assert bounded_settings("config.example.yaml")["requests"]["detail_requests"] == 20


def test_workflow_has_no_active_schedule_and_preserves_active_run():
    workflow = yaml.load(
        Path(".github/workflows/event-radar.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["write-state"]["permissions"] == {"contents": "write"}
    assert workflow["jobs"]["write-state"]["environment"] == "event-radar-delivery"
