"""Fail closed for registered R3 offers entering the older publication runner.

The legacy request carries a frozen snapshot and perhaps an old final receipt,
but it cannot carry the current post-COMMON admission and its offer-wide budget
reservation. No legacy approval can be upgraded into that authority here.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.config import ROOT
from shared_platform import publication_r2_review


BLOCKED_CODE = "REGISTERED_R3_PUBLISH_ADMISSION_BLOCKED"


class RegisteredR3PublishAdmissionBlocked(ValueError):
    code = BLOCKED_CODE

    def __init__(self):
        super().__init__(BLOCKED_CODE)


def require_legacy_publish_admission(offer_id: str, *, release_store=None,
                                     plan_id: str | None = None) -> None:
    """Permit only offers outside the registered R3 lifecycle.

    Registry lookup errors also block: an unknown lifecycle is not evidence
    that the old writer is authorized. A future R3 writer must verify and bind
    the current admission independently; it must not relax this old path.
    """
    try:
        registered = publication_r2_review.has_registration(
            offer_id, runtime_root=publication_r2_review.review_runtime_root(ROOT),
        )
    except (OSError, TypeError, ValueError) as error:
        raise RegisteredR3PublishAdmissionBlocked() from error
    if registered:
        raise RegisteredR3PublishAdmissionBlocked()
    # The immutable plan is a second, independent R3 marker. A missing local
    # registry file must not turn that same approved plan into a legacy writer.
    if (release_store is not None and plan_id is not None
            and callable(getattr(release_store, "get_plan", None))):
        try:
            plan = release_store.get_plan(plan_id)
        except Exception as error:
            raise RegisteredR3PublishAdmissionBlocked() from error
        if isinstance(plan, Mapping):
            payload = plan.get("payload")
            if (plan.get("product_id") == offer_id and isinstance(payload, Mapping)
                    and "r3_marketplace_binding" in payload):
                raise RegisteredR3PublishAdmissionBlocked()
