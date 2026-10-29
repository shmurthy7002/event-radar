import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from event_radar.__main__ import main
from event_radar.models import DescriptionStatus, Event, Observation
from event_radar.state import (
    State,
    StateError,
    load_state,
    observe,
    parse_state,
    save_state,
    state_data,
    unemailed_events,
)

NOW = datetime(2026, 10, 29, tzinfo=UTC)


def event():
    return Event(
        "evt-test",
        "https://luma.com/test",
        "Robotics",
        NOW + timedelta(days=2),
        (Observation("sf", NOW),),
    )


def test_initialize_roundtrip_and_observe_changes(tmp_path):
    path = tmp_path / "state.json"
    with pytest.raises(StateError):
        load_state(path)
    save_state(path, State(), initialize=True)
    with pytest.raises(StateError):
        save_state(path, State(), initialize=True)
    state = observe(load_state(path), [event()], now=NOW)
    save_state(path, state)
    later = NOW + timedelta(days=1)
    state = observe(load_state(path), [replace(event(), title="Edited AI safety")], now=later)
    save_state(path, state)
    entry = load_state(path).events[event().id]
    assert entry.first_seen_at == NOW and entry.last_seen_at == later
    assert entry.event.title == "Edited AI safety" and entry.emailed_at is None
    assert unemailed_events(load_state(path), now=later) == [entry.event]
    assert unemailed_events(state, now=NOW + timedelta(days=3)) == []


@pytest.mark.parametrize("cached_text", ["Sales networking", ""])
@pytest.mark.parametrize(
    "fresh_text,fresh_status",
    [
        (None, DescriptionStatus.NOT_FETCHED),
        (None, DescriptionStatus.FAILED),
        ("Updated description", DescriptionStatus.CONTENT),
        ("", DescriptionStatus.EMPTY),
    ],
)
def test_observations_preserve_completed_descriptions(
    tmp_path, cached_text, fresh_text, fresh_status
):
    def described(text):
        status = (
            DescriptionStatus.NOT_FETCHED
            if text is None
            else DescriptionStatus.CONTENT
            if text
            else DescriptionStatus.EMPTY
        )
        return replace(event(), description=text, description_status=status)

    path = tmp_path / "state.json"
    save_state(path, observe(State(), [described(cached_text)], now=NOW), initialize=True)
    fresh = replace(
        described(fresh_text),
        description_status=fresh_status,
        title="Edited title",
        latitude=37.8,
        longitude=-122.3,
    )
    state = observe(load_state(path), [fresh], now=NOW + timedelta(hours=1))
    save_state(path, state)
    entry = load_state(path).events["evt-test"]
    expected = cached_text if fresh_text is None else fresh_text
    assert entry.event.description == expected
    assert entry.event.description_status == (
        DescriptionStatus.CONTENT if expected else DescriptionStatus.EMPTY
    )
    assert entry.event.title == "Edited title" and entry.event.latitude == 37.8
    assert entry.first_seen_at == NOW and entry.emailed_at is None


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(version=2),
        lambda d: d.update(version=True),
        lambda d: d.update(events=[]),
        lambda d: d["events"]["evt-test"].update(first_seen_at="naive"),
        lambda d: d["events"]["evt-test"]["event"].update(url="https://evil.test/x"),
        lambda d: d["events"]["evt-test"].update(secret="bad"),
    ],
)
def test_corruption_rejected(change):
    data = state_data(observe(State(), [event()], now=NOW))
    change(data)
    with pytest.raises(StateError):
        parse_state(data)


def test_atomic_write_failure_keeps_previous(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    save_state(path, State(), initialize=True)
    old = path.read_bytes()

    def fail(*_):
        raise OSError("disk failure")

    monkeypatch.setattr("event_radar.state.os.replace", fail)
    with pytest.raises(StateError):
        save_state(path, observe(State(), [event()], now=NOW))
    assert path.read_bytes() == old
    assert not list(tmp_path.glob("*.tmp"))


def test_duplicate_json_and_missing_save(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version":1,"version":1,"events":{}}')
    with pytest.raises(StateError):
        load_state(path)
    with pytest.raises(StateError):
        save_state(tmp_path / "missing.json", State())


def test_transient_windows_replace_conflict_retries_only_storage(tmp_path, monkeypatch):
    import event_radar.state as store

    path = tmp_path / "state.json"
    save_state(path, State(), initialize=True)
    real_replace = store.os.replace
    calls = []
    sleeps = []

    def replace_file(source, target):
        calls.append(target)
        if len(calls) == 1:
            error = PermissionError("temporary lock")
            error.winerror = 5
            raise error
        real_replace(source, target)

    monkeypatch.setattr(store.os, "replace", replace_file)
    monkeypatch.setattr(store.time, "sleep", sleeps.append)
    save_state(path, observe(State(), [event()], now=NOW))
    assert len(calls) == 2 and sleeps == [0.05]
    assert load_state(path).events


def test_init_cli_has_no_network_and_preserves_existing(tmp_path):
    path = tmp_path / "state.json"
    assert main(["--init-state", "--state", str(path)]) == 0
    assert main(["--init-state", "--state", str(path)]) == 6
    assert json.loads(path.read_text())["events"] == {}


def test_preview_does_not_touch_state_and_observe_is_explicit(tmp_path):
    import runpy

    fixture = runpy.run_path("tests/test_pipeline.py")
    path = tmp_path / "state.json"
    save_state(path, State(), initialize=True)
    initial = path.read_bytes()
    arguments = [
        "--config",
        "config.example.yaml",
        "--output",
        str(tmp_path / "preview"),
        "--state",
        str(path),
    ]
    assert main(arguments, client_factory=fixture["factory"], now=fixture["NOW"]) == 0
    assert path.read_bytes() == initial
    assert (
        main(arguments + ["--observe"], client_factory=fixture["factory"], now=fixture["NOW"]) == 0
    )
    assert load_state(path).events
    assert all(entry.emailed_at is None for entry in load_state(path).events.values())
