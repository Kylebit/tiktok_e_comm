"""Exact, existing-item-only recovery for a partially published Shopee run."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from modules.shopee.skill_regions import (
    ShopeeRegionRuntime,
    shopee_description_image_ids,
    shopee_description_text,
)
from shared_platform.product_publication_executors import _result


SCHEMA_VERSION = "shopee-regional-recovery/v1"
RESULT_SCHEMA_VERSION = "shopee-regional-recovery-result/v1"
_TARGET = re.compile(r"^shopee:(MY|TH|VN)$")
_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_ALLOWED_ACTIONS = (
    "update_images_existing_media",
    "update_copy_and_description_media",
    "update_description_media",
    "list_existing_item",
)
_MEDIA_ALGORITHM = "exif-rgb-contain-pad256-sha256+dhash32-bidirectional/v1"
_MEDIA_MAX_DISTANCE = 0.08
_MEDIA_MIN_MARGIN = 0.04
_BUILDER_RELATIVE_PATH = "skills/publish-approved-product/scripts/build_shopee_existing_media_binding.py"
_BUILDER_DIGEST_ALGORITHM = "sha256-normalized-lf/v1"
_LEGACY_BUILDER_SOURCE_SHA256 = (
    "sha256:e01944eed35a8cf6d63c50cf5949f381a0e18e7ff9e9ba67ddb29e1d1f0b5eaa"
)
_LEGACY_BUILDER_GIT_BLOB = "fc0ea714bb4dc462e0150eba360e9206322a1253"
_LEGACY_BUILDER_AUDIT_COMMIT = "69caed4767e8db88462ec7c2e609b4949b346b50"


def _normalized_source_bytes(value: bytes) -> bytes:
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _portable_source_identity(value: bytes) -> dict[str, str]:
    normalized = _normalized_source_bytes(value)
    blob = b"blob " + str(len(normalized)).encode("ascii") + b"\0" + normalized
    return {
        "algorithm": _BUILDER_DIGEST_ALGORITHM,
        "repo_relative_path": _BUILDER_RELATIVE_PATH,
        "sha256": _file_digest(normalized),
        "git_blob_sha1": "sha1:" + hashlib.sha1(blob).hexdigest(),
    }


def _trusted_legacy_builder_source(
    *, repo_root: Path, commit: str = _LEGACY_BUILDER_AUDIT_COMMIT,
    relative_path: str = _BUILDER_RELATIVE_PATH,
    blob_id: str = _LEGACY_BUILDER_GIT_BLOB,
    expected_sha256: str = _LEGACY_BUILDER_SOURCE_SHA256,
) -> bool:
    try:
        tree_blob = subprocess.check_output(
            ["git", "rev-parse", f"{commit}:{relative_path}"],
            cwd=repo_root, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        raw = subprocess.check_output(
            ["git", "cat-file", "blob", blob_id], cwd=repo_root,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    git_object = hashlib.sha1(
        b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    ).hexdigest()
    return (
        tree_blob == blob_id
        and git_object == blob_id
        and _file_digest(raw) == expected_sha256
    )


class ShopeeRegionalRecoveryError(ValueError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _positive_id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text.isdecimal() or int(text) <= 0:
        raise ShopeeRegionalRecoveryError(f"{label} is invalid")
    return text


def _safe_reason(value: object) -> str:
    if isinstance(value, ShopeeRegionalRecoveryError):
        return "Recovery contract validation failed"
    return f"Provider {type(value).__name__[:64]}"


def _evidence_digest(value: Mapping[str, object]) -> str:
    body = deepcopy(dict(value))
    supplied = body.pop("evidence_digest", None)
    expected = _digest(body)
    if supplied != expected:
        raise ShopeeRegionalRecoveryError("official readback evidence digest conflicts")
    return expected


def _candidate_target(candidate: Mapping[str, object], label: str) -> tuple[dict, dict, dict]:
    review = candidate.get("review_manifest")
    if not isinstance(review, Mapping):
        raise ShopeeRegionalRecoveryError("release candidate review manifest is missing")
    targets = [row for row in review.get("targets") or []
               if isinstance(row, Mapping) and row.get("target_label") == label]
    if len(targets) != 1:
        raise ShopeeRegionalRecoveryError("release candidate target is ambiguous")
    target = dict(targets[0])
    copies = [row for row in review.get("copy_sets") or []
              if isinstance(row, Mapping) and row.get("copy_set_id") == target.get("copy_set_id")]
    images = [row for row in review.get("image_sets") or []
              if isinstance(row, Mapping) and row.get("image_set_id") == target.get("image_set_id")]
    if len(copies) != 1 or len(images) != 1:
        raise ShopeeRegionalRecoveryError("approved copy or image route is ambiguous")
    return target, dict(copies[0]), dict(images[0])


def build_recovery_manifest(
    *, candidate: Mapping[str, object], approval: Mapping[str, object],
    prior_report: Mapping[str, object], global_readback: Mapping[str, object],
    global_model_readback: Mapping[str, object],
    regional_readback: Mapping[str, object], source_files: Mapping[str, str | Path],
    media_binding_receipt: Mapping[str, object], expected_prior_run_id: str,
    allowed_source_roots: Sequence[str | Path],
    target_labels: Sequence[str] = ("shopee:MY", "shopee:TH", "shopee:VN"),
    predecessor_attempt: Mapping[str, object] | None = None,
    reconciliation_receipt: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a zero-write recovery candidate from immutable and official facts."""
    labels = tuple(target_labels)
    if not labels or len(set(labels)) != len(labels) or any(not _TARGET.fullmatch(x) for x in labels):
        raise ShopeeRegionalRecoveryError("recovery target scope is invalid")
    continuation = None
    continuation_actions: dict[str, list[str]] = {}
    if (predecessor_attempt is None) != (reconciliation_receipt is None):
        raise ShopeeRegionalRecoveryError("recovery continuation evidence is incomplete")
    if predecessor_attempt is not None:
        from shared_platform.shopee_recovery_coordination import continuation_identity
        if predecessor_attempt.get("recovery_authorization") is None:
            raise ShopeeRegionalRecoveryError("recovery predecessor authority is unavailable")
        old_manifest = predecessor_attempt["recovery_authorization"]
        old_digest = str(old_manifest.get("manifest_digest") or "")
        old_snapshot = {
            "offer_id": candidate.get("offer_id"), "product_revision": candidate.get("product_revision"),
            "plan_id": candidate.get("plan_id"),
            "snapshot_digest": old_manifest.get("execution_snapshot_digest"),
        }
        if validate_recovery_manifest(
            old_manifest, snapshot=old_snapshot, candidate=candidate, approval=approval,
            allowed_source_roots=allowed_source_roots,
        ) != dict(old_manifest):
            raise ShopeeRegionalRecoveryError("recovery predecessor manifest is not deeply validated")
        remaining = reconciliation_receipt.get("new_manifest", {}).get("exact_remaining_differences") \
            if isinstance(reconciliation_receipt, Mapping) else None
        if not isinstance(remaining, list):
            raise ShopeeRegionalRecoveryError("recovery continuation differences are unavailable")
        for row in remaining:
            if not isinstance(row, Mapping):
                raise ShopeeRegionalRecoveryError("recovery continuation differences are invalid")
            differences = set(row.get("differences") or [])
            actions = set()
            if differences & {"copy.title", "copy.description"}:
                actions.add("update_copy_and_description_media")
            elif differences & {"description.type", "description.images"}:
                actions.add("update_description_media")
            if "gallery.images" in differences:
                actions.add("update_images_existing_media")
            if "item.status" in differences:
                actions.add("list_existing_item")
            continuation_actions[str(row.get("target_label") or "")] = sorted(actions)
        continuation = continuation_identity(
            reconciliation_receipt, run_id=str(predecessor_attempt.get("run_id") or ""),
            report_id=str(predecessor_attempt.get("report_id") or ""),
            manifest_digest=old_digest, allowed_evidence_roots=allowed_source_roots,
            target_scope=labels, action_budgets=continuation_actions,
        )
        continuation["direct_predecessor"] = {
            "run_id": predecessor_attempt.get("run_id"),
            "report_id": predecessor_attempt.get("report_id"),
            "report_digest": _digest(predecessor_attempt),
            "manifest_digest": old_digest,
        }
        continuation["original_ancestor"] = {
            "run_id": old_manifest.get("prior_run_id"),
            "report_digest": old_manifest.get("prior_report_digest"),
            "candidate_digest": old_manifest.get("candidate_digest"),
            "approval_digest": old_manifest.get("approval_digest"),
        }
        receipt_time = str(reconciliation_receipt.get("official_direct_id_readback", {}).get("observed_at") or "")
        preflight_times = [str(source.get("observed_at") or "") for source in
                           (regional_readback, global_readback, global_model_readback)]
        try:
            receipt_stamp = datetime.fromisoformat(receipt_time.replace("Z", "+00:00"))
            if any(datetime.fromisoformat(value.replace("Z", "+00:00")) <= receipt_stamp
                   for value in preflight_times):
                raise ShopeeRegionalRecoveryError("fresh continuation preflight must postdate reconciliation")
        except ValueError as error:
            raise ShopeeRegionalRecoveryError("recovery continuation timestamps are invalid") from error
    candidate_digest = str(candidate.get("candidate_digest") or "")
    business_snapshot_digest = str(candidate.get("snapshot_digest") or "")
    execution_snapshot_digest = str((prior_report.get("snapshot") or {}).get("digest") or "")
    approval_digest = str(approval.get("approval_digest") or "")
    if any(not _DIGEST.fullmatch(value) for value in
           (candidate_digest, business_snapshot_digest, execution_snapshot_digest, approval_digest)):
        raise ShopeeRegionalRecoveryError("approved identity is invalid")
    if any((
        candidate.get("schema_version") != "publication-release-candidate/v1",
        candidate.get("status") != "READY_FOR_FINAL_REVIEW",
        approval.get("schema_version") != "final-marketplace-approval/v1",
        approval.get("status") != "APPROVED",
        approval.get("candidate_digest") != candidate_digest,
        approval.get("snapshot_digest") != business_snapshot_digest,
        approval.get("offer_id") != candidate.get("offer_id"),
        approval.get("plan_id") != candidate.get("plan_id"),
        approval.get("target_labels") != candidate.get("target_labels"),
        any(label not in (approval.get("target_labels") or []) for label in labels),
    )):
        raise ShopeeRegionalRecoveryError("final approval does not bind the recovery scope")
    expected_prior_run_id = str(expected_prior_run_id or "").strip()
    if not expected_prior_run_id:
        raise ShopeeRegionalRecoveryError("prior run identity is invalid")
    candidate_shopee_labels = [label for label in candidate.get("target_labels") or []
                               if isinstance(label, str) and label.startswith("shopee:")]
    prior_rows = prior_report.get("targets") or []
    prior_labels = [row.get("target_label") for row in prior_rows if isinstance(row, Mapping)]
    prior_targets = {row.get("target_label"): row.get("status")
                     for row in prior_rows if isinstance(row, Mapping)}
    common_prior_conflict = (
        prior_report.get("schema_version") != "product-publication-report/v2"
        or prior_report.get("report_id") != f"publication-report:{expected_prior_run_id}"
        or prior_report.get("revision") != candidate.get("product_revision")
        or prior_report.get("offer_id") != candidate.get("offer_id")
        or prior_report.get("plan_id") != candidate.get("plan_id")
        or prior_report.get("run_id") != expected_prior_run_id
        or (prior_report.get("snapshot") or {}).get("digest") != execution_snapshot_digest
        or prior_report.get("release_authorization") != {
            "candidate_digest": candidate_digest, "approval_digest": approval_digest}
    )
    if continuation is None:
        prior_conflict = (
            prior_labels != candidate_shopee_labels
            or len(prior_targets) != len(candidate_shopee_labels)
            or prior_report.get("status") != "PARTIAL"
            or any(prior_targets.get(label) != "FAILED" for label in labels)
            or any(prior_targets.get(label) != "PUBLISHED"
                   for label in candidate_shopee_labels if label not in labels)
        )
    else:
        old_manifest = predecessor_attempt["recovery_authorization"]
        old_labels = old_manifest.get("target_labels") if isinstance(old_manifest, Mapping) else None
        prior_conflict = (
            dict(prior_report) != dict(predecessor_attempt)
            or prior_labels != old_labels
            or len(prior_targets) != len(old_labels or [])
            or prior_report.get("status") not in {"PROCESSING", "PARTIAL"}
            or old_manifest.get("candidate_digest") != candidate_digest
            or old_manifest.get("approval_digest") != approval_digest
            or old_manifest.get("business_snapshot_digest") != business_snapshot_digest
            or old_manifest.get("execution_snapshot_digest") != execution_snapshot_digest
            or old_manifest.get("offer_id") != candidate.get("offer_id")
            or old_manifest.get("plan_id") != candidate.get("plan_id")
            or old_manifest.get("product_revision") != candidate.get("product_revision")
            or any(label not in (old_labels or []) for label in labels)
        )
    if common_prior_conflict or prior_conflict:
        raise ShopeeRegionalRecoveryError("prior partial report does not bind failed targets")
    if global_readback.get("schema_version") != "shopee-global-published-list-readback/v1" \
            or global_readback.get("product_writes") != 0:
        raise ShopeeRegionalRecoveryError("global official readback is invalid")
    if regional_readback.get("schema_version") != "shopee-regional-recovery-preflight/v1" \
            or regional_readback.get("product_writes") != 0:
        raise ShopeeRegionalRecoveryError("regional official readback is invalid")
    regional_digest = _evidence_digest(regional_readback)
    if global_model_readback.get("schema_version") != "shopee-global-model-readback/v1" \
            or global_model_readback.get("product_writes") != 0:
        raise ShopeeRegionalRecoveryError("global model official readback is invalid")
    global_model_digest = _evidence_digest(global_model_readback)
    global_item_id = _positive_id(regional_readback.get("global_item_id"), "global item id")
    if str(global_readback.get("global_item_id") or "") != global_item_id:
        raise ShopeeRegionalRecoveryError("official global identity conflicts")
    if str(global_model_readback.get("global_item_id") or "") != global_item_id:
        raise ShopeeRegionalRecoveryError("official global model identity conflicts")
    approved_model_skus = {
        str(price.get("model_sku") or "")
        for label in labels
        for _target, _copy, _images in [_candidate_target(candidate, label)]
        for price in (_target.get("prices") or [])
        if isinstance(price, Mapping)
    }
    if len(approved_model_skus) != 1 or not next(iter(approved_model_skus)):
        raise ShopeeRegionalRecoveryError("approved model SKU is ambiguous")
    approved_model_sku = next(iter(approved_model_skus))
    official_global_models = [row for row in global_model_readback.get("models") or []
                              if isinstance(row, Mapping)
                              and row.get("global_model_sku") == approved_model_sku]
    if len(official_global_models) != 1:
        raise ShopeeRegionalRecoveryError("official global model is ambiguous")
    global_model_id = _positive_id(official_global_models[0].get("global_model_id"), "global model id")
    published_rows = (((global_readback.get("response") or {}).get("response") or {})
                      .get("published_item") or [])
    published = {(str(row.get("shop_region") or "").upper(), str(row.get("shop_id") or "")):
                 str(row.get("item_id") or "") for row in published_rows if isinstance(row, Mapping)}
    regional_rows = {row.get("target_label"): row for row in regional_readback.get("targets") or []
                     if isinstance(row, Mapping)}
    builder_path = Path(__file__).parents[1] / _BUILDER_RELATIVE_PATH
    builder_identity = _portable_source_identity(builder_path.read_bytes())
    recorded_identity = media_binding_receipt.get("builder_source_identity")
    schema_version = media_binding_receipt.get("schema_version")
    legacy_builder_valid = (
        schema_version == "shopee-existing-media-content-binding/v1"
        and recorded_identity is None
        and media_binding_receipt.get("builder_code_sha256")
        == _LEGACY_BUILDER_SOURCE_SHA256
        and _trusted_legacy_builder_source(repo_root=Path(__file__).parents[1])
    )
    if (schema_version not in {
                "shopee-existing-media-content-binding/v1",
                "shopee-existing-media-content-binding/v2",
            }
            or media_binding_receipt.get("product_writes") != 0
            or media_binding_receipt.get("prior_run_id") != expected_prior_run_id
            or media_binding_receipt.get("candidate_digest") != candidate_digest
            or media_binding_receipt.get("regional_readback_digest") != regional_digest
            or media_binding_receipt.get("status") != "VERIFIED_UNIQUE"
            or media_binding_receipt.get("algorithm") != _MEDIA_ALGORITHM
            or media_binding_receipt.get("max_perceptual_distance") != _MEDIA_MAX_DISTANCE
            or media_binding_receipt.get("minimum_second_best_margin") != _MEDIA_MIN_MARGIN
            or (schema_version == "shopee-existing-media-content-binding/v1"
                and media_binding_receipt.get("builder_code_sha256")
                != _LEGACY_BUILDER_SOURCE_SHA256)
            or (schema_version == "shopee-existing-media-content-binding/v2"
                and media_binding_receipt.get("builder_code_sha256")
                != builder_identity["sha256"])
            or (schema_version == "shopee-existing-media-content-binding/v2"
                and recorded_identity != builder_identity)
            or (recorded_identity is not None and recorded_identity != builder_identity)
            or (recorded_identity is None and not legacy_builder_valid)):
        raise ShopeeRegionalRecoveryError("existing media binding receipt is invalid")
    media_digest = _evidence_digest(media_binding_receipt)
    media_rows = {row.get("target_label"): row for row in media_binding_receipt.get("targets") or []
                  if isinstance(row, Mapping)}
    targets = []
    for label in labels:
        observed = regional_rows.get(label)
        if not isinstance(observed, Mapping):
            raise ShopeeRegionalRecoveryError("official regional target is missing")
        target, copy_set, image_set = _candidate_target(candidate, label)
        region = label.split(":", 1)[1]
        shop_id = _positive_id(observed.get("shop_id"), "shop id")
        item_id = _positive_id(observed.get("item_id"), "item id")
        if published.get((region, shop_id)) != item_id:
            raise ShopeeRegionalRecoveryError("merchant and shop readbacks disagree")
        models = observed.get("models") or []
        prices = target.get("prices") or []
        if len(models) != 1 or len(prices) != 1:
            raise ShopeeRegionalRecoveryError("recovery requires one exact model")
        model, price = models[0], prices[0]
        if not isinstance(model, Mapping) or not isinstance(price, Mapping):
            raise ShopeeRegionalRecoveryError("model or price evidence is invalid")
        model_id = _positive_id(model.get("model_id"), "model id")
        if model.get("model_sku") != price.get("model_sku"):
            raise ShopeeRegionalRecoveryError("model SKU conflicts")
        expected_category = str(target.get("category", {}).get("id") or "")
        differences = set()
        if continuation is not None:
            differences = set(next(row["differences"] for row in continuation["exact_remaining_differences"]
                                   if row["target_label"] == label))
        required_status = "UNLIST" if continuation is None or "item.status" in differences else "NORMAL"
        if (str(observed.get("item_status") or "").upper() != required_status
                or str(observed.get("category_id") or "") != expected_category):
            raise ShopeeRegionalRecoveryError("official status or category conflicts")
        price_rows = [row for row in model.get("price_info") or [] if isinstance(row, Mapping)
                      and str(row.get("currency") or "").upper() == str(price.get("currency") or "").upper()]
        if len(price_rows) != 1 or Decimal(str(price_rows[0].get("original_price"))) != Decimal(str(price.get("amount"))):
            raise ShopeeRegionalRecoveryError("official regional price conflicts")
        gallery_ids = [str(x or "").strip() for x in observed.get("gallery_image_ids") or []]
        gallery_urls = [str(x or "").strip() for x in observed.get("gallery_image_urls") or []]
        images = image_set.get("images") or []
        if (len(gallery_ids) != len(images) or not gallery_ids
                or any(not x for x in gallery_ids) or len(set(gallery_ids)) != len(gallery_ids)):
            raise ShopeeRegionalRecoveryError("existing media identity coverage conflicts")
        if len(gallery_urls) != len(gallery_ids) or any(
            not url.endswith(media_id) for url, media_id in zip(gallery_urls, gallery_ids)
        ):
            raise ShopeeRegionalRecoveryError("official media URL identity conflicts")
        media_row = media_rows.get(label)
        expected_sources = [str(row.get("url") or "") for row in images]
        bindings = media_row.get("bindings") if isinstance(media_row, Mapping) else None
        if (not isinstance(media_row, Mapping)
                or media_row.get("item_id") != item_id
                or media_row.get("ordered_source_urls") != expected_sources
                or media_row.get("ordered_media_ids") != gallery_ids
                or media_row.get("image_set_id") != target.get("image_set_id")
                or media_row.get("route_digest") != _digest({"ordered_urls": expected_sources})
                or not isinstance(bindings, list) or len(bindings) != len(images)
                or any(not isinstance(binding, Mapping)
                       or binding.get("source_url") != source_url
                       or binding.get("provider_url") != gallery_url
                       or binding.get("provider_media_id") != media_id
                       or binding.get("match") not in {"NORMALIZED_PIXEL_EXACT", "UNIQUE_PERCEPTUAL"}
                       or not _DIGEST.fullmatch(str(binding.get("source_sha256") or ""))
                       or not _DIGEST.fullmatch(str(binding.get("provider_sha256") or ""))
                       or not _DIGEST.fullmatch(str(binding.get("source_pixel_sha256") or ""))
                       or not _DIGEST.fullmatch(str(binding.get("provider_pixel_sha256") or ""))
                       or not _valid_media_match(binding)
                       for binding, source_url, media_id, gallery_url in zip(
                           bindings, expected_sources, gallery_ids, gallery_urls))):
            raise ShopeeRegionalRecoveryError("exact existing media binding is unproved")
        if continuation is None:
            budget_rows = [row for row in prior_report.get("mutation_budgets") or []
                           if isinstance(row, Mapping) and row.get("platform") == "SHOPEE"]
            if len(budget_rows) != 1:
                raise ShopeeRegionalRecoveryError("prior Shopee mutation budget is ambiguous")
            reservations = budget_rows[0].get("reservations") or []
            target_operations = [row.get("operation") for row in reservations
                                 if isinstance(row, Mapping) and row.get("target_label") == label]
            if target_operations.count("upload_regional_image") != len(images) \
                    or target_operations.count("update_regional_images") != 1:
                raise ShopeeRegionalRecoveryError("prior ordered media-write evidence is incomplete")
        logistics = [int(row["logistic_id"]) for row in observed.get("logistics") or []
                     if isinstance(row, Mapping) and row.get("enabled") is True
                     and type(row.get("logistic_id")) is int and row["logistic_id"] > 0]
        if not logistics:
            raise ShopeeRegionalRecoveryError("no enabled official logistics exists")
        built_target = {
            "target_label": label, "region": region, "shop_id": shop_id,
            "item_id": item_id, "model_id": model_id,
            "model_sku": str(model.get("model_sku")),
            "global_item_id": global_item_id, "global_model_id": global_model_id,
            "category_id": expected_category,
            "local_original_price": {"amount": str(price.get("amount")), "currency": str(price.get("currency"))},
            "approved_copy": {"title": str(copy_set.get("title") or ""), "description": str(copy_set.get("description") or "")},
            "approved_image_route": {
                "route_digest": _digest({"ordered_urls": [row.get("url") for row in images]}),
                "ordered_urls": [str(row.get("url") or "") for row in images],
                "existing_media_ids": gallery_ids,
                "source_to_existing_media": [
                    {"source_url": str(image.get("url") or ""), "existing_media_id": media_id}
                    for image, media_id in zip(images, gallery_ids)
                ],
                "binding_authority": "VERIFIED_EXISTING_MEDIA_BINDING_RECEIPT",
            },
            "enabled_logistics_ids": sorted(set(logistics)),
            "allowed_actions": continuation_actions[label] if continuation is not None else list(_ALLOWED_ACTIONS),
            "mutation_budget": {"shared_maximum": 0,
                                "target_maximum": len(continuation_actions[label]) if continuation is not None else 3},
        }
        if continuation is not None:
            old_rows = [row for row in old_manifest.get("targets") or []
                        if isinstance(row, Mapping) and row.get("target_label") == label]
            if len(old_rows) != 1:
                raise ShopeeRegionalRecoveryError("continuation predecessor target is unavailable")
            immutable_keys = {"target_label", "region", "shop_id", "item_id", "model_id", "model_sku",
                              "global_item_id", "global_model_id", "category_id", "local_original_price",
                              "approved_copy", "approved_image_route", "enabled_logistics_ids"}
            if any(built_target[key] != old_rows[0].get(key) for key in immutable_keys):
                raise ShopeeRegionalRecoveryError("continuation approved target facts drifted")
        targets.append(built_target)
    expected_source_objects = {
        "prior_report": prior_report, "global_readback": global_readback,
        "global_model_readback": global_model_readback,
        "regional_readback": regional_readback,
        "media_binding_receipt": media_binding_receipt,
    }
    if continuation is not None:
        expected_source_objects.update({"predecessor_attempt": predecessor_attempt,
                                        "reconciliation_receipt": reconciliation_receipt})
    sources = {}
    source_paths = {}
    for name, expected_object in expected_source_objects.items():
        try:
            source_path = _safe_source_path(source_files[name], allowed_source_roots)
            raw = source_path.read_bytes()
            parsed = json.loads(raw.decode("utf-8"))
        except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ShopeeRegionalRecoveryError("source evidence file is unavailable") from error
        if parsed != expected_object:
            raise ShopeeRegionalRecoveryError("source evidence object conflicts with file")
        sources[name] = _file_digest(raw)
        source_paths[name] = str(source_path)
    body: dict[str, object] = {
        "schema_version": SCHEMA_VERSION, "status": "READY_ZERO_WRITE_PREFLIGHT",
        "offer_id": candidate["offer_id"], "product_revision": candidate["product_revision"],
        "plan_id": candidate["plan_id"],
        "business_snapshot_digest": business_snapshot_digest,
        "execution_snapshot_digest": execution_snapshot_digest,
        "candidate_digest": candidate_digest, "approval_digest": approval_digest,
        "prior_run_id": prior_report["run_id"], "prior_report_digest": _digest(prior_report),
        "source_file_sha256": sources, "source_file_paths": source_paths,
        "regional_readback_digest": regional_digest,
        "global_model_readback_digest": global_model_digest,
        "media_binding_receipt_digest": media_digest,
        "global_item_id": global_item_id, "global_model_id": global_model_id,
        "target_labels": list(labels), "forbidden_operations": ["create_publish_task", "upload_image", "global_mutation"],
        "zero_write_preflight": {"completed": True, "external_write_count": 0},
        "targets": targets,
    }
    if continuation is not None:
        body["continuation"] = continuation
    body["manifest_digest"] = _digest(body)
    return body


def validate_recovery_manifest(value: Mapping[str, object], *, snapshot: Mapping[str, object],
                               candidate: Mapping[str, object], approval: Mapping[str, object],
                               allowed_source_roots: Sequence[str | Path]) -> dict[str, object]:
    body = deepcopy(dict(value))
    supplied = body.pop("manifest_digest", None)
    if supplied != _digest(body):
        raise ShopeeRegionalRecoveryError("recovery manifest digest conflicts")
    labels = body.get("target_labels")
    targets = body.get("targets")
    if (body.get("schema_version") != SCHEMA_VERSION or body.get("status") != "READY_ZERO_WRITE_PREFLIGHT"
            or body.get("offer_id") != snapshot.get("offer_id")
            or body.get("plan_id") != snapshot.get("plan_id")
            or body.get("execution_snapshot_digest") != snapshot.get("snapshot_digest")
            or body.get("business_snapshot_digest") != candidate.get("snapshot_digest")
            or body.get("candidate_digest") != candidate.get("candidate_digest")
            or body.get("approval_digest") != approval.get("approval_digest")
            or not isinstance(labels, list) or not isinstance(targets, list)
            or [row.get("target_label") for row in targets if isinstance(row, Mapping)] != labels
            or any(not _TARGET.fullmatch(x) for x in labels)
            or body.get("forbidden_operations") != ["create_publish_task", "upload_image", "global_mutation"]
            or body.get("zero_write_preflight") != {"completed": True, "external_write_count": 0}):
        raise ShopeeRegionalRecoveryError("recovery manifest identity conflicts")
    body["manifest_digest"] = supplied
    paths = body.get("source_file_paths")
    hashes = body.get("source_file_sha256")
    required = {"prior_report", "global_readback", "global_model_readback",
                "regional_readback", "media_binding_receipt"}
    has_continuation = "continuation" in body
    if has_continuation:
        required |= {"predecessor_attempt", "reconciliation_receipt"}
    if not isinstance(paths, Mapping) or not isinstance(hashes, Mapping) \
            or set(paths) != required or set(hashes) != required:
        raise ShopeeRegionalRecoveryError("recovery source evidence set conflicts")
    loaded = {}
    for name in required:
        try:
            raw = _safe_source_path(paths[name], allowed_source_roots).read_bytes()
            loaded[name] = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ShopeeRegionalRecoveryError("recovery source evidence is unavailable") from error
        if hashes[name] != _file_digest(raw):
            raise ShopeeRegionalRecoveryError("recovery source file digest conflicts")
    rebuilt = build_recovery_manifest(
        candidate=candidate, approval=approval,
        prior_report=loaded["prior_report"], global_readback=loaded["global_readback"],
        global_model_readback=loaded["global_model_readback"],
        regional_readback=loaded["regional_readback"], source_files=paths,
        media_binding_receipt=loaded["media_binding_receipt"],
        expected_prior_run_id=str(body.get("prior_run_id") or ""),
        allowed_source_roots=allowed_source_roots,
        target_labels=tuple(labels),
        predecessor_attempt=loaded.get("predecessor_attempt"),
        reconciliation_receipt=loaded.get("reconciliation_receipt"),
    )
    if rebuilt != body:
        raise ShopeeRegionalRecoveryError("recovery manifest does not match frozen evidence")
    return rebuilt


def build_recovery_executor(*, manifest: Mapping[str, object], approval: Mapping[str, object],
                            allowed_source_roots: Sequence[str | Path],
                            runtime: ShopeeRegionRuntime,
                            manifest_validator: Callable[..., Mapping[str, object]] | None = None,
                            ) -> Callable[[object], Mapping[str, Any]]:
    """Return an executor that can mutate only frozen existing regional items."""
    frozen = deepcopy(dict(manifest))
    frozen_approval = deepcopy(dict(approval))

    def execute(request: object) -> Mapping[str, Any]:
        labels = getattr(request, "target_labels", None)
        snapshot = getattr(request, "snapshot", None)
        candidate = getattr(request, "release_candidate", None)
        if not isinstance(labels, tuple) or not isinstance(snapshot, Mapping) or not isinstance(candidate, Mapping):
            raise ShopeeRegionalRecoveryError("recovery request is invalid")
        validator = manifest_validator or validate_recovery_manifest
        checked = validator(
            frozen, snapshot=snapshot, candidate=candidate, approval=frozen_approval
            , allowed_source_roots=allowed_source_roots
        )
        if list(labels) != checked["target_labels"]:
            raise ShopeeRegionalRecoveryError("recovery request target scope conflicts")
        rows = {row["target_label"]: row for row in checked["targets"]}
        statuses: dict[str, str] = {}
        evidence: dict[str, dict[str, object]] = {}
        total: int | None = 0
        for label in labels:
            fact = rows[label]
            count: int | None = 0
            attempted = False
            unknown = False
            stage = "PREFLIGHT"
            code = "shopee_recovery_preflight_failed"
            reason = "Official existing-item recovery preflight failed"
            try:
                context = runtime.context(fact["region"])
                if str(context.shop_id) != fact["shop_id"]:
                    raise ShopeeRegionalRecoveryError("shop identity drifted")
                item = runtime.regional_item(context, fact["item_id"])
                models = runtime.regional_models(context, fact["item_id"])
                linkage = runtime.resolved_global_item_id(context, fact["item_id"])
                _require_core_exact(item, models, linkage, fact)
                _require_global_model_exact(
                    runtime.global_models(context, fact["global_item_id"]), fact,
                    allow_unobservable_status=(checked.get("schema_version") in {
                        "shopee-reportless-recovery-execution-manifest/v1",
                        "shopee-known-zero-recovery-execution-manifest/v1",
                    }),
                )
                desired_ids = tuple(fact["approved_image_route"]["existing_media_ids"])
                if _image_ids(item) != desired_ids:
                    stage = "UPDATE_IMAGES"
                    attempted = True
                    _reserve(request, label, "update_images_existing_media", fact=fact)
                    try:
                        runtime.update_regional_images(context, fact["item_id"], image_ids=desired_ids)
                    except Exception as error:
                        item = runtime.regional_item(context, fact["item_id"])
                        if _image_ids(item) != desired_ids:
                            unknown = True
                            raise ShopeeRegionalRecoveryError(type(error).__name__)
                    count = 1
                item = runtime.regional_item(context, fact["item_id"])
                expected_copy = fact["approved_copy"]
                copy_wrong = (str(item.get("item_name") or "") != expected_copy["title"]
                              or shopee_description_text(item) != expected_copy["description"])
                media_wrong = (tuple(shopee_description_image_ids(item)) != desired_ids
                               or str(item.get("description_type") or "").lower() != "extended")
                if copy_wrong or media_wrong:
                    stage = "UPDATE_COPY_MEDIA" if copy_wrong else "UPDATE_DESCRIPTION_MEDIA"
                    operation = "update_copy_and_description_media" if copy_wrong else "update_description_media"
                    attempted = True
                    _reserve(request, label, operation, fact=fact)
                    try:
                        runtime.update_regional_copy(context, fact["item_id"],
                            title=expected_copy["title"], description=expected_copy["description"])
                    except Exception as error:
                        item = runtime.regional_item(context, fact["item_id"])
                        if not _copy_media_exact(item, fact):
                            unknown = True
                            raise ShopeeRegionalRecoveryError(type(error).__name__)
                    count = None if count is None else count + 1
                item = runtime.regional_item(context, fact["item_id"])
                _require_core_exact(item, runtime.regional_models(context, fact["item_id"]),
                                    runtime.resolved_global_item_id(context, fact["item_id"]), fact)
                if not _copy_media_exact(item, fact):
                    raise ShopeeRegionalRecoveryError("approved copy or media did not converge")
                if str(item.get("item_status") or "").upper() == "UNLIST":
                    stage = "LIST_EXISTING_ITEM"
                    attempted = True
                    _reserve(request, label, "list_existing_item", fact=fact)
                    try:
                        runtime.list_item(context, fact["item_id"])
                    except Exception as error:
                        item = runtime.regional_item(context, fact["item_id"])
                        if str(item.get("item_status") or "").upper() != "NORMAL":
                            unknown = True
                            raise ShopeeRegionalRecoveryError(type(error).__name__)
                    count = None if count is None else count + 1
                final_item = runtime.regional_item(context, fact["item_id"])
                _require_core_exact(final_item, runtime.regional_models(context, fact["item_id"]),
                                    runtime.resolved_global_item_id(context, fact["item_id"]), fact)
                _require_global_model_exact(
                    runtime.global_models(context, fact["global_item_id"]), fact,
                    allow_unobservable_status=(checked.get("schema_version") in {
                        "shopee-reportless-recovery-execution-manifest/v1",
                        "shopee-known-zero-recovery-execution-manifest/v1",
                    }),
                )
                if str(final_item.get("item_status") or "").upper() != "NORMAL" or not _copy_media_exact(final_item, fact):
                    raise ShopeeRegionalRecoveryError("final official readback did not converge")
                runtime.record_verified_item(
                    global_item_id=fact["global_item_id"], region=fact["region"],
                    shop_id=int(fact["shop_id"]), item_id=fact["item_id"],
                    model_id=fact["model_id"],
                    image_route_digest=fact["approved_image_route"]["route_digest"],
                    image_ids=tuple(fact["approved_image_route"]["existing_media_ids"]),
                )
                statuses[label] = "PUBLISHED"; stage = "READBACK"; code = "shopee_recovery_verified"
                reason = "Official existing-item readback matched the approved target"
            except Exception as error:
                if attempted and not isinstance(error, ShopeeRegionalRecoveryError):
                    unknown = True
                statuses[label] = "PROCESSING" if unknown else "FAILED"
                if unknown:
                    count = None
                reason = "Recovery outcome requires read-only reconciliation" if unknown else _safe_reason(error)
                if unknown: code = "shopee_recovery_outcome_unknown"
            evidence[label] = {"target_label": label, "status": statuses[label], "stage": stage,
                "provider_code": code, "provider_reason": reason, "request_attempted": attempted,
                "outcome_unknown": unknown, "external_write_count": count,
                "provider_identity_bound": True}
            total = None if total is None or count is None else total + count
        return _result("SHOPEE", labels, statuses, dispatch_attempted=any(x["request_attempted"] for x in evidence.values()),
                       readback_completed=all(not x["outcome_unknown"] for x in evidence.values()),
                       external_write_count=total, requires_human_action=any(x != "PUBLISHED" for x in statuses.values()),
                       target_evidence=evidence)
    return execute


def _reserve(request: object, label: str, operation: str, *, fact: Mapping[str, object]) -> None:
    if operation not in _ALLOWED_ACTIONS:
        raise ShopeeRegionalRecoveryError("operation is forbidden")
    if operation not in (fact.get("allowed_actions") or []):
        raise ShopeeRegionalRecoveryError("operation exceeds continuation differences")
    ledger = getattr(request, "write_budget_ledger", None)
    if ledger is None:
        raise ShopeeRegionalRecoveryError("recovery mutation budget is missing")
    ledger.reserve_target(label, operation)


def _safe_source_path(value: object, allowed_roots: Sequence[str | Path]) -> Path:
    if isinstance(value, Path):
        candidate = value
    elif isinstance(value, str) and value.strip():
        candidate = Path(value)
    else:
        raise ShopeeRegionalRecoveryError("source evidence path is invalid")
    if candidate.suffix.lower() != ".json" or not candidate.is_file():
        raise ShopeeRegionalRecoveryError("source evidence must be an existing JSON file")
    resolved = candidate.resolve(strict=True)
    roots = [Path(root).resolve(strict=True) for root in allowed_roots]
    if not roots or not any(resolved == root or root in resolved.parents for root in roots):
        raise ShopeeRegionalRecoveryError("source evidence path is outside allowed roots")
    return resolved


def _image_ids(item: Mapping[str, object] | None) -> tuple[str, ...]:
    image = item.get("image") if isinstance(item, Mapping) else None
    values = image.get("image_id_list") if isinstance(image, Mapping) else None
    return tuple(str(x or "").strip() for x in values or [])


def _price(model: Mapping[str, object], currency: str) -> Decimal | None:
    for row in model.get("price_info") or []:
        if isinstance(row, Mapping) and str(row.get("currency") or "").upper() == currency:
            try: return Decimal(str(row.get("original_price")))
            except (InvalidOperation, TypeError, ValueError): return None
    return None


def _require_core_exact(item: object, models: object, linkage: object, fact: Mapping[str, object]) -> None:
    if not isinstance(item, Mapping) or str(item.get("item_id") or "") != fact["item_id"]:
        raise ShopeeRegionalRecoveryError("item identity drifted")
    if str(item.get("category_id") or "") != fact["category_id"] or str(linkage or "") != fact["global_item_id"]:
        raise ShopeeRegionalRecoveryError("category or global linkage drifted")
    if str(item.get("item_status") or "").upper() not in {"UNLIST", "NORMAL"}:
        raise ShopeeRegionalRecoveryError("item status is not safely recoverable")
    if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], Mapping):
        raise ShopeeRegionalRecoveryError("model coverage drifted")
    model = models[0]; expected = fact["local_original_price"]
    if (str(model.get("model_id") or "") != fact["model_id"]
            or str(model.get("model_sku") or "") != fact["model_sku"]
            or _price(model, expected["currency"]) != Decimal(expected["amount"])):
        raise ShopeeRegionalRecoveryError("model identity or price drifted")
    model_status = str(model.get("model_status") or model.get("status") or "").upper()
    if model_status not in {"MODEL_NORMAL", "NORMAL"}:
        raise ShopeeRegionalRecoveryError("regional model status drifted")
    logistics = item.get("logistic_info") or []
    enabled = sorted({int(row["logistic_id"]) for row in logistics
                      if isinstance(row, Mapping) and row.get("enabled") is True
                      and type(row.get("logistic_id")) is int and row["logistic_id"] > 0})
    if enabled != fact["enabled_logistics_ids"]:
        raise ShopeeRegionalRecoveryError("enabled logistics drifted")


def _require_global_model_exact(models: object, fact: Mapping[str, object], *,
                                allow_unobservable_status: bool = False) -> None:
    if not isinstance(models, list):
        raise ShopeeRegionalRecoveryError("global model readback is invalid")
    matches = [row for row in models if isinstance(row, Mapping)
               and str(row.get("global_model_id") or "") == fact["global_model_id"]
               and str(row.get("global_model_sku") or row.get("model_sku") or "") == fact["model_sku"]]
    if len(matches) != 1:
        raise ShopeeRegionalRecoveryError("global model identity drifted")
    status = str(matches[0].get("global_model_status") or matches[0].get("status") or "").upper()
    if allow_unobservable_status and not status:
        return
    if status not in {"MODEL_NORMAL", "NORMAL"}:
        raise ShopeeRegionalRecoveryError("global model status drifted")


def _valid_media_match(binding: Mapping[str, object]) -> bool:
    dimensions = (binding.get("source_dimensions"), binding.get("provider_dimensions"))
    if any(not isinstance(value, list) or len(value) != 2
           or any(type(number) is not int or number <= 0 for number in value)
           for value in dimensions):
        return False
    if binding.get("match") == "NORMALIZED_PIXEL_EXACT":
        return binding.get("source_pixel_sha256") == binding.get("provider_pixel_sha256")
    distance = binding.get("perceptual_distance")
    row_margin = binding.get("second_best_margin")
    column_margin = binding.get("provider_second_best_margin")
    return (type(distance) in {int, float} and type(row_margin) in {int, float}
            and type(column_margin) in {int, float} and 0 <= distance <= _MEDIA_MAX_DISTANCE
            and row_margin >= _MEDIA_MIN_MARGIN and column_margin >= _MEDIA_MIN_MARGIN)


def _copy_media_exact(item: Mapping[str, object] | None, fact: Mapping[str, object]) -> bool:
    if not isinstance(item, Mapping): return False
    copy = fact["approved_copy"]; ids = tuple(fact["approved_image_route"]["existing_media_ids"])
    return (str(item.get("item_name") or "") == copy["title"]
            and shopee_description_text(item) == copy["description"]
            and str(item.get("description_type") or "").lower() == "extended"
            and tuple(shopee_description_image_ids(item)) == ids and _image_ids(item) == ids)


__all__ = ["SCHEMA_VERSION", "ShopeeRegionalRecoveryError", "build_recovery_executor",
           "build_recovery_manifest", "validate_recovery_manifest"]
