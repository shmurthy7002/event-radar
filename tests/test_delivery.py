import json

import pytest

from event_radar.config import EmailSettings
from event_radar.delivery import (
    DeliveryError,
    FakeProvider,
    Message,
    ProviderResponse,
    ResendProvider,
    validate_email_settings,
)

SETTINGS = EmailSettings("test-secret", "Radar <sender@example.test>", ("recipient@example.test",))
MESSAGE = Message("Event Radar", "<p>Robotics</p>", "Robotics")


def test_requires_explicit_enablement_and_valid_settings():
    with pytest.raises(DeliveryError, match="explicit"):
        ResendProvider(SETTINGS)
    for settings in (
        EmailSettings(),
        EmailSettings("secret", "invalid", ("a@example.test",)),
        EmailSettings("secret", "a@example.test", ("bad\r\naddress",)),
    ):
        with pytest.raises(DeliveryError) as exc:
            validate_email_settings(settings)
        assert "test-secret" not in str(exc.value)


def test_html_text_headers_acceptance_and_private_scope():
    captured = []

    def transport(payload, headers, timeout):
        captured.append((json.loads(payload), headers, timeout))
        return ProviderResponse(200, b'{"id":"message-example"}')

    provider = ResendProvider(SETTINGS, send_enabled=True, transport=transport)
    assert (
        provider.send(MESSAGE, idempotency_key="event-radar/test").message_id == "message-example"
    )
    body, headers, timeout = captured[0]
    assert body["html"] == MESSAGE.html and body["text"] == MESSAGE.text
    assert headers["Idempotency-Key"] == "event-radar/test" and timeout == 15
    assert len(provider.scope) == 64 and "example.test" not in provider.scope


@pytest.mark.parametrize(
    "status,outcome",
    [
        (401, "rejected"),
        (422, "rejected"),
        (429, "rejected"),
        (500, "ambiguous"),
        (409, "ambiguous"),
        (302, "ambiguous"),
    ],
)
def test_provider_errors_no_retry_and_no_response_leak(status, outcome):
    calls = []

    def transport(*args):
        calls.append(args)
        return ProviderResponse(status, b"recipient@example.test test-secret")

    provider = ResendProvider(SETTINGS, send_enabled=True, transport=transport)
    with pytest.raises(DeliveryError) as exc:
        provider.send(MESSAGE, idempotency_key="test")
    assert exc.value.outcome == outcome
    assert "example.test" not in str(exc.value) and "test-secret" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"bad", b"{}", b'{"id":"recipient@example.test"}'])
def test_malformed_success_is_ambiguous(body):
    provider = ResendProvider(
        SETTINGS, send_enabled=True, transport=lambda *_: ProviderResponse(200, body)
    )
    with pytest.raises(DeliveryError) as exc:
        provider.send(MESSAGE, idempotency_key="test")
    assert exc.value.outcome == "ambiguous"


def test_network_failure_sanitized_and_fake_idempotent():
    def fail(*_):
        raise TimeoutError("test-secret")

    provider = ResendProvider(SETTINGS, send_enabled=True, transport=fail)
    with pytest.raises(DeliveryError, match="acceptance unknown") as exc:
        provider.send(MESSAGE, idempotency_key="test")
    assert "test-secret" not in str(exc.value)
    fake = FakeProvider()
    assert fake.send(MESSAGE, idempotency_key="test") == fake.send(MESSAGE, idempotency_key="test")
    assert len(fake.accepted) == 1
