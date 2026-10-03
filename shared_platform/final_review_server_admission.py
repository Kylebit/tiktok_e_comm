"""Fail-closed policy seam for the one post-COMMON final decision.

This module neither authenticates Windows users nor issues receipts.  The
production constructor has no receipt source and therefore cannot approve.
Only a future service-owned, OS-authenticated channel may install a source
whose writer and atomic consume operation live outside ordinary HTTP/CLI.
In particular, loopback, Host, Origin, CSRF, approved_by and user_approved are
not operator identity.  The caller of this seam must rebuild FrozenReview from
domain-owned state, never from the request body.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
import hashlib
import json
import time


class ApprovalBlocked(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FrozenReview:
    offer_id: str
    plan_id: str
    candidate_digest: str
    critical_content_digest: str
    targets: tuple[str, ...]
    common_plan_id: str
    common_run_id: str
    common_readback_digest: str
    common_budget_digest: str
    round1_digest: str
    round2_digest: str
    action_id: str

    def __post_init__(self):
        for field in fields(self):
            if field.name == "targets":
                continue
            value = getattr(self, field.name)
            if (not isinstance(value, str) or not value or len(value) > 256
                    or value != value.strip() or any(ord(char) < 32 for char in value)):
                raise ApprovalBlocked("FROZEN_REVIEW_INVALID")
        if (not isinstance(self.targets, tuple) or not self.targets
                or len(self.targets) > 64 or len(set(self.targets)) != len(self.targets)
                or any(not isinstance(value, str) or not value or len(value) > 256
                       or value != value.strip() or any(ord(char) < 32 for char in value)
                       for value in self.targets)):
            raise ApprovalBlocked("FROZEN_REVIEW_INVALID")

    def digest(self) -> str:
        material = {field.name: getattr(self, field.name) for field in fields(self)}
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class TrustedReceipt:
    """Only a value returned from a server-owned receipt source is meaningful.

    A caller-created instance, including one with an SID-looking subject, is
    never an authentication result.  No HTTP route may accept this value.
    """

    receipt_id: str
    binding: FrozenReview
    operator_subject: str
    issued_at_epoch: int
    expires_at_epoch: int
    decision: str


class DurableAtomicReceiptSource(ABC):
    """OS-authenticated server source; the implementation needs independent audit.

    The source must persist receipts and implement consume_exact as one durable
    compare-and-swap against receipt id, binding digest, expiry and unused state.
    It must read its own trusted clock at the atomic commit boundary; no time
    value provided by this policy layer or an HTTP caller can establish TTL.
    Subclassing only asserts an interface; it cannot itself prove OS identity,
    atomicity, or cross-store transaction safety.  There is no production
    implementation in this package.
    """

    @abstractmethod
    def read_pending(self, receipt_id: str) -> TrustedReceipt | None:
        raise NotImplementedError

    @abstractmethod
    def consume_exact(self, receipt_id: str, binding_digest: str) -> TrustedReceipt | None:
        raise NotImplementedError


def reject_http_approval(_untrusted_payload: object) -> None:
    """The legacy HTTP/CLI approval surface has no identity-bearing capability."""
    raise ApprovalBlocked("TRUSTED_CHANNEL_REQUIRED")


class FinalApprovalAdmission:
    def __init__(self, receipt_source: DurableAtomicReceiptSource | None = None):
        if receipt_source is not None and not isinstance(receipt_source, DurableAtomicReceiptSource):
            raise ApprovalBlocked("TRUSTED_RECEIPT_SOURCE_INVALID")
        self._source = receipt_source

    def accept_receipt(self, receipt_id: str, current: FrozenReview) -> TrustedReceipt:
        """Consume one exact decision; caller must persist domain approval safely.

        The future integration must couple this consume with ReleaseStore's
        durable approval, or reconcile an interrupted consume before retry.
        This function does not itself make a candidate executable.
        """
        if self._source is None:
            raise ApprovalBlocked("TRUSTED_CHANNEL_UNAVAILABLE")
        if (not isinstance(receipt_id, str) or not receipt_id
                or not isinstance(current, FrozenReview)):
            raise ApprovalBlocked("FROZEN_REVIEW_INVALID")
        try:
            pending = self._source.read_pending(receipt_id)
        except Exception:
            raise ApprovalBlocked("TRUSTED_RECEIPT_SOURCE_FAILED") from None
        if not isinstance(pending, TrustedReceipt):
            raise ApprovalBlocked("RECEIPT_UNAVAILABLE_OR_USED")
        if pending.receipt_id != receipt_id or pending.binding != current:
            raise ApprovalBlocked("FROZEN_REVIEW_CHANGED")
        if (not isinstance(pending.operator_subject, str)
                or not pending.operator_subject or len(pending.operator_subject) > 256):
            raise ApprovalBlocked("TRUSTED_IDENTITY_INVALID")
        if pending.decision != "approve":
            raise ApprovalBlocked("APPROVAL_DECISION_REQUIRED")
        # read_pending may block. Never use a timestamp sampled before it.
        try:
            now_epoch = int(time.time())
        except Exception:
            raise ApprovalBlocked("TRUSTED_CLOCK_UNAVAILABLE") from None
        if (type(pending.issued_at_epoch) is not int
                or type(pending.expires_at_epoch) is not int
                or pending.expires_at_epoch <= pending.issued_at_epoch
                or pending.expires_at_epoch - pending.issued_at_epoch > 300
                or not pending.issued_at_epoch <= now_epoch < pending.expires_at_epoch):
            raise ApprovalBlocked("RECEIPT_EXPIRED")
        try:
            claimed = self._source.consume_exact(receipt_id, current.digest())
        except Exception:
            raise ApprovalBlocked("TRUSTED_RECEIPT_SOURCE_FAILED") from None
        if claimed is None:
            raise ApprovalBlocked("RECEIPT_UNAVAILABLE_OR_USED")
        if claimed != pending:
            raise ApprovalBlocked("RECEIPT_CONCURRENTLY_CHANGED")
        # A source is required to enforce TTL in its own atomic transaction;
        # this final defensive check prevents a faulty source from handing a
        # post-expiry receipt to the domain approval writer.
        try:
            if int(time.time()) >= claimed.expires_at_epoch:
                raise ApprovalBlocked("RECEIPT_EXPIRED_AFTER_CONSUMPTION")
        except ApprovalBlocked:
            raise
        except Exception:
            raise ApprovalBlocked("TRUSTED_CLOCK_UNAVAILABLE") from None
        return claimed


@dataclass(frozen=True)
class ExecutionComparison:
    disposition: str
    authorization: bool = False


def compare_before_execution(approved: FrozenReview, current: FrozenReview,
                             *, execution_blockers: tuple[str, ...] = ()) -> ExecutionComparison:
    """Return a server-side comparison, never an execution grant.

    A new review is required only for business-critical content or scope drift.
    An updated container/provenance with the same critical content needs a
    server-owned rebind, while transient blockers preserve the old decision.
    """
    if not isinstance(approved, FrozenReview) or not isinstance(current, FrozenReview):
        raise ApprovalBlocked("FROZEN_REVIEW_INVALID")
    if (approved.offer_id != current.offer_id
            or approved.critical_content_digest != current.critical_content_digest
            or approved.targets != current.targets
            or approved.common_readback_digest != current.common_readback_digest
            or approved.common_budget_digest != current.common_budget_digest
            or approved.round1_digest != current.round1_digest
            or approved.round2_digest != current.round2_digest):
        return ExecutionComparison("NEW_FINAL_REVIEW_REQUIRED")
    if (not isinstance(execution_blockers, tuple)
            or any(not isinstance(value, str) or not value for value in execution_blockers)):
        raise ApprovalBlocked("EXECUTION_BLOCKERS_INVALID")
    if execution_blockers:
        return ExecutionComparison("EXECUTION_BLOCKED_APPROVAL_PRESERVED")
    if approved != current:
        return ExecutionComparison("NONCRITICAL_REBIND_REQUIRED")
    return ExecutionComparison("MATCH_ONLY")
