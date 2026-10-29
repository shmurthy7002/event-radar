"""Versioned, validated local observations; seen is deliberately distinct from sent."""

import hashlib
import json
import os
import re
import tempfile
import time
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
    batches: dict[str, "Batch"] = field(default_factory=dict)


def message_hash(subject: str, html: str, text: str) -> str:
    return hashlib.sha256(
        json.dumps([subject, html, text], ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Batch:
    created_at: datetime
    event_ids: tuple[str, ...]
    scope: str
    payload_hash: str
    subject: str
    html: str
    text: str
    outcome: str = "prepared"
    provider_message_id: str | None = None
    accepted_at: datetime | None = None

    @property
    def key(self) -> str:
        value = [self.created_at.isoformat(), self.event_ids, self.scope, self.payload_hash]
        return (
            "event-radar/"
            + hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()
        )

    def to_dict(self) -> dict:
        return {
            "created_at": self.created_at.isoformat(),
            "event_ids": list(self.event_ids),
            "scope": self.scope,
            "payload_hash": self.payload_hash,
            "subject": self.subject,
            "html": self.html,
            "text": self.text,
            "outcome": self.outcome,
            "provider_message_id": self.provider_message_id,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
        }


def _batch_from_dict(data) -> Batch:
    if not isinstance(data, dict) or set(data) != set(Batch.__dataclass_fields__):
        raise ValueError
    values = dict(data)
    ids = values["event_ids"]
    if (
        not isinstance(ids, list)
        or any(not isinstance(x, str) or not x for x in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError
    values["event_ids"] = tuple(ids)
    values["created_at"] = utc_timestamp(values["created_at"])
    values["accepted_at"] = (
        utc_timestamp(values["accepted_at"]) if values["accepted_at"] is not None else None
    )
    batch = Batch(**values)
    if batch.outcome not in ("prepared", "ambiguous", "rejected", "accepted") or any(
        not isinstance(getattr(batch, k), str)
        for k in ("scope", "payload_hash", "subject", "html", "text")
    ):
        raise ValueError
    if not re.fullmatch(r"[a-f0-9]{64}", batch.scope) or not re.fullmatch(
        r"[a-f0-9]{64}", batch.payload_hash
    ):
        raise ValueError
    if batch.outcome in ("prepared", "ambiguous") and (
        not batch.html
        or not batch.text
        or message_hash(batch.subject, batch.html, batch.text) != batch.payload_hash
    ):
        raise ValueError
    if batch.outcome == "accepted":
        if (
            not isinstance(batch.provider_message_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", batch.provider_message_id)
            or batch.accepted_at is None
            or batch.accepted_at < batch.created_at
        ):
            raise ValueError
    elif batch.provider_message_id is not None or batch.accepted_at is not None:
        raise ValueError
    return batch


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
        "batches": {key: batch.to_dict() for key, batch in sorted(state.batches.items())},
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
            or not {"version", "events"} <= set(data) <= {"version", "events", "batches"}
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
        raw_batches = data.get("batches", {})
        if not isinstance(raw_batches, dict):
            raise TypeError
        batches = {key: _batch_from_dict(value) for key, value in raw_batches.items()}
        if any(
            key != batch.key or any(event_id not in entries for event_id in batch.event_ids)
            for key, batch in batches.items()
        ):
            raise ValueError
        if sum(batch.outcome in ("prepared", "ambiguous") for batch in batches.values()) > 1:
            raise ValueError
        if any(
            entries[event_id].emailed_at is None
            for batch in batches.values()
            if batch.outcome == "accepted"
            for event_id in batch.event_ids
        ):
            raise ValueError
        return State(entries, batches)
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
        try:
            lock_path.unlink()
        except OSError:
            raise StateError(
                "state lock cleanup failed; inspect saved history and verify no active writer before recovery"
            ) from None


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
        for attempt in range(3):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                # Windows sync/indexing processes can briefly hold the destination.
                # Retry only replacement, never provider delivery or state recreation.
                if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except (OSError, ValueError, TypeError) as exc:
        code = (
            f" (OS error {getattr(exc, 'winerror', exc.errno)})" if isinstance(exc, OSError) else ""
        )
        raise StateError(
            f"state write failed{code}; existing state was not intentionally replaced"
        ) from None
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                raise StateError(
                    "state temporary-file cleanup failed; check storage permissions"
                ) from None


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
    return replace(state, events=entries)


def unemailed_events(state: State, *, now: datetime) -> list[Event]:
    now = utc_timestamp(now)
    return [
        entry.event
        for _, entry in sorted(state.events.items())
        if entry.emailed_at is None and entry.event.start >= now
    ]
