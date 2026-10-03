#!/usr/bin/env python3
"""Execute the round-3 Miaoshou and immutable release-handoff boundary."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _image_skill_module():
    path = (
        REPO_ROOT
        / "skills"
        / "prepare-product-images"
        / "scripts"
        / "prepare_product_images.py"
    )
    spec = importlib.util.spec_from_file_location("round2_image_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the round-2 image contract")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _approved_master_image_count(
    round1: Mapping[str, Any], generation: Mapping[str, Any]
) -> int:
    """Resolve one exact approved master-image count without category constants."""

    plan = round1.get("image_plan")
    approved_count = 0
    if isinstance(plan, Mapping):
        approved_count = sum(
            max(0, int(asset.get("quantity") or 0))
            for brand in (plan.get("brand_plans") or [])
            if isinstance(brand, Mapping)
            for asset in (brand.get("generated_assets") or [])
            if isinstance(asset, Mapping)
        )
    generated_count = int(generation.get("planned_image_count") or 0)
    if approved_count and generated_count and approved_count != generated_count:
        raise ValueError("round2 planned image count conflicts with round1 approval")
    expected_count = approved_count or generated_count
    if expected_count <= 0:
        # Compatibility for older immutable reports which predate both count fields.
        expected_count = len(
            [row for row in generation.get("assets") or [] if isinstance(row, Mapping)]
        )
    if expected_count <= 0:
        raise ValueError("round2 brand image generation is incomplete: approved plan has no master images")
    return expected_count


def freeze_round2_image_snapshot(offer_id: str) -> dict[str, Any]:
    """Join completed image artifacts without re-evaluating round-1 facts."""

    from shared_platform.publication_rounds import (
        ROUND2_SCHEMA,
        canonical_digest,
        load_round1_snapshot,
        report_dir,
    )

    directory = report_dir(offer_id)
    existing_path = directory / "round2-image-snapshot.json"
    if existing_path.is_file():
        existing = _read(existing_path)
        unsigned = dict(existing)
        supplied = str(unsigned.pop("snapshot_digest", ""))
        if existing.get("schema_version") != ROUND2_SCHEMA or supplied != canonical_digest(unsigned):
            raise ValueError("round2 image snapshot is invalid")
        return existing

    round1 = load_round1_snapshot(offer_id)
    generation = _read(directory / "brand-image-generation.json")
    translation_plan = _read(directory / "brand-image-translation-plan.json")
    approved_task_count = int(translation_plan.get("approved_task_count") or 0)
    if approved_task_count:
        translation = _read(directory / "brand-image-translation.json")
    else:
        translation = {
            "schema_version": "brand-image-translation/v1",
            "status": "NOT_REQUIRED",
            "approved_task_count": 0,
            "assets": [],
            "external_generation_count": 0,
        }
    assets = [row for row in generation.get("assets") or [] if isinstance(row, dict)]
    expected_asset_count = _approved_master_image_count(round1, generation)
    generation_round1_digest = str(generation.get("round1_snapshot_digest") or "")
    if generation_round1_digest and generation_round1_digest != round1["snapshot_digest"]:
        raise ValueError("round2 brand image generation is bound to another round1 snapshot")
    if (
        generation.get("status") != "BRAND_IMAGE_REVIEW_REQUIRED"
        or len(assets) != expected_asset_count
    ):
        raise ValueError("round2 brand image generation is incomplete")
    if translation_plan.get("status") not in {
        "APPROVED_IN_CONVERSATION",
        "APPROVED_BY_AUTOPILOT",
    }:
        raise ValueError("round2 translation route is incomplete")
    if translation.get("status") not in {
        "LOCALIZED_IMAGE_REVIEW_REQUIRED",
        "NOT_REQUIRED",
    }:
        raise ValueError("round2 localized image generation is incomplete")
    if approved_task_count != len(translation.get("assets") or []):
        raise ValueError("round2 localized image receipt coverage is incomplete")
    qa_path = directory / "automated-image-qa.json"
    if not qa_path.is_file():
        raise ValueError("durable automated image QA is required")
    qa = _read(qa_path)
    required_qa_checks = {
        "ROLE_COVERAGE",
        "FACTUAL_ALIGNMENT",
        "OCR_LANGUAGE",
        "DUPLICATION",
        "TARGET_ROUTING",
    }
    qa_codes = {
        str(row.get("code") or "")
        for row in (qa.get("checks") or [])
        if isinstance(row, Mapping) and row.get("status") == "PASSED"
    }
    if (
        qa.get("schema_version") != "automated-image-qa/v1"
        or qa.get("status") != "PASSED"
        or str(qa.get("offer_id") or "") != str(offer_id)
        or qa.get("round1_snapshot_digest") != round1["snapshot_digest"]
        or int(qa.get("generated_asset_count", -1)) != len(assets)
        or int(qa.get("localized_asset_count", -1))
        != len(translation.get("assets") or [])
        or not required_qa_checks.issubset(qa_codes)
    ):
        raise ValueError("automated image QA did not reach a complete pass")
    document: dict[str, Any] = {
        "schema_version": ROUND2_SCHEMA,
        "status": "IMAGES_READY",
        "offer_id": str(offer_id),
        "round1_snapshot_digest": round1["snapshot_digest"],
        "generation_digest": canonical_digest(generation),
        "translation_plan_digest": canonical_digest(translation_plan),
        "translation_result_digest": canonical_digest(translation),
        "master_image_count": len(assets),
        "localized_image_count": len(translation.get("assets") or []),
        "automated_qa_status": "PASSED",
        "automated_qa_digest": canonical_digest(qa),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "external_generation_count": int(generation.get("external_generation_count") or 0)
        + int(translation.get("external_generation_count") or 0),
        "miaoshou_write_count": 0,
        "marketplace_write_count": 0,
        "next_round": "ROUND3_EXECUTE_AND_VERIFY",
    }
    document["snapshot_digest"] = canonical_digest(document)
    encoded = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with existing_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
    except FileExistsError:
        if existing_path.read_text(encoding="utf-8") != encoded:
            raise ValueError("immutable round2 image snapshot conflicts")
    return document


def release_snapshot_selector(handoff: Mapping[str, Any]) -> dict[str, str]:
    """Select exactly one immutable release-store lookup identity."""
    snapshot_digest = str(handoff.get("snapshot_digest") or "").strip()
    if snapshot_digest:
        return {"snapshot_digest": snapshot_digest}
    plan_id = str(handoff.get("plan_id") or "").strip()
    if plan_id:
        return {"plan_id": plan_id}
    raise ValueError("release handoff has no snapshot identity")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def bind_sku_display_name_successor_handoff(
    offer_id: str, *, store, directory: Path
) -> dict[str, Any] | None:
    """Rebind round 3 to an approved display-name-only successor.

    The predecessor bridge remains the authority for images, copy, targets and
    the verified Miaoshou baseline.  This boundary changes only the release
    identity after proving the immutable successor and predecessor lineage.
    It performs no provider write.
    """

    from shared_platform.publication_rounds import canonical_digest

    successor_path = directory / "sku-display-name-successor.json"
    if not successor_path.is_file():
        return None
    successor = _read(successor_path)
    if (
        successor.get("schema_version")
        != "sku-display-name-successor-preparation/v1"
        or successor.get("status") != "APPROVED_AND_FROZEN"
        or str(successor.get("offer_id") or "") != str(offer_id)
        or successor.get("platform_writes") != 0
        or (successor.get("reconciliation") or {}).get("status")
        != "MATCHED_PERSISTED_SUCCESSOR"
        or (successor.get("reconciliation") or {}).get("external_write_count") != 0
    ):
        raise ValueError("SKU display-name successor evidence is invalid")

    workflow = _read(directory / "workflow-handoff.json")
    bridge_path = directory / "dual-brand-publication-handoff.json"
    bridge = _read(bridge_path)
    release = bridge.get("release_handoff")
    predecessor_plan = str(successor.get("predecessor_plan_id") or "")
    predecessor_digest = str(successor.get("predecessor_snapshot_digest") or "")
    release_predecessor_ok = (
        not isinstance(release, Mapping)
        or (
            str(release.get("plan_id") or "") == predecessor_plan
            and str(release.get("snapshot_digest") or "") == predecessor_digest
        )
    )
    successor_plan = str(successor.get("successor_plan_id") or "")
    successor_digest = str(successor.get("successor_snapshot_digest") or "")
    already_bound = (
        workflow.get("status") == "READY_TO_PUBLISH"
        and str(workflow.get("plan_id") or "") == successor_plan
        and str(workflow.get("snapshot_digest") or "") == successor_digest
        and bridge.get("status") == "MIAOSHOU_VERIFIED"
        and isinstance(release, Mapping)
        and str(release.get("plan_id") or "") == successor_plan
        and str(release.get("snapshot_digest") or "") == successor_digest
    )
    if already_bound:
        return workflow
    if not (
        workflow.get("status") == "READY_TO_PUBLISH"
        and str(workflow.get("plan_id") or "") == predecessor_plan
        and str(workflow.get("snapshot_digest") or "") == predecessor_digest
        and bridge.get("status") == "MIAOSHOU_VERIFIED"
        and isinstance(bridge.get("miaoshou_sync"), Mapping)
        and bridge["miaoshou_sync"].get("verified") is True
        and bridge["miaoshou_sync"].get("published") is False
        and release_predecessor_ok
    ):
        raise ValueError("SKU display-name successor predecessor handoff drifted")

    plan_id = successor_plan
    snapshot_digest = successor_digest
    plan = store.get_plan(plan_id)
    active = store.active_plan_for_product(str(offer_id))
    snapshot = store.approved_publication_snapshot(
        offer_id=str(offer_id), plan_id=plan_id
    )
    if not (
        isinstance(plan, Mapping)
        and plan.get("status") == "APPROVED"
        and isinstance(active, Mapping)
        and active.get("plan_id") == plan_id
        and isinstance(snapshot, Mapping)
        and snapshot.get("schema_version") == "approved-publication-snapshot/v4"
        and snapshot.get("snapshot_digest") == snapshot_digest
        and isinstance(successor.get("successor_snapshot"), Mapping)
        and dict(successor["successor_snapshot"]) == dict(snapshot)
        and list(plan.get("targets") or []) == list(bridge.get("targets") or [])
    ):
        raise ValueError("SKU display-name successor snapshot readback failed")

    lineage = {
        "schema_version": "sku-display-name-handoff-lineage/v1",
        "predecessor_plan_id": predecessor_plan,
        "predecessor_snapshot_digest": predecessor_digest,
        "successor_plan_id": plan_id,
        "successor_snapshot_digest": snapshot_digest,
        "successor_evidence_digest": canonical_digest(successor),
        "external_write_count": 0,
    }
    rebound = dict(workflow)
    rebound.update(
        {
            "plan_id": plan_id,
            "snapshot_digest": snapshot_digest,
            "payload_digest": plan.get("payload_digest"),
            "target_count": len(plan.get("targets") or []),
            "product_revision": snapshot.get("product_revision"),
            "sku_display_name_successor": lineage,
            "platform_writes": 0,
        }
    )
    _write_json_atomic(directory / "workflow-handoff.json", rebound)
    bridge["release_handoff"] = {
        "status": "READY_TO_PUBLISH",
        "plan_id": plan_id,
        "snapshot_digest": snapshot_digest,
        "sku_display_name_successor": lineage,
    }
    _write_json_atomic(bridge_path, bridge)
    authorization_path = directory / "publication-authorization.json"
    if authorization_path.is_file():
        authorization = _read(authorization_path)
        authorization["current_execution_status"] = "READY_TO_PUBLISH"
        authorization["plan_id"] = plan_id
        authorization["snapshot_digest"] = snapshot_digest
        authorization["sku_display_name_successor"] = lineage
        _write_json_atomic(authorization_path, authorization)
    return rebound


def bind_target_copy_successor_handoff(
    offer_id: str, *, store, directory: Path
) -> dict[str, Any] | None:
    """Rebind a verified predecessor handoff to a copy-only successor."""
    from shared_platform.publication_rounds import canonical_digest

    successor_path = directory / "target-copy-successor.json"
    if not successor_path.is_file():
        return None
    successor = _read(successor_path)
    if (
        successor.get("schema_version")
        != "target-copy-successor-preparation/v1"
        or successor.get("status") != "APPROVED_AND_FROZEN"
        or str(successor.get("offer_id") or "") != str(offer_id)
        or successor.get("platform_writes") != 0
        or (successor.get("reconciliation") or {}).get("status")
        != "MATCHED_PERSISTED_SUCCESSOR"
        or (successor.get("reconciliation") or {}).get("external_write_count") != 0
        or not isinstance(successor.get("content_by_target"), Mapping)
    ):
        raise ValueError("target copy successor evidence is invalid")

    workflow_path = directory / "workflow-handoff.json"
    bridge_path = directory / "dual-brand-publication-handoff.json"
    workflow = _read(workflow_path)
    bridge = _read(bridge_path)
    release = bridge.get("release_handoff")
    predecessor_plan = str(successor.get("predecessor_plan_id") or "")
    predecessor_digest = str(successor.get("predecessor_snapshot_digest") or "")
    successor_plan = str(successor.get("successor_plan_id") or "")
    successor_digest = str(successor.get("successor_snapshot_digest") or "")
    expected_copy = successor.get("content_by_target")
    if (
        workflow.get("status") == "READY_TO_PUBLISH"
        and str(workflow.get("plan_id") or "") == successor_plan
        and str(workflow.get("snapshot_digest") or "") == successor_digest
        and isinstance(release, Mapping)
        and str(release.get("plan_id") or "") == successor_plan
        and str(release.get("snapshot_digest") or "") == successor_digest
    ):
        target_facts = dict(bridge.get("target_facts") or {})
        for label, copy in expected_copy.items():
            facts = dict(target_facts.get(label) or {})
            facts["copy"] = {
                "language": copy["locale"],
                "title": copy["title"],
                "description": copy["description"],
            }
            target_facts[label] = facts
        bridge["target_facts"] = target_facts
        _write_json_atomic(bridge_path, bridge)
        return workflow
    if not (
        workflow.get("status") == "READY_TO_PUBLISH"
        and str(workflow.get("plan_id") or "") == predecessor_plan
        and str(workflow.get("snapshot_digest") or "") == predecessor_digest
        and bridge.get("status") == "MIAOSHOU_VERIFIED"
        and isinstance(bridge.get("miaoshou_sync"), Mapping)
        and bridge["miaoshou_sync"].get("verified") is True
        and bridge["miaoshou_sync"].get("published") is False
        and isinstance(release, Mapping)
        and str(release.get("plan_id") or "") == predecessor_plan
        and str(release.get("snapshot_digest") or "") == predecessor_digest
    ):
        raise ValueError("target copy successor predecessor handoff drifted")

    plan = store.get_plan(successor_plan)
    active = store.active_plan_for_product(str(offer_id))
    snapshot = store.approved_publication_snapshot(
        offer_id=str(offer_id), plan_id=successor_plan
    )
    if not (
        isinstance(plan, Mapping)
        and plan.get("status") == "APPROVED"
        and isinstance(active, Mapping)
        and active.get("plan_id") == successor_plan
        and isinstance(snapshot, Mapping)
        and snapshot.get("snapshot_digest") == successor_digest
        and (snapshot.get("product") or {}).get("content_by_target")
        == expected_copy
        and list(plan.get("targets") or []) == list(bridge.get("targets") or [])
    ):
        raise ValueError("target copy successor snapshot readback failed")

    lineage = {
        "schema_version": "target-copy-handoff-lineage/v1",
        "predecessor_plan_id": predecessor_plan,
        "predecessor_snapshot_digest": predecessor_digest,
        "successor_plan_id": successor_plan,
        "successor_snapshot_digest": successor_digest,
        "successor_evidence_digest": canonical_digest(successor),
        "external_write_count": 0,
    }
    rebound = dict(workflow)
    rebound.update(
        {
            "plan_id": successor_plan,
            "snapshot_digest": successor_digest,
            "payload_digest": plan.get("payload_digest"),
            "target_count": len(plan.get("targets") or []),
            "product_revision": snapshot.get("product_revision"),
            "target_copy_successor": lineage,
            "platform_writes": 0,
        }
    )
    _write_json_atomic(workflow_path, rebound)
    target_facts = dict(bridge.get("target_facts") or {})
    for label, copy in expected_copy.items():
        facts = dict(target_facts.get(label) or {})
        facts["copy"] = {
            "language": copy["locale"],
            "title": copy["title"],
            "description": copy["description"],
        }
        target_facts[label] = facts
    bridge["target_facts"] = target_facts
    bridge["release_handoff"] = {
        "status": "READY_TO_PUBLISH",
        "plan_id": successor_plan,
        "snapshot_digest": successor_digest,
        "target_copy_successor": lineage,
    }
    _write_json_atomic(bridge_path, bridge)
    authorization_path = directory / "publication-authorization.json"
    if authorization_path.is_file():
        authorization = _read(authorization_path)
        authorization["current_execution_status"] = "READY_TO_PUBLISH"
        authorization["plan_id"] = successor_plan
        authorization["snapshot_digest"] = successor_digest
        authorization["target_copy_successor"] = lineage
        _write_json_atomic(authorization_path, authorization)
    return rebound


def bind_shopee_category_attribute_successor_handoff(
    offer_id: str,
    *,
    store: Any,
    directory: Path,
) -> dict[str, Any] | None:
    """Rebind an already verified handoff to a zero-write category successor."""

    from shared_platform.publication_rounds import canonical_digest

    successor_path = (
        REPO_ROOT
        / "reports"
        / "product-publication"
        / str(offer_id)
        / "shopee-category-attribute-successor.json"
    )
    if not successor_path.is_file():
        return None
    successor = _read(successor_path)
    if (
        successor.get("schema_version")
        != "shopee-category-attribute-successor-preparation/v1"
        or successor.get("status") != "APPROVED_AND_FROZEN"
        or str(successor.get("offer_id") or "") != str(offer_id)
        or successor.get("platform_writes") != 0
    ):
        raise ValueError("Shopee category successor evidence is invalid")

    workflow_path = directory / "workflow-handoff.json"
    bridge_path = directory / "dual-brand-publication-handoff.json"
    workflow = _read(workflow_path)
    bridge = _read(bridge_path)
    release = bridge.get("release_handoff")
    predecessor_plan = str(successor.get("predecessor_plan_id") or "")
    predecessor_digest = str(successor.get("predecessor_snapshot_digest") or "")
    successor_plan = str(successor.get("successor_plan_id") or "")
    successor_digest = str(successor.get("successor_snapshot_digest") or "")
    if (
        workflow.get("status") == "READY_TO_PUBLISH"
        and str(workflow.get("plan_id") or "") == successor_plan
        and str(workflow.get("snapshot_digest") or "") == successor_digest
        and isinstance(release, Mapping)
        and str(release.get("plan_id") or "") == successor_plan
        and str(release.get("snapshot_digest") or "") == successor_digest
    ):
        return workflow
    if not (
        workflow.get("status") == "READY_TO_PUBLISH"
        and str(workflow.get("plan_id") or "") == predecessor_plan
        and str(workflow.get("snapshot_digest") or "") == predecessor_digest
        and bridge.get("status") == "MIAOSHOU_VERIFIED"
        and isinstance(bridge.get("miaoshou_sync"), Mapping)
        and bridge["miaoshou_sync"].get("verified") is True
        and bridge["miaoshou_sync"].get("published") is False
        and isinstance(release, Mapping)
        and str(release.get("plan_id") or "") == predecessor_plan
        and str(release.get("snapshot_digest") or "") == predecessor_digest
    ):
        raise ValueError("Shopee category successor predecessor handoff drifted")

    plan = store.get_plan(successor_plan)
    active = store.active_plan_for_product(str(offer_id))
    snapshot = store.approved_publication_snapshot(
        offer_id=str(offer_id), plan_id=successor_plan
    )
    category = (
        (snapshot.get("shopee_global_master") or {}).get("category_decision")
        if isinstance(snapshot, Mapping)
        else None
    )
    if not (
        isinstance(plan, Mapping)
        and plan.get("status") == "APPROVED"
        and isinstance(active, Mapping)
        and active.get("plan_id") == successor_plan
        and isinstance(snapshot, Mapping)
        and snapshot.get("snapshot_digest") == successor_digest
        and isinstance(category, Mapping)
        and category.get("status") == "APPROVED"
        and list(plan.get("targets") or []) == list(bridge.get("targets") or [])
    ):
        raise ValueError("Shopee category successor snapshot readback failed")

    lineage = {
        "schema_version": "shopee-category-attribute-handoff-lineage/v1",
        "predecessor_plan_id": predecessor_plan,
        "predecessor_snapshot_digest": predecessor_digest,
        "successor_plan_id": successor_plan,
        "successor_snapshot_digest": successor_digest,
        "successor_evidence_digest": canonical_digest(successor),
        "external_write_count": 0,
    }
    rebound = dict(workflow)
    rebound.update(
        {
            "plan_id": successor_plan,
            "snapshot_digest": successor_digest,
            "payload_digest": plan.get("payload_digest"),
            "target_count": len(plan.get("targets") or []),
            "product_revision": snapshot.get("product_revision"),
            "shopee_category_attribute_successor": lineage,
            "platform_writes": 0,
        }
    )
    _write_json_atomic(workflow_path, rebound)
    bridge["release_handoff"] = {
        "status": "READY_TO_PUBLISH",
        "plan_id": successor_plan,
        "snapshot_digest": successor_digest,
        "shopee_category_attribute_successor": lineage,
    }
    _write_json_atomic(bridge_path, bridge)
    authorization_path = directory / "publication-authorization.json"
    if authorization_path.is_file():
        authorization = _read(authorization_path)
        authorization["current_execution_status"] = "READY_TO_PUBLISH"
        authorization["plan_id"] = successor_plan
        authorization["snapshot_digest"] = successor_digest
        authorization["shopee_category_attribute_successor"] = lineage
        _write_json_atomic(authorization_path, authorization)
    return rebound


def execute(args: argparse.Namespace) -> dict[str, Any]:
    from shared_platform.publication_rounds import autopilot_authorizes

    if getattr(args, "reports_root", None) is not None:
        raise ValueError("R3_LOCAL_REPORTS_NOT_AUTHORITY: use the Product Center stage service")

    image_snapshot = freeze_round2_image_snapshot(args.offer_id)
    result: dict[str, Any] = {
        "schema_version": "round3-publication-preparation-result/v1",
        "offer_id": str(args.offer_id),
        "status": "ROUND2_IMAGES_BOUND",
        "round2_snapshot_digest": image_snapshot["snapshot_digest"],
        "miaoshou_external_write_count": 0,
        "marketplace_write_count": 0,
    }
    module = _image_skill_module()
    if args.execute_miaoshou:
        if not args.confirm_miaoshou_write and not autopilot_authorizes(
            "miaoshou_common_baseline_sync"
        ):
            raise ValueError("Miaoshou common-baseline synchronization is not authorized")
        module._persist_dual_brand_publication_bridge(
            args.offer_id, approved_by="Kyle"
        )
        bridge = module.sync_dual_brand_miaoshou_baseline(args.offer_id)
        if bridge.get("status") != "MIAOSHOU_VERIFIED":
            raise ValueError("Miaoshou common baseline did not reach verified readback")
        result.update(
            {
                "status": bridge.get("status"),
                "miaoshou_external_write_count": int(
                    ((bridge.get("miaoshou_sync") or {}).get("external_write_count") or 0)
                ),
            }
        )
    if args.finalize_release_handoff:
        from shared_platform.publication_preflight import (
            build_platform_preflight,
            persist_platform_preflight,
        )
        from shared_platform.release_store import default_release_store

        directory = REPO_ROOT / "reports" / "product-preparation" / str(args.offer_id)
        release_store = default_release_store()
        handoff = bind_sku_display_name_successor_handoff(
            args.offer_id, store=release_store, directory=directory
        )
        copy_handoff = bind_target_copy_successor_handoff(
            args.offer_id, store=release_store, directory=directory
        )
        if copy_handoff is not None:
            handoff = copy_handoff
        category_handoff = bind_shopee_category_attribute_successor_handoff(
            args.offer_id, store=release_store, directory=directory
        )
        if category_handoff is not None:
            handoff = category_handoff
        if handoff is None:
            handoff = module.finalize_dual_brand_release_handoff(args.offer_id)
        snapshot = release_store.approved_publication_snapshot(
            offer_id=str(args.offer_id),
            **release_snapshot_selector(handoff),
        )
        if snapshot is None:
            raise ValueError("final release snapshot is unavailable for preflight")
        preflight_evidence = {
            "workflow_handoff": _read(directory / "workflow-handoff.json"),
            "publication_bridge": _read(directory / "dual-brand-publication-handoff.json"),
            "image_qa": _read(directory / "automated-image-qa.json"),
        }
        preflight = build_platform_preflight(snapshot, evidence=preflight_evidence)
        persist_platform_preflight(
            preflight, path=directory / "platform-preflight.json"
        )
        if preflight.get("status") != "PASSED":
            raise ValueError("platform technical preflight did not pass")
        result.update(
            {
                "status": "READY_FOR_FINAL_REVIEW",
                "plan_id": handoff.get("plan_id"),
                "snapshot_digest": handoff.get("snapshot_digest"),
                "target_count": handoff.get("target_count"),
                "platform_preflight_digest": preflight.get("preflight_digest"),
            }
        )
    return result


def _execute_product_center_stage(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Use the Product Center's durable approval boundary for explicit loopback CLI work."""
    from shared_platform.publication_r3_image_bridge import request_publication_stage

    chosen = sum(bool(value) for value in (
        args.execute_miaoshou, args.approve_marketplace,
        args.resume_approval_binding, args.finalize_release_handoff,
    ))
    if chosen > 1:
        raise ValueError("select exactly one Product Center stage action")
    if ((args.confirm_miaoshou_write and not args.execute_miaoshou)
            or ((args.user_approved or args.approved_by or args.preview_digest)
                and not args.approve_marketplace)
            or (args.confirmation_token and not (args.execute_miaoshou or args.approve_marketplace))
            or (args.plan_id and not (args.execute_miaoshou or args.approve_marketplace
                                      or args.resume_approval_binding))):
        raise ValueError("Product Center stage flags do not match the selected action")
    data: dict[str, Any] = {"offer_id": str(args.offer_id)}
    if args.execute_miaoshou:
        if not (args.confirm_miaoshou_write and args.plan_id and args.confirmation_token):
            raise ValueError("exact approved COMMON plan/token and write confirmation are required")
        data.update(release_stage="R3_COMMON", publication_targets=["miaoshou:COMMON"],
                    plan_id=args.plan_id, confirmation_token=args.confirmation_token,
                    confirm_miaoshou_write=True)
        path = "/api/product-workspace/miaoshou-draft/commit"
    elif args.approve_marketplace:
        if not (args.user_approved and args.approved_by == "Kyle" and args.plan_id
                and args.confirmation_token and args.preview_digest):
            raise ValueError("exact reviewed marketplace plan, digest, token and Kyle approval are required")
        data.update(plan_id=args.plan_id, confirmation_token=args.confirmation_token,
                    preview_digest=args.preview_digest, user_approved=True,
                    approved_by=args.approved_by)
        path = "/api/product-workspace/r3-marketplace/approve"
    elif args.resume_approval_binding:
        if not args.plan_id:
            raise ValueError("original approved marketplace plan is required to resume binding")
        data["plan_id"] = args.plan_id
        path = "/api/product-workspace/r3-marketplace/resume-binding"
    elif args.finalize_release_handoff:
        path = "/api/product-workspace/r3-marketplace/preview"
    else:
        data.update(release_stage="R3_COMMON", publication_targets=["miaoshou:COMMON"])
        path = "/api/product-workspace/r3-common/preview"
    status, result = request_publication_stage(path, data=data, base_url=args.base_url)
    return status, dict(result, http_status=status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--execute-miaoshou", action="store_true")
    parser.add_argument("--confirm-miaoshou-write", action="store_true")
    parser.add_argument("--finalize-release-handoff", action="store_true")
    parser.add_argument("--base-url")
    parser.add_argument("--plan-id")
    parser.add_argument("--confirmation-token")
    parser.add_argument("--preview-digest")
    parser.add_argument("--approve-marketplace", action="store_true")
    parser.add_argument("--resume-approval-binding", action="store_true")
    parser.add_argument("--user-approved", action="store_true")
    parser.add_argument("--approved-by")
    args = parser.parse_args(argv)
    try:
        if args.base_url:
            status, result = _execute_product_center_stage(args)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            return 0 if status == 200 and result.get("ok") is True else 2
        if (args.plan_id or args.confirmation_token or args.preview_digest
                or args.approve_marketplace or args.resume_approval_binding
                or args.user_approved or args.approved_by):
            raise ValueError("an explicit Product Center loopback base URL is required")
        result = execute(args)
    except Exception as error:
        print(
            json.dumps(
                {
                    "schema_version": "round3-publication-preparation-result/v1",
                    "offer_id": str(args.offer_id),
                    "status": "FAILED",
                    "reason": str(error)[:300],
                    "marketplace_write_count": 0,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
