"""Read-only admission for the continuation HTTP contract.

No runner, queue or provider is registered here. A successful admission is not
execution authority: the server must also claim the existing ledger and obtain
its domain lock. The standalone endpoint helper remains a diagnostic response.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from shared_platform.tiktok_lineage_recovery import (
    TIKTOK_TARGET_ORDER,
    assert_manifest_matches_approved_snapshot,
    validate_tiktok_lineage_recovery_manifest,
)


class ContinuationAdmissionError(ValueError):
    pass


def _digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _sha(value):
    if not isinstance(value, str):
        raise ContinuationAdmissionError("digest is required")
    value = value.removeprefix("sha256:")
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ContinuationAdmissionError("invalid digest")
    return "sha256:" + value


def load_scope_policy(path: Path, *, expected_sha256: str, root: Path):
    """Read an explicitly pinned local policy; never create one from an HTTP body."""
    root = Path(root).resolve(strict=True)
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or path.is_symlink():
        raise ContinuationAdmissionError("scope policy path is not trusted")
    raw = resolved.read_bytes()
    if "sha256:" + hashlib.sha256(raw).hexdigest() != _sha(expected_sha256):
        raise ContinuationAdmissionError("scope policy file identity differs")
    policy = json.loads(raw)
    fields = {"schema_version", "lineage", "approved_target_labels",
              "retired_target_labels", "authorization_evidence_digest",
              "manifest_digest", "policy_digest"}
    if not isinstance(policy, dict) or set(policy) != fields:
        raise ContinuationAdmissionError("scope policy fields differ")
    body = {k: v for k, v in policy.items() if k != "policy_digest"}
    if policy["schema_version"] != "tiktok-continuation-scope-policy/v1" or policy["policy_digest"] != _digest(body):
        raise ContinuationAdmissionError("scope policy integrity differs")
    return policy


def validate_request_shape(data):
    required = {"offer_id", "plan_id", "snapshot_digest", "candidate_digest", "continuation_manifest"}
    if not isinstance(data, Mapping) or not required <= set(data) or set(data) - required - {"retry_of_run_id"}:
        raise ContinuationAdmissionError("request fields differ; client policy and allowlists are forbidden")
    offer = data['offer_id']
    if type(offer) is not str or not offer.isascii() or not offer.isdigit() or not 1 <= len(offer) <= 20:
        raise ContinuationAdmissionError("offer identity must be numeric")
    if (type(data['plan_id']) is not str or not 1 <= len(data['plan_id']) <= 200
            or any(c in data['plan_id'] for c in ('/', '\\', '\x00', '\n', '\r'))):
        raise ContinuationAdmissionError("plan identity is invalid")
    retry = data.get('retry_of_run_id')
    if retry is not None and (type(retry) is not str or not 1 <= len(retry) <= 128
            or not retry[0].isascii() or not retry[0].isalnum()
            or any(not c.isascii() or not (c.isalnum() or c in '._-') for c in retry)):
        raise ContinuationAdmissionError('retry identity is invalid')
    _sha(data['snapshot_digest']); _sha(data['candidate_digest'])
    return validate_tiktok_lineage_recovery_manifest(data['continuation_manifest'])


def validate_admission(data: Mapping, *, completion: bool, policy: Mapping,
                       snapshot: Mapping, candidate: Mapping, approval: Mapping,
                       asset_digest_reader):
    """Bind the entire request to trusted, already persisted authority.

    Callers supply only server-loaded policy and approved documents. An absent
    policy is an evidence gap, not an empty retired-target list.
    """
    manifest = validate_request_shape(data)
    is_completion = manifest.get("operation_kind") == "approved_first_completion"
    if is_completion != completion:
        raise ContinuationAdmissionError("manifest does not match endpoint")
    lineage = manifest["lineage"]
    for field in ("offer_id", "plan_id"):
        if data[field] != lineage[field] or snapshot[field] != lineage[field]:
            raise ContinuationAdmissionError("request and frozen identity differ")
    for field in ("snapshot_digest", "candidate_digest"):
        if _sha(data[field]) != _sha(lineage[field]):
            raise ContinuationAdmissionError("request digest differs")
    if (_sha(snapshot["snapshot_digest"]) != _sha(lineage["snapshot_digest"])
            or str(snapshot["product_revision"]) != str(lineage["revision"])
            or _sha(candidate["candidate_digest"]) != _sha(lineage["candidate_digest"])
            or _sha(approval["approval_digest"]) != _sha(lineage["approval_digest"])):
        raise ContinuationAdmissionError("persisted approval identity differs")
    if not isinstance(policy, Mapping):
        raise ContinuationAdmissionError("server scope policy is unavailable")
    if (policy.get("schema_version") != "tiktok-continuation-scope-policy/v1"
            or policy.get("policy_digest") != _digest({k: v for k, v in policy.items() if k != "policy_digest"})):
        raise ContinuationAdmissionError("server scope policy integrity differs")
    expected_lineage = {k: lineage[k] for k in (
        "offer_id", "revision", "plan_id", "snapshot_digest", "candidate_digest", "approval_digest")}
    if (policy.get("lineage") != expected_lineage
            or policy.get("manifest_digest") != manifest["manifest_digest"]
            or policy.get("authorization_evidence_digest") != manifest.get("authority_addendum_digest", manifest.get("authority_receipt_digest"))
            or policy.get("approved_target_labels") != list(TIKTOK_TARGET_ORDER)):
        raise ContinuationAdmissionError("server scope policy does not bind this approved request")
    retired = policy.get("retired_target_labels")
    if (not isinstance(retired, list) or any(not isinstance(v, str) for v in retired)
            or len(set(retired)) != len(retired) or set(retired) - set(TIKTOK_TARGET_ORDER)):
        raise ContinuationAdmissionError("retired target evidence is invalid")
    selected = manifest.get("completion_target_labels") if completion else manifest.get("recovery_target_labels")
    if set(selected) & set(retired):
        raise ContinuationAdmissionError("request contains retired targets")
    retry = manifest.get("zero_write_retry")
    if data.get("retry_of_run_id") != (retry.get("source_run_id") if retry else None):
        raise ContinuationAdmissionError("retry source identity differs")
    from shared_platform.publication_autopilot import (
        validate_release_candidate_for_execution, validate_final_approval_receipt,
    )
    candidate_order = tuple(label for label in candidate.get('target_labels', ())
                            if label.split(':', 1)[0].lower() == 'tiktok')
    if set(candidate_order) != set(TIKTOK_TARGET_ORDER) or len(candidate_order) != len(TIKTOK_TARGET_ORDER):
        raise ContinuationAdmissionError('persisted candidate lacks the complete approved scope')
    checked_candidate = validate_release_candidate_for_execution(
        candidate, snapshot=snapshot, platform_scope=("TIKTOK",),
        target_labels=candidate_order)
    validate_final_approval_receipt(approval, checked_candidate, snapshot=snapshot)
    # Check every target before exposing an admissible scope. This read-only
    # adapter must be pinned by the host; no implicit network fallback exists.
    assert_manifest_matches_approved_snapshot(manifest, snapshot, asset_digest_reader=asset_digest_reader)
    return {"manifest_digest": manifest["manifest_digest"],
            "policy_digest": policy["policy_digest"],
            "approved_target_labels": list(TIKTOK_TARGET_ORDER),
            "selected_target_labels": list(selected), "execution_authority": False,
            "source_retry_verification": "NOT_CHECKED_BY_ADMISSION" if retry else "NOT_REQUESTED"}


def endpoint(data, *, completion: bool, context=None):
    """HTTP-compatible admission preview; deliberately never returns 202."""
    if context is None:
        return 503, {"ok": False, "code": "CONTINUATION_ADMISSION_NOT_CONFIGURED",
                     "external_write_count": 0, "execution_authority": False}
    try:
        # Context is a host-owned reader. Body cannot select paths or providers.
        validate_request_shape(data)
        trusted = context(data)
        result = validate_admission(data, completion=completion, **trusted)
    except (ValueError, KeyError, TypeError, OSError):
        return 409, {"ok": False, "code": "CONTINUATION_ADMISSION_REJECTED",
                     "external_write_count": 0, "execution_authority": False}
    return 503, {"ok": False, "code": "CONTINUATION_EXECUTION_ADAPTER_NOT_AVAILABLE",
                 "admission": result, "external_write_count": 0,
                 "execution_authority": False, "queued": False}
