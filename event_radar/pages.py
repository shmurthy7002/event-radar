"""Durable allowlisted Pages archive, explicit builds, and bounded public verification."""

import argparse
import json
import os
import re
import time
from html import escape
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from event_radar.delivery import _NoRedirects
from event_radar.git_state import GitStateError, GitStateStore
from event_radar.snapshots import Snapshot, pages_base_url


class PublicationError(RuntimeError):
    """Publication failed; never contact the provider or acknowledge events."""


class PagesArchive(GitStateStore):
    """Separate branch of public HTML only. Never a working-tree directory upload."""

    def __init__(self, directory, remote, *, token=None):
        super().__init__(directory, remote, branch="event-radar-pages", token=token)

    def fetch(self):
        head = self._remote_head()
        if head is None:
            raise PublicationError(
                "Pages archive missing; explicitly initialize it before delivery"
            )
        self._run(["fetch", "--no-tags", "origin", f"refs/heads/{self.branch}"])
        self.revision = self._run(["rev-parse", "FETCH_HEAD"]).stdout.decode().strip()
        if self.revision != head:
            raise PublicationError("Pages archive changed during fetch")
        files = {}
        raw = self._run(["ls-tree", "-r", "-z", self.revision]).stdout
        for record in raw.split(b"\0"):
            if not record:
                continue
            metadata, path = record.decode("ascii").split("\t")
            mode, kind, oid = metadata.split()
            if (
                mode != "100644"
                or kind != "blob"
                or not (
                    path in ("index.html", ".nojekyll")
                    or re.fullmatch(r"digests/\d{4}-\d{2}-\d{2}/[a-f0-9]{64}\.html", path)
                )
            ):
                raise PublicationError(
                    "Pages archive contains unexpected files; refusing publication"
                )
            files[path] = oid
        if not {".nojekyll", "index.html"} <= files.keys():
            raise PublicationError("Pages archive is incomplete; restore trusted archive history")
        return files

    def _commit(self, files):
        if self._remote_head() != self.revision:
            raise PublicationError("Pages archive head changed; refusing stale update")
        tree = {}
        for path, oid in files.items():
            node = tree
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = oid

        def write_tree(node):
            rows = []
            for name, value in sorted(node.items()):
                rows.append(
                    f"040000 tree {write_tree(value)}\t{name}\n"
                    if isinstance(value, dict)
                    else f"100644 blob {value}\t{name}\n"
                )
            return self._run(["mktree"], data="".join(rows).encode()).stdout.decode().strip()

        root = write_tree(tree)
        parent = ["-p", self.revision] if self.revision else []
        commit = (
            self._run(["commit-tree", root, *parent], data=b"Archive public Event Radar digest\n")
            .stdout.decode()
            .strip()
        )
        if self._remote_head() != self.revision:
            raise PublicationError("Pages archive changed before push")
        self._run(["push", "origin", f"{commit}:refs/heads/{self.branch}"])
        self.revision = commit

    def _blob(self, value):
        return (
            self._run(["hash-object", "-w", "--stdin"], data=value.encode()).stdout.decode().strip()
        )

    def initialize(self):
        if self._remote_head() is not None:
            raise PublicationError("Pages archive already exists; refusing initialization")
        self.revision = None
        self._commit(
            {
                ".nojekyll": self._blob(""),
                "index.html": self._blob(
                    "<!doctype html><html lang=en><title>Event Radar</title><h1>Event Radar</h1><p>No digests published yet.</p></html>"
                ),
            }
        )

    def publish(self, snapshot: Snapshot):
        snapshot.validate()
        files = self.fetch()
        if snapshot.path in files:
            existing = self._run(["cat-file", "blob", files[snapshot.path]]).stdout
            if existing != snapshot.html.encode():
                raise PublicationError("immutable snapshot collision; refusing overwrite")
            return  # A retry must not move latest backwards.
        files[snapshot.path] = self._blob(snapshot.html)
        # Only relative links, fixed markup, and the allowlisted snapshot path.
        files["index.html"] = self._blob(
            f'<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Event Radar</title><h1>Event Radar</h1><p><a href="{escape(snapshot.path, quote=True)}">Read the latest digest</a></p></html>'
        )
        self._commit(files)


def github_request(repository, token, endpoint, *, method="GET"):
    request = Request(
        f"https://api.github.com/repos/{repository}/pages{endpoint}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "event-radar",
        },
        method=method,
        data=b"{}" if method == "POST" else None,
    )
    try:
        with build_opener(_NoRedirects()).open(request, timeout=15) as response:
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError
            return json.loads(data)
    except (OSError, ValueError, URLError):
        raise PublicationError(
            "Pages API failed; check source settings, permissions, and build status"
        ) from None


def public_content(url, limit):
    # Deliberately no Authorization header, cookies, or redirect following.
    try:
        with build_opener(_NoRedirects()).open(
            Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "event-radar"}),
            timeout=10,
        ) as response:
            if response.status != 200:
                return None
            return response.read(limit + 1)
    except (OSError, HTTPError, URLError):
        return None


class PagesPublisher:
    def __init__(
        self,
        archive,
        base_url,
        repository,
        token,
        *,
        api=github_request,
        get=public_content,
        sleep=time.sleep,
        attempts=30,
        interval=10,
    ):
        self.archive, self.base_url = archive, pages_base_url(base_url)
        if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repository or ""):
            raise PublicationError("Pages requires a valid GitHub repository identity")
        self.repository, self.token = repository, token
        self.api, self.get, self.sleep = api, get, sleep
        if not 1 <= attempts <= 30 or not 0 <= interval <= 10:
            raise PublicationError("Pages verification bounds invalid")
        self.attempts, self.interval = attempts, interval

    def publish(self, snapshot):
        try:
            snapshot.validate()
            if snapshot.url != self.base_url + "/" + snapshot.path:
                raise PublicationError(
                    "pending snapshot uses another Pages site; reconcile before migration"
                )
            settings = self.api(self.repository, self.token, "")
            if (
                settings.get("build_type") != "legacy"
                or settings.get("source") != {"branch": "event-radar-pages", "path": "/"}
                or pages_base_url(settings.get("html_url")) != self.base_url
                or settings.get("cname")
            ):
                raise PublicationError(
                    "Pages must use the dedicated archive branch and supplied HTTPS domain"
                )
            self.archive.publish(snapshot)
            expected = snapshot.html.encode()
            if self.get(snapshot.url, len(expected)) == expected:
                return
            # GITHUB_TOKEN pushes do not implicitly trigger Pages builds.
            self.api(self.repository, self.token, "/builds", method="POST")
            for attempt in range(self.attempts):
                if self.get(snapshot.url, len(expected)) == expected:
                    return
                status = self.api(self.repository, self.token, "/builds/latest")
                if status.get("status") == "errored":
                    raise PublicationError("Pages build failed; no email sent")
                if attempt + 1 < self.attempts:
                    self.sleep(self.interval)
            raise PublicationError("public snapshot verification timed out; no email sent")
        except (GitStateError, ValueError) as exc:
            raise PublicationError(
                "Pages archive or snapshot integrity failed; no email sent"
            ) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explicitly initialize a public-HTML-only Pages archive"
    )
    parser.add_argument("operation", choices=["initialize"])
    parser.add_argument("--remote", required=True)
    parser.add_argument("--directory", default="state/pages-cache")
    args = parser.parse_args(argv)
    try:
        PagesArchive(args.directory, args.remote, token=os.environ.get("GITHUB_TOKEN")).initialize()
    except (PublicationError, GitStateError):
        print(
            "Pages archive initialization failed; existing history was not intentionally replaced."
        )
        return 9
    print("Public Pages archive initialized; no email sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
