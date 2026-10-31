import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from event_radar.config import load_config
from event_radar.digest import render_digest
from event_radar.pages import PagesArchive, PagesPublisher, PublicationError
from event_radar.relevance import rank_events
from event_radar.snapshots import pages_base_url, prepare_snapshot
from tests.test_runner import NOW, event

BASE = "https://example.github.io/event-radar"


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(path)], capture_output=True, check=True)
    return path


def snapshot(now=NOW):
    config = load_config("config.example.yaml")
    item = replace(event(), title="Robotics <script>bad</script>", city="Berkeley & SF")
    digest = render_digest(
        rank_events([item], config).matches,
        limit=30,
        now=now,
        warnings=["Partial discovery <unsafe>"],
    )
    return prepare_snapshot(digest, base_url=BASE, now=now)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.github.io",
        "https://evil.test",
        "https://example.github.io:443/project",
        "https://user@example.github.io/project",
        "https://example.github.io/../repo",
        "https://example.github.io/%2e%2e",
        "https://example.github.io/repo?x=1",
        "https://example.github.io/repo#x",
        "https://example.github.io/repo/other",
        "https://example.github.io/..",
        "//example.github.io",
        "https://example.github.io\\evil",
        "https://example.github.io/repo\n",
    ],
)
def test_reject_unsafe_pages_urls(url):
    with pytest.raises(ValueError):
        pages_base_url(url)


def test_project_path_escaping_notification_and_dst_date():
    assert pages_base_url(BASE + "/") == BASE
    assert pages_base_url("https://example.github.io/") == "https://example.github.io"
    a, message = snapshot(datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    assert "/digests/2026-10-31/" in a.url
    assert a.url.startswith(BASE + "/")
    assert "<script>" not in a.html and "&lt;script&gt;" in a.html
    assert "Partial discovery &lt;unsafe&gt;" in a.html
    assert "Robotics" not in message.html + message.text
    assert a.url in message.html and a.url in message.text
    assert "1 upcoming" in message.text and "2026-10-31" in message.subject
    assert snapshot(NOW)[0].path != snapshot(NOW + timedelta(seconds=1))[0].path
    for now, label in [
        (datetime(2026, 11, 1, 8, 30, tzinfo=UTC), "PDT"),
        (datetime(2026, 11, 1, 9, 30, tzinfo=UTC), "PST"),
    ]:
        item = replace(event(), start=now)
        digest = render_digest(
            rank_events([item], load_config("config.example.yaml")).matches, limit=30, now=now
        )
        assert label in prepare_snapshot(digest, base_url=BASE, now=now)[0].html


class Archive:
    def __init__(self):
        self.snapshots = []

    def publish(self, item):
        self.snapshots.append(item)


def settings():
    return {
        "build_type": "legacy",
        "source": {"branch": "event-radar-pages", "path": "/"},
        "html_url": BASE + "/",
        "cname": None,
    }


def test_publish_requests_build_and_verifies_exact_content():
    item, _ = snapshot()
    archive = Archive()
    calls, sleeps = [], []
    responses = iter([b"stale", None, item.html.encode()])

    def api(repo, token, endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return settings() if not endpoint else {"status": "building"}

    publisher = PagesPublisher(
        archive,
        BASE,
        "example/event-radar",
        "private-token",
        api=api,
        get=lambda *args: next(responses),
        sleep=sleeps.append,
        attempts=2,
    )
    publisher.publish(item)
    assert archive.snapshots == [item]
    assert ("/builds", {"method": "POST"}) in calls
    assert sleeps == [10]


@pytest.mark.parametrize("status", ["errored", "building"])
def test_public_verification_failure_is_bounded(status):
    item, _ = snapshot()
    calls = []

    def api(repo, token, endpoint, **kwargs):
        calls.append(endpoint)
        return settings() if not endpoint else {"status": status}

    publisher = PagesPublisher(
        Archive(),
        BASE,
        "example/event-radar",
        "token",
        api=api,
        get=lambda *args: b"wrong page",
        sleep=lambda _: None,
        attempts=2,
    )
    with pytest.raises(PublicationError, match="failed|timed out"):
        publisher.publish(item)
    assert calls.count("/builds/latest") <= 2


def test_wrong_site_and_unexpected_files_fail_before_publish():
    item, _ = snapshot()
    archive = Archive()
    publisher = PagesPublisher(
        archive,
        BASE,
        "example/event-radar",
        "token",
        api=lambda *a, **k: {**settings(), "source": {"branch": "main", "path": "/"}},
    )
    with pytest.raises(PublicationError):
        publisher.publish(item)
    assert not archive.snapshots


def test_archive_retains_same_day_snapshots_on_fresh_cache(remote, tmp_path):
    archive = PagesArchive(tmp_path / "cache", str(remote))
    with pytest.raises(PublicationError, match="missing"):
        archive.fetch()
    archive.initialize()
    first, _ = snapshot()
    second, _ = snapshot(NOW + timedelta(minutes=1))
    archive.publish(first)
    archive.publish(second)
    fresh = PagesArchive(tmp_path / "fresh", str(remote))
    files = fresh.fetch()
    assert set(files) == {"index.html", ".nojekyll", first.path, second.path}
    latest = files["index.html"]
    fresh.publish(first)
    assert fresh.fetch()["index.html"] == latest
    with pytest.raises(PublicationError, match="collision"):
        from event_radar.snapshots import content_hash

        fresh.publish(replace(first, html="changed", sha256=content_hash("changed")))
    with pytest.raises(PublicationError, match="already exists"):
        fresh.initialize()


def test_archive_racing_push_and_disclosure_boundary(remote, tmp_path, monkeypatch):
    from event_radar.git_state import GitStateError

    a = PagesArchive(tmp_path / "a", str(remote))
    b = PagesArchive(tmp_path / "b", str(remote))
    a.initialize()
    first, _ = snapshot()
    second, _ = snapshot(NOW + timedelta(minutes=1))
    original = a._run

    def race(args, **kwargs):
        if args[0] == "push":
            b.publish(second)
        return original(args, **kwargs)

    monkeypatch.setattr(a, "_run", race)
    with pytest.raises(GitStateError):
        a.publish(first)
    assert first.path not in b.fetch()
    # Even a committed state/config file cannot be deployed by this adapter.
    files = b.fetch()
    files["state.json"] = b._blob('{"recipient": "private@example.test"}')
    b._commit(files)
    with pytest.raises(PublicationError, match="unexpected files"):
        b.publish(first)
