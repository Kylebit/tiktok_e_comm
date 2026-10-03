"""Pure approved TikTok continuation protocol; no executor or network reader.

Derived from formal fb37a63b module revision 554d7b7d.
Execution and HTTPS asset reading are intentionally unavailable in this package.
"""

from __future__ import annotations


import hashlib


import json


from collections.abc import Callable, Mapping, Sequence


from copy import deepcopy


from typing import Any


from urllib.parse import urlparse


SCHEMA_VERSION = "tiktok-lineage-recovery-manifest/v2"


AUTHORITY_SCHEMA_VERSION = "tiktok-lineage-recovery-authority/v2"


RESULT_SCHEMA_VERSION = "tiktok-lineage-recovery-result/v2"


APPROVED_COMPLETION_SCHEMA_VERSION = "tiktok-approved-first-completion-manifest/v1"


APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION = "tiktok-approved-first-completion-manifest/v2"


APPROVED_COMPLETION_RETRY_DEPTH2_MANIFEST_SCHEMA_VERSION = "tiktok-approved-first-completion-manifest/v3"


APPROVED_COMPLETION_AUTHORITY_SCHEMA_VERSION = "tiktok-first-completion-authority-addendum/v2"


APPROVED_COMPLETION_RETRY_SCHEMA_VERSION = "tiktok-approved-first-completion-zero-write-retry/v1"


APPROVED_COMPLETION_RETRY_DEPTH2_SCHEMA_VERSION = "tiktok-approved-first-completion-zero-write-retry/v2"


EXACT_ASSET_REHOST_RECEIPT_SCHEMA_VERSION = "neutral-cdn-exact-byte-rehost-receipt/v1"


TRANSPORT_BINDING_KIND = "neutral-cdn-content-binding/v1"


TIKTOK_TARGET_ORDER = (
    "tiktok:LH_PH", "tiktok:LH_MY", "tiktok:LH_TH", "tiktok:LH_VN",
    "tiktok:MX", "tiktok:GB", "tiktok:HB_PH", "tiktok:HB_MY",
    "tiktok:HB_TH", "tiktok:HB_VN",
)


NON_RESUBMITTABLE_STATUSES = frozenset(
    {"UNKNOWN", "PROCESSING", "PUBLISHED", "SUBMITTED_UNVERIFIED"}
)


_FACT_FIELDS = frozenset(
    {"title", "description", "skus", "category", "gallery", "warehouse", "provider_target"}
)


class TikTokLineageRecoveryError(ValueError):
    pass


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise TikTokLineageRecoveryError(f"{field} is required")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TikTokLineageRecoveryError(f"{field} must be a mapping")
    return value


def _digest(value: object, field: str) -> str:
    result = _text(value, field).lower()
    if result.startswith("sha256:"):
        result = result[7:]
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise TikTokLineageRecoveryError(f"{field} must be a sha256 digest")
    return "sha256:" + result


def _approved_targets(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TikTokLineageRecoveryError("approved_lineage.targets must be a sequence")
    rows = tuple(_mapping(row, "approved target") for row in value)
    labels = tuple(_text(row.get("target_label"), "target_label") for row in rows)
    if labels != TIKTOK_TARGET_ORDER:
        raise TikTokLineageRecoveryError("approved lineage must contain the canonical ten TikTok targets")
    return rows


def _facts(value: object, field: str) -> dict[str, Any]:
    row = _mapping(value, field)
    if set(row) != _FACT_FIELDS:
        raise TikTokLineageRecoveryError(f"{field} fields are invalid")
    result = deepcopy(dict(row))
    for name in ("title", "description"):
        _text(result.get(name), f"{field}.{name}")
    skus = result.get("skus")
    if not isinstance(skus, list) or not skus:
        raise TikTokLineageRecoveryError(f"{field}.skus is invalid")
    seen_models = set()
    for index, sku in enumerate(skus):
        sku = _mapping(sku, f"{field}.skus[{index}]")
        if set(sku) != {"seller_sku", "model_sku", "price", "parcel", "specification", "variant_gallery"}:
            raise TikTokLineageRecoveryError(f"{field}.skus[{index}] fields are invalid")
        _text(sku.get("seller_sku"), f"{field}.skus[{index}].seller_sku")
        model = _text(sku.get("model_sku"), f"{field}.skus[{index}].model_sku")
        if model in seen_models: raise TikTokLineageRecoveryError(f"{field}.skus model_sku is duplicated")
        seen_models.add(model)
        price = _mapping(sku.get("price"), f"{field}.skus[{index}].price")
        if not price or any(type(k) is not str or not k or type(v) is not str or not v for k, v in price.items()):
            raise TikTokLineageRecoveryError(f"{field}.skus[{index}].price is invalid")
        parcel = _mapping(sku.get("parcel"), f"{field}.skus[{index}].parcel")
        if set(parcel) != {"weight_kg", "package_cm"}:
            raise TikTokLineageRecoveryError(f"{field}.skus[{index}].parcel is invalid")
        specification = _mapping(sku.get("specification"), f"{field}.skus[{index}].specification")
        if any(type(k) is not str or type(v) is not str for k, v in specification.items()):
            raise TikTokLineageRecoveryError(f"{field}.skus[{index}].specification is invalid")
        variant_gallery = sku.get("variant_gallery")
        if not isinstance(variant_gallery, list) or any(set(_mapping(asset, "variant gallery")) != {"content_digest"} or not _digest(asset.get("content_digest"), "variant content digest") for asset in variant_gallery):
            raise TikTokLineageRecoveryError(f"{field}.skus[{index}].variant_gallery is invalid")
    category = _mapping(result.get("category"), f"{field}.category")
    _text(category.get("id"), f"{field}.category.id")
    _digest(category.get("evidence_digest"), f"{field}.category.evidence_digest")
    gallery = result.get("gallery")
    if not isinstance(gallery, list) or not gallery:
        raise TikTokLineageRecoveryError(f"{field}.gallery is invalid")
    for index, asset in enumerate(gallery):
        asset = _mapping(asset, f"{field}.gallery[{index}]")
        if set(asset) != {"content_digest"}:
            raise TikTokLineageRecoveryError(f"{field}.gallery[{index}] fields are invalid")
        _digest(asset.get("content_digest"), f"{field}.gallery[{index}].content_digest")
    warehouse = _mapping(result.get("warehouse"), f"{field}.warehouse")
    if set(warehouse) != {"semantic_digest", "inventory_digest"}:
        raise TikTokLineageRecoveryError(f"{field}.warehouse is invalid")
    _digest(warehouse.get("semantic_digest"), f"{field}.warehouse.semantic_digest")
    _digest(warehouse.get("inventory_digest"), f"{field}.warehouse.inventory_digest")
    provider_target = _mapping(result.get("provider_target"), f"{field}.provider_target")
    if (
        provider_target.get("expected_title") != result["title"]
        or provider_target.get("expected_description") != result["description"]
        or provider_target.get("expected_skus") != result["skus"]
        or str(provider_target.get("expected_category_id") or "") != str(category.get("id"))
        or provider_target.get("expected_gallery") != result["gallery"]
        or provider_target.get("expected_warehouse") != result["warehouse"]
    ):
        raise TikTokLineageRecoveryError(f"{field}.provider_target conflicts")
    _canonical_digest(result)
    return result


def _transport_binding(value: object, approved_gallery: list[dict[str, Any]], field: str) -> dict[str, Any]:
    binding = _mapping(value, field)
    if set(binding) != {"kind", "assets", "verification_receipt_digest"}:
        raise TikTokLineageRecoveryError(f"{field} fields are invalid")
    if binding.get("kind") != TRANSPORT_BINDING_KIND:
        raise TikTokLineageRecoveryError("neutral CDN transport binding is required")
    verification = _digest(binding.get("verification_receipt_digest"), f"{field}.verification_receipt_digest")
    assets = binding.get("assets")
    if not isinstance(assets, list) or len(assets) != len(approved_gallery):
        raise TikTokLineageRecoveryError("neutral CDN asset coverage is invalid")
    normalized = []
    for index, (raw, approved) in enumerate(zip(assets, approved_gallery)):
        asset = _mapping(raw, f"{field}.assets[{index}]")
        if set(asset) != {"url", "content_digest", "verification_digest"}:
            raise TikTokLineageRecoveryError("neutral CDN asset fields are invalid")
        url = _text(asset.get("url"), f"{field}.assets[{index}].url")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise TikTokLineageRecoveryError("neutral CDN URL must be HTTPS")
        content = _digest(asset.get("content_digest"), f"{field}.assets[{index}].content_digest")
        if content != _digest(approved.get("content_digest"), "approved gallery content_digest"):
            raise TikTokLineageRecoveryError("neutral CDN content differs from approved gallery")
        normalized.append({"url": url, "content_digest": content,
                           "verification_digest": _digest(asset.get("verification_digest"), f"{field}.assets[{index}].verification_digest")})
    receipt_body = {"kind": TRANSPORT_BINDING_KIND, "assets": normalized}
    if verification != _canonical_digest(receipt_body):
        raise TikTokLineageRecoveryError("neutral CDN verification receipt is invalid")
    return {**receipt_body, "verification_receipt_digest": verification}


def _all_approved_assets(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [*facts["gallery"], *[
        asset for sku in facts["skus"] for asset in sku["variant_gallery"]
    ]]


def completion_evidence_from_addendum(addendum: Mapping[str, Any]) -> dict[str, Any]:
    """Project only execution-critical evidence from a trusted v2 addendum."""
    scope = _mapping(addendum.get("scope"), "authority_addendum.scope")
    included = scope.get("included_targets")
    if not isinstance(included, list): raise TikTokLineageRecoveryError("authority included targets are invalid")
    targets = {}
    for raw in included:
        row = _mapping(raw, "authority included target")
        label = _text(row.get("target_label"), "authority target_label")
        facts = _facts(row.get("facts"), f"authority {label}.facts")
        warehouse = _mapping(row.get("warehouse_authority"), f"authority {label}.warehouse_authority")
        targets[label] = {
            "existing_detail_id": _text(row.get("existing_detail_id"), f"authority {label}.existing_detail_id"),
            "shop_id": _text(row.get("shop_id"), f"authority {label}.shop_id"),
            "site_code": _text(row.get("site_code"), f"authority {label}.site_code"),
            "approved_content_digests": [asset["content_digest"] for asset in _all_approved_assets(facts)],
            "warehouse": deepcopy(facts["warehouse"]),
            "warehouse_authority": deepcopy(warehouse),
            "positive_stock": warehouse.get("positive_stock") is True,
            "preread_target_digest": _digest(row.get("preread_target_digest"), f"authority {label}.preread_target_digest"),
            "transport_binding": deepcopy(row.get("transport_binding")),
        }
    return {
        "approved_by": _text(addendum.get("subject"), "completion approved_by"),
        "approved_at": _text(addendum.get("created_at"), "completion approved_at"),
        "readonly_whole_file_digest": _digest(addendum.get("readonly_whole_file_digest"), "readonly whole-file digest"),
        "neutral_checkpoint_whole_file_digest": _digest(addendum.get("neutral_checkpoint_whole_file_digest"), "neutral checkpoint whole-file digest"),
        "canonical_ten_target_status_table": deepcopy(addendum.get("canonical_ten_target_status_table")),
        "targets": targets,
    }


def validate_tiktok_first_completion_authority_addendum(value: Mapping[str, Any]) -> dict[str, Any]:
    addendum = deepcopy(dict(_mapping(value, "authority_addendum")))
    body = dict(addendum); supplied = body.pop("authority_receipt_digest", None)
    if addendum.get("schema_version") != APPROVED_COMPLETION_AUTHORITY_SCHEMA_VERSION or supplied != _canonical_digest(body):
        raise TikTokLineageRecoveryError("first completion authority addendum integrity failed")
    if (addendum.get("authority_kind") != "first_exact_completion"
            or addendum.get("status") != "READY_FOR_RUNTIME_VALIDATION"
            or addendum.get("external_writes_during_artifact_generation") != {"neutral_cdn_upload": 1, "miaoshou": 0, "marketplace": 0}
            or _text(addendum.get("subject"), "authority subject") != "Kyle"):
        raise TikTokLineageRecoveryError("first completion authority state is invalid")
    chronology = addendum.get("user_authorization_chronology")
    if not isinstance(chronology, list) or len(chronology) < 4 or [row.get("sequence") for row in chronology] != list(range(1, len(chronology) + 1)):
        raise TikTokLineageRecoveryError("first completion session authority is invalid")
    scope = _mapping(addendum.get("scope"), "authority scope")
    included, excluded = scope.get("included_targets"), scope.get("excluded_targets")
    if (not isinstance(included, list) or [row.get("target_label") for row in included if isinstance(row, Mapping)] != list(label for label in TIKTOK_TARGET_ORDER if label not in {"tiktok:LH_PH", "tiktok:HB_PH"})
            or not isinstance(excluded, list)
            or [(row.get("target_label"), row.get("status"), row.get("retry_forbidden")) for row in excluded if isinstance(row, Mapping)] != [("tiktok:LH_PH", "PUBLISHED", True), ("tiktok:HB_PH", "PROCESSING", True)]):
        raise TikTokLineageRecoveryError("first completion included or excluded scope is invalid")
    constraints = _mapping(addendum.get("execution_constraints"), "authority execution constraints")
    state_machine = _mapping(constraints.get("state_machine"), "authority state machine")
    if (constraints.get("provider_channel") != "official_api_only"
            or constraints.get("browser_or_ui_control_allowed") is not False
            or constraints.get("create_detail_allowed") is not False
            or constraints.get("claim_detail_allowed") is not False
            or constraints.get("target_scope_expansion_allowed") is not False
            or state_machine != {"accepted_without_official_exact": "PROCESSING", "official_exact": "PUBLISHED", "unknown_write": "NO_RETRY"}):
        raise TikTokLineageRecoveryError("first completion execution constraints are invalid")
    matrix = addendum.get("canonical_ten_target_status_table")
    if not isinstance(matrix, list) or [row.get("target_label") for row in matrix if isinstance(row, Mapping)] != list(TIKTOK_TARGET_ORDER):
        raise TikTokLineageRecoveryError("first completion ten-target evidence is invalid")
    completion_evidence_from_addendum(addendum)
    addendum["authority_receipt_digest"] = supplied
    return addendum


def _authority(value: object, identity: Mapping[str, str], recovery_labels: tuple[str, ...]) -> dict[str, Any]:
    authority = _mapping(value, "authority")
    if authority.get("schema_version") != AUTHORITY_SCHEMA_VERSION:
        raise TikTokLineageRecoveryError("authority schema is invalid")
    for field in ("offer_id", "revision", "plan_id"):
        if _text(authority.get(field), f"authority.{field}") != identity[field]:
            raise TikTokLineageRecoveryError(f"authority {field} conflicts")
    for field in ("snapshot_digest", "candidate_digest", "approval_digest"):
        if _digest(authority.get(field), f"authority.{field}") != identity[field]:
            raise TikTokLineageRecoveryError(f"authority {field} conflicts")
    operations = _mapping(authority.get("target_operations"), "authority.target_operations")
    if tuple(operations) != recovery_labels:
        raise TikTokLineageRecoveryError("authority target scope must equal the ordered recovery subset")
    for label in recovery_labels:
        if operations[label] != {"save_draft": 1, "publish_target": 1}:
            raise TikTokLineageRecoveryError(f"{label} authority budget is invalid")
    receipt_body = {key: deepcopy(authority[key]) for key in authority if key != "authority_receipt_digest"}
    receipt_digest = _digest(authority.get("authority_receipt_digest"), "authority.authority_receipt_digest")
    if receipt_digest != _canonical_digest(receipt_body):
        raise TikTokLineageRecoveryError("authority receipt digest is invalid")
    return {"authority_receipt_digest": receipt_digest,
            "subject": _text(authority.get("subject"), "authority.subject"),
            "target_operations": deepcopy(dict(operations))}


def snapshot_tiktok_target_projection(snapshot: Mapping[str, Any], target_label: str) -> dict[str, Any]:
    """Project immutable snapshot facts that a continuation command may consume."""
    from domains.product_operations.approved_publication_snapshot import (
        publication_content_for_target,
        publication_images_for_target,
        validate_approved_publication_snapshot,
    )
    document = validate_approved_publication_snapshot(snapshot).payload()
    targets = {row["target_label"]: row for row in document["publication_targets"]}
    if target_label not in targets or not target_label.startswith("tiktok:"):
        raise TikTokLineageRecoveryError("snapshot TikTok target is unavailable")
    content = publication_content_for_target(document, target_label)
    skus = [{
        "seller_sku": row["seller_sku"], "model_sku": row["model_sku"],
        "price": deepcopy(row["prices"][target_label]),
        "parcel": deepcopy(row["parcel"]),
        "specification": deepcopy(row["specification"]),
        "variant_images": deepcopy(row["variant_images"]),
    } for row in document["skus"]]
    return {
        "target": deepcopy(targets[target_label]),
        "content": deepcopy(content),
        "images": publication_images_for_target(document, target_label),
        "category": deepcopy(document["categories_by_target"][target_label]),
        "skus": skus,
        "warehouse": deepcopy(document["product"].get("warehouse_inventory_by_target", {}).get(target_label)),
        "stock_policy": deepcopy(document["product"].get("stock_policy")),
    }


def _asset_reader_unavailable(_url: str) -> str:
    raise TikTokLineageRecoveryError('ASSET_READER_NOT_AVAILABLE: pass an explicitly supplied digest reader')


def assert_manifest_matches_approved_snapshot(manifest: Mapping[str, Any], snapshot: Mapping[str, Any], *,
                                             asset_digest_reader: Callable[[str], str] = _asset_reader_unavailable) -> None:
    """Reject self-consistent manifests whose executable facts differ from frozen approval."""
    checked = validate_tiktok_lineage_recovery_manifest(manifest)
    for command in checked["commands"]:
        projection = snapshot_tiktok_target_projection(snapshot, command["target_label"])
        if command["snapshot_projection_digest"] != _canonical_digest(projection):
            raise TikTokLineageRecoveryError("continuation command is not bound to approved snapshot facts")
        facts = command["approved_facts"]
        content, skus = projection["content"], projection["skus"]
        category = projection["category"].get("category")
        first_completion = checked.get("operation_kind") == "approved_first_completion"
        gallery = (facts["gallery"] if first_completion else [
            {"content_digest": _digest(asset_digest_reader(url), "approved image content digest")}
            for url in projection["images"]
        ])
        sku_facts_match = len(facts["skus"]) == len(skus) and all(
            supplied["seller_sku"] == frozen["seller_sku"]
            and supplied["model_sku"] == frozen["model_sku"]
            and supplied["price"] == frozen["price"]
            and supplied["parcel"] == frozen["parcel"]
            and supplied["specification"] == frozen["specification"]
            and supplied["variant_gallery"] == (supplied["variant_gallery"] if first_completion else [
                {"content_digest": _digest(asset_digest_reader(url), "approved variant image content digest")}
                for url in frozen["variant_images"]
            ])
            and len(supplied["variant_gallery"]) == len(frozen["variant_images"])
            for supplied, frozen in zip(facts["skus"], skus)
        )
        warehouse = projection["warehouse"]
        if first_completion:
            if warehouse not in (None, {}):
                raise TikTokLineageRecoveryError("first completion expects operational warehouse facts outside the frozen snapshot")
            expected_warehouse = facts["warehouse"]
        else:
            if not isinstance(warehouse, Mapping):
                raise TikTokLineageRecoveryError("approved target warehouse facts are unavailable")
            warehouse_rows = warehouse.get("warehouses")
            if not isinstance(warehouse_rows, list):
                raise TikTokLineageRecoveryError("approved target warehouse facts are invalid")
            warehouse_semantic = {
            "schema_version": warehouse.get("schema_version"), "shop_id": warehouse.get("shop_id"),
            "warehouses": [{"warehouse_id": row.get("warehouse_id"), "warehouse_name": row.get("warehouse_name")} for row in warehouse_rows],
            "source": warehouse.get("source"), "approved_by": warehouse.get("approved_by"),
            "approved_at": warehouse.get("approved_at"),
            }
            warehouse_inventory = {
            "shop_id": warehouse.get("shop_id"),
            "warehouses": [{"warehouse_id": row.get("warehouse_id"), "stock": row.get("stock")} for row in warehouse_rows],
            "total_stock": warehouse.get("total_stock"),
            }
            expected_warehouse = {"semantic_digest": _canonical_digest(warehouse_semantic),
                                  "inventory_digest": _canonical_digest(warehouse_inventory)}
        if (
            facts["title"] != content["title"]
            or facts["description"] != content["description"]
            or not sku_facts_match
            or not isinstance(category, Mapping)
            or str(facts["category"]["id"]) != str(category.get("id"))
            or facts["category"]["evidence_digest"] != _canonical_digest(projection["category"])
            or facts["gallery"] != gallery
            or len(facts["gallery"]) != len(projection["images"])
            or facts["warehouse"] != expected_warehouse
        ):
            raise TikTokLineageRecoveryError("continuation executable facts differ from approved snapshot")


def compile_tiktok_lineage_recovery(approved_lineage: Mapping[str, Any], observations: Mapping[str, Any], *,
                                    recovery_target_labels: Sequence[str], authority: Mapping[str, Any]) -> dict[str, Any]:
    """Compile a ten-target lineage into one exact, non-expanding continuation."""
    lineage = _mapping(approved_lineage, "approved_lineage")
    target_rows = _approved_targets(lineage.get("targets"))
    identity = {"offer_id": _text(lineage.get("offer_id"), "approved_lineage.offer_id"),
                "revision": _text(lineage.get("revision"), "approved_lineage.revision"),
                "plan_id": _text(lineage.get("plan_id"), "approved_lineage.plan_id"),
                "snapshot_digest": _digest(lineage.get("snapshot_digest"), "approved_lineage.snapshot_digest"),
                "candidate_digest": _digest(lineage.get("candidate_digest"), "approved_lineage.candidate_digest"),
                "approval_digest": _digest(lineage.get("approval_digest"), "approved_lineage.approval_digest")}
    source_evidence_digest = _digest(lineage.get("source_evidence_digest"), "approved_lineage.source_evidence_digest")
    source_run_id = _text(lineage.get("source_run_id"), "approved_lineage.source_run_id")
    recovery_labels = tuple(recovery_target_labels)
    recovery_set = set(recovery_labels)
    if not recovery_labels or len(recovery_labels) != len(recovery_set) or tuple(label for label in TIKTOK_TARGET_ORDER if label in recovery_set) != recovery_labels:
        raise TikTokLineageRecoveryError("recovery targets must be a unique ordered subset")
    if any(label not in TIKTOK_TARGET_ORDER for label in recovery_labels):
        raise TikTokLineageRecoveryError("recovery target expands approved scope")
    auth = _authority(authority, identity, recovery_labels)
    observed = _mapping(observations, "observations")
    if tuple(observed) != TIKTOK_TARGET_ORDER:
        raise TikTokLineageRecoveryError("observations must cover the canonical ten targets in order")
    commands, projections = [], []
    approved_by_label = {row["target_label"]: row for row in target_rows}
    for label in TIKTOK_TARGET_ORDER:
        approved_facts = _facts(approved_by_label[label].get("facts"), f"approved_lineage.targets.{label}.facts")
        observed_row = _mapping(observed[label], f"observations.{label}")
        status = _text(observed_row.get("status"), f"observations.{label}.status").upper()
        selected = label in recovery_set
        blockers: list[str] = []
        if selected and status != "FAILED": blockers.append("selected_target_is_not_failed")
        if not selected and status not in NON_RESUBMITTABLE_STATUSES: blockers.append("failed_target_missing_from_recovery_subset")
        if selected and (observed_row.get("outcome_known") is not True or observed_row.get("submission_accepted") is not False): blockers.append("zero_submission_failure_not_proven")
        try: normalized_observed_facts = _facts(observed_row.get("facts"), f"observations.{label}.facts")
        except TikTokLineageRecoveryError: normalized_observed_facts = None
        if normalized_observed_facts != approved_facts: blockers.append("approved_facts_changed")
        detail_id = str(observed_row.get("existing_detail_id") or "").strip()
        if selected and not detail_id: blockers.append("existing_detail_identity_required")
        if selected and observed_row.get("create_or_claim_allowed") is not False: blockers.append("create_or_claim_must_be_forbidden")
        transport = None
        if selected:
            try: transport = _transport_binding(observed_row.get("transport_binding"), _all_approved_assets(approved_facts), f"observations.{label}.transport_binding")
            except TikTokLineageRecoveryError as error: blockers.append(str(error))
        projection = {"target_label": label, "source_status": status, "selected": selected,
                      "runnable": selected and not blockers, "blockers": blockers,
                      "write_budget": auth["target_operations"].get(label, {"save_draft": 0, "publish_target": 0})}
        projections.append(projection)
        if selected and not blockers:
            command = {"target_label": label, "existing_detail_id": detail_id,
                       "approved_facts": approved_facts, "restored_fact_digest": _canonical_digest(approved_facts),
                       "snapshot_projection_digest": _digest(
                           approved_by_label[label].get("snapshot_projection_digest"),
                           f"approved_lineage.targets.{label}.snapshot_projection_digest",
                       ),
                       "transport_binding": transport, "write_budget": projection["write_budget"],
                       "forbidden_operations": ["create", "claim"], "no_scope_expansion": True}
            command["command_digest"] = _canonical_digest(command)
            commands.append(command)
    if any(row["blockers"] for row in projections) or tuple(row["target_label"] for row in commands) != recovery_labels:
        raise TikTokLineageRecoveryError("one or more selected recovery targets are blocked")
    body = {"schema_version": SCHEMA_VERSION, "lineage": identity,
            "source_evidence_digest": source_evidence_digest, "source_run_id": source_run_id,
            "authority_receipt_digest": auth["authority_receipt_digest"], "authority_subject": auth["subject"],
            "approved_target_labels": list(TIKTOK_TARGET_ORDER), "recovery_target_labels": list(recovery_labels),
            "targets": projections, "commands": commands, "no_scope_expansion": True,
            "default_provider_writes_enabled": False}
    body["manifest_digest"] = _canonical_digest(body)
    return body


def validate_tiktok_lineage_recovery_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping) and value.get("schema_version") in {
        APPROVED_COMPLETION_SCHEMA_VERSION,
        APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION,
        APPROVED_COMPLETION_RETRY_DEPTH2_MANIFEST_SCHEMA_VERSION,
    }:
        return validate_tiktok_approved_first_completion_manifest(value)
    if not isinstance(value, Mapping): raise TikTokLineageRecoveryError("manifest must be a mapping")
    manifest = deepcopy(dict(value)); supplied = dict(manifest); expected = supplied.pop("manifest_digest", None)
    if manifest.get("schema_version") != SCHEMA_VERSION or expected != _canonical_digest(supplied): raise TikTokLineageRecoveryError("manifest integrity check failed")
    if manifest.get("approved_target_labels") != list(TIKTOK_TARGET_ORDER) or manifest.get("no_scope_expansion") is not True: raise TikTokLineageRecoveryError("manifest approved scope is invalid")
    recovery, commands = manifest.get("recovery_target_labels"), manifest.get("commands")
    if not isinstance(recovery, list) or not isinstance(commands, list) or [row.get("target_label") for row in commands] != recovery: raise TikTokLineageRecoveryError("manifest command scope is invalid")
    for command in commands:
        body = dict(command); digest = body.pop("command_digest", None)
        if digest != _canonical_digest(body) or command.get("write_budget") != {"save_draft": 1, "publish_target": 1} or command.get("forbidden_operations") != ["create", "claim"] or command.get("no_scope_expansion") is not True: raise TikTokLineageRecoveryError("manifest command integrity is invalid")
        facts = _facts(command.get("approved_facts"), "manifest command approved_facts")
        if _digest(command.get("restored_fact_digest"), "manifest command restored_fact_digest") != _canonical_digest(facts):
            raise TikTokLineageRecoveryError("manifest restored facts digest is invalid")
        _digest(command.get("snapshot_projection_digest"), "manifest command snapshot_projection_digest")
        _transport_binding(command.get("transport_binding"), _all_approved_assets(facts), "manifest command transport_binding")
    lineage = _mapping(manifest.get("lineage"), "manifest.lineage")
    receipt_body = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "subject": _text(manifest.get("authority_subject"), "manifest.authority_subject"),
        **{field: lineage[field] for field in (
            "offer_id", "revision", "plan_id", "snapshot_digest",
            "candidate_digest", "approval_digest",
        )},
        "target_operations": {
            command["target_label"]: deepcopy(command["write_budget"])
            for command in commands
        },
    }
    if _digest(manifest.get("authority_receipt_digest"), "manifest.authority_receipt_digest") != _canonical_digest(receipt_body):
        raise TikTokLineageRecoveryError("manifest authority receipt is invalid")
    return manifest


def compile_tiktok_approved_first_completion(
    approved_lineage: Mapping[str, Any],
    preread: Mapping[str, Any],
    *,
    completion_target_labels: Sequence[str],
    authority_addendum: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile a first execution of an approved candidate over existing details only.

    This contract deliberately has no source_run_id/source failure claim.  The
    addendum is an input receipt; callers must persist it in a trusted store and
    independently re-resolve the candidate, approval and snapshot before use.
    """
    lineage = _mapping(approved_lineage, "approved_lineage")
    raw_targets = lineage.get("targets")
    if not isinstance(raw_targets, Sequence) or isinstance(raw_targets, (str, bytes, bytearray)):
        raise TikTokLineageRecoveryError("approved completion targets must be a sequence")
    target_rows = tuple(_mapping(row, "approved completion target") for row in raw_targets)
    identity = {
        "offer_id": _text(lineage.get("offer_id"), "approved_lineage.offer_id"),
        "revision": _text(lineage.get("revision"), "approved_lineage.revision"),
        "plan_id": _text(lineage.get("plan_id"), "approved_lineage.plan_id"),
        "snapshot_digest": _digest(lineage.get("snapshot_digest"), "approved_lineage.snapshot_digest"),
        "candidate_digest": _digest(lineage.get("candidate_digest"), "approved_lineage.candidate_digest"),
        "approval_digest": _digest(lineage.get("approval_digest"), "approved_lineage.approval_digest"),
    }
    labels = tuple(completion_target_labels)
    selected = set(labels)
    if not labels or len(labels) != len(selected) or tuple(x for x in TIKTOK_TARGET_ORDER if x in selected) != labels:
        raise TikTokLineageRecoveryError("completion targets must be a unique ordered subset")
    if tuple(_text(row.get("target_label"), "approved completion target label") for row in target_rows) != labels:
        raise TikTokLineageRecoveryError("approved completion facts must equal the ordered completion subset")
    addendum = validate_tiktok_first_completion_authority_addendum(authority_addendum)
    addendum_digest = addendum["authority_receipt_digest"]
    approved = _mapping(addendum.get("approved_lineage"), "authority_addendum.approved_lineage")
    if (str(addendum.get("offer_id")) != identity["offer_id"] or str(addendum.get("revision")) != identity["revision"]
            or approved.get("plan_id") != identity["plan_id"]
            or approved.get("execution_snapshot_digest") != identity["snapshot_digest"]
            or approved.get("candidate_digest") != identity["candidate_digest"]
            or approved.get("approval_digest") != identity["approval_digest"]):
        raise TikTokLineageRecoveryError("first completion authority lineage conflicts")
    scope = _mapping(addendum.get("scope"), "authority_addendum.scope")
    included = scope.get("included_targets")
    if not isinstance(included, list) or [row.get("target_label") for row in included if isinstance(row, Mapping)] != list(labels):
        raise TikTokLineageRecoveryError("first completion authority scope conflicts")
    commands, projections = [], []
    by_label = {row["target_label"]: row for row in target_rows}
    preread_map = _mapping(preread, "preread")
    if tuple(preread_map) != TIKTOK_TARGET_ORDER:
        raise TikTokLineageRecoveryError("preread must cover the canonical ten targets in order")
    included_by_label = {row["target_label"]: row for row in included}
    evidence_bindings = completion_evidence_from_addendum(addendum)
    for label in TIKTOK_TARGET_ORDER:
        row = _mapping(preread_map[label], f"preread.{label}")
        blockers = []
        if label in selected:
            facts = _facts(by_label[label].get("facts"), f"approved_lineage.targets.{label}.facts")
            scope_row = included_by_label[label]
            detail_id = str(row.get("existing_detail_id") or "").strip()
            if not detail_id or detail_id != str(scope_row.get("existing_detail_id") or "").strip(): blockers.append("existing_detail_identity_conflicts")
            if row.get("facts") != facts: blockers.append("approved_facts_changed")
            if row.get("create_or_claim_allowed") is not False or scope_row.get("create_or_claim_allowed") is not False: blockers.append("create_or_claim_must_be_forbidden")
            if scope_row.get("write_budget") != {"save_draft": 1, "publish_target": 1}: blockers.append("write_budget_conflicts")
            try:
                if _facts(scope_row.get("facts"), f"authority {label}.facts") != facts: blockers.append("authority_facts_conflict")
                warehouse_authority = _mapping(scope_row.get("warehouse_authority"), f"authority {label}.warehouse_authority")
                if (warehouse_authority.get("positive_stock") is not True
                        or warehouse_authority.get("unique_semantic_match") is not True
                        or warehouse_authority.get("fresh_reread_required_before_execution") is not True
                        or warehouse_authority.get("semantic_digest") != facts["warehouse"]["semantic_digest"]
                        or warehouse_authority.get("inventory_digest") != facts["warehouse"]["inventory_digest"]): blockers.append("warehouse_authority_conflicts")
            except TikTokLineageRecoveryError as error: blockers.append(str(error))
            try: transport = _transport_binding(row.get("transport_binding"), _all_approved_assets(facts), f"preread.{label}.transport_binding")
            except TikTokLineageRecoveryError as error: blockers.append(str(error)); transport = None
            if transport is not None and scope_row.get("transport_binding") != transport: blockers.append("authority_transport_conflicts")
            if not blockers:
                command = {"target_label": label, "existing_detail_id": detail_id, "approved_facts": facts,
                           "restored_fact_digest": _canonical_digest(facts),
                           "snapshot_projection_digest": _digest(by_label[label].get("snapshot_projection_digest"), f"approved_lineage.targets.{label}.snapshot_projection_digest"),
                           "transport_binding": transport, "write_budget": {"save_draft": 1, "publish_target": 1},
                           "forbidden_operations": ["create", "claim"], "no_scope_expansion": True}
                command["command_digest"] = _canonical_digest(command); commands.append(command)
        projections.append({"target_label": label, "source_status": str(row.get("status") or "UNKNOWN").upper(),
                            "selected": label in selected, "runnable": label in selected and not blockers,
                            "blockers": blockers, "write_budget": {"save_draft": 1, "publish_target": 1} if label in selected else {"save_draft": 0, "publish_target": 0}})
    if any(row["blockers"] for row in projections) or [row["target_label"] for row in commands] != list(labels):
        raise TikTokLineageRecoveryError("one or more approved completion targets are blocked")
    body = {"schema_version": APPROVED_COMPLETION_SCHEMA_VERSION, "operation_kind": "approved_first_completion",
            "lineage": identity, "authority_addendum_digest": addendum_digest,
            "evidence_bindings": evidence_bindings,
            "authority_subject": _text(addendum.get("subject"), "authority_addendum.subject"),
            "approved_target_labels": list(TIKTOK_TARGET_ORDER), "recovery_target_labels": list(labels),
            "completion_target_labels": list(labels), "targets": projections, "commands": commands,
            "no_scope_expansion": True, "default_provider_writes_enabled": False}
    body["manifest_digest"] = _canonical_digest(body)
    return body


def compile_tiktok_approved_first_completion_zero_write_retry(
    original_manifest: Mapping[str, Any],
    source_report: Mapping[str, Any],
    *,
    transport_rehost_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind one transport-only successor to an exact zero-write completion run."""
    original = validate_tiktok_approved_first_completion_manifest(original_manifest)
    report = deepcopy(dict(_mapping(source_report, "source_report")))
    source = validate_tiktok_approved_completion_zero_write_retry_source(
        report=report, original_manifest=original
    )
    report_digest = source["report_digest"]
    evidence = report["continuation_evidence"]

    receipt = deepcopy(dict(_mapping(transport_rehost_receipt, "transport_rehost_receipt")))
    receipt_body = dict(receipt)
    supplied_receipt_digest = receipt_body.pop("receipt_digest", None)
    source = _mapping(receipt.get("source"), "transport rehost source")
    transport = _mapping(receipt.get("transport"), "transport rehost transport")
    readbacks = transport.get("readbacks")
    content_digest = _digest(source.get("sha256"), "transport rehost content digest")
    direct_url = _text(transport.get("direct_url"), "transport rehost direct_url")
    if (
        receipt.get("schema_version") != EXACT_ASSET_REHOST_RECEIPT_SCHEMA_VERSION
        or supplied_receipt_digest != _canonical_digest(receipt_body)
        or receipt.get("target_label") != "tiktok:HB_TH"
        or receipt.get("offer_id") != original["lineage"]["offer_id"]
        or receipt.get("seller_sku") != "0988"
        or source.get("content_modified") is not False
        or not isinstance(readbacks, list) or len(readbacks) != 2
        or [(row.get("ordinal"), row.get("http_status"), row.get("final_url"), row.get("sha256")) for row in readbacks]
           != [(1, 200, direct_url, content_digest), (2, 200, direct_url, content_digest)]
        or receipt.get("external_writes") != {"neutral_cdn_upload": 1, "miaoshou": 0, "marketplace": 0}
    ):
        raise TikTokLineageRecoveryError("transport rehost receipt is invalid")
    parsed = urlparse(direct_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise TikTokLineageRecoveryError("transport rehost URL is invalid")

    retry = deepcopy(original)
    command = next(row for row in retry["commands"] if row["target_label"] == "tiktok:HB_TH")
    matching = [row for row in command["transport_binding"]["assets"] if row["content_digest"] == content_digest]
    if len(matching) != 1:
        raise TikTokLineageRecoveryError("transport replacement does not identify one approved asset")
    old_url = matching[0]["url"]
    old_verification_digest = matching[0]["verification_digest"]
    matching[0]["url"] = direct_url
    matching[0]["verification_digest"] = supplied_receipt_digest
    binding_body = {"kind": TRANSPORT_BINDING_KIND, "assets": command["transport_binding"]["assets"]}
    command["transport_binding"]["verification_receipt_digest"] = _canonical_digest(binding_body)
    command_body = dict(command); command_body.pop("command_digest")
    command["command_digest"] = _canonical_digest(command_body)
    retry["evidence_bindings"]["targets"]["tiktok:HB_TH"]["transport_binding"] = deepcopy(command["transport_binding"])
    retry["zero_write_retry"] = {
        "schema_version": APPROVED_COMPLETION_RETRY_SCHEMA_VERSION,
        "source_run_id": report["run_id"], "source_report_id": report["report_id"],
        "source_report_digest": report_digest,
        "source_result_digest": evidence["result_digest"],
        "source_manifest_digest": original["manifest_digest"],
        "transport_rehost_receipt_digest": supplied_receipt_digest,
        "replacement": {"target_label": "tiktok:HB_TH", "old_url": old_url,
                        "new_url": direct_url, "content_digest": content_digest,
                        "old_verification_digest": old_verification_digest},
        "automatic_retry": False, "no_scope_expansion": True,
    }
    retry["schema_version"] = APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION
    retry_body = dict(retry); retry_body.pop("manifest_digest")
    retry["manifest_digest"] = _canonical_digest(retry_body)
    return validate_tiktok_approved_first_completion_manifest(retry)


def _completion_executable_payload_digest(manifest: Mapping[str, Any]) -> str:
    return _canonical_digest({key: deepcopy(manifest[key]) for key in (
        "lineage", "authority_addendum_digest", "evidence_bindings",
        "authority_subject", "approved_target_labels", "recovery_target_labels",
        "completion_target_labels", "targets", "commands", "no_scope_expansion",
        "default_provider_writes_enabled",
    )})


def _publication_run_identity_digest(run: Mapping[str, Any]) -> str:
    return _canonical_digest({key: deepcopy(run[key]) for key in (
        "run_id", "report_id", "offer_id", "revision", "plan_id",
        "snapshot_schema_version", "snapshot_digest", "platform_scope",
        "target_count", "execution_identity", "request_identity",
    )})


def compile_tiktok_approved_first_completion_zero_write_retry_depth2(
    predecessor_manifest: Mapping[str, Any],
    source_report: Mapping[str, Any],
    *,
    source_run: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the sole depth-2 successor to an exact zero-write v2 run."""
    predecessor = validate_tiktok_approved_first_completion_manifest(predecessor_manifest)
    if predecessor["schema_version"] != APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION:
        raise TikTokLineageRecoveryError("depth-2 retry requires the exact v2 predecessor")
    run = deepcopy(dict(_mapping(source_run, "depth-2 source run")))
    source = validate_tiktok_approved_completion_zero_write_retry_source(
        report=source_report,
        original_manifest=predecessor,
        source_run=run,
        expected_source_run_id=_text(run.get("run_id"), "depth-2 source run_id"),
        expected_source_manifest_digest=predecessor["manifest_digest"],
    )
    report = source["report"]
    evidence = _mapping(report.get("continuation_evidence"), "depth-2 source evidence")
    source_request_identity = deepcopy(dict(_mapping(run.get("request_identity"), "depth-2 source request identity")))
    report_retry_source = deepcopy(dict(_mapping(evidence.get("retry_source"), "depth-2 source retry evidence")))
    report_binding_digest = report_retry_source.pop("binding_digest", None)
    if (report_retry_source != source_request_identity
            or report_binding_digest != _canonical_digest(source_request_identity)):
        raise TikTokLineageRecoveryError("depth-2 source request identity conflicts")
    predecessor_binding = deepcopy(dict(predecessor["zero_write_retry"]))
    retry = deepcopy(predecessor)
    binding = {
        "schema_version": APPROVED_COMPLETION_RETRY_DEPTH2_SCHEMA_VERSION,
        "retry_depth": 2,
        "source_run_id": run["run_id"],
        "source_report_id": report["report_id"],
        "source_report_digest": source["report_digest"],
        "source_result_digest": evidence["result_digest"],
        "source_manifest_digest": predecessor["manifest_digest"],
        "source_run_identity_digest": _publication_run_identity_digest(run),
        "source_request_identity": source_request_identity,
        "source_request_identity_digest": _canonical_digest(source_request_identity),
        "source_retry_binding_digest": report_binding_digest,
        "predecessor_retry_binding": predecessor_binding,
        "predecessor_retry_binding_digest": _canonical_digest(predecessor_binding),
        "root_manifest_digest": predecessor_binding["source_manifest_digest"],
        "transport_mode": "INHERIT_EXACT",
        "executable_payload_digest": _completion_executable_payload_digest(predecessor),
        "automatic_retry": False,
        "no_scope_expansion": True,
    }
    binding["chain_digest"] = _canonical_digest(binding)
    retry["schema_version"] = APPROVED_COMPLETION_RETRY_DEPTH2_MANIFEST_SCHEMA_VERSION
    retry["zero_write_retry"] = binding
    body = dict(retry); body.pop("manifest_digest")
    retry["manifest_digest"] = _canonical_digest(body)
    return validate_tiktok_approved_first_completion_manifest(retry)


def predecessor_tiktok_approved_completion_retry_manifest(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover and verify the exact v2 predecessor embedded by a v3 manifest."""
    manifest = validate_tiktok_approved_first_completion_manifest(value)
    if manifest["schema_version"] != APPROVED_COMPLETION_RETRY_DEPTH2_MANIFEST_SCHEMA_VERSION:
        raise TikTokLineageRecoveryError("completion manifest is not a depth-2 retry")
    binding = manifest["zero_write_retry"]
    predecessor = deepcopy(manifest)
    predecessor["schema_version"] = APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION
    predecessor["zero_write_retry"] = deepcopy(binding["predecessor_retry_binding"])
    body = dict(predecessor); body.pop("manifest_digest")
    predecessor["manifest_digest"] = _canonical_digest(body)
    if predecessor["manifest_digest"] != binding["source_manifest_digest"]:
        raise TikTokLineageRecoveryError("completion depth-2 predecessor manifest conflicts")
    return validate_tiktok_approved_first_completion_manifest(predecessor)


def validate_tiktok_approved_first_completion_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping): raise TikTokLineageRecoveryError("completion manifest must be a mapping")
    manifest = deepcopy(dict(value)); body = dict(manifest); supplied = body.pop("manifest_digest", None)
    schema = manifest.get("schema_version")
    if schema not in {APPROVED_COMPLETION_SCHEMA_VERSION, APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION,
                      APPROVED_COMPLETION_RETRY_DEPTH2_MANIFEST_SCHEMA_VERSION} or supplied != _canonical_digest(body):
        raise TikTokLineageRecoveryError("completion manifest integrity check failed")
    if (manifest.get("operation_kind") != "approved_first_completion"
            or manifest.get("approved_target_labels") != list(TIKTOK_TARGET_ORDER)
            or manifest.get("no_scope_expansion") is not True
            or manifest.get("default_provider_writes_enabled") is not False):
        raise TikTokLineageRecoveryError("completion manifest scope is invalid")
    lineage = _mapping(manifest.get("lineage"), "completion lineage")
    for field in ("offer_id", "revision", "plan_id"): _text(lineage.get(field), f"completion lineage.{field}")
    for field in ("snapshot_digest", "candidate_digest", "approval_digest"): _digest(lineage.get(field), f"completion lineage.{field}")
    _digest(manifest.get("authority_addendum_digest"), "completion authority_addendum_digest")
    evidence = _mapping(manifest.get("evidence_bindings"), "completion evidence_bindings")
    target_evidence = _mapping(evidence.get("targets"), "completion evidence targets")
    labels = manifest.get("completion_target_labels")
    if (not isinstance(labels, list) or len(labels) != len(set(labels))
            or tuple(label for label in TIKTOK_TARGET_ORDER if label in set(labels)) != tuple(labels)
            or labels != manifest.get("recovery_target_labels")
            or [row.get("target_label") for row in manifest.get("commands", [])] != labels):
        raise TikTokLineageRecoveryError("completion command scope is invalid")
    targets = manifest.get("targets")
    if not isinstance(targets, list) or [row.get("target_label") for row in targets if isinstance(row, Mapping)] != list(TIKTOK_TARGET_ORDER):
        raise TikTokLineageRecoveryError("completion target projection is invalid")
    for command in manifest["commands"]:
        command_body = dict(command); digest = command_body.pop("command_digest", None)
        if digest != _canonical_digest(command_body) or command.get("write_budget") != {"save_draft": 1, "publish_target": 1} or command.get("forbidden_operations") != ["create", "claim"]:
            raise TikTokLineageRecoveryError("completion command integrity is invalid")
        facts = _facts(command.get("approved_facts"), "completion command facts")
        if command.get("restored_fact_digest") != _canonical_digest(facts): raise TikTokLineageRecoveryError("completion facts digest is invalid")
        _digest(command.get("snapshot_projection_digest"), "completion snapshot projection digest")
        _transport_binding(command.get("transport_binding"), _all_approved_assets(facts), "completion transport binding")
        evidence_row = _mapping(target_evidence.get(command["target_label"]), "completion target evidence")
        if (evidence_row.get("existing_detail_id") != command["existing_detail_id"]
                or evidence_row.get("approved_content_digests") != [row["content_digest"] for row in _all_approved_assets(facts)]
                or evidence_row.get("warehouse") != facts["warehouse"]
                or evidence_row.get("positive_stock") is not True):
            raise TikTokLineageRecoveryError("completion operational evidence conflicts")
    retry = manifest.get("zero_write_retry")
    if ((schema == APPROVED_COMPLETION_SCHEMA_VERSION and retry is not None)
            or (schema in {APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION,
                           APPROVED_COMPLETION_RETRY_DEPTH2_MANIFEST_SCHEMA_VERSION} and retry is None)):
        raise TikTokLineageRecoveryError("completion manifest retry schema conflicts")
    if retry is not None and schema == APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION:
        retry = _mapping(retry, "completion zero_write_retry")
        if (set(retry) != {"schema_version", "source_run_id", "source_report_id", "source_report_digest",
                           "source_result_digest", "source_manifest_digest", "transport_rehost_receipt_digest",
                           "replacement", "automatic_retry", "no_scope_expansion"}
                or retry.get("schema_version") != APPROVED_COMPLETION_RETRY_SCHEMA_VERSION
                or retry.get("source_report_id") != "publication-report:" + _text(retry.get("source_run_id"), "retry source_run_id")
                or retry.get("automatic_retry") is not False or retry.get("no_scope_expansion") is not True):
            raise TikTokLineageRecoveryError("completion zero-write retry binding is invalid")
        for field in ("source_report_digest", "source_result_digest", "source_manifest_digest", "transport_rehost_receipt_digest"):
            _digest(retry.get(field), "retry " + field)
        replacement = _mapping(retry.get("replacement"), "retry replacement")
        if (set(replacement) != {"target_label", "old_url", "new_url", "content_digest", "old_verification_digest"}
                or replacement.get("target_label") != "tiktok:HB_TH"
                or replacement.get("old_url") == replacement.get("new_url")):
            raise TikTokLineageRecoveryError("completion zero-write retry replacement is invalid")
        _digest(replacement.get("content_digest"), "retry replacement content_digest")
        _digest(replacement.get("old_verification_digest"), "retry replacement old_verification_digest")
        if any(urlparse(_text(replacement.get(field), "retry replacement " + field)).scheme != "https"
               for field in ("old_url", "new_url")):
            raise TikTokLineageRecoveryError("completion zero-write retry URL is invalid")
    if retry is not None and schema == APPROVED_COMPLETION_RETRY_DEPTH2_MANIFEST_SCHEMA_VERSION:
        retry = _mapping(retry, "completion depth-2 retry")
        fields = {
            "schema_version", "retry_depth", "source_run_id", "source_report_id",
            "source_report_digest", "source_result_digest", "source_manifest_digest",
            "source_run_identity_digest", "source_request_identity",
            "source_request_identity_digest", "source_retry_binding_digest",
            "predecessor_retry_binding", "predecessor_retry_binding_digest",
            "root_manifest_digest", "transport_mode", "executable_payload_digest",
            "automatic_retry", "no_scope_expansion", "chain_digest",
        }
        if (set(retry) != fields
                or retry.get("schema_version") != APPROVED_COMPLETION_RETRY_DEPTH2_SCHEMA_VERSION
                or retry.get("retry_depth") != 2
                or retry.get("source_report_id") != "publication-report:" + _text(retry.get("source_run_id"), "depth-2 source_run_id")
                or retry.get("transport_mode") != "INHERIT_EXACT"
                or retry.get("automatic_retry") is not False
                or retry.get("no_scope_expansion") is not True):
            raise TikTokLineageRecoveryError("completion depth-2 retry binding is invalid")
        for field in (
            "source_report_digest", "source_result_digest", "source_manifest_digest",
            "source_run_identity_digest", "source_request_identity_digest",
            "source_retry_binding_digest", "predecessor_retry_binding_digest",
            "root_manifest_digest", "executable_payload_digest", "chain_digest",
        ):
            _digest(retry.get(field), "depth-2 " + field)
        chain_body = dict(retry); chain_digest = chain_body.pop("chain_digest")
        predecessor_binding = _mapping(retry.get("predecessor_retry_binding"), "depth-2 predecessor binding")
        source_identity = _mapping(retry.get("source_request_identity"), "depth-2 source request identity")
        if (chain_digest != _canonical_digest(chain_body)
                or retry["predecessor_retry_binding_digest"] != _canonical_digest(predecessor_binding)
                or retry["source_request_identity_digest"] != _canonical_digest(source_identity)
                or retry["source_retry_binding_digest"] != retry["source_request_identity_digest"]
                or retry["root_manifest_digest"] != predecessor_binding.get("source_manifest_digest")
                or retry["executable_payload_digest"] != _completion_executable_payload_digest(manifest)):
            raise TikTokLineageRecoveryError("completion depth-2 retry chain conflicts")
        expected_source_identity = {
            "kind": "TIKTOK_FIRST_COMPLETION_ZERO_WRITE_RETRY",
            "authority_digest": manifest["authority_addendum_digest"],
            "manifest_digest": retry["source_manifest_digest"],
            "retry_of_run_id": predecessor_binding.get("source_run_id"),
            "source_report_digest": predecessor_binding.get("source_report_digest"),
            "source_result_digest": predecessor_binding.get("source_result_digest"),
            "source_manifest_digest": predecessor_binding.get("source_manifest_digest"),
        }
        if dict(source_identity) != expected_source_identity:
            raise TikTokLineageRecoveryError("completion depth-2 source identity conflicts")
        predecessor = deepcopy(manifest)
        predecessor["schema_version"] = APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION
        predecessor["zero_write_retry"] = deepcopy(dict(predecessor_binding))
        predecessor_body = dict(predecessor); predecessor_body.pop("manifest_digest")
        predecessor["manifest_digest"] = _canonical_digest(predecessor_body)
        if predecessor["manifest_digest"] != retry["source_manifest_digest"]:
            raise TikTokLineageRecoveryError("completion depth-2 predecessor manifest conflicts")
        validate_tiktok_approved_first_completion_manifest(predecessor)
    return manifest


def validate_tiktok_approved_completion_zero_write_retry_source(
    *, report: Mapping[str, Any], original_manifest: Mapping[str, Any],
    source_run: Mapping[str, Any] | None = None,
    expected_source_run_id: str | None = None,
    expected_source_manifest_digest: str | None = None,
) -> dict[str, Any]:
    """Prove the exact immutable Path-B source failed before every mutation."""
    from shared_platform.product_publication_reports import validate_publication_report

    original = validate_tiktok_approved_first_completion_manifest(original_manifest)
    retry_binding = original.get("zero_write_retry")
    source_manifest_digest = expected_source_manifest_digest or (
        retry_binding["source_manifest_digest"] if retry_binding is not None
        else original["manifest_digest"]
    )
    expected_run_id = expected_source_run_id or (
        source_run.get("run_id") if isinstance(source_run, Mapping) else report.get("run_id")
    )
    expected_run_id = _text(expected_run_id, "retry source run_id")
    raw = deepcopy(dict(_mapping(report, "source_report")))
    try:
        validate_publication_report({
            key: value for key, value in raw.items()
            if key not in {"report_path", "summary_digest", "created_at", "updated_at"}
        })
    except (TypeError, ValueError) as error:
        raise TikTokLineageRecoveryError("retry source publication report is invalid") from error
    evidence = _mapping(raw.get("continuation_evidence"), "source continuation evidence")
    selected = set(original["completion_target_labels"])
    excluded = set(TIKTOK_TARGET_ORDER) - selected
    source_projection = {row["target_label"]: row for row in original["targets"]}
    public_order = (
        "tiktok:LH_PH", "tiktok:LH_MY", "tiktok:LH_TH", "tiktok:LH_VN",
        "tiktok:HB_PH", "tiktok:HB_MY", "tiktok:HB_TH", "tiktok:HB_VN",
        "tiktok:MX", "tiktok:GB",
    )
    rows = evidence.get("targets")
    public_rows = raw.get("targets")
    budgets = raw.get("mutation_budgets")
    if (
        raw.get("run_id") != expected_run_id
        or raw.get("report_id") != "publication-report:" + expected_run_id
        or raw.get("offer_id") != original["lineage"]["offer_id"] or str(raw.get("revision")) != str(original["lineage"]["revision"])
        or raw.get("plan_id") != original["lineage"]["plan_id"]
        or (raw.get("snapshot") or {}).get("digest") != original["lineage"]["snapshot_digest"]
        or raw.get("status") != "PARTIAL"
        or raw.get("release_authorization") != {
            "candidate_digest": original["lineage"]["candidate_digest"].removeprefix("sha256:"),
            "approval_digest": original["lineage"]["approval_digest"].removeprefix("sha256:"),
        }
        or evidence.get("manifest_digest") != source_manifest_digest
        or evidence.get("lineage") != original["lineage"] or evidence.get("reservations") != []
        or evidence.get("confirmed_write_count") != 0 or evidence.get("unknown_write_count") != 0
        or evidence.get("automatic_retry_performed") is not False or evidence.get("no_scope_expansion") is not True
        or evidence.get("remaining_target_labels") != [
            label for label in TIKTOK_TARGET_ORDER if label != "tiktok:LH_PH"
        ]
        or (raw.get("summary") or {}).get("evidence") != {
            "snapshot_verified": True, "dispatch_attempted": False,
            "readback_completed": True, "external_write_count": 0,
        }
        or not isinstance(rows, list) or [row.get("target_label") for row in rows] != list(TIKTOK_TARGET_ORDER)
        or not isinstance(public_rows, list) or [row.get("target_label") for row in public_rows] != list(public_order)
        or any(row.get("evidence") is not None for row in public_rows)
        or not isinstance(budgets, list) or len(budgets) != 1
    ):
        raise TikTokLineageRecoveryError("retry source does not prove the exact zero-write completion failure")
    budget = budgets[0]
    attempts = budget.get("attempts") if isinstance(budget, Mapping) else None
    if (budget.get("platform") != "TIKTOK" or budget.get("reservations") != []
            or not isinstance(attempts, Mapping) or attempts.get("shared") != 0 or attempts.get("total") != 0
            or attempts.get("per_target") != {label: 0 for label in TIKTOK_TARGET_ORDER}):
        raise TikTokLineageRecoveryError("retry source mutation ledger is not zero-write")
    by_label = {row["target_label"]: row for row in rows}
    public_by_label = {row["target_label"]: row for row in public_rows}
    zero_attempts = {"save_draft": 0, "publish_target": 0, "official_readback": 0}
    for label in selected:
        if (by_label[label] != {"target_label": label, "status": "FAILED", "stage": "VERIFY",
                                "attempts": zero_attempts, "confirmed_write_count": 0,
                                "unknown_write_count": 0, "reason": "continuation_target_exception"}
                or public_by_label[label] != {"target_label": label, "status": "FAILED", "evidence": None}):
            raise TikTokLineageRecoveryError("retry target is not an exact zero-write VERIFY failure")
    for label in excluded:
        expected_status = source_projection[label]["source_status"]
        if (by_label[label] != {"target_label": label, "status": expected_status, "stage": "BLOCKED",
                                "attempts": zero_attempts, "confirmed_write_count": 0,
                                "unknown_write_count": 0, "reason": "outside_recovery_subset"}
                or public_by_label[label] != {"target_label": label, "status": expected_status, "evidence": None}):
            raise TikTokLineageRecoveryError("retry excluded target state changed")
    if source_run is not None:
        run = _mapping(source_run, "retry source run")
        if (run.get("run_id") != raw["run_id"] or run.get("report_id") != raw["report_id"]
                or run.get("final_report_id") != raw["report_id"] or run.get("state") != "COMPLETED"
                or run.get("offer_id") != raw["offer_id"] or run.get("revision") != raw["revision"]
                or run.get("plan_id") != raw["plan_id"] or run.get("snapshot_digest") != raw["snapshot"]["digest"]
                or run.get("platform_scope") != ["TIKTOK"] or run.get("target_count") != 10
                or run.get("execution_identity") != raw.get("execution_identity")):
            raise TikTokLineageRecoveryError("retry source run identity conflicts")
    return {"report_digest": _canonical_digest(raw), "report": raw}


class TikTokLineageVerificationFailure(RuntimeError):
    """Fixed report-safe codes shared by verification and pure result validation."""

    _CODES = frozenset({
        "ASSET_DIGEST_CONFLICT", "ASSET_DIGEST_MISMATCH", "ASSET_READ_UNAVAILABLE",
        "SCOPE_VERIFY_BLOCKED", "TARGET_VERIFY_FAILED",
    })

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("TikTok verification failure code is invalid")
        super().__init__(code)
        self.code = code


def validate_tiktok_lineage_recovery_result(value: Mapping[str, Any], *, manifest: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_tiktok_lineage_recovery_manifest(manifest)
    if not isinstance(value, Mapping):
        raise TikTokLineageRecoveryError("continuation result must be a mapping")
    result = deepcopy(dict(value)); body = dict(result); supplied = body.pop("result_digest", None)
    if supplied != _canonical_digest(body) or result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise TikTokLineageRecoveryError("continuation result integrity check failed")
    if result.get("manifest_digest") != checked["manifest_digest"] or result.get("lineage") != checked["lineage"]:
        raise TikTokLineageRecoveryError("continuation result lineage conflicts")
    verification = result.get("verification_summary")
    if verification is not None:
        verification = _mapping(verification, "continuation verification summary")
        fields = {"schema_version", "status", "target_count", "unique_asset_count", "failures", "verification_digest"}
        verification_body = dict(verification); verification_digest = verification_body.pop("verification_digest", None)
        failures = verification.get("failures")
        if (set(verification) != fields
                or verification.get("schema_version") != "tiktok-scope-verification/v1"
                or verification.get("status") not in {"VERIFIED", "FAILED"}
                or verification.get("target_count") != len(checked["recovery_target_labels"])
                or type(verification.get("unique_asset_count")) is not int
                or verification["unique_asset_count"] < 0
                or not isinstance(failures, list)
                or verification_digest != _canonical_digest(verification_body)
                or (verification["status"] == "VERIFIED") != (failures == [])):
            raise TikTokLineageRecoveryError("continuation verification summary is invalid")
        for failure in failures:
            if (not isinstance(failure, Mapping)
                    or set(failure) != {"target_label", "stage", "code"}
                    or failure.get("target_label") not in checked["recovery_target_labels"]
                    or failure.get("stage") != "VERIFY"
                    or failure.get("code") not in TikTokLineageVerificationFailure._CODES - {"SCOPE_VERIFY_BLOCKED"}):
                raise TikTokLineageRecoveryError("continuation verification failure is invalid")
    rows = result.get("targets")
    if not isinstance(rows, list) or [row.get("target_label") for row in rows if isinstance(row, Mapping)] != list(TIKTOK_TARGET_ORDER):
        raise TikTokLineageRecoveryError("continuation result target coverage conflicts")
    for row in rows:
        if set(row) != {"target_label", "status", "stage", "attempts", "confirmed_write_count", "unknown_write_count", "reason"}:
            raise TikTokLineageRecoveryError("continuation result target fields are invalid")
        attempts = row["attempts"]
        if not isinstance(attempts, Mapping) or set(attempts) != {"save_draft", "publish_target", "official_readback"} or any(type(count) is not int or count not in {0, 1} for count in attempts.values()):
            raise TikTokLineageRecoveryError("continuation result attempts are invalid")
        if row["status"] not in {"PUBLISHED", "PROCESSING", "FAILED", "UNKNOWN"} or type(row["confirmed_write_count"]) is not int or type(row["unknown_write_count"]) is not int or row["confirmed_write_count"] < 0 or row["unknown_write_count"] < 0:
            raise TikTokLineageRecoveryError("continuation result target outcome is invalid")
        mutation_attempts = attempts["save_draft"] + attempts["publish_target"]
        if row["confirmed_write_count"] + row["unknown_write_count"] > mutation_attempts:
            raise TikTokLineageRecoveryError("continuation result target writes exceed reserved attempts")
        if row["target_label"] not in checked["recovery_target_labels"] and (
            mutation_attempts != 0 or row["confirmed_write_count"] != 0 or row["unknown_write_count"] != 0
        ):
            raise TikTokLineageRecoveryError("continuation result wrote outside recovery scope")
    reservations = result.get("reservations")
    if not isinstance(reservations, list):
        raise TikTokLineageRecoveryError("continuation result reservations are invalid")
    seen = set()
    for ordinal, reservation in enumerate(reservations, 1):
        if not isinstance(reservation, Mapping) or set(reservation) != {"target_label", "operation", "ordinal"} or reservation.get("ordinal") != ordinal:
            raise TikTokLineageRecoveryError("continuation result reservation shape is invalid")
        pair = (reservation.get("target_label"), reservation.get("operation"))
        if pair in seen or pair[0] not in checked["recovery_target_labels"] or pair[1] not in {"save_draft", "publish_target"}:
            raise TikTokLineageRecoveryError("continuation result reservation scope is invalid")
        seen.add(pair)
    by_label = {row["target_label"]: row for row in rows}
    if any(by_label[label]["attempts"][operation] != (1 if (label, operation) in seen else 0)
           for label in checked["recovery_target_labels"] for operation in ("save_draft", "publish_target")):
        raise TikTokLineageRecoveryError("continuation reservations conflict with attempts")
    if result.get("confirmed_write_count") != sum(row["confirmed_write_count"] for row in rows) or result.get("unknown_write_count") != sum(row["unknown_write_count"] for row in rows):
        raise TikTokLineageRecoveryError("continuation result write counts conflict")
    if result.get("remaining_target_labels") != [row["target_label"] for row in rows if row["status"] != "PUBLISHED"]:
        raise TikTokLineageRecoveryError("continuation remaining targets conflict")
    if result.get("automatic_retry_performed") is not False or result.get("no_scope_expansion") is not True:
        raise TikTokLineageRecoveryError("continuation result governance conflicts")
    return result


__all__ = ['APPROVED_COMPLETION_AUTHORITY_SCHEMA_VERSION', 'APPROVED_COMPLETION_RETRY_DEPTH2_MANIFEST_SCHEMA_VERSION', 'APPROVED_COMPLETION_RETRY_DEPTH2_SCHEMA_VERSION', 'APPROVED_COMPLETION_RETRY_MANIFEST_SCHEMA_VERSION', 'APPROVED_COMPLETION_RETRY_SCHEMA_VERSION', 'APPROVED_COMPLETION_SCHEMA_VERSION', 'AUTHORITY_SCHEMA_VERSION', 'RESULT_SCHEMA_VERSION', 'SCHEMA_VERSION', 'TIKTOK_TARGET_ORDER', 'TRANSPORT_BINDING_KIND', 'TikTokLineageRecoveryError', 'assert_manifest_matches_approved_snapshot', 'compile_tiktok_approved_first_completion', 'compile_tiktok_approved_first_completion_zero_write_retry', 'compile_tiktok_approved_first_completion_zero_write_retry_depth2', 'compile_tiktok_lineage_recovery', 'completion_evidence_from_addendum', 'predecessor_tiktok_approved_completion_retry_manifest', 'snapshot_tiktok_target_projection', 'validate_tiktok_approved_completion_zero_write_retry_source', 'validate_tiktok_approved_first_completion_manifest', 'validate_tiktok_first_completion_authority_addendum', 'validate_tiktok_lineage_recovery_manifest', 'validate_tiktok_lineage_recovery_result']

