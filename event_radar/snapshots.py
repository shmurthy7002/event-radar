"""Pure immutable public digest snapshots and short link notifications."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from html import escape
from urllib.parse import urlsplit

from event_radar.delivery import Message
from event_radar.digest import PACIFIC
from event_radar.models import utc_timestamp


def pages_base_url(value: str) -> str:
    """Allow only supplied HTTPS GitHub Pages domains and an optional project path."""
    if not isinstance(value, str) or not re.fullmatch(
        r"https://[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\.github\.io(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)?/?",
        value,
    ):
        raise ValueError(
            "Pages base URL must be an HTTPS owner.github.io URL with an optional repository path"
        )
    parts = urlsplit(value)
    if parts.path.strip("/") in (".", ".."):
        raise ValueError("invalid Pages project path")
    return value.rstrip("/")


def content_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    path: str
    url: str
    html: str
    sha256: str

    def validate(self):
        if not all(isinstance(v, str) for v in asdict(self).values()):
            raise ValueError("invalid snapshot fields")
        if not re.fullmatch(r"digests/\d{4}-\d{2}-\d{2}/[a-f0-9]{64}\.html", self.path):
            raise ValueError("invalid snapshot identity")
        suffix = "/" + self.path
        if (
            not self.url.endswith(suffix)
            or pages_base_url(self.url[: -len(suffix)]) + suffix != self.url
        ):
            raise ValueError("invalid snapshot URL")
        if not self.html or content_hash(self.html) != self.sha256:
            raise ValueError("snapshot content integrity failure")
        return self


def prepare_snapshot(digest, *, base_url: str, now: datetime) -> tuple[Snapshot, Message]:
    now = utc_timestamp(now)
    date = now.astimezone(PACIFIC).date().isoformat()
    sha = content_hash(digest.html)
    identity = hashlib.sha256(
        json.dumps([now.isoformat(), digest.selected_ids, sha]).encode()
    ).hexdigest()
    path = f"digests/{date}/{identity}.html"
    url = pages_base_url(base_url) + "/" + path
    snapshot = Snapshot(path, url, digest.html, sha).validate()
    count = len(digest.selected_ids)
    subject = f"Event Radar: {count} events for {date}"
    text = f"Event Radar — {date} (America/Los_Angeles)\n{count} upcoming event matches.\nRead your digest: {url}\nPublic discovery is incomplete; see the page for coverage notes.\n"
    html = f'<html><body><h1>Event Radar</h1><p>{count} upcoming event matches for {date} (America/Los_Angeles).</p><p><a href="{escape(url, quote=True)}">Read your digest</a></p><p>Public discovery is incomplete; see the page for coverage notes.</p></body></html>'
    return snapshot, Message(subject, html, text)
