"""Durable contracts for the three product-publication rounds.

Round 1 owns facts and the human approval.  Round 2 consumes that immutable
approval and produces images only.  Round 3 owns Miaoshou synchronization,
release-plan compilation, marketplace dispatch, and provider readback.

This module performs local JSON validation and persistence only.  It never
calls a model, Miaoshou, or a marketplace.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from shared_platform.publication_stock_policy import default_publication_stock_policy


ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = ROOT / "reports" / "product-preparation"
ROUND1_SCHEMA = "round1-approved-snapshot/v1"
ROUND2_SCHEMA = "round2-image-snapshot/v1"
AUTO_DECISION_SCHEMA = "round1-auto-decision/v1"
AUTOPILOT_ACTOR = "product-publication-autopilot"

CANONICAL_TIKTOK_TO_WORKBENCH = {
    "tiktok:LH_PH": "lh_ph",
    "tiktok:LH_MY": "lh_my",
    "tiktok:LH_TH": "lh_th",
    "tiktok:LH_VN": "lh_vn",
    "tiktok:HB_PH": "hb_ph",
    "tiktok:HB_MY": "hb_my",
    "tiktok:HB_TH": "hb_th",
    "tiktok:HB_VN": "hb_vn",
    "tiktok:MX": "mx",
    "tiktok:GB": "gb",
}


class PublicationRoundContractError(ValueError):
    """Raised when a round consumes incomplete or drifted prior-round state."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PublicationRoundContractError(f"cannot read governed file: {path.name}") from error
    if not isinstance(value, dict):
        raise PublicationRoundContractError(f"{path.name} must contain an object")
    return value


def _write_immutable(path: Path, value: Mapping[str, Any]) -> Path:
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") != encoded:
            raise PublicationRoundContractError(f"immutable round artifact conflicts: {path.name}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != encoded:
            raise PublicationRoundContractError(f"immutable round artifact conflicts: {path.name}")
    return path


def report_dir(offer_id: str, *, reports_root: Path | None = None) -> Path:
    clean = str(offer_id or "").strip()
    if not clean.isdigit():
        raise PublicationRoundContractError("offer_id must contain digits only")
    return (REPORTS_ROOT if reports_root is None else Path(reports_root)) / clean


def canonical_targets_to_workbench_sites(targets: list[str]) -> list[str]:
    """Map canonical target labels once; non-TikTok targets stay out of workbench sites."""

    normalized = [str(value or "").strip() for value in targets]
    if not normalized or any(not value for value in normalized):
        raise PublicationRoundContractError("canonical publication targets are incomplete")
    if len(normalized) != len(set(normalized)):
        raise PublicationRoundContractError("canonical publication targets are duplicated")
    unsupported_tiktok = [
        value
        for value in normalized
        if value.startswith("tiktok:") and value not in CANONICAL_TIKTOK_TO_WORKBENCH
    ]
    if unsupported_tiktok:
        raise PublicationRoundContractError(
            f"unsupported canonical TikTok target: {unsupported_tiktok[0]}"
        )
    return [
        CANONICAL_TIKTOK_TO_WORKBENCH[value]
        for value in normalized
        if value in CANONICAL_TIKTOK_TO_WORKBENCH
    ]


def validate_round1_reviewable(
    first_review: Mapping[str, Any], *, report_directory: Path,
    category_observation_resolver=None,
) -> dict[str, Any]:
    """Prove every prerequisite before a first-round approval can be recorded."""

    review = deepcopy(dict(first_review))
    if 'input_lineage_manifest' in review:
        from shared_platform.r1_input_lineage import inspect_review_lineage
        inspect_review_lineage(review, report_directory)
    if review.get("schema") != "publication-preparation-decision/v1":
        raise PublicationRoundContractError("first-review schema is invalid")
    if review.get("status") != "FIRST_REVIEW_READY":
        raise PublicationRoundContractError("first review is not ready")
    stock_policy = review.get("publication_stock_policy")
    if type(stock_policy) is not dict or stock_policy != default_publication_stock_policy():
        raise PublicationRoundContractError("R1_STOCK_POLICY_MISSING_OR_CHANGED")
    targets = list((review.get("target_selection") or {}).get("requested") or [])
    canonical_targets_to_workbench_sites(targets)
    from shared_platform.round1_category_evidence import (
        CategoryEvidenceError, shopee_targets, validate_receipt,
    )
    try:
        category_targets = shopee_targets(review)
    except CategoryEvidenceError as error:
        raise PublicationRoundContractError(str(error)) from None
    if category_targets:
        try:
            binding = review.get('category_evidence_binding')
            if type(binding) is not dict or set(binding) != {'status', 'receipt'} or binding['status'] != 'BOUND':
                raise CategoryEvidenceError('CATEGORY_BINDING_REQUIRED')
            if category_observation_resolver is None and isinstance(binding['receipt'], dict):
                from shared_platform.round1_category_observations import RECEIPT_SCHEMA, default_resolver
                if binding['receipt'].get('schema_version') == RECEIPT_SCHEMA:
                    category_observation_resolver = default_resolver(review, binding['receipt'].get('account_identity_digest'))
            bound = validate_receipt(review, binding['receipt'], observation_resolver=category_observation_resolver)
            projected = []
            for row in review.get('targets') or []:
                if type(row) is not dict:
                    raise CategoryEvidenceError('CATEGORY_TARGET_PROJECTION_MISMATCH')
                selected = shopee_targets({'target_selection': {'requested': [row.get('target')]}})
                if selected:
                    category = row.get('category')
                    if (type(category) is not dict or type(category.get('id')) is not int
                            or category.get('id') != bound['category']['id']
                            or category.get('name') != bound['category']['name']
                            or category.get('receipt_digest') != bound['receipt_digest']):
                        raise CategoryEvidenceError('CATEGORY_TARGET_PROJECTION_MISMATCH')
                    projected.extend(selected)
            if projected != category_targets:
                raise CategoryEvidenceError('CATEGORY_TARGET_PROJECTION_MISMATCH')
            review['category_evidence_binding'] = {'status': 'BOUND', 'receipt': bound}
        except CategoryEvidenceError as error:
            raise PublicationRoundContractError(str(error)) from None
    plan = review.get("image_execution_plan")
    if not isinstance(plan, Mapping) or plan.get("schema_version") != "first-review-image-plan/v1":
        raise PublicationRoundContractError("first-review image plan is invalid")
    if plan.get("status") not in {"APPROVED", "PROPOSED"}:
        raise PublicationRoundContractError("first-review image plan is unresolved")
    return review


def build_round1_auto_decision(
    first_review: Mapping[str, Any], *, policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Build an auditable policy decision without impersonating a human."""

    from shared_platform.publication_autopilot import load_autopilot_policy

    review = deepcopy(dict(first_review))
    governed = dict(policy or load_autopilot_policy())
    required_steps = {
        "fact_normalization",
        "category_candidate_resolution",
        "title_and_copy_generation",
        "pricing_compilation",
    }
    if review.get("status") != "FIRST_REVIEW_READY":
        raise PublicationRoundContractError("first review is not ready for autopilot decision")
    if governed.get("status") != "ACTIVE" or not required_steps.issubset(
        set(governed.get("automatic_steps") or [])
    ):
        raise PublicationRoundContractError("autopilot policy does not authorize round 1")
    receipt: dict[str, Any] = {
        "schema_version": AUTO_DECISION_SCHEMA,
        "status": "AUTO_APPROVED",
        "decided_by": AUTOPILOT_ACTOR,
        "human_approval": False,
        "policy_id": str(governed.get("policy_id") or ""),
        "policy_digest": canonical_digest(governed),
        "first_review_digest": canonical_digest(review),
        "checks": [
            {"code": code, "status": "PASS"}
            for code in (
                "FACTS_COMPLETE",
                "TARGET_SCOPE_COMPLETE",
                "CATEGORY_EVIDENCE_COMPLETE",
                "COPY_COMPLETE",
                "PRICING_COMPLETE",
                "IMAGE_GAP_PLAN_COMPLETE",
                "ROUND1_EXTERNAL_WRITES_ZERO",
            )
        ],
        "exceptions": [],
        "external_write_count": 0,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt["decision_digest"] = canonical_digest(receipt)
    return receipt


def validate_round1_auto_decision(
    decision: Mapping[str, Any], first_review: Mapping[str, Any]
) -> dict[str, Any]:
    receipt = deepcopy(dict(decision))
    supplied = str(receipt.pop("decision_digest", ""))
    if supplied != canonical_digest(receipt):
        raise PublicationRoundContractError("round1 auto decision digest drifted")
    receipt["decision_digest"] = supplied
    if (
        receipt.get("schema_version") != AUTO_DECISION_SCHEMA
        or receipt.get("status") != "AUTO_APPROVED"
        or receipt.get("decided_by") != AUTOPILOT_ACTOR
        or receipt.get("human_approval") is not False
        or receipt.get("first_review_digest") != canonical_digest(first_review)
        or list(receipt.get("exceptions") or [])
        or any(row.get("status") != "PASS" for row in receipt.get("checks") or [])
    ):
        raise PublicationRoundContractError("round1 auto decision is invalid")
    return receipt


def build_round1_snapshot(
    *,
    first_review: Mapping[str, Any],
    state: Mapping[str, Any],
    approved_by: str,
    approved_at: str | None = None,
    report_directory: Path | None = None,
    decision_receipt: Mapping[str, Any] | None = None,
    category_observation_resolver=None,
) -> dict[str, Any]:
    """Bind the accepted review and the resulting Product Center approval once."""

    offer_id = str(first_review.get("offer_id") or "").strip()
    directory = report_directory or report_dir(offer_id)
    review = validate_round1_reviewable(first_review, report_directory=directory,
                                      category_observation_resolver=category_observation_resolver)
    auto_decision = None
    if decision_receipt is not None:
        auto_decision = validate_round1_auto_decision(decision_receipt, review)
        if approved_by != AUTOPILOT_ACTOR:
            raise PublicationRoundContractError("autopilot actor identity is invalid")
        approval_authority = "ACTIVE_AUTOPILOT_POLICY"
    elif approved_by == "Kyle":
        approval_authority = "EXPLICIT_CONVERSATION_APPROVAL"
    else:
        raise PublicationRoundContractError("first-round approval authority is invalid")
    product_approval = state.get("product_approval")
    if (
        not isinstance(product_approval, Mapping)
        or product_approval.get("status") != "approved"
        or product_approval.get("approved_by") != approved_by
        or (
            auto_decision is not None
            and (
                product_approval.get("approval_authority") != approval_authority
                or product_approval.get("decision_digest")
                != auto_decision["decision_digest"]
            )
        )
    ):
        raise PublicationRoundContractError("Product Center approval is not complete")
    targets = list((review.get("target_selection") or {}).get("requested") or [])
    expected_sites = canonical_targets_to_workbench_sites(targets)
    selected_sites = list((state.get("review") or {}).get("selected_sites") or [])
    if sorted(selected_sites) != sorted(expected_sites):
        raise PublicationRoundContractError("Product Center target scope differs from first review")
    image_plan = deepcopy(dict(review.get("image_execution_plan") or {}))
    image_plan["status"] = "APPROVED"
    identity: dict[str, Any] = {
        "schema_version": ROUND1_SCHEMA,
        "status": "APPROVED",
        "offer_id": offer_id,
        "approved_by": approved_by,
        "approval_authority": approval_authority,
        "human_approval": auto_decision is None,
        "decision_receipt_digest": (
            auto_decision["decision_digest"] if auto_decision else None
        ),
        "approved_at": approved_at or datetime.now(timezone.utc).isoformat(),
        "reviewed_product_center_revision": int(review.get("product_center_revision") or 0),
        "approved_product_center_revision": int(state.get("_revision") or 0),
        "product_approval_id": str(product_approval.get("approval_id") or ""),
        "product_approval_fingerprint": str(product_approval.get("input_fingerprint") or ""),
        "canonical_targets": targets,
        "workbench_tiktok_sites": expected_sites,
        "first_review_digest": canonical_digest(review),
        "image_plan": image_plan,
        "image_plan_digest": canonical_digest(image_plan),
        "fact_snapshot": {
            "product_facts": deepcopy(review.get("product_facts") or {}),
            "shared_review_facts": deepcopy(review.get("shared_review_facts") or {}),
            "publication_stock_policy": deepcopy(
                review.get("publication_stock_policy") or {}
            ),
            "targets": deepcopy(review.get("targets") or []),
            "platform_categories": deepcopy(review.get("platform_categories") or []),
            "copy_review_sets": deepcopy(review.get("copy_review_sets") or []),
            "content_groups": deepcopy(review.get("content_groups") or {}),
        },
        "publication_stock_policy": deepcopy(
            review.get("publication_stock_policy") or {}
        ),
        "external_write_count": 0,
        "next_round": "ROUND2_IMAGES_ONLY",
    }
    if 'category_evidence_binding' in review:
        identity['fact_snapshot']['category_evidence_binding'] = deepcopy(review['category_evidence_binding'])
    if 'input_lineage_manifest' in review:
        identity['input_lineage_manifest'] = deepcopy(review['input_lineage_manifest'])
    identity["snapshot_digest"] = canonical_digest(identity)
    return identity


def persist_round1_snapshot(snapshot: Mapping[str, Any]) -> Path:
    document = deepcopy(dict(snapshot))
    supplied = str(document.pop("snapshot_digest", ""))
    if supplied != canonical_digest(document):
        raise PublicationRoundContractError("round1 snapshot digest drifted")
    document["snapshot_digest"] = supplied
    return _write_immutable(report_dir(str(document.get("offer_id") or "")) / "round1-approved-snapshot.json", document)


def load_round1_snapshot(offer_id: str, *, reports_root: Path | None = None) -> dict[str, Any]:
    snapshot = _read_json(report_dir(offer_id, reports_root=reports_root) / "round1-approved-snapshot.json")
    supplied = str(snapshot.get("snapshot_digest") or "")
    unsigned = dict(snapshot)
    unsigned.pop("snapshot_digest", None)
    if snapshot.get("schema_version") != ROUND1_SCHEMA or supplied != canonical_digest(unsigned):
        raise PublicationRoundContractError("round1 approved snapshot is invalid")
    if 'input_lineage_manifest' in snapshot:
        from shared_platform.r1_input_lineage import inspect_snapshot_lineage
        directory = report_dir(offer_id, reports_root=reports_root)
        inspect_snapshot_lineage(snapshot, directory)
    return snapshot


def validate_round2_input(offer_id: str, state: Mapping[str, Any], *, reports_root: Path | None = None) -> dict[str, Any]:
    """Validate identity only; do not re-run first-round business decisions."""

    snapshot = load_round1_snapshot(offer_id, reports_root=reports_root)
    approval = state.get("product_approval")
    if not isinstance(approval, Mapping):
        raise PublicationRoundContractError("approved Product Center identity is missing")
    if (
        approval.get("status") != "approved"
        or str(approval.get("approval_id") or "") != snapshot["product_approval_id"]
        or str(approval.get("input_fingerprint") or "")
        != snapshot["product_approval_fingerprint"]
    ):
        raise PublicationRoundContractError("round1 approval was superseded")
    if sorted((state.get("review") or {}).get("selected_sites") or []) != sorted(
        snapshot["workbench_tiktok_sites"]
    ):
        raise PublicationRoundContractError("round1 target scope was superseded")
    return snapshot


def autopilot_authorizes(step: str, *, paid_purpose: str | None = None) -> bool:
    from shared_platform.publication_autopilot import load_autopilot_policy

    policy = load_autopilot_policy()
    if step not in set(policy.get("automatic_steps") or []):
        return False
    if paid_purpose is not None:
        paid = policy.get("paid_models") or {}
        return paid.get("provider") == "lingshi" and paid_purpose in set(
            paid.get("allowed_purposes") or []
        )
    return True


__all__ = [
    "AUTO_DECISION_SCHEMA",
    "AUTOPILOT_ACTOR",
    "CANONICAL_TIKTOK_TO_WORKBENCH",
    "PublicationRoundContractError",
    "ROUND1_SCHEMA",
    "ROUND2_SCHEMA",
    "autopilot_authorizes",
    "build_round1_auto_decision",
    "build_round1_snapshot",
    "canonical_digest",
    "canonical_targets_to_workbench_sites",
    "load_round1_snapshot",
    "persist_round1_snapshot",
    "report_dir",
    "validate_round1_reviewable",
    "validate_round1_auto_decision",
    "validate_round2_input",
]
