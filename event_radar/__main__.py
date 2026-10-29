"""Explicit read-only local collection and preview commands."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from event_radar.collectors.luma import LumaClient, TotalCollectionFailure
from event_radar.config import ConfigError, load_config
from event_radar.pipeline import build_preview, write_preview
from event_radar.smoke import run_smoke, smoke_config


def main(argv=None, *, client_factory=LumaClient, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Event Radar local discovery tools")
    parser.add_argument("--version", action="version", version="event-radar 0.1.0")
    parser.add_argument("--config", default="config.yaml", help="non-sensitive YAML settings")
    parser.add_argument("--output", default="output/preview", help="local artifact directory")
    parser.add_argument(
        "--smoke-check", action="store_true", help="explicit bounded live health/coverage check"
    )
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.smoke_check:
            config = smoke_config(config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.smoke_check:
            summary = run_smoke(config, client=client_factory(config.requests), now=now)
            try:
                directory = Path(args.output)
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "smoke.json").write_text(
                    json.dumps(summary, indent=2) + "\n", encoding="utf-8"
                )
            except OSError:
                print("Output error: could not write smoke diagnostics.", file=sys.stderr)
                return 4
            print(json.dumps(summary, indent=2))
            return 0 if summary["healthy"] else 5
        preview = build_preview(config, client=client_factory(config.requests), now=now)
    except TotalCollectionFailure as exc:
        print(
            "Collection failed: no source produced usable data; no new artifacts written.",
            file=sys.stderr,
        )
        for source in exc.result.sources:
            for error in source.errors:
                print(f"{source.source}: {error}", file=sys.stderr)
        return 3
    try:
        write_preview(preview, args.output)
    except (OSError, UnicodeError):
        print(
            "Output error: could not write preview artifacts; check directory permissions and disk space.",
            file=sys.stderr,
        )
        return 4
    summary = preview.summary
    print(
        f"{'PARTIAL' if summary['partial'] else 'OK'}: {summary['collected']} collected, {summary['matches']} matches, {len(preview.digest.selected_ids)} shown; {summary['requests']}/{summary['request_budget']} HTTP attempts."
    )
    for warning in summary["warnings"]:
        print(f"Warning: {warning}")
    print(f"Preview written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
