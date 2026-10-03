"""Canonical error classification and bounded recovery policy."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "publication_error_policy.json"


class PublicationErrorPolicyError(ValueError):
    pass


def load_publication_error_policy(path: Path | None = None) -> dict[str, Any]:
    value = json.loads((path or POLICY_PATH).read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "publication-error-policy/v1"
        or value.get("status") != "ACTIVE"
        or not isinstance(value.get("classes"), dict)
    ):
        raise PublicationErrorPolicyError("publication error policy is invalid")
    required = {
        "LOCAL_VALIDATION", "TRANSIENT_ZERO_WRITE", "PROCESSING",
        "UNKNOWN_WRITE", "AUTH_OR_CAPABILITY", "PROVIDER_REJECTED",
    }
    if set(value["classes"]) != required:
        raise PublicationErrorPolicyError("publication error classes are incomplete")
    for name, row in value["classes"].items():
        if not isinstance(row, Mapping):
            raise PublicationErrorPolicyError(f"{name} policy is invalid")
        for key in (
            "automatic_repair_attempts",
            "automatic_retry_attempts",
            "readback_reconciliation_attempts",
        ):
            if type(row.get(key)) is not int or row[key] < 0:
                raise PublicationErrorPolicyError(f"{name} retry budget is invalid")
        if not str(row.get("next_action") or ""):
            raise PublicationErrorPolicyError(f"{name} next action is invalid")
    return deepcopy(value)


def classify_publication_error(
    *,
    stage: str,
    reason_category: str = "",
    provider_code: str = "",
    external_write_count: int | None,
    outcome_unknown: bool = False,
    status: str = "FAILED",
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    governed = dict(policy or load_publication_error_policy())
    category = str(reason_category or "").upper()
    code = str(provider_code or "").casefold()
    normalized_status = str(status or "").upper()
    if outcome_unknown or external_write_count is None:
        class_name = "UNKNOWN_WRITE"
    elif normalized_status == "PROCESSING":
        class_name = "PROCESSING"
    elif category in {"AUTH", "CAPABILITY", "INVENTORY"}:
        class_name = "AUTH_OR_CAPABILITY"
    elif int(external_write_count) == 0 and category in {
        "CONTENT", "LOGISTICS", "PRE_SUBMIT", "SYSTEMIC_CONTRACT",
    }:
        class_name = "LOCAL_VALIDATION"
    elif int(external_write_count) == 0 and any(
        marker in code for marker in ("timeout", "rate_limit", "temporar", "unavailable")
    ):
        class_name = "TRANSIENT_ZERO_WRITE"
    else:
        class_name = "PROVIDER_REJECTED"
    rules = dict(governed["classes"][class_name])
    if rules.get("requires_zero_confirmed_writes") is True and external_write_count != 0:
        class_name = "UNKNOWN_WRITE"
        rules = dict(governed["classes"][class_name])
    return {
        "schema_version": "publication-error-classification/v1",
        "policy_id": governed["policy_id"],
        "class": class_name,
        "stage": str(stage or "UNKNOWN"),
        "reason_category": category or "UNKNOWN",
        "provider_code": str(provider_code or "")[:120],
        "external_write_count": external_write_count,
        "outcome_unknown": class_name == "UNKNOWN_WRITE",
        **rules,
    }


__all__ = [
    "PublicationErrorPolicyError",
    "classify_publication_error",
    "load_publication_error_policy",
]
