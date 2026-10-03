"""Build a Shopee-category-attribute-only immutable release successor."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

APPROVAL_PATH_ENV = "ORBIT_SHOPEE_CATEGORY_SUCCESSOR_APPROVAL_PATH"
APPROVAL_OFFER_ENV = "ORBIT_SHOPEE_CATEGORY_SUCCESSOR_OFFER_ID"
APPROVAL_SHA256_ENV = "ORBIT_SHOPEE_CATEGORY_SUCCESSOR_APPROVAL_SHA256"
EVIDENCE_PATH_ENV = "ORBIT_SHOPEE_CATEGORY_OBSERVATION_PATH"
EVIDENCE_SHA256_ENV = "ORBIT_SHOPEE_CATEGORY_OBSERVATION_SHA256"
GLOBAL_SCAN_PATH_ENV = "ORBIT_SHOPEE_GLOBAL_SKU_SCAN_PATH"
GLOBAL_SCAN_SHA256_ENV = "ORBIT_SHOPEE_GLOBAL_SKU_SCAN_SHA256"
GLOBAL_SCAN_SKU_ENV = "ORBIT_SHOPEE_GLOBAL_SKU_SCAN_SKU"


def _read_pinned_json(path_text: str, expected_sha256: str, name: str) -> dict[str, Any]:
    path = Path(path_text).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{name} path must be an absolute file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError(f"{name} digest drifted")
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} is invalid")
    return value


def _predecessor_snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    schema = snapshot.get("schema_version")
    if schema == "publication-snapshot-preview/v1":
        if "snapshot_digest" in snapshot:
            raise ValueError("predecessor preview contains an approved digest")
        return _digest(snapshot.get("preview_digest"), "predecessor preview digest")
    if schema == "approved-publication-snapshot/v4":
        if "preview_digest" in snapshot:
            raise ValueError("approved predecessor contains a preview digest")
        return _digest(snapshot.get("snapshot_digest"), "predecessor snapshot digest")
    raise ValueError("predecessor snapshot schema is invalid")


def configured_successor_approval(offer_id: str) -> dict[str, Any] | None:
    selected = os.environ.get(APPROVAL_PATH_ENV)
    if not selected:
        return None
    pinned_offer = str(os.environ.get(APPROVAL_OFFER_ENV) or "").strip()
    approval_sha = str(os.environ.get(APPROVAL_SHA256_ENV) or "").strip()
    evidence_path = str(os.environ.get(EVIDENCE_PATH_ENV) or "").strip()
    evidence_sha = str(os.environ.get(EVIDENCE_SHA256_ENV) or "").strip()
    scan_path = str(os.environ.get(GLOBAL_SCAN_PATH_ENV) or "").strip()
    scan_sha = str(os.environ.get(GLOBAL_SCAN_SHA256_ENV) or "").strip()
    scan_sku = str(os.environ.get(GLOBAL_SCAN_SKU_ENV) or "").strip()
    if not all((pinned_offer, approval_sha, evidence_path, evidence_sha, scan_path, scan_sha, scan_sku)):
        raise ValueError("Shopee successor pin is incomplete")
    if str(offer_id).strip() != pinned_offer:
        return None
    approval = _read_pinned_json(selected, approval_sha, "Shopee successor approval")
    evidence = _read_pinned_json(evidence_path, evidence_sha, "Shopee official observation")
    scan = _read_pinned_json(scan_path, scan_sha, "Shopee official global SKU scan")
    decision = approved_category_decision(approval, offer_id=pinned_offer)
    public = evidence.get("public_observation")
    binding = evidence.get("account_binding")
    mandatory = [row for row in evidence.get("attribute_tree", []) if isinstance(row, Mapping) and row.get("is_mandatory") is True]
    selected_attribute = decision["required_attributes"]
    allowed = mandatory[0].get("attribute_value_list") if len(mandatory) == 1 else None
    selected_value = selected_attribute[0]["attribute_value_list"][0] if len(selected_attribute) == 1 else None
    if (
        evidence.get("schema_version") != "shopee-current-official-category-observation/v1"
        or evidence.get("offer_id") != pinned_offer
        or evidence.get("targets") != ["shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"]
        or evidence.get("authority") != "SHOPEE_OFFICIAL"
        or evidence.get("source_region") != "PH"
        or not isinstance(binding, Mapping)
        or binding.get("shop_region_exact") is not True
        or binding.get("merchant_identity_exact") is not True
        or not isinstance(public, Mapping)
        or public.get("schema_version") != "shopee-official-new-global-candidate-observation/v1"
        or public.get("authority") != "shopee_official_open_api"
        or (public.get("checks") or {}).get("recommendation_observed") is not True
        or (public.get("checks") or {}).get("selected_category_observed") is not True
        or (public.get("checks") or {}).get("selected_category_was_recommended") is not True
        or evidence.get("selected_category_id") != int(decision["category"]["id"])
        or [str(row.get("category_id")) for row in evidence.get("selected_category_path", [])] != [row["id"] for row in decision["category"]["path"]]
        or len(mandatory) != 1
        or mandatory[0].get("attribute_id") != selected_attribute[0].get("attribute_id")
        or not isinstance(allowed, list)
        or selected_value not in allowed
        or approval.get("decision_basis", {}).get("source_evidence_digest") != evidence.get("evidence_digest")
        or evidence.get("official_read_count") != len(evidence.get("official_reads", [])) + 1
        or evidence.get("platform_product_write_count") != 0
        or evidence.get("external_writes_performed") != []
    ):
        raise ValueError("Shopee official observation does not bind the approved decision")
    if (
        scan.get("schema_version") != "shopee-current-global-sku-scan/v1"
        or scan.get("offer_id") != pinned_offer
        or scan.get("seller_sku") != scan_sku
        or scan.get("model_sku") != scan_sku
        or scan.get("authority") != "SHOPEE_OFFICIAL"
        or scan.get("source_region") != "PH"
        or scan.get("scan_mode") != "NORMAL_UNLIST_BANNED_COMPLETE"
        or scan.get("matches") != []
        or scan.get("classification") != "NEW_GLOBAL"
        or scan.get("official_read_count") != len(scan.get("official_reads", [])) + 1
        or scan.get("platform_product_write_count") != 0
        or scan.get("external_writes_performed") != []
    ):
        raise ValueError("Shopee official global SKU scan does not prove NEW_GLOBAL")
    return approval


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _digest(value: object, name: str) -> str:
    text = str(value or "").strip()
    plain = text.removeprefix("sha256:")
    if len(plain) != 64 or any(char not in "0123456789abcdef" for char in plain):
        raise ValueError(f"{name} is invalid")
    return "sha256:" + plain


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def approved_category_decision(approval: Mapping[str, Any], *, offer_id: str) -> dict[str, Any]:
    if (
        not isinstance(approval, Mapping)
        or approval.get("schema_version") != "shopee-required-attribute-approval/v1"
        or approval.get("status") != "APPROVED"
        or approval.get("platform") != "shopee"
        or approval.get("offer_id") != offer_id
        or approval.get("approval_scope") != "SHOPEE_REQUIRED_CATEGORY_ATTRIBUTES_ONLY"
        or approval.get("approved_by") != "Kyle"
        or approval.get("platform_product_write_count") != 0
        or approval.get("external_writes_performed") != []
    ):
        raise ValueError("Shopee required-attribute approval is invalid")
    basis = approval.get("decision_basis")
    if (
        not isinstance(basis, Mapping)
        or basis.get("authority") != "SHOPEE_OFFICIAL"
        or not _text(basis.get("user_authorization"), "user authorization")
    ):
        raise ValueError("Shopee required-attribute authority is invalid")
    source_digest = _digest(basis.get("source_evidence_digest"), "Shopee official evidence digest")
    category = approval.get("category")
    if not isinstance(category, Mapping):
        raise ValueError("Shopee approved category is missing")
    category_id = _text(category.get("category_id"), "Shopee category_id")
    category_name = _text(category.get("category_name"), "Shopee category_name")
    raw_path = category.get("category_path")
    if type(raw_path) is not list or not raw_path:
        raise ValueError("Shopee category path is missing")
    path: list[dict[str, str]] = []
    for raw in raw_path:
        if not isinstance(raw, Mapping) or set(raw) != {"id", "name"}:
            raise ValueError("Shopee category path is invalid")
        path.append({"id": _text(raw.get("id"), "Shopee category path id"), "name": _text(raw.get("name"), "Shopee category path name")})
    if path[-1] != {"id": category_id, "name": category_name}:
        raise ValueError("Shopee category leaf conflicts with its path")
    raw_attributes = approval.get("required_attributes")
    if type(raw_attributes) is not list or not raw_attributes:
        raise ValueError("Shopee required attributes are missing")
    attributes: list[dict[str, Any]] = []
    seen_attributes: set[int] = set()
    for raw in raw_attributes:
        if not isinstance(raw, Mapping):
            raise ValueError("Shopee required attribute is invalid")
        attribute_id = raw.get("attribute_id")
        values = raw.get("attribute_value_list")
        if (
            type(attribute_id) is not int or attribute_id <= 0
            or attribute_id in seen_attributes or type(values) is not list or len(values) != 1
        ):
            raise ValueError("Shopee required attribute identity is invalid")
        value = values[0]
        if not isinstance(value, Mapping) or type(value.get("value_id")) is not int or value["value_id"] <= 0:
            raise ValueError("Shopee required attribute value is invalid")
        seen_attributes.add(attribute_id)
        attributes.append({
            "attribute_id": attribute_id,
            "attribute_value_list": [{
                "value_id": value["value_id"],
                "original_value_name": _text(value.get("original_value_name"), "Shopee required attribute value name"),
            }],
        })
    decision: dict[str, Any] = {
        "status": "APPROVED",
        "category": {"id": category_id, "name": category_name, "path": path},
        "required_attributes": attributes,
        "source_decision_digest": source_digest,
    }
    decision["decision_digest"] = _sha({"schema_version": "shopee-global-category-decision/v1", **decision})
    return decision


def build_shopee_category_attribute_successor_payload(
    predecessor_payload: Mapping[str, Any], *, predecessor_snapshot: Mapping[str, Any],
    approval: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(predecessor_payload, Mapping) or not isinstance(predecessor_snapshot, Mapping):
        raise TypeError("Shopee category successor requires frozen predecessor facts")
    offer_id = _text(predecessor_snapshot.get("offer_id"), "offer_id")
    if predecessor_payload.get("product_id") != offer_id:
        raise ValueError("Shopee category successor identity drifted")
    decision = approved_category_decision(approval, offer_id=offer_id)
    candidate = deepcopy(dict(predecessor_payload))
    master = candidate.get("shopee_global_master")
    if not isinstance(master, dict):
        raise ValueError("predecessor Shopee global master is missing")
    predecessor_decision = master.get("category_decision")
    if not isinstance(predecessor_decision, Mapping) or predecessor_decision.get("status") not in {"DEFERRED_TO_SKILL", "APPROVED"}:
        raise ValueError("predecessor Shopee category decision is invalid")
    master["category_decision"] = decision
    candidate["shopee_category_attribute_evidence"] = {
        "schema_version": "shopee-category-attribute-evidence/v1",
        "approval_digest": _sha(dict(approval)),
        "source_snapshot_digest": _predecessor_snapshot_digest(predecessor_snapshot),
        "decision_digest": decision["decision_digest"],
        "external_write_count": 0,
    }
    candidate.pop("plan_id", None)
    candidate["plan_id"] = "omnichannel:" + _sha(candidate).removeprefix("sha256:")
    return candidate


__all__ = ["approved_category_decision", "build_shopee_category_attribute_successor_payload", "configured_successor_approval"]
