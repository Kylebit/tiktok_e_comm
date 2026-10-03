"""Zero-write technical preflight performed after Miaoshou exact readback."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from shared_platform.publication_rounds import canonical_digest


PREFLIGHT_SCHEMA = "platform-publication-preflight/v2"


def build_platform_preflight(
    snapshot: Mapping[str, Any], *, evidence: Mapping[str, Any]
) -> dict[str, Any]:
    from domains.product_operations import (
        publication_images_for_target,
        validate_approved_publication_snapshot,
    )

    snapshot_valid = False
    try:
        snapshot = validate_approved_publication_snapshot(snapshot).payload()
        snapshot_valid = True
    except (TypeError, ValueError):
        # A failed preflight remains reviewable, but copied digest strings are
        # never evidence that the frozen source actually passed validation.
        pass

    return _build_platform_preflight(snapshot, evidence=evidence, snapshot_valid=snapshot_valid)


def build_platform_preview_preflight(snapshot: Mapping[str, Any], *, evidence: Mapping[str, Any]) -> dict[str, Any]:
    from domains.product_operations.approved_publication_snapshot import validate_publication_preview
    snapshot = validate_publication_preview(snapshot)
    snapshot['snapshot_digest'] = snapshot['preview_digest']
    result = _build_platform_preflight(snapshot, evidence=evidence, snapshot_valid=True)
    result['schema_version'] = 'platform-publication-preview-preflight/v1'
    result['approval_status'] = 'NOT_APPROVED'
    result.pop('preflight_digest')
    result['preflight_digest'] = canonical_digest(result)
    return result


def _build_platform_preflight(snapshot, *, evidence, snapshot_valid):
    from domains.product_operations import publication_images_for_target

    targets = [
        dict(row)
        for row in snapshot.get("publication_targets") or ()
        if isinstance(row, Mapping)
    ]
    target_labels = [str(row.get("target_label") or "") for row in targets]
    bridge = evidence.get("publication_bridge")
    workflow = evidence.get("workflow_handoff")
    qa = evidence.get("image_qa")
    checks: list[dict[str, Any]] = []
    warehouse_semantics_by_target: dict[str, str] = {}

    def check(code: str, ok: bool, *, target: str | None = None) -> None:
        checks.append({
            "code": code,
            "status": "PASSED" if ok else "FAILED",
            **({"target_label": target} if target else {}),
        })

    identity_ok = (
        snapshot_valid
        and str(snapshot.get("snapshot_digest") or "")
        and isinstance(bridge, Mapping)
        and bridge.get("status") == "MIAOSHOU_VERIFIED"
        and isinstance(workflow, Mapping)
        and str(workflow.get("snapshot_digest") or "")
        == str(snapshot.get("snapshot_digest") or "")
    )
    check("SNAPSHOT_AND_MIAOSHOU_READBACK", identity_ok)
    check(
        "AUTOMATED_IMAGE_QA",
        isinstance(qa, Mapping)
        and qa.get("schema_version") == "automated-image-qa/v1"
        and qa.get("status") == "PASSED",
    )
    categories = snapshot.get("categories_by_target")
    categories = categories if isinstance(categories, Mapping) else {}
    skus = [row for row in snapshot.get("skus") or () if isinstance(row, Mapping)]
    model_skus = [str(row.get("model_sku") or "").strip() for row in skus]
    shopee_master = snapshot.get("shopee_global_master")
    shopee_master = shopee_master if isinstance(shopee_master, Mapping) else {}
    shopee_decision = shopee_master.get("category_decision")
    shopee_decision = shopee_decision if isinstance(shopee_decision, Mapping) else {}
    shopee_bindings = shopee_master.get("variant_image_bindings")
    if snapshot_valid and shopee_master.get("schema_version") == "shopee-global-master/v1":
        # The accepted v1 domain contract binds zero-based position + image_url.
        # Domain validation above verifies both against the frozen common images.
        shopee_bindings = shopee_master.get("variant_image_positions")
    shopee_bindings = shopee_bindings if isinstance(shopee_bindings, list) else []
    for target in targets:
        label = str(target.get("target_label") or "")
        platform = str(target.get("platform") or "").upper()
        category = categories.get(label)
        category_ok = isinstance(category, Mapping) and bool(
            str(category.get("id") or "")
            or str((category.get("category") or {}).get("id") or "")
        )
        check(f"{platform}_CATEGORY_AND_ATTRIBUTES", category_ok, target=label)
        try:
            images = publication_images_for_target(snapshot, label)
        except Exception:
            images = []
        check(
            f"{platform}_IMAGE_AND_DESCRIPTION_ROUTE",
            bool(images)
            and len(images) == len(set(images))
            and all(str(url).startswith("https://") for url in images),
            target=label,
        )
        variants_ok = bool(skus) and all(
            isinstance(row.get("specification"), Mapping)
            and isinstance(row.get("parcel"), Mapping)
            and isinstance((row.get("prices") or {}).get(label), Mapping)
            for row in skus
        )
        check(f"{platform}_VARIANT_PRICE_AND_LOGISTICS", variants_ok, target=label)
        if platform == "TIKTOK":
            from modules.miaoshou.tiktok_warehouses import (
                mainland_pickup_warehouse_semantic,
            )

            semantic = mainland_pickup_warehouse_semantic(label)
            if semantic:
                warehouse_semantics_by_target[label] = semantic
            check("TIKTOK_EXACT_SHOP_WAREHOUSE_SEMANTIC", bool(semantic), target=label)
        elif platform == "SHOPEE":
            category_row = shopee_decision.get("category")
            attributes = shopee_decision.get("required_attributes")
            shopee_category_ready = bool(
                shopee_decision.get("status") not in {None, "", "DEFERRED_TO_SKILL"}
                and isinstance(category_row, Mapping)
                and str(category_row.get("id") or "").strip()
                and isinstance(attributes, list)
            )
            check(
                "SHOPEE_OFFICIAL_CATEGORY_ATTRIBUTES_FROZEN",
                shopee_category_ready,
                target=label,
            )
            binding_models = [
                str(row.get("model_sku") or "").strip()
                for row in shopee_bindings
                if isinstance(row, Mapping)
            ]
            binding_urls = [
                str(row.get("image_url") or "").strip()
                for row in shopee_bindings
                if isinstance(row, Mapping)
            ]
            bindings_exact = bool(
                snapshot_valid
                and model_skus
                and len(binding_models) == len(model_skus)
                and set(binding_models) == set(model_skus)
                and all(url.startswith("https://") for url in binding_urls)
                and len(binding_urls) == len(set(binding_urls))
            )
            check(
                "SHOPEE_VARIANT_IMAGE_BINDINGS_EXACT",
                bindings_exact,
                target=label,
            )
    status = "PASSED" if checks and all(row["status"] == "PASSED" for row in checks) else "FAILED"
    document: dict[str, Any] = {
        "schema_version": PREFLIGHT_SCHEMA,
        "status": status,
        "offer_id": str(snapshot.get("offer_id") or ""),
        "plan_id": str(snapshot.get("plan_id") or ""),
        "snapshot_digest": str(snapshot.get("snapshot_digest") or ""),
        "target_labels": target_labels,
        "checks": checks,
        "warehouse_semantics_by_target": warehouse_semantics_by_target,
        "external_write_count": 0,
    }
    document["preflight_digest"] = canonical_digest(document)
    return document


def persist_platform_preflight(document: Mapping[str, Any], *, path: Path) -> Path:
    value = deepcopy(dict(document))
    supplied = str(value.pop("preflight_digest", ""))
    if supplied != canonical_digest(value):
        raise ValueError("platform preflight digest drifted")
    value["preflight_digest"] = supplied
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        previous_text = path.read_text(encoding="utf-8")
        if previous_text == encoded:
            return path
        previous = json.loads(previous_text)
        if not isinstance(previous, dict):
            raise ValueError("previous platform preflight is invalid")
        previous_unsigned = dict(previous)
        previous_digest = str(previous_unsigned.pop("preflight_digest", ""))
        if previous_digest != canonical_digest(previous_unsigned):
            raise ValueError("previous platform preflight digest drifted")
        history = path.parent / "platform-preflight-history"
        history.mkdir(parents=True, exist_ok=True)
        archived = history / f"platform-preflight-{previous_digest[7:]}.json"
        if archived.is_file() and archived.read_text(encoding="utf-8") != previous_text:
            raise ValueError("archived platform preflight conflicts")
        if not archived.is_file():
            with archived.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(previous_text)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8", newline="\n")
        temporary.replace(path)
        return path
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
    return path


__all__ = ["PREFLIGHT_SCHEMA", "build_platform_preflight", "persist_platform_preflight"]
