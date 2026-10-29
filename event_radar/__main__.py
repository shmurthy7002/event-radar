"""Safe-default preview and explicitly selected state/delivery operations."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from event_radar.collectors.luma import LumaClient, TotalCollectionFailure
from event_radar.config import ConfigError, load_config, load_email_settings
from event_radar.delivery import DeliveryError, ResendProvider, validate_email_settings
from event_radar.pipeline import build_preview, write_preview
from event_radar.runner import PostSendStateError, reconcile_batch, run_delivery
from event_radar.smoke import run_smoke, smoke_config
from event_radar.state import (
    State,
    StateError,
    load_state,
    observe,
    save_state,
    state_lock,
    unemailed_events,
)


def _main(
    argv=None,
    *,
    client_factory=LumaClient,
    provider_factory=None,
    now: datetime | None = None,
    report=None,
    checkpoint=None,
) -> int:
    emit = report.log
    parser = argparse.ArgumentParser(description="Event Radar local discovery tools")
    parser.add_argument("--version", action="version", version="event-radar 0.1.0")
    parser.add_argument("--config", default="config.yaml", help="non-sensitive YAML settings")
    parser.add_argument("--output", default="output/preview", help="local artifact directory")
    parser.add_argument(
        "--smoke-check", action="store_true", help="explicit bounded live health/coverage check"
    )
    parser.add_argument("--state", default="state/state.json", help="local versioned state path")
    parser.add_argument(
        "--init-state",
        action="store_true",
        help="explicitly create empty state without network access",
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help="persist observations after preview; never mark delivered",
    )
    parser.add_argument(
        "--pending-preview",
        action="store_true",
        help="read state to preview pending matches without modifying history",
    )
    parser.add_argument(
        "--send", action="store_true", help="explicitly send pending matches via Resend"
    )
    parser.add_argument(
        "--send-empty",
        action="store_true",
        help="with --send, allow one nothing-new summary per UTC day",
    )
    parser.add_argument("--batch-id", help="unresolved batch key for explicit reconciliation")
    parser.add_argument(
        "--reconcile-accepted",
        metavar="MESSAGE_ID",
        help="after provider verification, acknowledge an accepted batch without sending",
    )
    parser.add_argument(
        "--reconcile-not-accepted",
        action="store_true",
        help="after provider verification, release a batch confirmed not accepted",
    )
    parser.add_argument("--summary-json", help="sanitized structured run report")
    parser.add_argument("--summary-markdown", help="sanitized Actions-compatible summary")
    parser.add_argument(
        "--diagnostics-only",
        action="store_true",
        help="write/print only sanitized diagnostics, no event artifacts",
    )
    args = parser.parse_args(argv)
    report.json_path, report.markdown_path, report.only = (
        args.summary_json,
        args.summary_markdown,
        args.diagnostics_only,
    )
    if (
        sum(
            bool(x)
            for x in (
                args.init_state,
                args.observe,
                args.smoke_check,
                args.pending_preview,
                args.send,
                args.reconcile_accepted,
                args.reconcile_not_accepted,
            )
        )
        > 1
    ):
        parser.error("choose only one preview, state, send, or reconciliation operation")
    reconciliation = bool(args.reconcile_accepted or args.reconcile_not_accepted)
    if bool(args.batch_id) != reconciliation:
        parser.error("--batch-id and exactly one reconciliation outcome must be provided together")
    if args.send_empty and not args.send:
        parser.error("--send-empty requires --send")
    try:
        if reconciliation:
            reconcile_batch(
                args.state,
                args.batch_id,
                now=now if now is not None else datetime.now(UTC),
                accepted_message_id=args.reconcile_accepted,
                confirmed_not_accepted=args.reconcile_not_accepted,
            )
            emit("Confirmed provider outcome recorded; no email sent.")
            return 0
        if args.init_state:
            save_state(args.state, State(), initialize=True)
            emit("Empty state initialized; no delivery history overwritten.")
            return 0
        state = (
            load_state(args.state) if args.observe or args.pending_preview or args.send else None
        )
    except StateError as exc:
        emit(f"State error: {exc}", file=sys.stderr)
        return 6
    try:
        config = load_config(args.config)
        if args.smoke_check:
            config = smoke_config(config)
    except ConfigError as exc:
        emit(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.send:
            settings = load_email_settings()
            validate_email_settings(settings)
            provider = (
                provider_factory(settings)
                if provider_factory
                else ResendProvider(settings, send_enabled=True)
            )
            result = run_delivery(
                config,
                args.state,
                provider,
                client=client_factory(config.requests),
                now=now,
                send_enabled=True,
                nothing_new=args.send_empty,
                report=report,
                checkpoint=checkpoint,
            )
            report.outcome = (
                "provider_accepted" if result.outcome == "accepted" else "healthy_empty"
            )
            report.update({"pending": result.pending, "pending_batches": 0})
            emit(
                f"Delivery {result.outcome}: {result.included} provider-accepted events; {result.pending} eligible events remain pending."
            )
            for warning in result.warnings:
                emit(f"Warning: {warning}")
            return 0
        if args.smoke_check:
            summary = run_smoke(config, client=client_factory(config.requests), now=now)
            report.update(summary)
            report.outcome = "smoke_healthy" if summary["healthy"] else None
            try:
                directory = Path(args.output)
                directory.mkdir(parents=True, exist_ok=True)
                if not report.only:
                    (directory / "smoke.json").write_text(
                        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
                    )
            except OSError:
                emit("Output error: could not write smoke diagnostics.", file=sys.stderr)
                return 4
            emit(json.dumps(summary, indent=2))
            return 0 if summary["healthy"] else 5
        preview = build_preview(
            config,
            client=client_factory(config.requests),
            now=now,
            previous_events=unemailed_events(
                state, now=now if now is not None else datetime.now(UTC)
            )
            if args.pending_preview
            else None,
            suppressed_ids={
                key for key, entry in state.events.items() if entry.emailed_at is not None
            }
            if args.pending_preview
            else None,
        )
    except PostSendStateError:
        emit(
            "Post-send persistence error: provider accepted; inspect retained state before retry (duplicate risk).",
            file=sys.stderr,
        )
        return 8
    except StateError as exc:
        emit(f"State error: {exc}", file=sys.stderr)
        return 6
    except DeliveryError as exc:
        report.outcome = {
            "ambiguous": "provider_ambiguous",
            "rejected": "provider_rejected",
            "configuration": "configuration_error",
        }.get(exc.outcome, "provider_failed")
        emit(
            f"Delivery {report.outcome}: inspect settings/provider logs and retained batch before retry.",
            file=sys.stderr,
        )
        return 7
    except TotalCollectionFailure as exc:
        from dataclasses import asdict

        report.update(
            {
                "sources": [asdict(source) for source in exc.result.sources],
                "requests": exc.result.requests,
            }
        )
        emit(
            "Collection failed: no source produced usable data; no new artifacts written.",
            file=sys.stderr,
        )
        for source in exc.result.sources:
            for error in source.errors:
                emit(f"{source.source}: {error}", file=sys.stderr)
        return 3
    report.update(preview.summary)
    report.preview(preview.digest.data)
    try:
        if not report.only:
            write_preview(preview, args.output)
    except (OSError, UnicodeError):
        emit(
            "Output error: could not write preview artifacts; check directory permissions and disk space.",
            file=sys.stderr,
        )
        return 4
    if args.observe:
        try:
            with state_lock(args.state):
                save_state(
                    args.state,
                    observe(
                        load_state(args.state),
                        preview.observed_events,
                        now=now if now is not None else datetime.now(UTC),
                    ),
                    lock_held=True,
                )
        except StateError as exc:
            emit(f"State error: {exc}", file=sys.stderr)
            return 6
    summary = preview.summary
    emit(
        f"{'PARTIAL' if summary['partial'] else 'OK'}: {summary['collected']} collected, {summary['matches']} matches, {len(preview.digest.selected_ids)} shown; {summary['requests']}/{summary['request_budget']} HTTP attempts."
    )
    for warning in summary["warnings"]:
        emit(f"Warning: {warning}")
    emit(f"Preview written to {args.output}")
    return 0


def main(argv=None, *, client_factory=LumaClient, provider_factory=None, now=None, checkpoint=None):
    from event_radar.diagnostics import RunReport

    report = RunReport()
    code = _main(
        argv,
        client_factory=client_factory,
        provider_factory=provider_factory,
        now=now,
        report=report,
        checkpoint=checkpoint,
    )
    return report.finish(code)


if __name__ == "__main__":
    raise SystemExit(main())
