"""Deterministic final-review candidate and write-boundary contracts.

This module performs no provider call. Explicit persist functions write local
immutable candidate and approval receipts; policy loading is read-only. It compiles an
approved v4 snapshot into the one packet that must be reviewed immediately
before marketplace publication.  The packet is also the allow-list consumed
by publication runners and platform executors.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "product_publication_autopilot_policy.json"
INCIDENT_REGISTRY_PATH = (
    ROOT
    / "skills"
    / "publish-approved-product"
    / "references"
    / "incident-registry.json"
)

POLICY_SCHEMA = "autopilot-policy/v1"
INCIDENT_SCHEMA = "publication-incident-registry/v1"
CANDIDATE_SCHEMA = "publication-release-candidate/v1"
SNAPSHOT_SCHEMA = "approved-publication-snapshot/v4"
SUPPORTED_PLATFORMS = ("TIKTOK", "SHOPEE", "OZON")


class PublicationAutopilotContractError(ValueError):
    """Raised when an automation or publication boundary is not exact."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PublicationAutopilotContractError(
            f"cannot read governed JSON: {path.name}"
        ) from error
    if not isinstance(value, dict):
        raise PublicationAutopilotContractError(f"{path.name} must be an object")
    return value


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    return value.strip() if type(value) is str else ""


def _positive_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _quality_snapshot_with_round1_claims(
    snapshot: Mapping[str, Any],
    durable_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project explicitly approved round-1 claim facts for read-only QA."""

    projected = deepcopy(dict(snapshot))
    product = projected.get("product")
    product = dict(product) if isinstance(product, Mapping) else {}
    verified = product.get("verified_claims")
    verified = dict(verified) if isinstance(verified, Mapping) else {}
    from shared_platform.publication_quality_evidence import validate_round1_evidence_identity
    try:
        round1 = validate_round1_evidence_identity(snapshot,durable_evidence)
    except ValueError:
        round1 = None
    human_approved = (
        isinstance(round1, Mapping)
        and round1.get("status") == "APPROVED"
        and round1.get("approved_by") == "Kyle"
    )
    autopilot_approved = (
        isinstance(round1, Mapping)
        and round1.get("status") == "APPROVED"
        and round1.get("approved_by") == "product-publication-autopilot"
        and round1.get("approval_authority") == "ACTIVE_AUTOPILOT_POLICY"
        and round1.get("human_approval") is False
    )
    if human_approved or autopilot_approved:
        facts = round1.get("fact_snapshot")
        facts = facts.get("product_facts") if isinstance(facts, Mapping) else None
        title = str(facts.get("title") or "") if isinstance(facts, Mapping) else ""
        supported_terms = {
            "self_adhesive": ("自粘", "背部带胶"),
            "waterproof": ("防水",),
            "removable": ("可移除", "可移"),
        }
        for claim, terms in supported_terms.items():
            clauses = [clause for clause in re.split(r"[，。；;\n]", title)
                       if any(term in clause for term in terms)]
            # Compatibility for a bound legacy title is deliberately narrow.
            # A questioned, pending, negative or attributed claim is not a fact,
            # irrespective of whether its qualifier precedes or follows the term.
            qualified = any(re.search(
                r"不|非|未|无|是否|待|可能|预计|据称|声称|宣称|疑似|[？?]", clause
            ) for clause in clauses)
            if clauses and not qualified and claim not in verified:
                verified[claim] = {
                    "value": True,
                    "fact_verified": True,
                    "evidence_level": "round1_approved_product_fact",
                    "approved_by": str(round1.get("approved_by") or ""),
                    "source": "round1_approved_snapshot_projection",
                }
    product["verified_claims"] = verified
    projected["product"] = product
    return projected


def _platform(value: object) -> str:
    platform = _text(value).upper()
    if platform not in SUPPORTED_PLATFORMS:
        raise PublicationAutopilotContractError("unsupported publication platform")
    return platform


def validate_autopilot_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    policy = deepcopy(dict(value))
    if policy.get("schema_version") != POLICY_SCHEMA:
        raise PublicationAutopilotContractError("autopilot policy schema is invalid")
    if policy.get("status") != "ACTIVE":
        raise PublicationAutopilotContractError("autopilot policy is not active")
    steps = policy.get("automatic_steps")
    if (
        not isinstance(steps, list)
        or not steps
        or any(not _text(step) for step in steps)
        or len(steps) != len(set(steps))
    ):
        raise PublicationAutopilotContractError("automatic steps are invalid")
    paid = policy.get("paid_models")
    if not isinstance(paid, Mapping) or paid.get("provider") != "lingshi":
        raise PublicationAutopilotContractError("only Lingshi paid models are allowed")
    cap = paid.get("maximum_confirmed_requests_per_product")
    if type(cap) is not int or cap < 1:
        raise PublicationAutopilotContractError("product paid request budget must be a positive integer")
    purposes = paid.get("allowed_purposes")
    if (not isinstance(purposes, list) or not purposes
            or any(not _text(purpose) for purpose in purposes) or len(purposes) != len(set(purposes))):
        raise PublicationAutopilotContractError("paid purposes must be explicit and unique")
    authority = policy.get("authority")
    if (not isinstance(authority, Mapping) or not _text(authority.get("approved_by"))
            or not _text(authority.get("approved_at")) or not _text(authority.get("scope"))
            or authority.get("kind") not in {"standing_user_instruction", "explicit_conversation_approval"}):
        raise PublicationAutopilotContractError("paid policy requires attributable existing user authority")
    if paid.get("automatic_paid_retry") is not True:
        raise PublicationAutopilotContractError("automatic paid retry policy is invalid")
    if paid.get("maximum_automatic_paid_retries_per_task") != 3:
        raise PublicationAutopilotContractError("automatic paid retry limit must be exactly three")
    if paid.get("unknown_outcome_policy") != "STOP_AND_RECONCILE":
        raise PublicationAutopilotContractError("unknown paid outcomes must stop for reconciliation")
    if set(paid.get("retry_requires") or ()) != {
        "KNOWN_FAILED_OUTCOME",
        "EXACT_FAILED_ASSET",
        "DURABLE_RETRY_ATTEMPT",
    }:
        raise PublicationAutopilotContractError("automatic paid retry conditions are incomplete")
    final = policy.get("final_marketplace_publish")
    if (
        not isinstance(final, Mapping)
        or final.get("requires_user_approval") is not True
        or final.get("approval_kind") != "FINAL_MARKETPLACE_PUBLISH"
    ):
        raise PublicationAutopilotContractError(
            "marketplace publication must retain the final human gate"
        )
    review_contract = policy.get("review_contract")
    if (
        policy.get("policy_id") == "orbit-product-publication-default-v1"
        and review_contract is None
    ):
        raise PublicationAutopilotContractError(
            "production policy requires the single final-review contract"
        )
    if review_contract is not None:
        expected_reuse = {
            "READ_ONLY_RECONCILIATION",
            "BOUNDED_TECHNICAL_RETRY",
            "ASYNC_PROVIDER_CONVERGENCE",
            "SYSTEM_DEFECT_CONTINUATION_WITH_EXACT_APPROVED_LINEAGE",
        }
        expected_new_authority = {
            "FROZEN_CANDIDATE_CHANGE",
            "TARGET_SCOPE_EXPANSION",
            "PAID_ACTION_OUTSIDE_EXISTING_AUTHORITY",
            "NEW_EXTERNAL_WRITE_CLASS",
        }
        rounds = review_contract.get("intermediate_candidate_rounds") if isinstance(review_contract, Mapping) else None
        if (
            not isinstance(review_contract, Mapping)
            or rounds != [
                "ROUND1_FACTS_COPY_PRICING_AND_IMAGE_PLAN",
                "ROUND2_IMAGES_LOCALIZATION_AND_QA",
            ]
            or review_contract.get("intermediate_human_approval_required") is not False
            or review_contract.get("intermediate_artifacts_are_execution_authority") is not False
            or review_contract.get("sole_human_gate") != "FINAL_MARKETPLACE_PUBLISH"
            or set(review_contract.get("reuse_existing_final_approval_for") or ()) != expected_reuse
            or set(review_contract.get("new_explicit_authority_required_for") or ()) != expected_new_authority
            or final.get("single_review_per_frozen_candidate") is not True
            or final.get("valid_receipt_must_not_be_reprompted") is not True
        ):
            raise PublicationAutopilotContractError(
                "single final-review contract is invalid"
            )
    budgets = policy.get("write_budgets")
    if not isinstance(budgets, Mapping) or set(budgets) != set(SUPPORTED_PLATFORMS):
        raise PublicationAutopilotContractError("platform write budgets are incomplete")
    for platform in SUPPORTED_PLATFORMS:
        row = budgets[platform]
        if (
            not isinstance(row, Mapping)
            or type(row.get("shared_maximum")) is not int
            or type(row.get("per_target_maximum")) is not int
            or row["shared_maximum"] < 0
            or row["per_target_maximum"] < 0
        ):
            raise PublicationAutopilotContractError("platform write budget is invalid")
    overrides = policy.get("write_budget_overrides", {})
    if not isinstance(overrides, Mapping):
        raise PublicationAutopilotContractError("offer write budget overrides are invalid")
    for offer_id, offer_budgets in overrides.items():
        if not isinstance(offer_id, str) or not offer_id.isdigit() or int(offer_id) <= 0:
            raise PublicationAutopilotContractError("offer write budget identity is invalid")
        if not isinstance(offer_budgets, Mapping) or set(offer_budgets) != set(SUPPORTED_PLATFORMS):
            raise PublicationAutopilotContractError("offer write budgets are incomplete")
        for platform in SUPPORTED_PLATFORMS:
            row = offer_budgets[platform]
            if (
                not isinstance(row, Mapping)
                or type(row.get("shared_maximum")) is not int
                or type(row.get("per_target_maximum")) is not int
                or row["shared_maximum"] < 0
                or row["per_target_maximum"] < 0
            ):
                raise PublicationAutopilotContractError("offer write budget is invalid")
    return policy


def load_autopilot_policy(path: Path | None = None) -> dict[str, Any]:
    return validate_autopilot_policy(_read_json(path or POLICY_PATH))


def validate_incident_registry(value: Mapping[str, Any]) -> dict[str, Any]:
    registry = deepcopy(dict(value))
    if registry.get("schema_version") != INCIDENT_SCHEMA:
        raise PublicationAutopilotContractError("incident registry schema is invalid")
    incidents = registry.get("incidents")
    if not isinstance(incidents, list):
        raise PublicationAutopilotContractError("incident registry rows are invalid")
    seen: set[str] = set()
    for row in incidents:
        if not isinstance(row, Mapping):
            raise PublicationAutopilotContractError("incident row must be an object")
        incident_id = _text(row.get("incident_id"))
        if (
            not incident_id
            or incident_id in seen
            or row.get("status") not in {
                "CONFIRMED",
                "FIXED_LOCAL_PENDING_MERGE",
                "DEFERRED_BY_USER",
            }
            or not _text(row.get("invariant"))
            or not isinstance(row.get("regression_tests"), list)
            or not row["regression_tests"]
        ):
            raise PublicationAutopilotContractError("confirmed incident row is invalid")
        fix_commit = _text(row.get("fix_commit"))
        if (
            (row.get("status") == "CONFIRMED" and fix_commit == "WORKTREE_PENDING")
            or (
                row.get("status") == "FIXED_LOCAL_PENDING_MERGE"
                and fix_commit != "WORKTREE_PENDING"
            )
            or (
                row.get("status") == "DEFERRED_BY_USER"
                and fix_commit != "WORKTREE_PENDING"
            )
        ):
            raise PublicationAutopilotContractError(
                "incident lifecycle and fix commit disagree"
            )
        seen.add(incident_id)
    return registry


def load_incident_registry(path: Path | None = None) -> dict[str, Any]:
    return validate_incident_registry(_read_json(path or INCIDENT_REGISTRY_PATH))


def _blocker(code: str, message: str, *, target: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"code": code, "message_zh": message}
    if target:
        row["target_label"] = target
    return row


def _selected_targets(
    snapshot: Mapping[str, Any], target_scope: Sequence[str] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    raw = snapshot.get("publication_targets")
    if not isinstance(raw, list) or not raw:
        return [], [_blocker("target_scope_missing", "发布目标为空。")]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, Mapping):
            blockers.append(_blocker("target_contract_invalid", "发布目标格式无效。"))
            continue
        label = _text(value.get("target_label"))
        platform = _text(value.get("platform")).upper()
        if label == "miaoshou:COMMON" and platform == "MIAOSHOU":
            # COMMON is a required execution prerequisite, not a storefront
            # marketplace target in the sole final approval matrix.
            continue
        if not label or label in seen or platform not in SUPPORTED_PLATFORMS:
            blockers.append(_blocker("target_contract_invalid", "发布目标身份重复或无效。"))
            continue
        seen.add(label)
        rows.append({**dict(value), "target_label": label, "platform": platform})
    if target_scope is None:
        return rows, blockers
    requested = list(target_scope)
    if (
        not requested
        or any(not _text(label) for label in requested)
        or len(requested) != len(set(requested))
        or not set(requested).issubset(seen)
    ):
        blockers.append(_blocker("target_scope_conflict", "目标级执行范围不属于冻结快照。"))
        return [], blockers
    selected = [row for row in rows if row["target_label"] in set(requested)]
    selected.sort(key=lambda row: requested.index(row["target_label"]))
    return selected, blockers


def _target_images(snapshot: Mapping[str, Any], label: str) -> list[str]:
    from domains.product_operations import (
        ApprovedPublicationSnapshotError,
        publication_images_for_target,
    )

    try:
        images = publication_images_for_target(snapshot, label)
    except ApprovedPublicationSnapshotError:
        return []
    if any(not value.startswith("https://") for value in images):
        return []
    return images if images and len(images) == len(set(images)) else []


def _review_category(value: object) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    nested = row.get("category") if isinstance(row.get("category"), Mapping) else {}
    source = nested or row
    decision = row.get("decision") if isinstance(row.get("decision"), Mapping) else {}
    return {
        "id": _text(source.get("id")),
        "name": _text(source.get("name")),
        "path": _text(source.get("path")) or _text(source.get("name")),
        "status": _text(decision.get("status")) or "FROZEN",
    }


def _review_bridge(
    snapshot: Mapping[str, Any], durable_evidence: Mapping[str, Any] | None
) -> Mapping[str, Any]:
    bridge = (
        durable_evidence.get("publication_bridge")
        if isinstance(durable_evidence, Mapping)
        else None
    )
    if not isinstance(bridge, Mapping):
        return {}
    handoff = bridge.get("release_handoff")
    handoff = handoff if isinstance(handoff, Mapping) else {}
    direct_match = (
        _text(bridge.get("offer_id")) != _text(snapshot.get("offer_id"))
        or _text(handoff.get("plan_id")) != _text(snapshot.get("plan_id"))
        or _text(handoff.get("snapshot_digest"))
        != _text(snapshot.get("snapshot_digest"))
    ) is False
    if direct_match:
        return bridge
    successor = durable_evidence.get("shopee_variant_image_successor")
    master = snapshot.get("shopee_global_master")
    master = master if isinstance(master, Mapping) else {}
    reconciliation = (
        successor.get("reconciliation")
        if isinstance(successor, Mapping)
        else None
    )
    successor_match = bool(
        isinstance(successor, Mapping)
        and successor.get("schema_version")
        == "shopee-variant-image-successor-preparation/v1"
        and successor.get("status") == "APPROVED_AND_FROZEN"
        and successor.get("platform_writes") == 0
        and _text(successor.get("offer_id")) == _text(snapshot.get("offer_id"))
        and _text(successor.get("successor_plan_id"))
        == _text(snapshot.get("plan_id"))
        and _text(successor.get("successor_snapshot_digest"))
        == _text(snapshot.get("snapshot_digest"))
        and isinstance(successor.get("successor_snapshot"), Mapping)
        and dict(successor["successor_snapshot"]) == dict(snapshot)
        and successor.get("bindings") == master.get("variant_image_bindings")
        and _text(successor.get("predecessor_plan_id"))
        == _text(handoff.get("plan_id"))
        and _text(successor.get("predecessor_snapshot_digest"))
        == _text(handoff.get("snapshot_digest"))
        and isinstance(reconciliation, Mapping)
        and reconciliation.get("status") == "MATCHED_PERSISTED_SUCCESSOR"
        and reconciliation.get("external_write_count") == 0
    )
    if not successor_match:
        return {}
    return bridge


def _build_final_review_manifest(
    snapshot: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
    skus: Sequence[object],
    durable_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Bind every shopper-facing and operational fact shown at final review."""

    product = snapshot.get("product")
    product = product if isinstance(product, Mapping) else {}
    categories = snapshot.get("categories_by_target")
    categories = categories if isinstance(categories, Mapping) else {}
    bridge = _review_bridge(snapshot, durable_evidence)
    bridge_facts = bridge.get("target_facts")
    bridge_facts = bridge_facts if isinstance(bridge_facts, Mapping) else {}
    bridge_routes = bridge.get("image_routes")
    bridge_routes = bridge_routes if isinstance(bridge_routes, Mapping) else {}
    bridge_locales = bridge.get("route_locales")
    bridge_locales = bridge_locales if isinstance(bridge_locales, Mapping) else {}
    frozen_content = product.get("content_by_target")
    frozen_content = frozen_content if isinstance(frozen_content, Mapping) else {}

    shopee_master = snapshot.get("shopee_global_master")
    shopee_master = shopee_master if isinstance(shopee_master, Mapping) else {}
    shopee_master_schema = _text(shopee_master.get("schema_version"))
    shopee_variant_images: dict[str, dict[str, Any]] = {}
    if shopee_master_schema == "shopee-global-master/v2":
        raw_bindings = shopee_master.get("variant_image_bindings")
        if isinstance(raw_bindings, list):
            for raw_binding in raw_bindings:
                if not isinstance(raw_binding, Mapping):
                    continue
                model_sku = _text(raw_binding.get("model_sku"))
                if not model_sku:
                    continue
                shopee_variant_images[model_sku] = {
                    "contract_version": shopee_master_schema,
                    "image_url": _text(raw_binding.get("image_url")),
                    "image_digest": _text(raw_binding.get("image_digest")),
                    "source": deepcopy(dict(raw_binding.get("source") or {})),
                    "status": "FROZEN_AND_DIGEST_VERIFIED",
                }
    elif shopee_master_schema == "shopee-global-master/v1":
        raw_positions = shopee_master.get("variant_image_positions")
        common_images = product.get("images")
        common_images = common_images if isinstance(common_images, list) else []
        if isinstance(raw_positions, list):
            for raw_binding in raw_positions:
                if not isinstance(raw_binding, Mapping):
                    continue
                model_sku = _text(raw_binding.get("model_sku"))
                position = raw_binding.get("position")
                image_url = (
                    _text(common_images[position])
                    if type(position) is int and 0 <= position < len(common_images)
                    else ""
                )
                if model_sku:
                    shopee_variant_images[model_sku] = {
                        "contract_version": shopee_master_schema,
                        "image_url": image_url,
                        "source": {"kind": "COMMON_GALLERY", "source_position": position + 1},
                        "status": "FROZEN_POSITION",
                    }

    variants: list[dict[str, Any]] = []
    sku_by_model: dict[str, Mapping[str, Any]] = {}
    for raw in skus:
        if not isinstance(raw, Mapping):
            continue
        model = _text(raw.get("model_sku"))
        if model:
            sku_by_model[model] = raw
        variant = {
                "variant_key": _text(raw.get("variant_key")),
                "seller_sku": _text(raw.get("seller_sku")),
                "model_sku": model,
                "sku_id": model,
                "specification": deepcopy(dict(raw.get("specification") or {})),
                "cost": deepcopy(dict(raw.get("cost") or {})),
                "parcel": deepcopy(dict(raw.get("parcel") or {})),
            }
        if model in shopee_variant_images:
            variant["shopee_variant_image"] = deepcopy(
                shopee_variant_images[model]
            )
        variants.append(variant)

    copy_groups: dict[str, dict[str, Any]] = {}
    image_groups: dict[str, dict[str, Any]] = {}
    target_rows: list[dict[str, Any]] = []
    for target in targets:
        label = _text(target.get("target_label"))
        facts = bridge_facts.get(label)
        facts = facts if isinstance(facts, Mapping) else {}
        copy = facts.get("copy")
        copy = copy if isinstance(copy, Mapping) else {}
        frozen_copy = frozen_content.get(label)
        frozen_copy = frozen_copy if isinstance(frozen_copy, Mapping) else {}
        title = _text(copy.get("title")) or _text(frozen_copy.get("title"))
        title = title or _text(product.get("title"))
        description = _text(copy.get("description")) or _text(
            frozen_copy.get("description")
        )
        description = description or _text(product.get("description"))
        language = (
            _text(copy.get("language"))
            or _text(frozen_copy.get("locale"))
            or "frozen-master"
        )
        copy_key = _canonical_digest(
            {"title": title, "description": description, "language": language}
        )
        copy_id = f"copy-{copy_key[:12]}"
        if copy_id not in copy_groups:
            copy_groups[copy_id] = {
                "copy_set_id": copy_id,
                "language": language,
                "title": title,
                "description": description,
                "target_labels": [],
            }
        copy_groups[copy_id]["target_labels"].append(label)

        urls = _target_images(snapshot, label)
        enriched = bridge_routes.get(label)
        route_items: list[dict[str, Any]] = []
        if (
            isinstance(enriched, list)
            and [_text(row.get("url")) for row in enriched if isinstance(row, Mapping)]
            == urls
        ):
            for index, row in enumerate(enriched, 1):
                route_items.append(
                    {
                        "position": index,
                        "url": urls[index - 1],
                        "brand_id": _text(row.get("brand_id")),
                        "role": _text(row.get("role")),
                        "kind": _text(row.get("kind")),
                    }
                )
        else:
            route_items = [
                {"position": index, "url": url, "brand_id": "", "role": "", "kind": ""}
                for index, url in enumerate(urls, 1)
            ]
        image_key = _canonical_digest({"images": route_items})
        image_id = f"images-{image_key[:12]}"
        if image_id not in image_groups:
            image_groups[image_id] = {
                "image_set_id": image_id,
                "target_labels": [],
                "images": route_items,
            }
        image_groups[image_id]["target_labels"].append(label)

        formula_rows: dict[str, Mapping[str, Any]] = {}
        price_evidence = facts.get("price")
        price_evidence = price_evidence if isinstance(price_evidence, Mapping) else {}
        raw_formula_rows = price_evidence.get("sku_prices")
        if isinstance(raw_formula_rows, list):
            formula_rows = {
                _text(row.get("model_sku")): row
                for row in raw_formula_rows
                if isinstance(row, Mapping) and _text(row.get("model_sku"))
            }
        prices: list[dict[str, Any]] = []
        for model, sku in sku_by_model.items():
            frozen_prices = sku.get("prices")
            frozen_price = (
                frozen_prices.get(label) if isinstance(frozen_prices, Mapping) else None
            )
            if not isinstance(frozen_price, Mapping):
                continue
            price_row = {
                "model_sku": model,
                "amount": frozen_price.get("amount"),
                "currency": _text(frozen_price.get("currency")),
            }
            evidence_row = formula_rows.get(model)
            if (
                isinstance(evidence_row, Mapping)
                and str(evidence_row.get("amount")) == str(frozen_price.get("amount"))
                and _text(evidence_row.get("currency"))
                == _text(frozen_price.get("currency"))
            ):
                for key in ("old_price", "status", "calculation"):
                    if key in evidence_row:
                        price_row[key] = deepcopy(evidence_row[key])
            prices.append(price_row)

        target_rows.append(
            {
                "target_label": label,
                "platform": _text(target.get("platform")).upper(),
                "site": _text(target.get("site")),
                "store": _text(target.get("store")),
                "locale": _text(bridge_locales.get(label)) or _text(target.get("site")),
                "category": _review_category(categories.get(label)),
                "copy_set_id": copy_id,
                "image_set_id": image_id,
                "prices": prices,
            }
        )
        allocations = product.get("warehouse_inventory_by_target")
        allocation = allocations.get(label) if isinstance(allocations, Mapping) else None
        if _text(target.get("platform")).upper() == "TIKTOK" and isinstance(allocation, Mapping):
            target_rows[-1]["inventory"] = {
                "quantity_basis": "PER_MODEL_SKU",
                "model_skus": list(sku_by_model),
                "total_stock_per_model_sku": allocation["total_stock"],
                "warehouses": [{"warehouse_name": row["warehouse_name"],
                    "stock_per_model_sku": row["stock"]} for row in allocation["warehouses"]],
                "note_zh": "以下每仓数量分别应用于每个已批准 Model SKU，不是商品所有变体合计库存。",
            }
        if _text(target.get("platform")).upper() == "OZON" and isinstance(product.get("stock_policy"), Mapping):
            target_rows[-1]["inventory"] = {
                "quantity_basis":"PER_MODEL_SKU", "model_skus":list(sku_by_model),
                "stock_per_model_sku":product["stock_policy"]["quantity_per_sku"],
                "warehouse_selection_policy":"UNIQUE_ACTIVE_OR_CREATED_NON_KGT",
                "note_zh":"按冻结库存策略为每个已批准 SKU 设置库存；只读确认唯一启用的非 KGT 仓库，准备、提交和回读必须保持同一仓库身份。",
            }

    manifest: dict[str, Any] = {
        "schema_version": "publication-final-review-manifest/v1",
        "product": {
            "seller_sku": (
                next(iter({row["seller_sku"] for row in variants}))
                if variants
                and len({row["seller_sku"] for row in variants}) == 1
                else ""
            ),
            "title": _text(product.get("title")),
            "description": _text(product.get("description")),
            "main_category": deepcopy(dict(product.get("main_category") or {})),
            "verified_claims": deepcopy(dict(product.get("verified_claims") or {})),
        },
        "variants": variants,
        "copy_sets": list(copy_groups.values()),
        "image_sets": list(image_groups.values()),
        "targets": target_rows,
    }
    if isinstance(product.get("stock_policy"), Mapping):
        manifest["product"]["stock_policy"] = deepcopy(dict(product["stock_policy"]))
    if isinstance(product.get("source_conformance"), Mapping):
        manifest["product"]["source_conformance"] = deepcopy(
            dict(product["source_conformance"])
        )
    manifest["manifest_digest"] = _canonical_digest(manifest)
    return manifest


def _companion_action_review(
    snapshot: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project known post-dispatch actions into the sole final review."""

    product = snapshot.get("product")
    product = product if isinstance(product, Mapping) else {}
    target_labels = [_text(row.get("target_label")) for row in targets]
    actions: list[dict[str, Any]] = []

    stock_policy = product.get("stock_policy")
    if "ozon:RU" in target_labels and isinstance(stock_policy, Mapping):
        warehouse_decision = product.get("ozon_stock_decision")
        warehouse_decision = (
            warehouse_decision
            if isinstance(warehouse_decision, Mapping)
            else None
        )
        actions.append(
            {
                "action_kind": "OZON_FBS_STOCK_CONVERGENCE",
                "prerequisite_target": "ozon:RU",
                "quantity_basis": "PER_MODEL_SKU",
                "quantity_per_sku": stock_policy.get("quantity_per_sku"),
                "warehouse_selection_policy": (
                    warehouse_decision.get("selection_policy")
                    if warehouse_decision is not None
                    else "UNIQUE_ACTIVE_OR_CREATED_NON_KGT"
                ),
                "warehouse_identity_bound": warehouse_decision is not None,
                "warehouse_source": (
                    warehouse_decision.get("source")
                    if warehouse_decision is not None
                    else "RUNTIME_READ_ONLY_RESOLUTION"
                ),
                "provider_identity_resolution": (
                    "FROZEN_ID_REVALIDATED_AT_EXECUTION"
                    if warehouse_decision is not None
                    else "READ_ONLY_AT_EXECUTION"
                ),
                "completion_authority": "EXACT_WAREHOUSE_SCOPED_READBACK",
            }
        )

    promotion_policy = product.get("postpublish_promotion_policy")
    if isinstance(promotion_policy, Mapping):
        from shared_platform.postpublish_promotions import (
            eligible_promotion_action_targets,
            promotion_target_policy,
        )

        for action_target in eligible_promotion_action_targets(target_labels):
            policy = promotion_target_policy(action_target)
            actions.append(
                {
                    "action_kind": "POSTPUBLISH_DIRECT_DISCOUNT",
                    "action_target": action_target,
                    "prerequisite_target": policy["prerequisite_target"],
                    "discount_source": policy["discount_source"],
                    "selection_policy": policy["selection_policy"],
                    "execution_surface": policy["execution_surface"],
                    "completion_authority": "OFFICIAL_ACTIVITY_PRODUCT_READBACK",
                }
            )

    return {
        "schema_version": "publication-companion-action-review/v1",
        "actions": actions,
        "continuation_authority": {
            "without_new_human_approval": [
                "READ_ONLY_RECONCILIATION",
                "ASYNC_PROVIDER_CONVERGENCE",
                "BUDGETED_RETRY_AFTER_CONFIRMED_ZERO_WRITES",
                "SYSTEM_DEFECT_CONTINUATION_WITH_EXACT_APPROVED_LINEAGE",
            ],
            "requires_new_human_approval": [
                "TARGET_SCOPE_CHANGE",
                "SKU_OR_CONTENT_CHANGE",
                "PRICE_OR_DISCOUNT_DECISION_CHANGE",
                "STOCK_QUANTITY_OR_WAREHOUSE_POLICY_CHANGE",
                "NEW_PAID_OR_PROVIDER_ACTION",
            ],
            "system_defect_rule": (
                "A dropped known fact may be restored only from immutable approved "
                "lineage, with no scope expansion and an explicit continuation receipt."
            ),
        },
    }


def compile_release_candidate(
    snapshot: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
    incident_registry: Mapping[str, Any] | None = None,
    platform_scope: Sequence[str] | None = None,
    target_scope: Sequence[str] | None = None,
    durable_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a read-only, exact final-review packet from one frozen snapshot."""

    if not isinstance(snapshot, Mapping):
        raise PublicationAutopilotContractError("snapshot must be an object")
    from domains.product_operations import validate_approved_publication_snapshot
    try:
        snapshot = validate_approved_publication_snapshot(snapshot).payload()
    except (TypeError, ValueError) as error:
        raise PublicationAutopilotContractError("approved publication snapshot is invalid") from error
    candidate = _compile_release_facts(
        snapshot,
        policy=policy,
        incident_registry=incident_registry,
        platform_scope=platform_scope,
        target_scope=target_scope,
        durable_evidence=durable_evidence,
    )
    # The final approval is bound to approval-metadata-independent business
    # facts, while the runner consumes one exact approved v4 snapshot.  Keep
    # both identities explicit instead of overloading ``snapshot_digest``.
    # ``snapshot_digest`` remains the business identity for compatibility with
    # already persisted final candidates and approval receipts.
    candidate.pop("candidate_digest", None)
    candidate["approved_execution_snapshot_digest"] = snapshot["snapshot_digest"]
    candidate["business_snapshot_digest"] = _business_snapshot_digest(snapshot)
    candidate["snapshot_digest"] = candidate["business_snapshot_digest"]
    candidate["candidate_digest"] = _canonical_digest(candidate)
    return candidate


def compile_release_preview(
    preview: Mapping[str, Any], *, policy: Mapping[str, Any] | None = None,
    incident_registry: Mapping[str, Any] | None = None,
    platform_scope: Sequence[str] | None = None, target_scope: Sequence[str] | None = None,
    durable_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate preview evidence, then project the same facts as a final candidate.

    Quality and platform-preflight evidence are intentionally joined against the
    preview digest.  Only a blocker-free preview is converted to the approval-
    neutral business snapshot identity used by the durable final candidate.
    """
    from domains.product_operations.approved_publication_snapshot import (
        validate_publication_preview,
        validate_publication_business_snapshot,
    )
    preview = validate_publication_preview(preview)
    quality_snapshot = dict(preview)
    quality_snapshot["snapshot_digest"] = quality_snapshot["preview_digest"]
    preview_candidate = _compile_release_facts(
        quality_snapshot,
        policy=policy,
        incident_registry=incident_registry,
        platform_scope=platform_scope,
        target_scope=target_scope,
        durable_evidence=durable_evidence,
        preview_only=True,
    )
    if preview_candidate["status"] != "PREVIEW_READY":
        return preview_candidate

    business = dict(preview)
    business.pop("preview_digest")
    business.pop("status")
    business["schema_version"] = "publication-business-snapshot/v1"
    business["business_snapshot_digest"] = (
        "sha256:" + _canonical_digest(business)
    )
    business = validate_publication_business_snapshot(business)
    candidate = dict(preview_candidate)
    candidate.pop("candidate_digest")
    candidate.pop("approval_status")
    candidate["schema_version"] = CANDIDATE_SCHEMA
    candidate["status"] = "READY_FOR_FINAL_REVIEW"
    candidate["snapshot_digest"] = business["business_snapshot_digest"]
    candidate.pop("preview_snapshot_digest")
    candidate["candidate_digest"] = _canonical_digest(candidate)
    return candidate


def _compile_release_facts(
    snapshot: Mapping[str, Any], *, policy: Mapping[str, Any] | None,
    incident_registry: Mapping[str, Any] | None, platform_scope: Sequence[str] | None,
    target_scope: Sequence[str] | None, durable_evidence: Mapping[str, Any] | None,
    preview_only: bool = False,
    expected_snapshot_schema: str | None = None,
) -> dict[str, Any]:
    governed_policy = validate_autopilot_policy(policy or load_autopilot_policy())
    offer_id = _text(snapshot.get("offer_id"))
    offer_overrides = governed_policy.get("write_budget_overrides", {})
    scoped_budgets = offer_overrides.get(offer_id)
    if scoped_budgets is not None:
        governed_policy = deepcopy(governed_policy)
        governed_policy["write_budgets"] = deepcopy(scoped_budgets)
    incidents = validate_incident_registry(
        incident_registry or load_incident_registry()
    )
    from shared_platform.publication_error_policy import (
        load_publication_error_policy,
    )

    error_policy = load_publication_error_policy()
    blockers: list[dict[str, Any]] = []
    if snapshot.get("schema_version") != (
        expected_snapshot_schema
        or ("publication-snapshot-preview/v1" if preview_only else SNAPSHOT_SCHEMA)
    ):
        blockers.append(_blocker("snapshot_schema_invalid", "发布快照版本无效。"))
    digest = _text(snapshot.get("snapshot_digest"))
    digest_value = digest.removeprefix("sha256:")
    if len(digest_value) != 64 or any(
        char not in "0123456789abcdef" for char in digest_value
    ):
        blockers.append(_blocker("snapshot_digest_invalid", "发布快照摘要无效。"))

    targets, target_blockers = _selected_targets(snapshot, target_scope)
    blockers.extend(target_blockers)
    requested_platforms = (
        tuple(_platform(value) for value in platform_scope)
        if platform_scope is not None
        else tuple(dict.fromkeys(row["platform"] for row in targets))
    )
    if len(requested_platforms) != len(set(requested_platforms)):
        blockers.append(_blocker("platform_scope_conflict", "平台范围包含重复项。"))
    selected_platforms = set(requested_platforms)
    targets = [row for row in targets if row["platform"] in selected_platforms]
    if not targets:
        blockers.append(_blocker("target_scope_empty", "本轮没有可执行目标。"))

    product = snapshot.get("product")
    product = product if isinstance(product, Mapping) else {}
    if not _text(product.get("title")) or not _text(product.get("description")):
        blockers.append(_blocker("consumer_copy_incomplete", "标题或商品描述为空。"))
    if ":shadow-" in _text(snapshot.get("plan_id")):
        conformance = product.get("source_conformance")
        if not isinstance(conformance, Mapping) or conformance.get("status") != "PASS":
            blockers.append(
                _blocker(
                    "SHADOW_FORMAL_CONFORMANCE_REQUIRED",
                    "影子来源缺少逐目标文案、类目、价格、相册与描述图交接一致性凭证。",
                )
            )
        stock_policy = product.get("stock_policy")
        if (
            not isinstance(stock_policy, Mapping)
            or stock_policy.get("schema_version") != "publication-default-stock/v1"
            or stock_policy.get("quantity_per_sku") != 200
        ):
            blockers.append(
                _blocker(
                    "STOCK_POLICY_REQUIRED",
                    "影子来源缺少第一轮冻结的逐 SKU 库存策略。",
                )
            )

    categories = snapshot.get("categories_by_target")
    categories = categories if isinstance(categories, Mapping) else {}
    route_summary: dict[str, dict[str, Any]] = {}
    for target in targets:
        label = target["target_label"]
        category = categories.get(label)
        category_value = category.get("category") if isinstance(category, Mapping) else None
        decision = category.get("decision") if isinstance(category, Mapping) else None
        category_ready = bool(
            isinstance(category, Mapping)
            and _text(category.get("id"))
            and _text(category.get("name"))
        ) or bool(
            isinstance(category_value, Mapping)
            and _text(category_value.get("id"))
            and _text(category_value.get("name"))
        ) or bool(
            isinstance(decision, Mapping)
            and decision.get("status") in {"DEFERRED_TO_SKILL", "NOT_APPLICABLE"}
        )
        if not category_ready:
            blockers.append(
                _blocker("target_category_incomplete", "目标类目尚未完整冻结。", target=label)
            )
        images = _target_images(snapshot, label)
        if not images:
            blockers.append(
                _blocker("target_image_route_incomplete", "目标图片路由为空、重复或不是 HTTPS。", target=label)
            )
        route_summary[label] = {
            "gallery_count": len(images),
            "description_count": len(images),
            "same_order": bool(images),
            "unique": bool(images),
        }

    skus = snapshot.get("skus")
    if not isinstance(skus, list) or not skus:
        blockers.append(_blocker("variant_coverage_missing", "冻结快照没有变体。"))
        skus = []
    seen_models: set[str] = set()
    target_labels = [row["target_label"] for row in targets]
    for row in skus:
        if not isinstance(row, Mapping):
            blockers.append(_blocker("variant_contract_invalid", "变体格式无效。"))
            continue
        model = _text(row.get("model_sku"))
        if not model or model in seen_models:
            blockers.append(_blocker("variant_identity_invalid", "变体身份为空或重复。"))
        seen_models.add(model)
        try:
            from domains.product_operations.sku_display_name import (
                validate_specification_mapping,
            )

            validate_specification_mapping(
                row.get("specification"), model_sku=model
            )
        except (TypeError, ValueError):
            blockers.append(
                _blocker(
                    "variant_display_name_invalid",
                    f"变体 {model or '未知'} 的发布规格名仍是供应商原始键、包含 SKU ID 或不够消费者可读。",
                )
            )
        parcel = row.get("parcel")
        package = parcel.get("package_cm") if isinstance(parcel, Mapping) else None
        if (
            not isinstance(row.get("specification"), Mapping)
            or not row["specification"]
            or not isinstance(parcel, Mapping)
            or not _positive_number(parcel.get("weight_kg"))
            or not isinstance(package, list)
            or len(package) != 3
            or not all(_positive_number(value) for value in package)
        ):
            blockers.append(_blocker("variant_facts_incomplete", "变体规格、重量或包裹尺寸不完整。"))
        prices = row.get("prices")
        for label in target_labels:
            price = prices.get(label) if isinstance(prices, Mapping) else None
            if (
                not isinstance(price, Mapping)
                or not _positive_number(price.get("amount"))
                or not _text(price.get("currency"))
            ):
                blockers.append(
                    _blocker(
                        "variant_price_coverage_incomplete",
                        f"变体 {model or '未知'} 缺少该目标的金额或币种。",
                        target=label,
                    )
                )

    if "SHOPEE" in selected_platforms and len(skus) > 1:
        shopee_master = snapshot.get("shopee_global_master")
        shopee_master = shopee_master if isinstance(shopee_master, Mapping) else {}
        master_schema = _text(shopee_master.get("schema_version"))
        if master_schema == "shopee-global-master/v2":
            bindings = shopee_master.get("variant_image_bindings")
            bindings = bindings if isinstance(bindings, list) else []
            binding_models = [
                _text(row.get("model_sku"))
                for row in bindings
                if isinstance(row, Mapping)
            ]
            binding_urls = [
                _text(row.get("image_url"))
                for row in bindings
                if isinstance(row, Mapping)
            ]
            binding_digests = [
                _text(row.get("image_digest"))
                for row in bindings
                if isinstance(row, Mapping)
            ]
            if (
                binding_models != [
                    _text(row.get("model_sku"))
                    for row in skus
                    if isinstance(row, Mapping)
                ]
                or any(not value.startswith("https://") for value in binding_urls)
                or any(not value.startswith("sha256:") for value in binding_digests)
                or len(binding_urls) != len(set(binding_urls))
                or len(binding_digests) != len(set(binding_digests))
            ):
                blockers.append(
                    _blocker(
                        "shopee_variant_image_binding_invalid",
                        "虾皮多变体图片缺少完整逐 SKU 绑定，或图片并非彼此独立。",
                    )
                )
        elif master_schema == "shopee-global-master/v1":
            bindings = shopee_master.get("variant_image_positions")
            bindings = bindings if isinstance(bindings, list) else []
            positions = [
                row.get("position")
                for row in bindings
                if isinstance(row, Mapping)
            ]
            models = [
                _text(row.get("model_sku"))
                for row in bindings
                if isinstance(row, Mapping)
            ]
            if (
                models != [
                    _text(row.get("model_sku"))
                    for row in skus
                    if isinstance(row, Mapping)
                ]
                or any(type(position) is not int or position < 0 for position in positions)
                or len(positions) != len(set(positions))
            ):
                blockers.append(
                    _blocker(
                        "shopee_variant_image_binding_invalid",
                        "虾皮多变体图片位置缺少完整逐 SKU 绑定，或多个 SKU 复用了同一张图。",
                    )
                )
        else:
            blockers.append(
                _blocker(
                    "shopee_variant_image_binding_missing",
                    "虾皮多变体发布缺少冻结的逐 SKU 图片绑定。",
                )
            )

    from shared_platform.publication_quality import (
        evaluate_publication_quality,
        select_product_family_pack,
        PublicationQualityContractError,
    )

    quality_snapshot = _quality_snapshot_with_round1_claims(snapshot, durable_evidence)
    from shared_platform.publication_quality_evidence import validate_round1_evidence_identity
    try:
        validate_round1_evidence_identity(snapshot,durable_evidence)
    except ValueError:
        blockers.append(_blocker('ROUND1_EVIDENCE_IDENTITY_CONFLICT','第一轮事实证据的商品、摘要、目标或冻结发布绑定不一致。'))
    automated_quality_gate = evaluate_publication_quality(quality_snapshot)
    for error in automated_quality_gate["errors"]:
        blockers.append(
            _blocker(
                str(error["code"]),
                str(error["message_zh"]),
                target=(
                    str(error["target_label"])
                    if error.get("target_label")
                    else None
                ),
            )
        )

    from shared_platform.publication_quality_evidence import (
        evaluate_publication_quality_evidence,
    )

    try:
        quality_pack = select_product_family_pack(quality_snapshot) or {}
    except PublicationQualityContractError:
        # The evaluator above already reports the exact selection failure.
        quality_pack = {}
    durable_quality_evidence = evaluate_publication_quality_evidence(
        snapshot,
        durable_evidence,
        pack=quality_pack,
    )
    exact_dual_brand_plan = _text(snapshot.get("plan_id")).startswith(
        f"dual-brand:{_text(snapshot.get('offer_id'))}:"
    )
    if durable_quality_evidence["status"] == "NOT_AVAILABLE" and exact_dual_brand_plan:
        blockers.append(
            _blocker(
                "DURABLE_EVIDENCE_REQUIRED",
                "双品牌正式交接缺少图片、翻译、OCR 或售价持久化证据。",
            )
        )
    for error in durable_quality_evidence["errors"]:
        blockers.append(
            _blocker(
                str(error["code"]),
                str(error["message_zh"]),
                target=(
                    str(error["target_label"])
                    if error.get("target_label")
                    else None
                ),
            )
        )

    write_budget: dict[str, dict[str, Any]] = {}
    for platform in requested_platforms:
        count = sum(row["platform"] == platform for row in targets)
        budget = governed_policy["write_budgets"][platform]
        # TikTok preparation may need one platform-draft creation per approved
        # target when the product did not originate from an existing Miaoshou
        # platform draft.  Claim and save remain target-scoped mutations.
        shared_maximum = budget["shared_maximum"]
        required_shared = 0
        if platform == "TIKTOK":
            required_shared = count
        if platform == "SHOPEE":
            common_images = list(product.get("images") or [])
            master = snapshot.get("shopee_global_master") or {}
            variant_images = [row["image_url"] for row in master.get("variant_image_bindings", [])]
            # Each unique missing image is one POST. Cached images consume zero;
            # cache availability is only confirmed by the runtime checkpoint.
            required_shared = len(set(common_images + variant_images)) + 2
        if platform == "OZON":
            product = snapshot.get("product")
            stock_policy = (
                product.get("stock_policy") if isinstance(product, Mapping) else None
            )
            governed_stock_write = (
                1
                if isinstance(stock_policy, Mapping)
                and stock_policy.get("source") == "SYSTEM_GOVERNED_DEFAULT"
                else 0
            )
            # One import attempt per approved model SKU plus one batch stock
            # mutation.  This is snapshot-derived so the frozen budget is
            # neither a legacy two-write cap nor an unnecessarily broad limit.
            required_shared = len(skus) + governed_stock_write
        if required_shared > shared_maximum:
            blockers.append(_blocker("write_budget_insufficient",
                f"{platform} 冻结范围未缓存准备需要最多 {required_shared} 次共享请求，已批准硬上限为 {shared_maximum}；先核对已持久缓存或准备新的授权候选，不能自动扩大旧上限。"))
        write_budget[platform] = {
            "target_labels": [
                row["target_label"] for row in targets if row["platform"] == platform
            ],
            "shared_maximum": shared_maximum,
            "per_target_maximum": budget["per_target_maximum"],
            "required_shared_mutations_uncached": required_shared,
            "shared_budget_basis": "ACTUAL_HTTP_MUTATIONS_CACHED_UPLOADS_ZERO",
            "maximum_confirmed_writes": (
                shared_maximum + budget["per_target_maximum"] * count
            ),
        }

    candidate: dict[str, Any] = {
        "schema_version": CANDIDATE_SCHEMA,
        "status": "BLOCKED" if blockers else "READY_FOR_FINAL_REVIEW",
        "offer_id": _text(snapshot.get("offer_id")),
        "product_revision": snapshot.get("product_revision"),
        "plan_id": _text(snapshot.get("plan_id")),
        "snapshot_digest": digest,
        "policy_id": governed_policy["policy_id"],
        "write_budget_scope": {
            "kind": "EXACT_OFFER_OVERRIDE" if scoped_budgets is not None else "DEFAULT_POLICY",
            "offer_id": offer_id if scoped_budgets is not None else None,
        },
        "target_labels": target_labels,
        "platform_scope": list(requested_platforms),
        "variant_count": len(skus),
        "review_manifest": _build_final_review_manifest(
            snapshot, targets, skus, durable_evidence
        ),
        "companion_actions": _companion_action_review(snapshot, targets),
        "image_route_checks": {
            "all_targets_complete": bool(targets) and all(
                row["gallery_count"] > 0
                and row["gallery_count"] == row["description_count"]
                and row["same_order"]
                and row["unique"]
                for row in route_summary.values()
            ),
            "targets": route_summary,
        },
        "automated_quality_gate": automated_quality_gate,
        "durable_quality_evidence": durable_quality_evidence,
        "zero_write_simulation": {
            "completed": True,
            "external_write_count": 0,
            "blocker_count": len(blockers),
        },
        "warehouse_inventory_policy": _warehouse_inventory_review(
            snapshot, targets
        ),
        "write_budget": write_budget,
        "incident_safeguards": [
            {
                "incident_id": row["incident_id"],
                "stage": row["stage"],
                "invariant": row["invariant"],
            }
            for row in incidents["incidents"]
        ],
        "error_recovery_policy": {
            "policy_id": error_policy["policy_id"],
            "classes": {
                name: {
                    "automatic_repair_attempts": row["automatic_repair_attempts"],
                    "automatic_retry_attempts": row["automatic_retry_attempts"],
                    "readback_reconciliation_attempts": row[
                        "readback_reconciliation_attempts"
                    ],
                    "next_action": row["next_action"],
                }
                for name, row in error_policy["classes"].items()
            },
        },
        "automatic_steps": list(governed_policy["automatic_steps"]),
        "approval_gate": {
            "kind": "FINAL_MARKETPLACE_PUBLISH",
            "requires_user_approval": True,
            "intermediate_human_gates": 0,
        },
        "blockers": blockers,
    }
    if preview_only:
        candidate["schema_version"] = "publication-candidate-preview/v1"
        candidate["approval_status"] = "NOT_APPROVED"
        candidate["status"] = "PREVIEW_BLOCKED" if blockers else "PREVIEW_READY"
        candidate["preview_snapshot_digest"] = candidate.pop("snapshot_digest")
    candidate["candidate_digest"] = _canonical_digest(candidate)
    return candidate


def _warehouse_inventory_review(
    snapshot: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    tiktok_labels = [
        row["target_label"] for row in targets if row["platform"] == "TIKTOK"
    ]
    product = snapshot.get("product")
    allocations = (
        product.get("warehouse_inventory_by_target")
        if isinstance(product, Mapping)
        else None
    )
    if allocations is None:
        from modules.miaoshou.tiktok_warehouses import (
            mainland_pickup_warehouse_semantic,
        )

        semantics = {
            label: mainland_pickup_warehouse_semantic(label)
            for label in tiktok_labels
        }
        if any(not name for name in semantics.values()):
            raise PublicationAutopilotContractError(
                "TikTok warehouse semantic is unavailable for an exact target"
            )
        return {
            "schema_version": "tiktok-miaoshou-warehouse-inventory-policy/v3",
            "scope": "TIKTOK_MIAOSHOU_EXACT_SHOP",
            "target_labels": tiktok_labels,
            "warehouse_semantics_by_target": semantics,
            "positive_stock_source": "CURRENT_PROVIDER_SKU_STOCK",
            "other_active_warehouse_stock": 0,
            "verification": "EXACT_SHOP_PRE_WRITE_AND_POST_WRITE_READBACK",
            "failure_mode": "ZERO_WRITE",
            "non_tiktok_note_zh": "Shopee 与 Ozon 使用各自平台库存机制，不套用妙手 TikTok 仓库字段。",
        }
    frozen_tiktok_labels = {
        row["target_label"] for row in snapshot["publication_targets"]
        if str(row["platform"]).upper() == "TIKTOK"
    }
    if not isinstance(allocations, Mapping) or set(allocations) != frozen_tiktok_labels:
        raise PublicationAutopilotContractError(
            "approved warehouse allocation target coverage drifted"
        )
    projected: dict[str, Any] = {}
    for label in tiktok_labels:
        allocation = allocations[label]
        if not isinstance(allocation, Mapping):
            raise PublicationAutopilotContractError(
                "approved warehouse allocation is invalid"
            )
        warehouses = allocation.get("warehouses")
        if not isinstance(warehouses, list) or not warehouses:
            raise PublicationAutopilotContractError(
                "approved warehouses are invalid"
            )
        public_rows: list[dict[str, Any]] = []
        calculated_total = 0
        for row in warehouses:
            if not isinstance(row, Mapping):
                raise PublicationAutopilotContractError(
                    "approved warehouse row is invalid"
                )
            name = row.get("warehouse_name")
            stock = row.get("stock")
            if type(name) is not str or not name.strip() or type(stock) is not int or stock < 0:
                raise PublicationAutopilotContractError(
                    "approved warehouse review fields are invalid"
                )
            public_rows.append({"warehouse_name": name, "stock": stock})
            calculated_total += stock
        if calculated_total <= 0 or allocation.get("total_stock") != calculated_total:
            raise PublicationAutopilotContractError(
                "approved warehouse review total drifted"
            )
        projected[label] = {
            "warehouses": public_rows,
            "total_stock": calculated_total,
            "quantity_basis": "PER_MODEL_SKU",
        }
    return {
        "schema_version": "tiktok-miaoshou-warehouse-inventory-policy/v2",
        "scope": "TIKTOK_MIAOSHOU_EXACT_SHOP",
        "target_labels": tiktok_labels,
        "allocations_by_target": projected,
        "source": "CONVERSATION_APPROVAL",
        "verification": "EXACT_SHOP_PRE_WRITE_AND_POST_WRITE_READBACK",
        "failure_mode": "ZERO_WRITE",
        "non_tiktok_note_zh": "Shopee 与 Ozon 使用各自平台库存机制，不套用妙手 TikTok 仓库字段。",
    }


def assert_write_within_release_candidate(
    candidate: Mapping[str, Any],
    *,
    platform: str,
    target_labels: Sequence[str],
    confirmed_write_count: int,
) -> None:
    """Reject scope or confirmed-write drift against the frozen candidate."""

    if not isinstance(candidate, Mapping) or candidate.get("status") != "READY_FOR_FINAL_REVIEW":
        raise PublicationAutopilotContractError("release candidate is not ready")
    normalized = _platform(platform)
    budget = candidate.get("write_budget")
    budget = budget.get(normalized) if isinstance(budget, Mapping) else None
    if not isinstance(budget, Mapping):
        raise PublicationAutopilotContractError("platform is outside release candidate")
    expected = tuple(budget.get("target_labels") or ())
    supplied = tuple(target_labels)
    supplied_set = set(supplied)
    if (
        not supplied
        or len(supplied_set) != len(supplied)
        or tuple(label for label in expected if label in supplied_set) != supplied
    ):
        raise PublicationAutopilotContractError("target scope exceeds release candidate")
    shared_maximum = budget.get("shared_maximum")
    per_target_maximum = budget.get("per_target_maximum")
    if (
        type(shared_maximum) is not int
        or shared_maximum < 0
        or type(per_target_maximum) is not int
        or per_target_maximum < 0
    ):
        raise PublicationAutopilotContractError("frozen mutation budget is invalid")
    maximum = shared_maximum + per_target_maximum * len(supplied)
    if (
        type(confirmed_write_count) is not int
        or confirmed_write_count < 0
        or type(maximum) is not int
        or confirmed_write_count > maximum
    ):
        raise PublicationAutopilotContractError("confirmed write budget exceeded")


def release_candidate_path(
    candidate: Mapping[str, Any], *, reports_root: Path | None = None
) -> Path:
    if not isinstance(candidate, Mapping) or candidate.get("schema_version") != CANDIDATE_SCHEMA:
        raise PublicationAutopilotContractError("release candidate schema is invalid")
    offer_id = _text(candidate.get("offer_id"))
    digest = _text(candidate.get("candidate_digest"))
    if (
        not offer_id.isdigit()
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise PublicationAutopilotContractError("release candidate identity is invalid")
    root = reports_root or (ROOT / "reports" / "product-preparation")
    return root / offer_id / "release-candidates" / f"{digest}.json"


def persist_release_candidate(
    candidate: Mapping[str, Any], *, reports_root: Path | None = None
) -> Path:
    """Persist one immutable digest-addressed packet; perform no external write."""

    document = deepcopy(dict(candidate))
    supplied = _text(document.pop("candidate_digest", None))
    if supplied != _canonical_digest(document):
        raise PublicationAutopilotContractError("release candidate digest drifted")
    document["candidate_digest"] = supplied
    path = release_candidate_path(document, reports_root=reports_root)
    _require_local_authority_file(path, reports_root=reports_root)
    encoded = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    from shared_platform.immutable_approval_files import ImmutableFileError, persist_immutable_bytes

    root = reports_root or (ROOT / "reports" / "product-preparation")
    try:
        return persist_immutable_bytes(path, encoded.encode("utf-8"), root=root)
    except ImmutableFileError as error:
        raise PublicationAutopilotContractError("immutable release candidate file conflicts: " + str(error)) from error



def build_final_approval_receipt(
    candidate: Mapping[str, Any], *, approved_by: str,
    execution_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind the sole human publication approval to one frozen candidate."""

    if candidate.get("status") != "READY_FOR_FINAL_REVIEW":
        raise PublicationAutopilotContractError("release candidate is not ready")
    if str(approved_by or "").strip() != "Kyle":
        raise PublicationAutopilotContractError(
            "final approval must be attributed to Kyle"
        )
    receipt: dict[str, Any] = {
        "schema_version": "final-marketplace-approval/v1",
        "status": "APPROVED",
        "approval_kind": "FINAL_MARKETPLACE_PUBLISH",
        "approved_by": "Kyle",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "offer_id": _text(candidate.get("offer_id")),
        "plan_id": _text(candidate.get("plan_id")),
        "candidate_digest": _text(candidate.get("candidate_digest")),
        "snapshot_digest": _text(candidate.get("snapshot_digest")),
        "target_labels": list(candidate.get("target_labels") or []),
    }
    companion_actions = candidate.get("companion_actions")
    if isinstance(companion_actions, Mapping):
        receipt["companion_actions_digest"] = _canonical_digest(
            companion_actions
        )
    if execution_snapshot is not None:
        validate_release_candidate_for_execution(
            candidate,
            snapshot=execution_snapshot,
            platform_scope=candidate.get("platform_scope") or (),
            target_labels=candidate.get("target_labels") or (),
        )
        receipt["approved_execution_snapshot_digest"] = _text(
            execution_snapshot.get("snapshot_digest")
        )
    receipt["approval_digest"] = _canonical_digest(receipt)
    return receipt


def validate_release_candidate_for_execution(
    candidate: Mapping[str, Any], *, snapshot: Mapping[str, Any],
    platform_scope: Sequence[str], target_labels: Sequence[str],
) -> dict[str, Any]:
    """Validate a persisted final packet without changing policy or recompiling it."""
    document = deepcopy(dict(candidate))
    supplied = _text(document.pop("candidate_digest", None))
    if supplied != _canonical_digest(document):
        raise PublicationAutopilotContractError("release candidate digest drifted")
    document["candidate_digest"] = supplied
    business_snapshot_digest = _business_snapshot_digest(snapshot)
    candidate_business_digest = document.get(
        "business_snapshot_digest", document.get("snapshot_digest")
    )
    approved_execution_digest = document.get("approved_execution_snapshot_digest")
    has_explicit_business_digest = "business_snapshot_digest" in document
    has_explicit_execution_digest = "approved_execution_snapshot_digest" in document
    if (document.get("schema_version") != CANDIDATE_SCHEMA
        or document.get("status") != "READY_FOR_FINAL_REVIEW"
        or document.get("blockers") != []
        or any(document.get(key) != snapshot.get(key) for key in
            ("offer_id", "plan_id", "product_revision"))
        or document.get("snapshot_digest") != business_snapshot_digest
        or candidate_business_digest != business_snapshot_digest
        # A candidate compiled before approval cannot know the v4 execution
        # digest because that digest intentionally includes the real approval
        # timestamp.  Bind such candidates to the approval-neutral business
        # digest.  Existing candidates that also froze an execution digest
        # remain valid and continue to require an exact match.
        or (has_explicit_execution_digest and not has_explicit_business_digest)
        or (approved_execution_digest is not None
            and approved_execution_digest != snapshot.get("snapshot_digest"))):
        raise PublicationAutopilotContractError("release candidate frozen identity conflicts")
    platforms = tuple(_platform(value) for value in platform_scope)
    approved = tuple(label for label in document.get("target_labels", [])
        if label.split(":", 1)[0].upper() in platforms)
    selected_set = set(target_labels)
    expected = tuple(label for label in approved if label in selected_set)
    if (not expected or len(selected_set) != len(tuple(target_labels))
        or tuple(target_labels) != expected
        or not set(platforms).issubset(document.get("platform_scope", []))):
        raise PublicationAutopilotContractError("execution target scope conflicts with final candidate")
    for platform in platforms:
        labels = tuple(label for label in expected if label.split(":", 1)[0].upper() == platform)
        assert_write_within_release_candidate(document, platform=platform,
            target_labels=labels, confirmed_write_count=0)
        budget = document["write_budget"][platform]
        for key in ("shared_maximum", "per_target_maximum"):
            if type(budget.get(key)) is not int or budget[key] < 0:
                raise PublicationAutopilotContractError("frozen mutation budget is invalid")
        approved_labels = tuple(
            label for label in approved
            if label.split(":", 1)[0].upper() == platform
        )
        if budget["maximum_confirmed_writes"] != budget["shared_maximum"] + budget["per_target_maximum"] * len(approved_labels):
            raise PublicationAutopilotContractError("frozen mutation budget arithmetic conflicts")
    return document


def _business_snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    """Return approval-metadata-independent identity for frozen business facts."""

    from domains.product_operations import (
        validate_approved_publication_snapshot,
        validate_publication_business_snapshot,
    )

    if snapshot.get("schema_version") == "publication-business-snapshot/v1":
        return validate_publication_business_snapshot(snapshot)[
            "business_snapshot_digest"
        ]
    document = validate_approved_publication_snapshot(snapshot).payload()
    document.pop("snapshot_digest", None)
    document.pop("approved_at", None)
    document.pop("approved_by", None)
    document["schema_version"] = "publication-business-snapshot/v1"
    document["business_snapshot_digest"] = "sha256:" + _canonical_digest(document)
    return validate_publication_business_snapshot(document)[
        "business_snapshot_digest"
    ]


def load_release_candidate(offer_id: str, candidate_digest: str, *, reports_root: Path | None = None) -> dict[str, Any]:
    path = release_candidate_path({"schema_version":CANDIDATE_SCHEMA,
        "offer_id":offer_id,"candidate_digest":candidate_digest},reports_root=reports_root)
    _require_local_authority_file(path, reports_root=reports_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError) as error:
        raise PublicationAutopilotContractError("frozen release candidate is unavailable") from error
    if not isinstance(value,dict) or value.get("offer_id") != offer_id or value.get("candidate_digest") != candidate_digest:
        raise PublicationAutopilotContractError("frozen release candidate file identity conflicts")
    return value


def _require_local_authority_file(path: Path, *, reports_root: Path | None) -> None:
    root = reports_root or (ROOT / "reports" / "product-preparation")
    from shared_platform.immutable_approval_files import ImmutableFileError, require_local_path

    try:
        require_local_path(path, root=root, allow_directory=True)
    except ImmutableFileError as error:
        raise PublicationAutopilotContractError("authority must be a regular local file inside reports root") from error


def resolve_persisted_execution_authority(
    *, snapshot: Mapping[str, Any], platform_scope: Sequence[str],
    target_labels: Sequence[str], reports_root: Path,
    candidate_digest: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Resolve the server's durable candidate relation, never an opt-in body flag.

    A plan/platform without any candidate retains its existing v4 authority. Once
    a candidate exists, execution needs a valid final packet. Another pending
    packet does not revoke an already approved packet for the same frozen scope.
    Multiple valid approvals require the caller to identify the intended one.
    """
    offer_id = snapshot["offer_id"]
    requires_bound_final = str(snapshot.get('plan_id') or '').startswith('r3-marketplace:')
    if candidate_digest is not None:
        candidate = load_release_candidate(offer_id, candidate_digest, reports_root=reports_root)
        candidate = validate_release_candidate_for_execution(candidate, snapshot=snapshot,
            platform_scope=platform_scope, target_labels=target_labels)
        return candidate, load_final_approval_receipt(
            candidate, reports_root=reports_root, snapshot=snapshot
        )
    directory = release_candidate_path({"schema_version":CANDIDATE_SCHEMA,
        "offer_id":offer_id,"candidate_digest":"0"*64}, reports_root=reports_root).parent
    _require_local_authority_file(directory, reports_root=reports_root)
    if not directory.exists():
        if requires_bound_final:
            raise PublicationAutopilotContractError('marketplace approval binding is incomplete')
        return None
    related = False
    approved = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise PublicationAutopilotContractError("release candidate must be a regular local file")
        candidate = load_release_candidate(offer_id, path.stem, reports_root=reports_root)
        document = dict(candidate)
        digest = document.pop("candidate_digest", None)
        if digest != _canonical_digest(document):
            raise PublicationAutopilotContractError("release candidate digest drifted")
        if (candidate.get("plan_id") != snapshot["plan_id"]
            or candidate.get("snapshot_digest") != _business_snapshot_digest(snapshot)
            or not set(platform_scope).intersection(candidate.get("platform_scope", []))):
            continue
        selected_labels = tuple(label for label in candidate.get("target_labels", [])
            if label.split(":", 1)[0].upper() in platform_scope)
        requested = tuple(target_labels)
        requested_set = set(requested)
        if (not requested or len(requested_set) != len(requested)
            or tuple(label for label in selected_labels if label in requested_set) != requested):
            continue
        related = True
        try:
            candidate = validate_release_candidate_for_execution(candidate, snapshot=snapshot,
                platform_scope=platform_scope, target_labels=target_labels)
            approval = load_final_approval_receipt(
                candidate, reports_root=reports_root, snapshot=snapshot
            )
        except PublicationAutopilotContractError:
            continue
        approved.append((candidate, approval))
    if len(approved) == 1:
        return approved[0]
    if len(approved) > 1:
        raise PublicationAutopilotContractError("multiple final approvals exist; identify the approved candidate digest")
    if related or requires_bound_final:
        raise PublicationAutopilotContractError("persisted release candidate requires final marketplace approval for this exact execution scope")
    return None


def validate_final_approval_receipt(
    receipt: Mapping[str, Any], candidate: Mapping[str, Any], *,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    document = deepcopy(dict(receipt))
    supplied = _text(document.pop("approval_digest", None))
    if supplied != _canonical_digest(document):
        raise PublicationAutopilotContractError("final approval digest drifted")
    document["approval_digest"] = supplied
    candidate_requires_post_approval_binding = (
        "business_snapshot_digest" in candidate
        and "approved_execution_snapshot_digest" not in candidate
    )
    bound_execution_digest = document.get("approved_execution_snapshot_digest")
    candidate_companion_actions = candidate.get("companion_actions")
    expected_companion_digest = (
        _canonical_digest(candidate_companion_actions)
        if isinstance(candidate_companion_actions, Mapping)
        else None
    )
    if expected_companion_digest is not None and document.get(
        "companion_actions_digest"
    ) != expected_companion_digest:
        raise PublicationAutopilotContractError(
            "final approval companion-action scope drifted"
        )
    if candidate_requires_post_approval_binding and not (
        type(bound_execution_digest) is str
        and len(bound_execution_digest) == 71
        and bound_execution_digest.startswith("sha256:")
        and all(
            character in "0123456789abcdef"
            for character in bound_execution_digest[7:]
        )
    ):
        raise PublicationAutopilotContractError(
            "post-approval execution snapshot binding is missing"
        )
    if snapshot is not None and (
        candidate_requires_post_approval_binding
        and bound_execution_digest != snapshot.get("snapshot_digest")
    ):
        raise PublicationAutopilotContractError(
            "post-approval execution snapshot binding drifted"
        )
    if (
        document.get("schema_version") != "final-marketplace-approval/v1"
        or document.get("status") != "APPROVED"
        or document.get("approval_kind") != "FINAL_MARKETPLACE_PUBLISH"
        or document.get("approved_by") != "Kyle"
        or document.get("offer_id") != _text(candidate.get("offer_id"))
        or document.get("plan_id") != _text(candidate.get("plan_id"))
        or document.get("candidate_digest")
        != _text(candidate.get("candidate_digest"))
        or document.get("snapshot_digest")
        != _text(candidate.get("snapshot_digest"))
    ):
        raise PublicationAutopilotContractError("final approval identity drifted")
    if document.get("target_labels") != list(candidate.get("target_labels") or []):
        raise PublicationAutopilotContractError("final approval target scope drifted")
    return document


def final_approval_receipt_path(
    candidate: Mapping[str, Any], *, reports_root: Path | None = None
) -> Path:
    """Return the immutable approval path for exactly one candidate digest."""

    candidate_path = release_candidate_path(candidate, reports_root=reports_root)
    return candidate_path.parent / "approvals" / candidate_path.name


def persist_final_approval_receipt(
    receipt: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    reports_root: Path | None = None,
) -> Path:
    """Persist a digest-addressed approval without performing an external write."""

    document = validate_final_approval_receipt(receipt, candidate)
    path = final_approval_receipt_path(candidate, reports_root=reports_root)
    _require_local_authority_file(path, reports_root=reports_root)
    encoded = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    from shared_platform.immutable_approval_files import ImmutableFileError, persist_immutable_bytes

    root = reports_root or (ROOT / "reports" / "product-preparation")
    try:
        return persist_immutable_bytes(path, encoded.encode("utf-8"), root=root)
    except ImmutableFileError as error:
        raise PublicationAutopilotContractError("immutable final approval file conflicts: " + str(error)) from error



def load_final_approval_receipt(
    candidate: Mapping[str, Any], *, reports_root: Path | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load and revalidate the approval; any candidate drift invalidates it."""

    path = final_approval_receipt_path(candidate, reports_root=reports_root)
    _require_local_authority_file(path, reports_root=reports_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PublicationAutopilotContractError(
            "final marketplace approval is unavailable"
        ) from error
    if not isinstance(value, Mapping):
        raise PublicationAutopilotContractError("final approval format is invalid")
    return validate_final_approval_receipt(value, candidate, snapshot=snapshot)


__all__ = [
    "CANDIDATE_SCHEMA",
    "PublicationAutopilotContractError",
    "assert_write_within_release_candidate",
    "build_final_approval_receipt",
    "compile_release_candidate",
    "final_approval_receipt_path",
    "load_final_approval_receipt",
    "load_release_candidate",
    "load_autopilot_policy",
    "load_incident_registry",
    "persist_release_candidate",
    "persist_final_approval_receipt",
    "release_candidate_path",
    "resolve_persisted_execution_authority",
    "validate_autopilot_policy",
    "validate_incident_registry",
    "validate_final_approval_receipt",
    "validate_release_candidate_for_execution",
]
