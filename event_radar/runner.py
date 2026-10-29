"""Transactional local delivery orchestration, independent of collection/provider adapters."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from event_radar.config import Config
from event_radar.delivery import IDEMPOTENCY_HOURS, DeliveryError, Message
from event_radar.models import utc_timestamp
from event_radar.pipeline import build_preview
from event_radar.relevance import filter_events, rank_events
from event_radar.state import (
    Batch,
    StateError,
    load_state,
    message_hash,
    observe,
    save_state,
    state_lock,
    unemailed_events,
)


class PostSendStateError(StateError):
    """Provider accepted but local acknowledgement was not durably persisted."""


@dataclass(frozen=True)
class DeliveryRun:
    outcome: str
    included: int
    pending: int
    partial: bool
    warnings: tuple[str, ...]


def run_delivery(
    config: Config,
    path,
    provider,
    *,
    client=None,
    now: datetime | None = None,
    send_enabled: bool = False,
    nothing_new: bool = False,
) -> DeliveryRun:
    if not send_enabled:
        raise DeliveryError("configuration", "delivery orchestration requires explicit send mode")
    clock = lambda: utc_timestamp(now if now is not None else datetime.now(UTC))
    with state_lock(path):
        state = load_state(path)
        preview = build_preview(
            config,
            client=client,
            now=clock(),
            previous_events=unemailed_events(state, now=clock()),
            suppressed_ids={
                key for key, entry in state.events.items() if entry.emailed_at is not None
            },
        )
        state = observe(state, preview.observed_events, now=clock())
        entries = dict(state.events)
        for event in preview.candidate_events:
            entries[event.id] = replace(entries[event.id], event=event)
        state = replace(state, events=entries)
        save_state(path, state, lock_held=True)
        eligible = rank_events(
            filter_events(unemailed_events(state, now=clock()), config, now=clock()).events, config
        ).matches
        eligible_ids = {r.event.id for r in eligible}
        pending = [
            batch for batch in state.batches.values() if batch.outcome in ("prepared", "ambiguous")
        ]
        if pending:
            batch = pending[0]
            if clock() < batch.created_at or clock() >= batch.created_at + timedelta(
                hours=IDEMPOTENCY_HOURS
            ):
                raise DeliveryError(
                    "ambiguous",
                    "pending batch is outside the idempotency window; reconcile provider acceptance before retry; duplicate-delivery risk",
                )
            if batch.scope != provider.scope:
                raise DeliveryError(
                    "ambiguous", "pending batch envelope/key changed; reconcile before retry"
                )
            if not set(batch.event_ids) <= eligible_ids:
                raise DeliveryError(
                    "ambiguous",
                    "pending batch includes expired or no-longer-relevant events; reconcile before retry",
                )
        else:
            already_empty = any(
                not b.event_ids
                and b.outcome == "accepted"
                and b.created_at.date() == clock().date()
                and b.scope == provider.scope
                for b in state.batches.values()
            )
            if not eligible and (not nothing_new or already_empty):
                return DeliveryRun(
                    "empty", 0, 0, preview.summary["partial"], tuple(preview.summary["warnings"])
                )
            # Eligibility was recomputed under the lock, including cached observations.
            from event_radar.digest import render_digest

            digest = render_digest(
                eligible,
                limit=config.digest_length,
                now=clock(),
                warnings=preview.summary["warnings"],
                coverage=preview.summary,
            )
            message = Message("Event Radar: upcoming matches", digest.html, digest.text)
            batch = Batch(
                clock(),
                digest.selected_ids,
                provider.scope,
                message_hash(message.subject, message.html, message.text),
                message.subject,
                message.html,
                message.text,
            )
            state = replace(state, batches={**state.batches, batch.key: batch})
            save_state(
                path, state, lock_held=True
            )  # Durable intent must precede the provider call.
        try:
            acceptance = provider.send(
                Message(batch.subject, batch.html, batch.text), idempotency_key=batch.key
            )
        except DeliveryError as exc:
            outcome = "ambiguous" if exc.outcome == "ambiguous" else "rejected"
            failed = replace(
                batch,
                outcome=outcome,
                html=batch.html if outcome == "ambiguous" else "",
                text=batch.text if outcome == "ambiguous" else "",
            )
            save_state(
                path, replace(state, batches={**state.batches, batch.key: failed}), lock_held=True
            )
            raise
        accepted_at = clock()
        acknowledged = replace(
            batch,
            outcome="accepted",
            provider_message_id=acceptance.message_id,
            accepted_at=accepted_at,
            html="",
            text="",
        )
        entries = dict(state.events)
        for key in batch.event_ids:
            entries[key] = replace(entries[key], emailed_at=accepted_at)
        acknowledged_state = replace(
            state, events=entries, batches={**state.batches, batch.key: acknowledged}
        )
        try:
            save_state(path, acknowledged_state, lock_held=True)
        except StateError as exc:
            raise PostSendStateError(
                f"provider accepted but {exc}; duplicate-delivery risk; retain pending batch and reconcile/retry within 24 hours"
            ) from None
        return DeliveryRun(
            "accepted",
            len(batch.event_ids),
            len(eligible_ids - set(batch.event_ids)),
            preview.summary["partial"],
            tuple(preview.summary["warnings"]),
        )
