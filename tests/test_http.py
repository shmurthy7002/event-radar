from dataclasses import replace
from datetime import UTC, datetime
from http.client import IncompleteRead
from urllib.error import URLError

import pytest

from event_radar.collectors.luma import BudgetExhausted, CollectionError, LumaClient, Response
from event_radar.config import Requests


def client_for(sequence, settings=None):
    calls, sleeps = [], []

    def transport(url, timeout):
        calls.append((url, timeout))
        value = sequence.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    return (
        LumaClient(
            settings or Requests(),
            transport=transport,
            sleep=sleeps.append,
            now=lambda: datetime(2026, 10, 28, tzinfo=UTC),
        ),
        calls,
        sleeps,
    )


def response(status=200, body=b"{}", **headers):
    return Response(status, headers, body)


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("secret"),
        URLError("secret"),
        IncompleteRead(b"partial"),
        response(503),
        response(429),
    ],
)
def test_transient_retry(failure):
    client, calls, sleeps = client_for([failure, response()])
    assert client.discovery({"cursor": "opaque+/="}) == {}
    assert len(calls) == 2
    assert calls[0][1] == 15
    assert "opaque%2B%2F%3D" in calls[0][0]
    assert sleeps == [1]


@pytest.mark.parametrize(
    "header,delay",
    [("99999", 30), ("-1", 0), ("bad", 1), ("NaN", 1), ("Wed, 28 Oct 2026 00:00:10 GMT", 10)],
)
def test_retry_after(header, delay):
    client, _, sleeps = client_for([response(429, **{"rEtRy-AfTeR": header}), response()])
    client.discovery({})
    assert sleeps == [delay]


def test_retry_exhaustion_and_redaction():
    client, calls, sleeps = client_for([TimeoutError("secret")] * 3)
    with pytest.raises(CollectionError, match="retries exhausted") as exc:
        client.discovery({})
    assert "secret" not in str(exc.value)
    assert len(calls) == 3
    assert sleeps == [1, 2]


@pytest.mark.parametrize("status", [301, 401, 403, 404])
def test_permanent_errors_do_not_retry(status):
    client, calls, sleeps = client_for([response(status)])
    with pytest.raises(CollectionError, match=str(status)):
        client.discovery({})
    assert len(calls) == 1
    assert not sleeps


@pytest.mark.parametrize("body", [b"<html>bad</html>", b"[]", b"\xff"])
def test_invalid_json(body):
    client, calls, _ = client_for([response(body=body)])
    with pytest.raises(CollectionError, match="JSON"):
        client.discovery({})
    assert len(calls) == 1


def test_budget_counts_retries_and_metadata():
    client, calls, sleeps = client_for([response(503)], replace(Requests(), total_requests=1))
    with pytest.raises(BudgetExhausted):
        client.page("sf")
    assert len(calls) == 1
    assert not sleeps
    with pytest.raises(BudgetExhausted):
        client.discovery({})
    assert len(calls) == 1


def test_pages_and_invalid_slug():
    client, calls, _ = client_for([response(body=b"<html></html>")])
    assert client.page("sf") == "<html></html>"
    assert calls[0][0] == "https://luma.com/sf"
    with pytest.raises(CollectionError, match="slug"):
        client.page("https://evil.test")
