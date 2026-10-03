"""Strict, unconnected HTTP envelope for a future final-review decision route.

The caller must run ``inspect_decision_headers`` before reading the body, read
exactly its bounded ``content_length`` bytes, then run ``parse_decision_body``.
Neither function grants approval or execution authority. A real server-held
session/CSRF capability does not exist in the current product server; without
one the parser returns AUTH_UNAVAILABLE. Request fields never name the reviewer.
"""

from dataclasses import dataclass
import ipaddress
import json
import re
from typing import Iterable, Optional, Protocol, Tuple


DECISION_TARGET = "/api/product-workspace/final-review/decision"
MAX_DECISION_BODY_BYTES = 4096
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}\Z", re.ASCII)
_OFFER_ID = re.compile(r"[1-9][0-9]{0,19}\Z", re.ASCII)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_FIELDS = frozenset({
    "offer_id", "task_id", "action_id", "plan_id",
    "contract_digest", "user_approved",
})


class FinalReviewEnvelopeError(ValueError):
    """A fixed machine code and HTTP status; no request secrets in the error."""

    def __init__(self, code: str, status: int):
        self.code = code
        self.status = status
        super().__init__(code)


class FinalReviewSessionCapability(Protocol):
    """Future trusted session store binding a server session to its CSRF secret.

    Implementations must obtain the subject from server-held authenticated state,
    validate the supplied CSRF token against that state, and return None for an
    absent/expired session or mismatched token. Merely echoing Cookie, a header,
    or a request body field is not an implementation of this capability.
    """

    def authenticate(
        self, *, headers: Tuple[Tuple[str, str], ...], origin: str,
        csrf_token: str,
    ) -> Optional[str]:
        ...


@dataclass(frozen=True)
class DecisionFraming:
    headers: Tuple[Tuple[str, str], ...]
    origin: str
    csrf_token: str
    content_length: int


@dataclass(frozen=True)
class FinalReviewDecisionEnvelope:
    offer_id: str
    task_id: str
    action_id: str
    plan_id: str
    contract_digest: str
    user_approved: bool
    subject: str


def _one(headers: Tuple[Tuple[str, str], ...], name: str, code: str,
         status: int) -> str:
    values = [value for key, value in headers if key.lower() == name]
    if len(values) != 1 or not values[0] or values[0] != values[0].strip():
        raise FinalReviewEnvelopeError(code, status)
    return values[0]


def inspect_decision_headers(
    *, target: str, headers: Iterable[Tuple[str, str]],
    client_address: str, server_address: Tuple[str, int],
) -> DecisionFraming:
    """Validate request framing without touching the body or any state."""
    if target != DECISION_TARGET:
        raise FinalReviewEnvelopeError("TARGET_INVALID", 400)
    try:
        client = ipaddress.ip_address(client_address)
        bound = ipaddress.ip_address(server_address[0])
        port = server_address[1]
    except (ValueError, TypeError, IndexError):
        raise FinalReviewEnvelopeError("LOOPBACK_REQUIRED", 403) from None
    if (not client.is_loopback or not bound.is_loopback
            or (isinstance(client, ipaddress.IPv6Address) and client.ipv4_mapped)):
        raise FinalReviewEnvelopeError("LOOPBACK_REQUIRED", 403)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise FinalReviewEnvelopeError("LOOPBACK_REQUIRED", 403)
    if bound not in (ipaddress.IPv4Address("127.0.0.1"),
                     ipaddress.IPv6Address("::1")):
        raise FinalReviewEnvelopeError("LOOPBACK_REQUIRED", 403)

    try:
        items = tuple(headers)
        if len(items) > 64 or any(
            not isinstance(key, str) or not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9-]+", key, re.ASCII)
            or "\r" in value or "\n" in value
            for key, value in items
        ) or sum(len(key) + len(value) for key, value in items) > 8192:
            raise ValueError
    except (TypeError, ValueError):
        raise FinalReviewEnvelopeError("FRAMING_INVALID", 400) from None

    host = _one(items, "host", "HOST_INVALID", 403)
    bound_label = "[::1]" if bound == ipaddress.IPv6Address("::1") else str(bound)
    if host != f"{bound_label}:{port}":
        raise FinalReviewEnvelopeError("HOST_INVALID", 403)
    origin = _one(items, "origin", "ORIGIN_INVALID", 403)
    if origin != f"http://{host}":
        raise FinalReviewEnvelopeError("ORIGIN_INVALID", 403)
    if any(key.lower() == "transfer-encoding" for key, _ in items):
        raise FinalReviewEnvelopeError("FRAMING_INVALID", 400)
    content_length = _one(items, "content-length", "FRAMING_INVALID", 400)
    if not re.fullmatch(r"[1-9][0-9]*", content_length, re.ASCII):
        raise FinalReviewEnvelopeError("FRAMING_INVALID", 400)
    if len(content_length) > len(str(MAX_DECISION_BODY_BYTES)):
        raise FinalReviewEnvelopeError("BODY_TOO_LARGE", 413)
    length = int(content_length)
    if length > MAX_DECISION_BODY_BYTES:
        raise FinalReviewEnvelopeError("BODY_TOO_LARGE", 413)
    if _one(items, "content-type", "MEDIA_TYPE_INVALID", 415) != "application/json":
        raise FinalReviewEnvelopeError("MEDIA_TYPE_INVALID", 415)
    csrf_token = _one(items, "x-orbit-csrf-token", "AUTH_REQUIRED", 403)
    if "," in csrf_token or len(csrf_token) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in csrf_token):
        raise FinalReviewEnvelopeError("AUTH_REQUIRED", 403)
    if sum(key.lower() == "cookie" for key, _ in items) > 1:
        raise FinalReviewEnvelopeError("FRAMING_INVALID", 400)
    return DecisionFraming(items, origin, csrf_token, length)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON number")


def parse_decision_body(
    framing: DecisionFraming, raw_body: bytes,
    *, session_capability: Optional[FinalReviewSessionCapability] = None,
) -> FinalReviewDecisionEnvelope:
    """Validate exact JSON and resolve a subject only through a trusted seam."""
    if not isinstance(raw_body, bytes) or len(raw_body) != framing.content_length:
        raise FinalReviewEnvelopeError("BODY_INCOMPLETE", 400)
    try:
        data = json.loads(raw_body.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise FinalReviewEnvelopeError("BODY_INVALID", 400) from None
    if not isinstance(data, dict) or set(data) != _FIELDS:
        raise FinalReviewEnvelopeError("BODY_INVALID", 400)
    if (not isinstance(data["offer_id"], str)
            or not _OFFER_ID.fullmatch(data["offer_id"])
            or any(not isinstance(data[key], str) or not _IDENTITY.fullmatch(data[key])
                   for key in ("task_id", "action_id", "plan_id"))
            or not isinstance(data["contract_digest"], str)
            or not _DIGEST.fullmatch(data["contract_digest"])
            or data["user_approved"] is not True):
        raise FinalReviewEnvelopeError("BODY_INVALID", 400)

    verifier = getattr(session_capability, "authenticate", None)
    if not callable(verifier):
        raise FinalReviewEnvelopeError("AUTH_UNAVAILABLE", 503)
    try:
        subject = verifier(headers=framing.headers, origin=framing.origin,
                           csrf_token=framing.csrf_token)
    except Exception:
        raise FinalReviewEnvelopeError("AUTH_UNAVAILABLE", 503) from None
    if subject is None:
        raise FinalReviewEnvelopeError("AUTH_REQUIRED", 403)
    if not isinstance(subject, str) or not _IDENTITY.fullmatch(subject):
        raise FinalReviewEnvelopeError("AUTH_UNAVAILABLE", 503)
    return FinalReviewDecisionEnvelope(
        offer_id=data["offer_id"], task_id=data["task_id"],
        action_id=data["action_id"], plan_id=data["plan_id"],
        contract_digest=data["contract_digest"], user_approved=True,
        subject=subject,
    )
