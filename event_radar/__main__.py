"""Explicit read-only local collection and preview commands."""

import argparse
import sys
from datetime import datetime

from event_radar.collectors.luma import LumaClient, TotalCollectionFailure
from event_radar.config import ConfigError, load_config
from event_radar.pipeline import build_preview, write_preview


def main(argv=None, *, client_factory=LumaClient, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Event Radar local discovery tools")
    parser.add_argument("--version", action="version", version="event-radar 0.1.0")
    parser.add_argument("--config", default="config.yaml", help="non-sensitive YAML settings")
    parser.add_argument("--output", default="output/preview", help="local artifact directory")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        preview = build_preview(config, client=client_factory(config.requests), now=now)
    except TotalCollectionFailure as exc:
        print(
            "Collection failed: no source produced usable data; no preview written.",
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
