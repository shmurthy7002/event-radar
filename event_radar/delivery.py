"""Explicitly enabled Resend delivery with injectable transport and sanitized errors."""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from event_radar.config import EmailSettings

RESEND_URL = "https://api.resend.com/emails"
IDEMPOTENCY_HOURS = 24


class DeliveryError(RuntimeError):
    def __init__(self, outcome: str, message: str):
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class Message:
    subject: str
    html: str
    text: str


@dataclass(frozen=True)
class Acceptance:
    message_id: str


@dataclass(frozen=True)
class ProviderResponse:
    status: int
    body: bytes


def validate_email_settings(settings: EmailSettings) -> None:
    address = r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    if not settings.api_key or not re.fullmatch(r"[!-~]+", settings.api_key):
        raise DeliveryError("configuration", "email API key is missing or malformed")
    sender = settings.sender or ""
    if not re.fullmatch(address, sender) and not re.fullmatch(
        r"[^<>\r\n]+ <" + address + r">", sender
    ):
        raise DeliveryError("configuration", "email sender is missing or malformed")
    if not 1 <= len(settings.recipients) <= 50 or any(
        not re.fullmatch(address, recipient) for recipient in settings.recipients
    ):
        raise DeliveryError("configuration", "email recipients must be 1–50 valid bare addresses")


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resend_transport(payload: bytes, headers: dict[str, str], timeout: float) -> ProviderResponse:
    request = Request(RESEND_URL, data=payload, headers=headers, method="POST")
    try:
        response = build_opener(_NoRedirects()).open(request, timeout=timeout)
    except HTTPError as exc:
        response = exc
    with response:
        body = response.read(65537)
        if len(body) > 65536:
            raise DeliveryError("ambiguous", "provider response exceeded limit; acceptance unknown")
        return ProviderResponse(response.code, body)


class ResendProvider:
    def __init__(
        self,
        settings: EmailSettings,
        *,
        send_enabled: bool = False,
        transport=resend_transport,
        timeout: float = 15,
    ):
        if not send_enabled:
            raise DeliveryError("configuration", "real delivery requires explicit send enablement")
        validate_email_settings(settings)
        self.settings = settings
        self.transport = transport
        self.timeout = timeout

    @property
    def scope(self) -> str:
        """Bind pending retries to this envelope without persisting private addresses."""
        envelope = json.dumps(
            [self.settings.sender, list(self.settings.recipients)], separators=(",", ":")
        )
        return hmac.new(
            self.settings.api_key.encode(), envelope.encode(), hashlib.sha256
        ).hexdigest()

    def send(self, message: Message, *, idempotency_key: str) -> Acceptance:
        if not re.fullmatch(r"[A-Za-z0-9/_-]{1,256}", idempotency_key):
            raise DeliveryError("configuration", "invalid provider idempotency key")
        if (
            not message.subject
            or any(c in message.subject for c in "\r\n")
            or not message.html
            or not message.text
        ):
            raise DeliveryError(
                "configuration", "email needs a valid subject, HTML, and plain text"
            )
        payload = json.dumps(
            {
                "from": self.settings.sender,
                "to": list(self.settings.recipients),
                "subject": message.subject,
                "html": message.html,
                "text": message.text,
            }
        ).encode()
        headers = {
            "Authorization": "Bearer " + self.settings.api_key,
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
            "User-Agent": "event-radar/0.1",
        }
        try:
            response = self.transport(payload, headers, self.timeout)
        except (OSError, URLError, HTTPException):
            raise DeliveryError(
                "ambiguous", "provider connection failed; acceptance unknown; do not blindly resend"
            ) from None
        if not 200 <= response.status < 300:
            if 400 <= response.status < 500 and response.status not in (408, 409):
                raise DeliveryError(
                    "rejected",
                    f"provider rejected request (HTTP {response.status}); no automatic retry",
                )
            raise DeliveryError("ambiguous", f"provider HTTP {response.status}; acceptance unknown")
        try:
            message_id = json.loads(response.body)["id"]
            if not isinstance(message_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,200}", message_id
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            raise DeliveryError(
                "ambiguous",
                "provider success response lacked a valid message ID; acceptance unknown",
            ) from None
        return Acceptance(message_id)


@dataclass
class FakeProvider:
    """In-memory idempotent fake; never opens a network connection."""

    scope: str = field(default_factory=lambda: hashlib.sha256(b"fake-envelope").hexdigest())
    calls: list = field(default_factory=list)
    accepted: dict = field(default_factory=dict)
    failure: DeliveryError | None = None

    def send(self, message: Message, *, idempotency_key: str) -> Acceptance:
        self.calls.append((message, idempotency_key))
        if self.failure:
            raise self.failure
        if idempotency_key in self.accepted:
            original, acceptance = self.accepted[idempotency_key]
            if original != message:
                raise DeliveryError("ambiguous", "idempotency payload mismatch")
            return acceptance
        acceptance = Acceptance(f"fake-{len(self.accepted) + 1}")
        self.accepted[idempotency_key] = (message, acceptance)
        return acceptance
