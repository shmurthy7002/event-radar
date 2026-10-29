"""Allowlisted run metrics for console/Actions; event and provider payloads stay local."""

import builtins
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

COUNTERS = (
    "collected",
    "duplicates",
    "requests",
    "request_budget",
    "page_budget_per_source",
    "matches",
    "eligible_before_ranking",
    "pending",
    "pending_batches",
    "provider_accepted",
)
OUTCOMES = {
    0: "preview_ready",
    2: "configuration_error",
    3: "collection_failed",
    4: "output_failed",
    5: "smoke_unhealthy",
    6: "state_failed",
    7: "provider_failed",
    8: "post_send_persistence_failed",
}
GUIDANCE = {
    "collection_failed": "Check source schemas/connectivity; no successful empty run or delivery is implied.",
    "provider_rejected": "Fix provider/settings; events remain pending.",
    "provider_ambiguous": "Retain the pending batch; retry the identical request within its window or reconcile provider logs.",
    "post_send_persistence_failed": "Provider accepted but persistence failed. Preserve state and reconcile; duplicate delivery is possible.",
    "state_failed": "Restore trustworthy state or resolve the writer lock before sending.",
    "configuration_error": "Check YAML and required environment settings without printing sensitive values.",
    "output_failed": "Check local output permissions/disk space; ignore stale artifacts.",
    "smoke_unhealthy": "Inspect source/description failure counts; budget truncation alone is expected.",
}


def _count(value):
    return value if type(value) is int and value >= 0 else None


def redact(value: str) -> str:
    for key, secret in os.environ.items():
        if key.startswith("EVENT_RADAR_EMAIL_") and secret:
            for part in (secret, *secret.split(",")):
                if part.strip():
                    value = value.replace(part.strip(), "[redacted]")
    value = re.sub(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+", "[redacted-email]", value)
    return value


@dataclass
class RunReport:
    outcome: str | None = None
    metrics: dict = field(default_factory=dict)
    json_path: str | None = None
    markdown_path: str | None = None
    only: bool = False

    def update(self, summary: dict):
        for key in COUNTERS:
            if key in summary:
                self.metrics[key] = _count(summary[key])
        if "partial" in summary:
            self.metrics["partial"] = bool(summary["partial"])
        if "warnings" in summary:
            self.metrics["warning_count"] = len(summary["warnings"])
        if "descriptions" in summary:
            self.metrics["descriptions"] = {
                key: _count(summary["descriptions"].get(key))
                for key in ("content", "empty", "failed", "not_fetched", "requests")
            }
        if "filter_exclusions" in summary:
            self.metrics["filter_exclusions"] = {
                key: _count(value)
                for key, value in summary["filter_exclusions"].items()
                if key
                in (
                    "past_start",
                    "beyond_horizon",
                    "unknown_location",
                    "outside_radius",
                    "sold_out",
                    "waitlist",
                )
            }
        if "sources" in summary:
            self.metrics["sources"] = []
            for source in summary["sources"]:
                item = {
                    key: _count(source.get(key))
                    for key in ("pages", "records", "normalized", "malformed", "usable_pages")
                }
                item.update(
                    source=source.get("source")
                    if source.get("source") in ("sf", "ai", "tech")
                    else "configured-source",
                    truncated=bool(source.get("truncated")),
                    errors=len(source.get("errors", [])),
                    warnings=len(source.get("warnings", [])),
                )
                comparison = source.get("page_comparison")
                if isinstance(comparison, dict):
                    item["page_comparison"] = {
                        key: _count(comparison.get(key))
                        for key in ("page_ids", "feed_ids", "overlap", "page_only", "feed_only")
                    }
                self.metrics["sources"].append(item)

    def log(self, *values, **kwargs):
        if not self.only:
            builtins.print(*(redact(str(value)) for value in values), **kwargs)

    def finish(self, code: int) -> int:
        outcome = self.outcome if self.outcome and code in (0, 7) else OUTCOMES.get(code, "failed")
        if code == 0 and self.outcome is None:
            outcome = (
                "partial_discovery"
                if self.metrics.get("partial")
                else "healthy_empty"
                if self.metrics.get("matches") == 0
                else "preview_ready"
            )
        data = {
            "schema_version": 1,
            "outcome": outcome,
            "exit_code": code,
            "metrics": self.metrics,
            "guidance": GUIDANCE.get(
                outcome,
                "Public discovery is incomplete; provider acceptance is not inbox delivery.",
            ),
        }
        markdown = f"## Event Radar: {outcome}\n\nExit code: {code}\n\n{data['guidance']}\n\n"
        markdown += "| Metric | Count |\n| --- | --- |\n" + "".join(
            f"| {key} | {self.metrics.get(key, 'unavailable')} |\n" for key in COUNTERS
        )
        for source in self.metrics.get("sources", []):
            markdown += f"\n- {source['source']}: {source['pages']} pages, {source['records']} records, {source['malformed']} malformed, {source['errors']} errors, truncated={source['truncated']}\n"
        for section in ("descriptions", "filter_exclusions"):
            if section in self.metrics:
                markdown += (
                    f"\n{section}: "
                    + ", ".join(f"{key}={value}" for key, value in self.metrics[section].items())
                    + "\n"
                )
        try:
            for name, content in (
                (self.json_path, json.dumps(data, indent=2) + "\n"),
                (self.markdown_path, markdown),
            ):
                if name:
                    path = Path(name)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
        except OSError:
            builtins.print("Output error: could not write run diagnostics.")
            return 4
        if self.only:
            builtins.print(markdown)
        return code
