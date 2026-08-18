"""Typed Miaoshou common-collect-box claim service.

This module owns only the first move from one existing common collect-box
detail into the TikTok and Shopee platform collect boxes.  It does not edit
content, claim a platform detail to a shop, or publish a marketplace listing.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

from modules.miaoshou.client import MiaoshouBusinessRejectedError

CLAIM_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/claimed"
)
CLAIM_REQUEST_SCHEMA_VERSION = "miaoshou-collectbox-claim-request/v1"
PLATFORM_CLAIM_REQUEST_SCHEMA_VERSION = (
    "miaoshou-collectbox-platform-claim-request/v1"
)
PLATFORM_CLAIM_RECEIPT_SCHEMA_VERSION = (
    "miaoshou-collectbox-platform-claim-receipt/v1"
)
CLAIM_RECEIPT_SCHEMA_VERSION = "miaoshou-collectbox-claim-receipt/v1"
CLAIM_PUBLIC_SCHEMA_VERSION = "miaoshou-collectbox-claim-public/v1"
CLAIM_PLATFORMS = ("tiktok", "shopee")
CLAIM_WRITE_CLASS_PREFIX = "miaoshou:collectbox:claim"
RATE_LIMIT_CODE = "accountApiQpsRateLimit"
ALREADY_CLAIMED_CODE = "alreadyClaimed"
RATE_LIMIT_RETRY_DELAY_SECONDS = 3.0

ACCEPTED = "ACCEPTED"
ALREADY_PRESENT = "ALREADY_PRESENT"
FAILED = "FAILED"
_STATUSES = (ACCEPTED, ALREADY_PRESENT, FAILED)
_CLAIM_SERIAL_LOCK = threading.Lock()

PostCallable = Callable[[str, dict[str, object]], Mapping[str, object]]
WaitCallable = Callable[[float], object]


class MiaoshouAlreadyPresentObservation(MiaoshouBusinessRejectedError):
    """Typed already-claimed result carrying an authoritative exact identity."""

    def __init__(self, platform_detail_id: int | str) -> None:
        super().__init__("already claimed with exact identity", code=ALREADY_CLAIMED_CODE)
        self.platform_detail_id = _positive_identifier(
            platform_detail_id, name="platform_detail_id"
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _positive_identifier(value: object, *, name: str) -> int:
    if type(value) is int:
        normalized = value
    elif type(value) is str:
        if not value or not value.isascii() or not value.isdigit():
            raise ValueError(f"{name} must be a positive ASCII decimal identifier")
        if len(value) > 1 and value.startswith("0"):
            raise ValueError(f"{name} must use its canonical decimal representation")
        normalized = int(value)
    else:
        raise TypeError(f"{name} must be a built-in int or string")
    if normalized <= 0:
        raise ValueError(f"{name} must be positive")
    return normalized


def _strict_nonempty_string(value: object, *, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a built-in string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty without edge whitespace")
    if len(value) > 256:
        raise ValueError(f"{name} is too long")
    return value


def _reason_code(value: object, *, fallback: str) -> str:
    if type(value) is not str or not value or len(value) > 128:
        return fallback
    if any(not (character.isalnum() or character in "._:-") for character in value):
        return fallback
    return value


@dataclass(frozen=True)
class CollectBoxClaimRequest:
    """Server-owned identity for the exact TikTok then Shopee claim batch."""

    common_detail_id: int | str = field(repr=False)
    platforms: tuple[str, str]
    idempotency_key: str = field(repr=False)
    schema_version: str = CLAIM_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CLAIM_REQUEST_SCHEMA_VERSION:
            raise ValueError("claim request schema is invalid")
        common_detail_id = _positive_identifier(
            self.common_detail_id, name="common_detail_id"
        )
        if type(self.platforms) is not tuple or self.platforms != CLAIM_PLATFORMS:
            raise ValueError(
                "platforms must be exactly ('tiktok', 'shopee')"
            )
        if any(type(platform) is not str for platform in self.platforms):
            raise TypeError("platforms must contain built-in strings")
        idempotency_key = _strict_nonempty_string(
            self.idempotency_key, name="idempotency_key"
        )
        object.__setattr__(self, "common_detail_id", common_detail_id)
        object.__setattr__(self, "idempotency_key", idempotency_key)

    @property
    def request_digest(self) -> str:
        return _digest(
            {
                "schema_version": self.schema_version,
                "common_detail_id": self.common_detail_id,
                "platforms": list(self.platforms),
                "idempotency_key": self.idempotency_key,
            }
        )

    @property
    def common_detail_identity_digest(self) -> str:
        return _digest(
            {
                "identity_kind": "miaoshou_common_collectbox_detail",
                "common_detail_id": self.common_detail_id,
            }
        )


@dataclass(frozen=True)
class CollectBoxPlatformClaimRequest:
    """One platform invocation, suitable for a durable control-plane step."""

    common_detail_id: int | str = field(repr=False)
    platform: str
    idempotency_key: str = field(repr=False)
    schema_version: str = PLATFORM_CLAIM_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PLATFORM_CLAIM_REQUEST_SCHEMA_VERSION:
            raise ValueError("platform claim request schema is invalid")
        common_detail_id = _positive_identifier(
            self.common_detail_id, name="common_detail_id"
        )
        if type(self.platform) is not str or self.platform not in CLAIM_PLATFORMS:
            raise ValueError("platform must be exactly tiktok or shopee")
        idempotency_key = _strict_nonempty_string(
            self.idempotency_key, name="idempotency_key"
        )
        object.__setattr__(self, "common_detail_id", common_detail_id)
        object.__setattr__(self, "idempotency_key", idempotency_key)

    @property
    def request_digest(self) -> str:
        return _digest(
            {
                "schema_version": self.schema_version,
                "common_detail_id": self.common_detail_id,
                "platform": self.platform,
                "idempotency_key": self.idempotency_key,
            }
        )

    @property
    def common_detail_identity_digest(self) -> str:
        return _digest(
            {
                "identity_kind": "miaoshou_common_collectbox_detail",
                "common_detail_id": self.common_detail_id,
            }
        )


@dataclass(frozen=True)
class PlatformClaimResult:
    platform: str
    status: str
    attempt_count: int
    dispatch_invoked: bool
    outcome_unknown: bool
    retry_safe: bool
    reconciliation_required: bool
    write_class: str
    write_outcome: str
    reason_code: str
    platform_detail_id: int | None = field(default=None, repr=False)
    platform_detail_identity_digest: str | None = None
    evidence_digest: str = ""

    def __post_init__(self) -> None:
        if self.platform not in CLAIM_PLATFORMS:
            raise ValueError("claim result platform is invalid")
        if self.status not in _STATUSES:
            raise ValueError("claim result status is invalid")
        if type(self.attempt_count) is not int or self.attempt_count not in (1, 2):
            raise ValueError("claim result attempt count is invalid")
        if type(self.dispatch_invoked) is not bool or not self.dispatch_invoked:
            raise ValueError("claim result must describe an invoked claim")
        if type(self.outcome_unknown) is not bool:
            raise TypeError("claim result outcome_unknown must be boolean")
        if type(self.retry_safe) is not bool:
            raise TypeError("claim result retry_safe must be boolean")
        if type(self.reconciliation_required) is not bool:
            raise TypeError(
                "claim result reconciliation_required must be boolean"
            )
        if self.write_class != f"{CLAIM_WRITE_CLASS_PREFIX}:{self.platform}":
            raise ValueError("claim result write class is invalid")
        if self.write_outcome not in {"ACCEPTED", "NONE", "UNKNOWN"}:
            raise ValueError("claim result write outcome is invalid")
        _strict_nonempty_string(self.reason_code, name="reason_code")
        if self.status in {ACCEPTED, ALREADY_PRESENT}:
            detail_id = _positive_identifier(
                self.platform_detail_id, name="platform_detail_id"
            )
            expected_identity_digest = _digest(
                {
                    "identity_kind": "miaoshou_platform_collectbox_detail",
                    "platform": self.platform,
                    "platform_detail_id": detail_id,
                }
            )
            if self.platform_detail_identity_digest != expected_identity_digest:
                raise ValueError("platform detail identity digest is invalid")
            if self.outcome_unknown:
                raise ValueError("resolved claim identity cannot be unknown")
            expected_write_outcome = (
                "ACCEPTED" if self.status == ACCEPTED else "NONE"
            )
            if self.write_outcome != expected_write_outcome:
                raise ValueError("resolved claim result facts are inconsistent")
            object.__setattr__(self, "platform_detail_id", detail_id)
        else:
            if self.platform_detail_id is not None:
                raise ValueError("non-accepted result cannot claim a detail identity")
            if self.platform_detail_identity_digest is not None:
                raise ValueError("non-accepted result cannot claim an identity digest")
        if self.outcome_unknown != (self.write_outcome == "UNKNOWN"):
            raise ValueError("claim result write uncertainty is inconsistent")
        if self.retry_safe and self.reconciliation_required:
            raise ValueError("claim result cannot be retry-safe and reconcile")
        if self.status in {ACCEPTED, ALREADY_PRESENT} and (
            self.retry_safe or self.reconciliation_required
        ):
            raise ValueError("resolved claim result cannot request recovery")
        if self.outcome_unknown and not self.reconciliation_required:
            raise ValueError("unknown claim result must require reconciliation")
        expected_evidence_digest = _digest(self._evidence_payload())
        if self.evidence_digest:
            if self.evidence_digest != expected_evidence_digest:
                raise ValueError("claim result evidence digest is invalid")
        else:
            object.__setattr__(self, "evidence_digest", expected_evidence_digest)

    def _evidence_payload(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "dispatch_invoked": self.dispatch_invoked,
            "outcome_unknown": self.outcome_unknown,
            "retry_safe": self.retry_safe,
            "reconciliation_required": self.reconciliation_required,
            "write_class": self.write_class,
            "write_outcome": self.write_outcome,
            "reason_code": self.reason_code,
            "platform_detail_identity_digest": self.platform_detail_identity_digest,
        }

    def public_projection(self) -> dict[str, object]:
        return self._evidence_payload() | {
            "evidence_digest": self.evidence_digest
        }


@dataclass(frozen=True)
class PlatformClaimReceipt:
    request_digest: str
    common_detail_identity_digest: str
    result: PlatformClaimResult
    schema_version: str = PLATFORM_CLAIM_RECEIPT_SCHEMA_VERSION
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != PLATFORM_CLAIM_RECEIPT_SCHEMA_VERSION:
            raise ValueError("platform claim receipt schema is invalid")
        for name, value in (
            ("request_digest", self.request_digest),
            ("common_detail_identity_digest", self.common_detail_identity_digest),
        ):
            if (
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{name} is invalid")
        if type(self.result) is not PlatformClaimResult:
            raise TypeError("platform claim receipt result is invalid")
        expected_receipt_digest = _digest(self._receipt_payload())
        if self.receipt_digest:
            if self.receipt_digest != expected_receipt_digest:
                raise ValueError("platform claim receipt digest is invalid")
        else:
            object.__setattr__(self, "receipt_digest", expected_receipt_digest)

    def _receipt_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "request_digest": self.request_digest,
            "common_detail_identity_digest": self.common_detail_identity_digest,
            "result": self.result.public_projection(),
        }

    def public_projection(self) -> dict[str, object]:
        return self._receipt_payload() | {
            "receipt_digest": self.receipt_digest
        }


@dataclass(frozen=True)
class CollectBoxClaimReceipt:
    request_digest: str
    common_detail_identity_digest: str
    platform_results: tuple[PlatformClaimResult, PlatformClaimResult]
    schema_version: str = CLAIM_RECEIPT_SCHEMA_VERSION
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != CLAIM_RECEIPT_SCHEMA_VERSION:
            raise ValueError("claim receipt schema is invalid")
        if (
            type(self.platform_results) is not tuple
            or tuple(row.platform for row in self.platform_results)
            != CLAIM_PLATFORMS
        ):
            raise ValueError("claim receipt platform results are invalid")
        for name, value in (
            ("request_digest", self.request_digest),
            ("common_detail_identity_digest", self.common_detail_identity_digest),
        ):
            if (
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{name} is invalid")
        expected_receipt_digest = _digest(self._receipt_payload())
        if self.receipt_digest:
            if self.receipt_digest != expected_receipt_digest:
                raise ValueError("claim receipt digest is invalid")
        else:
            object.__setattr__(self, "receipt_digest", expected_receipt_digest)

    def _receipt_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "request_digest": self.request_digest,
            "common_detail_identity_digest": self.common_detail_identity_digest,
            "platform_results": [
                row.public_projection() for row in self.platform_results
            ],
        }

    def public_projection(self) -> dict[str, object]:
        counts = {status: 0 for status in _STATUSES}
        for result in self.platform_results:
            counts[result.status] += 1
        return {
            "schema_version": CLAIM_PUBLIC_SCHEMA_VERSION,
            "request_digest": self.request_digest,
            "common_detail_identity_digest": self.common_detail_identity_digest,
            "platform_results": [
                result.public_projection() for result in self.platform_results
            ],
            "status_counts": counts,
            "receipt_digest": self.receipt_digest,
        }


def _claim_body(common_detail_id: int, platform: str) -> dict[str, object]:
    return {
        "detailSerialNumberPlatformList": [
            {
                "detailId": common_detail_id,
                "platform": platform,
                "serialNumber": 1,
            }
        ]
    }


def _accepted_result(
    *, platform: str, platform_detail_id: int, attempt_count: int
) -> PlatformClaimResult:
    identity_digest = _digest(
        {
            "identity_kind": "miaoshou_platform_collectbox_detail",
            "platform": platform,
            "platform_detail_id": platform_detail_id,
        }
    )
    return PlatformClaimResult(
        platform=platform,
        status=ACCEPTED,
        attempt_count=attempt_count,
        dispatch_invoked=True,
        outcome_unknown=False,
        retry_safe=False,
        reconciliation_required=False,
        write_class=f"{CLAIM_WRITE_CLASS_PREFIX}:{platform}",
        write_outcome="ACCEPTED",
        reason_code="claim_accepted",
        platform_detail_id=platform_detail_id,
        platform_detail_identity_digest=identity_digest,
    )


def _known_result(
    *,
    platform: str,
    status: str,
    attempt_count: int,
    reason_code: str,
    retry_safe: bool,
    reconciliation_required: bool = False,
    platform_detail_id: int | None = None,
) -> PlatformClaimResult:
    identity_digest = (
        _digest(
            {
                "identity_kind": "miaoshou_platform_collectbox_detail",
                "platform": platform,
                "platform_detail_id": platform_detail_id,
            }
        )
        if platform_detail_id is not None
        else None
    )
    return PlatformClaimResult(
        platform=platform,
        status=status,
        attempt_count=attempt_count,
        dispatch_invoked=True,
        outcome_unknown=False,
        retry_safe=retry_safe,
        reconciliation_required=reconciliation_required,
        write_class=f"{CLAIM_WRITE_CLASS_PREFIX}:{platform}",
        write_outcome="NONE",
        reason_code=reason_code,
        platform_detail_id=platform_detail_id,
        platform_detail_identity_digest=identity_digest,
    )


def _unknown_result(
    *, platform: str, attempt_count: int, reason_code: str
) -> PlatformClaimResult:
    return PlatformClaimResult(
        platform=platform,
        status=FAILED,
        attempt_count=attempt_count,
        dispatch_invoked=True,
        outcome_unknown=True,
        retry_safe=False,
        reconciliation_required=True,
        write_class=f"{CLAIM_WRITE_CLASS_PREFIX}:{platform}",
        write_outcome="UNKNOWN",
        reason_code=reason_code,
    )


def _platform_detail_id(
    response: object, *, common_detail_id: int, platform: str
) -> int:
    if not isinstance(response, Mapping):
        raise ValueError("claim response must be a mapping")
    if response.get("result") != "success":
        raise ValueError("claim response result is not success")
    data = response.get("data")
    root = (
        data.get("platformCollectBoxDetailIdMap")
        if isinstance(data, Mapping)
        else None
    )
    platform_map = root.get(platform) if isinstance(root, Mapping) else None
    if not isinstance(platform_map, Mapping):
        raise ValueError("claim response platform map is unavailable")
    raw = platform_map.get(str(common_detail_id))
    if raw is None:
        raw = platform_map.get(common_detail_id)
    return _positive_identifier(raw, name="platform_detail_id")


def _claim_one_platform(
    *,
    common_detail_id: int,
    platform: str,
    post: PostCallable,
    wait: WaitCallable,
) -> PlatformClaimResult:
    body = _claim_body(common_detail_id, platform)
    attempt_count = 0
    while True:
        attempt_count += 1
        try:
            response = post(CLAIM_PATH, body)
        except MiaoshouBusinessRejectedError as error:
            code = error.code
            if (
                code == RATE_LIMIT_CODE
                and attempt_count == 1
            ):
                wait(RATE_LIMIT_RETRY_DELAY_SECONDS)
                continue
            if code == ALREADY_CLAIMED_CODE:
                if type(error) is not MiaoshouAlreadyPresentObservation:
                    return _known_result(
                        platform=platform,
                        status=FAILED,
                        attempt_count=attempt_count,
                        reason_code="already_claimed_identity_unavailable",
                        retry_safe=False,
                        reconciliation_required=True,
                    )
                observed_detail_id = error.platform_detail_id
                return _known_result(
                    platform=platform,
                    status=ALREADY_PRESENT,
                    attempt_count=attempt_count,
                    reason_code=ALREADY_CLAIMED_CODE,
                    retry_safe=False,
                    platform_detail_id=observed_detail_id,
                )
            return _known_result(
                platform=platform,
                status=FAILED,
                attempt_count=attempt_count,
                reason_code=_reason_code(
                    code, fallback="business_rejected"
                ),
                retry_safe=True,
            )
        except Exception:
            return _unknown_result(
                platform=platform,
                attempt_count=attempt_count,
                reason_code="transport_outcome_unknown",
            )
        try:
            detail_id = _platform_detail_id(
                response,
                common_detail_id=common_detail_id,
                platform=platform,
            )
        except (TypeError, ValueError):
            return _unknown_result(
                platform=platform,
                attempt_count=attempt_count,
                reason_code="response_identity_unavailable",
            )
        return _accepted_result(
            platform=platform,
            platform_detail_id=detail_id,
            attempt_count=attempt_count,
        )


def claim_common_collectbox_platform(
    request: CollectBoxPlatformClaimRequest,
    *,
    post: PostCallable | None = None,
    wait: WaitCallable = time.sleep,
) -> PlatformClaimReceipt:
    """Invoke the claim endpoint for exactly one durable platform step."""

    if type(request) is not CollectBoxPlatformClaimRequest:
        raise TypeError(
            "request must be an exact CollectBoxPlatformClaimRequest"
        )
    if post is None:
        from modules.miaoshou.client import post_open

        post = post_open
    if not callable(post):
        raise TypeError("post must be callable")
    if not callable(wait):
        raise TypeError("wait must be callable")
    with _CLAIM_SERIAL_LOCK:
        result = _claim_one_platform(
            common_detail_id=int(request.common_detail_id),
            platform=request.platform,
            post=post,
            wait=wait,
        )
    return PlatformClaimReceipt(
        request_digest=request.request_digest,
        common_detail_identity_digest=request.common_detail_identity_digest,
        result=result,
    )


def claim_common_collectbox(
    request: CollectBoxClaimRequest,
    *,
    post: PostCallable | None = None,
    wait: WaitCallable = time.sleep,
) -> CollectBoxClaimReceipt:
    """Claim one common detail to TikTok and Shopee, serially.

    The shared lock prevents concurrent account calls from this service.  The
    control plane owns durable spacing between different platform invocations.
    This service sleeps only before the one allowed retry of the same platform
    after the exact ``accountApiQpsRateLimit`` business rejection.
    """

    if type(request) is not CollectBoxClaimRequest:
        raise TypeError("request must be an exact CollectBoxClaimRequest")
    if post is None:
        from modules.miaoshou.client import post_open

        post = post_open
    if not callable(post):
        raise TypeError("post must be callable")
    if not callable(wait):
        raise TypeError("wait must be callable")

    with _CLAIM_SERIAL_LOCK:
        results = tuple(
            _claim_one_platform(
                common_detail_id=int(request.common_detail_id),
                platform=platform,
                post=post,
                wait=wait,
            )
            for platform in request.platforms
        )
    return CollectBoxClaimReceipt(
        request_digest=request.request_digest,
        common_detail_identity_digest=request.common_detail_identity_digest,
        platform_results=results,  # type: ignore[arg-type]
    )
