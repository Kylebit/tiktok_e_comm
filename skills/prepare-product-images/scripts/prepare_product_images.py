#!/usr/bin/env python3
"""Freeze and optionally execute the approved second-round image plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PAID_TASKS = 30
HANDOFF_SCHEMA_VERSION = "product-publication-workflow-handoff/v1"


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        if not str(new_url or "").startswith("https://"):
            raise ValueError("localized image source redirected outside HTTPS")
        return super().redirect_request(request, fp, code, msg, headers, new_url)


def _download_source(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("localized image source must use HTTPS")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "OrbitProductImages/1.0", "Accept": "image/*"},
    )
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    with opener.open(request, timeout=45) as response:
        final_url = str(response.geturl() or "")
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if not final_url.startswith("https://") or not content_type.startswith("image/"):
            raise ValueError("localized image source response is invalid")
        data = response.read(MAX_SOURCE_BYTES + 1)
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError("localized image source size is invalid")
    return data


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_uploaded_assets(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("uploaded_assets") if isinstance(value, dict) else None
    if not isinstance(rows, dict):
        raise ValueError("localized uploaded-assets manifest is invalid")
    return {str(key): dict(row) for key, row in rows.items() if isinstance(row, dict)}


def _approved_base_plan(
    offer_id: str,
    project: Mapping[str, Any],
):
    """Return or locally freeze the exact base plan; never call a provider."""
    from modules.products.server import _release_plan_payload_from_dashboard
    from shared_platform.release_control import build_release_dashboard
    from shared_platform.release_store import PLAN_APPROVED, default_release_store

    store = default_release_store()
    targets = list((project.get("route_locales") or {}).keys())
    base_images = list(project.get("approved_ordered_images") or [])
    if not targets or not base_images:
        raise ValueError("second-round frozen input is incomplete")

    active = store.active_plan_for_product(offer_id)
    if active and active.get("status") == PLAN_APPROVED:
        snapshot = store.approved_publication_snapshot(
            offer_id=offer_id, plan_id=active["plan_id"]
        )
        if (
            isinstance(snapshot, dict)
            and list(active.get("targets") or []) == targets
            and list((snapshot.get("product") or {}).get("images") or []) == base_images
        ):
            return store, active, snapshot
        raise ValueError("active approved ReleasePlan differs from the frozen second round")

    dashboard = build_release_dashboard(offer_id=offer_id)
    payload, blockers = _release_plan_payload_from_dashboard(dashboard)
    if blockers:
        raise ValueError("ReleasePlan is not ready: " + str(blockers[0]))
    if list(payload.get("targets") or []) != targets:
        raise ValueError("ReleasePlan targets differ from the frozen second round")
    payload_images = list((payload.get("product_facts") or {}).get("images") or [])
    if payload_images != base_images:
        raise ValueError("ReleasePlan images differ from the frozen second round")

    preview = store.preview_plan(payload)
    existing = store.get_plan(preview["plan_id"])
    if existing is None:
        store.create_plan(payload)
    approval = store.approve_plan(
        preview["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=preview["confirmation_token"],
    )
    plan = store.get_plan(preview["plan_id"])
    snapshot = store.approved_publication_snapshot(
        offer_id=offer_id, plan_id=preview["plan_id"]
    )
    if not isinstance(plan, dict) or not isinstance(snapshot, dict):
        raise ValueError("base ReleasePlan approval did not freeze a v4 snapshot")
    if not isinstance(approval, dict):
        raise ValueError("base ReleasePlan approval receipt is missing")
    return store, plan, snapshot


def _rebound_supplement(
    project: Mapping[str, Any],
    *,
    plan_id: str,
    snapshot_digest: str,
) -> dict[str, Any]:
    approval = project.get("approval") or {}
    supplement = project.get("publication_supplement") or {}
    if project.get("status") != "APPROVED" or not approval or not supplement:
        raise ValueError("second-round conversation approval is incomplete")
    approval_identity = {
        "schema_version": "localized-image-approval/v1",
        "offer_id": project.get("offer_id"),
        "release_plan_id": plan_id,
        "approved_snapshot_digest": snapshot_digest,
        "selected_positions": list(project.get("selected_positions") or []),
        "approved_by": approval.get("approved_by"),
        "tasks": list(approval.get("tasks") or []),
    }
    approval_digest = _canonical_digest(approval_identity)
    identity = {
        "schema_version": "publication-image-supplement/v1",
        "offer_id": project.get("offer_id"),
        "release_plan_id": plan_id,
        "approved_snapshot_digest": snapshot_digest,
        "approval_digest": approval_digest,
        "routes": dict(supplement.get("routes") or {}),
    }
    return {
        **identity,
        "supplement_digest": _canonical_digest(identity),
        "status": "APPROVED_LOCAL_ASSETS",
        "platform_writes": 0,
        "product_center_mutated": False,
    }


def _uploaded_assets(
    project: Mapping[str, Any], manifest: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, str]]:
    assets: dict[str, dict[str, str]] = {}
    for task in project.get("tasks") or []:
        artifact_id = str(task.get("artifact_id") or "").strip()
        digest = str(task.get("output_digest") or "").strip()
        receipt = task.get("generation_receipt") or {}
        supplied = manifest.get(artifact_id) or {}
        url = str(receipt.get("public_url") or supplied.get("url") or "").strip()
        supplied_digest = str(supplied.get("artifact_digest") or digest).strip()
        if not artifact_id or not digest or supplied_digest != digest:
            raise ValueError("localized uploaded asset identity drifted")
        if not url.startswith("https://"):
            raise ValueError(
                "localized artifact has no durable public HTTPS URL; provide --uploaded-assets"
            )
        assets[artifact_id] = {"artifact_digest": digest, "url": url}
    if set(manifest) - set(assets):
        raise ValueError("localized uploaded-assets manifest contains unbound artifacts")
    return assets


def finalize_release_handoff(
    summary: Mapping[str, Any],
    *,
    uploaded_assets_path: Path | None = None,
) -> dict[str, Any]:
    """Freeze the approved second round into the exact publishable snapshot."""
    offer_id = str(summary.get("offer_id") or "").strip()
    project = summary.get("review") or {}
    sync = project.get("miaoshou_pre_review_sync") or {}
    if not (
        sync.get("status") == "VERIFIED"
        and sync.get("written_to_miaoshou") is True
        and sync.get("verified") is True
        and int(sync.get("external_write_count") or 0) == 1
    ):
        raise ValueError("one verified second-round Miaoshou sync is required")
    if not project.get("approval_intent") or project.get("status") != "APPROVED":
        raise ValueError("second-round conversation approval is required")

    store, base_plan, base_snapshot = _approved_base_plan(offer_id, project)
    tasks = list(project.get("tasks") or [])
    if tasks:
        supplement = _rebound_supplement(
            project,
            plan_id=str(base_plan["plan_id"]),
            snapshot_digest=str(base_snapshot["snapshot_digest"]),
        )
        assets = _uploaded_assets(project, _load_uploaded_assets(uploaded_assets_path))
        frozen = store.create_and_approve_localized_image_successor(
            str(base_plan["plan_id"]),
            supplement=supplement,
            uploaded_assets=assets,
            approved_by="Kyle",
            user_approved=True,
        )
        plan = frozen["plan"]
        snapshot = frozen["publication_snapshot"]
        if set(((snapshot.get("product") or {}).get("image_routing") or {}).get("routes") or {}) != set(plan.get("targets") or []):
            raise ValueError("localized successor snapshot route coverage is incomplete")
    else:
        plan = base_plan
        snapshot = base_snapshot

    handoff = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "offer_id": offer_id,
        "status": "READY_TO_PUBLISH",
        "plan_id": plan["plan_id"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "payload_digest": plan["payload_digest"],
        "target_count": len(plan.get("targets") or []),
        "localized_route_count": len(
            (((snapshot.get("product") or {}).get("image_routing") or {}).get("routes") or {})
        ),
        "miaoshou_external_write_count": 1,
        "platform_writes": 0,
    }
    _write_json_atomic(
        REPO_ROOT / "reports" / "product-preparation" / offer_id / "workflow-handoff.json",
        handoff,
    )
    return handoff


def _result(
    summary: dict[str, Any], *, executed: bool, handoff: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    review = summary.get("review") or {}
    tasks = review.get("tasks") or []
    statuses = [str(row.get("status") or "") for row in tasks if isinstance(row, dict)]
    ready = all(status == "READY_FOR_REVIEW" for status in statuses)
    approved = review.get("status") == "APPROVED"
    approval_recorded = bool(review.get("approval_intent") or review.get("approval"))
    miaoshou_sync = review.get("miaoshou_pre_review_sync") or {}
    miaoshou_verified = bool(
        miaoshou_sync.get("status") == "VERIFIED"
        and miaoshou_sync.get("written_to_miaoshou") is True
        and miaoshou_sync.get("verified") is True
        and int(miaoshou_sync.get("external_write_count") or 0) == 1
    )
    execution_status = (
        "READY_TO_PUBLISH"
        if handoff and handoff.get("status") == "READY_TO_PUBLISH"
        else "APPROVED"
        if approved and miaoshou_verified
        else (
            "READY_FOR_EXECUTION_CHECKS"
            if ready and miaoshou_verified
            else (
                "MIAOSHOU_SYNC_REQUIRED"
                if ready or approved or approval_recorded
                else "PAID_CONFIRMATION_REQUIRED"
            )
        )
    )
    return {
        "schema_version": "prepare-product-images-result/v1",
        "offer_id": str(summary.get("offer_id") or ""),
        "status": execution_status,
        "approval_status": "APPROVED" if approval_recorded else "PENDING",
        "execution_status": execution_status,
        "input_schema_version": review.get("input_schema_version"),
        "input_digest": review.get("approved_snapshot_digest"),
        "review_revision": review.get("revision"),
        "selected_positions": review.get("selected_positions") or [],
        "route_locales": review.get("route_locales") or {},
        "paid_task_count": len(tasks),
        "external_generation_count": int(review.get("external_generation_count") or 0),
        "miaoshou_external_write_count": int(
            miaoshou_sync.get("external_write_count") or 0
        ),
        "miaoshou_pre_review_sync": miaoshou_sync or None,
        "paid_generation_executed": executed,
        "product_center_mutated": bool(review.get("product_center_mutated")),
        "platform_writes": int(review.get("platform_writes") or 0),
        "handoff": dict(handoff) if handoff else None,
        "review_url": (
            "http://127.0.0.1:8765/new-product?offer_id="
            f"{summary.get('offer_id')}"
            if tasks else None
        ),
        "result_url": (
            "http://127.0.0.1:8765/localized-image-review?offer_id="
            f"{summary.get('offer_id')}"
            if tasks else None
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    from modules.sourcing.new_product_workbench import (
        approve_localized_image_review,
        generate_localized_image_review,
        initialize_second_round_image_review,
        sync_localized_images_to_miaoshou_before_review,
    )

    summary = initialize_second_round_image_review(args.offer_id)
    review = summary.get("review") or {}
    tasks = review.get("tasks") or []
    if len(tasks) > MAX_PAID_TASKS:
        raise ValueError("the approved second-round paid task count exceeds the safety limit")
    executed = False
    if args.execute_paid:
        if not tasks:
            raise ValueError("the approved second-round plan has no paid image tasks")
        if args.confirm_paid_generation is not True:
            raise ValueError("explicit paid generation confirmation is required")
        pending = [
            row for row in tasks
            if isinstance(row, dict)
            and row.get("status") in {"PENDING_GENERATION", "RETRY_REQUESTED"}
        ]
        if pending:
            source_urls = list(
                dict.fromkeys(str(row.get("source_url") or "") for row in pending)
            )
            source_bytes = {url: _download_source(url) for url in source_urls}
            summary = generate_localized_image_review(
                args.offer_id,
                expected_revision=review.get("revision"),
                source_bytes_by_url=source_bytes,
                confirm_paid_generation=True,
            )
        executed = True
        result = _result(summary, executed=True)
        if result["status"] not in {
            "READY_FOR_EXECUTION_CHECKS",
            "MIAOSHOU_SYNC_REQUIRED",
        }:
            raise ValueError("localized image generation did not reach human review")
        if result["external_generation_count"] != result["paid_task_count"]:
            raise ValueError("localized image generation receipt count is incomplete")

    if args.execute_miaoshou:
        if args.confirm_miaoshou_write is not True:
            raise ValueError("explicit Miaoshou write confirmation is required")
        review = summary.get("review") or {}
        summary = sync_localized_images_to_miaoshou_before_review(
            args.offer_id,
            expected_revision=review.get("revision"),
        )

    if args.approve_all:
        if args.approved_by != "Kyle":
            raise ValueError("second-round approval must be attributed to Kyle")
        review = summary.get("review") or {}
        if not review.get("approval_intent"):
            summary = approve_localized_image_review(
                args.offer_id,
                expected_revision=review.get("revision"),
                approved_by=args.approved_by,
            )
        result = _result(summary, executed=executed)
        if result["approval_status"] != "APPROVED":
            raise ValueError("localized image approval was not persisted")
    else:
        result = _result(summary, executed=executed)

    if getattr(args, "finalize_release_handoff", False):
        handoff = finalize_release_handoff(
            summary,
            uploaded_assets_path=getattr(args, "uploaded_assets", None),
        )
        result = _result(summary, executed=executed, handoff=handoff)

    if result["product_center_mutated"] or result["platform_writes"]:
        raise ValueError("localized image generation crossed its isolation boundary")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the approved second-round localized image review."
    )
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--execute-paid", action="store_true")
    parser.add_argument("--confirm-paid-generation", action="store_true")
    parser.add_argument("--approve-all", action="store_true")
    parser.add_argument("--approved-by", default="Kyle")
    parser.add_argument("--execute-miaoshou", action="store_true")
    parser.add_argument("--confirm-miaoshou-write", action="store_true")
    parser.add_argument("--finalize-release-handoff", action="store_true")
    parser.add_argument(
        "--uploaded-assets",
        type=Path,
        help="Optional durable JSON mapping for legacy generated artifacts without a provider result URL.",
    )
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as error:
        print(
            json.dumps(
                {
                    "schema_version": "prepare-product-images-result/v1",
                    "offer_id": str(args.offer_id),
                    "status": "FAILED",
                    "error": str(error),
                    "platform_writes": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
