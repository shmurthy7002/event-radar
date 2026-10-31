"""Bounded hosted-runner operations with remote state and sanitized artifacts only."""

import argparse
import os
import subprocess
from pathlib import Path

import yaml

from event_radar.__main__ import main as app_main
from event_radar.config import ConfigError, load_config
from event_radar.diagnostics import RunReport
from event_radar.git_state import GitStateStore
from event_radar.pages import PagesArchive, PagesPublisher, PublicationError
from event_radar.state import StateError, save_state


def bounded_settings(path):
    load_config(path)
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    caps = {
        "pages_per_source": 3,
        "total_requests": 40,
        "detail_requests": 20,
        "timeout_seconds": 10,
        "retries": 1,
        "max_backoff_seconds": 3,
    }
    for key, cap in caps.items():
        data["requests"][key] = min(data["requests"].get(key, cap), cap)
    return data


def main(argv=None, *, store_factory=GitStateStore, invoke=app_main, publisher_factory=None):
    parser = argparse.ArgumentParser(description="Bounded Event Radar Actions operation")
    parser.add_argument("mode", choices=("initialize", "preview", "smoke", "send"))
    parser.add_argument("--config", default="config.example.yaml")
    parser.add_argument("--directory", default="state/actions")
    parser.add_argument("--reports", default="output/actions")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--send-empty", action="store_true")
    args = parser.parse_args(argv)
    if args.send_empty and args.mode != "send":
        parser.error("--send-empty requires send mode")
    directory, reports = Path(args.directory), Path(args.reports)
    report = RunReport(
        json_path=str(reports / "workflow.json"),
        markdown_path=str(reports / "workflow.md"),
        only=True,
    )
    if args.mode == "send":
        enabled = os.environ.get("EVENT_RADAR_DELIVERY_ENABLED") == "true"
        verified = os.environ.get("EVENT_RADAR_ROLLOUT_VERIFIED") == "true"
        confirmed = args.confirmation == "SEND_CONFIGURED_RECIPIENT" or (
            args.scheduled and verified
        )
        if not enabled or not confirmed or (args.scheduled and not verified):
            report.outcome = "delivery_gated"
            return report.finish(7)
    if args.mode == "initialize" and args.confirmation != "INITIALIZE_EMPTY_STATE":
        return report.finish(2)
    if (
        args.mode in ("initialize", "send")
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        return report.finish(2)
    try:
        data = bounded_settings(args.config)
        directory.mkdir(parents=True, exist_ok=True)
        reports.mkdir(parents=True, exist_ok=True)
        config_path = directory / "config.yaml"
        config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        if args.mode == "smoke":
            codes = []
            for label, latitude, longitude in (
                ("berkeley", 37.8715, -122.273),
                ("sf", 37.7749, -122.4194),
            ):
                data["origin"] = {"latitude": latitude, "longitude": longitude}
                config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
                codes.append(
                    invoke(
                        [
                            "--smoke-check",
                            "--config",
                            str(config_path),
                            "--diagnostics-only",
                            "--summary-json",
                            str(reports / f"run-{label}.json"),
                            "--summary-markdown",
                            str(reports / f"run-{label}.md"),
                        ]
                    )
                )
            return max(codes)
        remote = args.remote
        if remote == "origin":
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"], capture_output=True, check=True, timeout=10
            )
            remote = result.stdout.decode().strip()
        store = store_factory(directory / "git-cache", remote, token=os.environ.get("GITHUB_TOKEN"))
        if args.mode == "initialize":
            store.initialize()
            report.outcome = "state_initialized"
            return report.finish(0)
        state = store.fetch()
        state_path = directory / "state.json"
        save_state(state_path, state, initialize=not state_path.exists())
        arguments = [
            "--send" if args.mode == "send" else "--pending-preview",
            "--config",
            str(config_path),
            "--state",
            str(state_path),
            "--diagnostics-only",
            "--summary-json",
            str(reports / "run.json"),
            "--summary-markdown",
            str(reports / "run.md"),
        ]
        if args.send_empty:
            arguments.append("--send-empty")
        kwargs = {"checkpoint": store.publish if args.mode == "send" else None}
        if args.mode == "send":
            kwargs["publisher"] = (
                publisher_factory()
                if publisher_factory
                else PagesPublisher(
                    PagesArchive(
                        directory / "pages-cache", remote, token=os.environ.get("GITHUB_TOKEN")
                    ),
                    os.environ.get("EVENT_RADAR_PAGES_BASE_URL", ""),
                    os.environ.get("GITHUB_REPOSITORY", ""),
                    os.environ.get("GITHUB_TOKEN", ""),
                )
            )
        return invoke(arguments, **kwargs)
    except PublicationError:
        return report.finish(9)
    except ValueError:
        return report.finish(2)
    except ConfigError:
        return report.finish(2)
    except StateError:
        return report.finish(6)
    except (OSError, subprocess.SubprocessError):
        return report.finish(4)


if __name__ == "__main__":
    raise SystemExit(main())
