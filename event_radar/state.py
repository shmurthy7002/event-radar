"""Versioned, validated local observations; seen is deliberately distinct from sent."""

import json
import os
import tempfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from event_radar.models import Event, Observation, utc_timestamp


class StateError(RuntimeError):
    """State is unavailable, invalid, or could not be persisted."""


@dataclass(frozen=True)
class Entry:
    event: Event
    first_seen_at: datetime
    last_seen_at: datetime
    emailed_at: datetime | None = None


@dataclass(frozen=True)
class State:
    events: dict[str, Entry] = field(default_factory=dict)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def event_from_dict(data: dict) -> Event:
    if not isinstance(data, dict) or set(data) != set(Event.__dataclass_fields__):
        raise ValueError("invalid normalized event fields")
    data = dict(data)
    if not isinstance(data["provenance"], list) or not isinstance(data["warnings"], list):
        raise TypeError("invalid event collections")
    data["provenance"] = tuple(Observation(**p) for p in data["provenance"])
    return Event(**data)


def state_data(state: State) -> dict:
    return {
        "version": 1,
        "events": {
            key: {
                "event": entry.event.to_dict(),
                "first_seen_at": entry.first_seen_at.isoformat(),
                "last_seen_at": entry.last_seen_at.isoformat(),
                "emailed_at": entry.emailed_at.isoformat() if entry.emailed_at else None,
            }
            for key, entry in sorted(state.events.items())
        },
    }


def parse_state(data) -> State:
    try:
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "events"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["events"], dict)
        ):
            raise ValueError
        entries = {}
        for key, value in data["events"].items():
            if not isinstance(value, dict) or set(value) != {
                "event",
                "first_seen_at",
                "last_seen_at",
                "emailed_at",
            }:
                raise ValueError
            event = event_from_dict(value["event"])
            first, last = (
                utc_timestamp(value["first_seen_at"]),
                utc_timestamp(value["last_seen_at"]),
            )
            emailed = (
                utc_timestamp(value["emailed_at"]) if value["emailed_at"] is not None else None
            )
            if key != event.id or first > last or (emailed is not None and emailed < first):
                raise ValueError
            entries[key] = Entry(event, first, last, emailed)
        return State(entries)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise StateError("state is corrupt or has an unsupported schema/version") from None


def load_state(path: str | Path) -> State:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        return parse_state(data)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise StateError(
            "state is missing, inaccessible, or invalid; initialize only an intentional first run"
        ) from None


@contextmanager
def state_lock(path: str | Path):
    """Exclusive local writer lock; stale locks require explicit operator recovery."""
    lock_path = Path(str(path) + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        raise StateError(
            "state is locked or inaccessible; verify no active writer before recovery"
        ) from None
    try:
        os.close(fd)
        yield
    finally:
        lock_path.unlink()


def save_state(
    path: str | Path, state: State, *, initialize: bool = False, lock_held: bool = False
) -> None:
    with nullcontext() if lock_held else state_lock(path):
        _save_state(path, state, initialize=initialize)


def _save_state(path: str | Path, state: State, *, initialize: bool) -> None:
    """Flush a same-directory temp file, then atomically publish it. Init never overwrites."""
    path = Path(path)
    temporary = None
    try:
        # Validate before writing, even if a caller constructed dataclasses directly.
        data = state_data(parse_state(state_data(state)))
        if initialize and path.exists():
            raise StateError("state already exists; refusing initialization over delivery history")
        if not initialize and not path.is_file():
            raise StateError("state disappeared before save; refusing implicit initialization")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".event-radar-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=True, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        raise StateError(
            "state write failed; existing state was not intentionally replaced"
        ) from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def observe(state: State, events: list[Event], *, now: datetime) -> State:
    now = utc_timestamp(now)
    entries = dict(state.events)
    for event in events:
        previous = entries.get(event.id)
        if previous and now < previous.last_seen_at:
            raise StateError("observation clock precedes persisted last_seen_at")
        entries[event.id] = (
            replace(previous, event=event, last_seen_at=now) if previous else Entry(event, now, now)
        )
    return State(entries)


def unemailed_events(state: State, *, now: datetime) -> list[Event]:
    now = utc_timestamp(now)
    return [
        entry.event
        for _, entry in sorted(state.events.items())
        if entry.emailed_at is None and entry.event.start >= now
    ]
