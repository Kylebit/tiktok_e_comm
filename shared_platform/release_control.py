"""Read-only release-candidate orchestration for the local Orbit console.

This module composes the five-domain contracts into a review surface.  It
never persists an approval, writes a workbench file, or calls a marketplace.
The product approval it creates is explicitly labelled as a simulation and is
used only to prove that the downstream channel draft contracts can be built.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

from core.config import ROOT
from core.db import connect_readonly
from domains.channel_operations import build_publication_plan
from domains.content_operations import build_workbench_content_package_handoff
from domains.product_operations import preview_product_approval_lock
from shared_platform.report_store import ReportRunStore
from shared_platform.weekly_profit_runner import build_weekly_profit_preview


DEFAULT_OFFER_ID = "3828811808"
DEFAULT_CANDIDATE_SELLER_SKU = "0946"


def _clean_offer_id(value: object) -> str:
    offer_id = str(value or "").strip()
    if not offer_id or not offer_id.isdigit() or len(offer_id) > 32:
        raise ValueError("offer_id must contain 1-32 digits")
    return offer_id


def _clean_seller_sku(value: object) -> str:
    seller_sku = str(value or "").strip()
    if not seller_sku or not seller_sku.isdigit() or len(seller_sku) > 32:
        raise ValueError("seller_sku must contain 1-32 digits")
    return seller_sku


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"required release evidence not found: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"release evidence must be a JSON object: {path}")
    return value


def _generation_audits(package_dir: Path) -> dict[str, dict[str, Any]]:
    audits: dict[str, dict[str, Any]] = {}
    candidates = sorted(package_dir.glob("generation_audit_*.json"))
    legacy = package_dir / "generation_audit.json"
    if legacy.is_file():
        candidates.insert(0, legacy)
    for path in candidates:
        artifact_id = (
            "wb1"
            if path.name == "generation_audit.json"
            else path.stem.removeprefix("generation_audit_")
        )
        audit = _read_json(path)
        local_image = package_dir / "generated" / f"{artifact_id}.png"
        # A stale audit must not claim technical completion when its local
        # verification artifact has disappeared.
        audit["download_verified"] = bool(audit.get("download_verified")) and local_image.is_file()
        audits[artifact_id] = audit
    return audits


def _known_seller_skus(database_path: Path) -> tuple[str, ...]:
    values: set[str] = set()
    with connect_readonly(database_path) as connection:
        for table in ("products", "shopee_products"):
            try:
                rows = connection.execute(
                    f"SELECT seller_sku FROM {table} "
                    "WHERE seller_sku IS NOT NULL AND TRIM(seller_sku) != ''"
                ).fetchall()
            except Exception:
                continue
            values.update(str(row["seller_sku"]).strip() for row in rows)
    return tuple(sorted(values))


def _content_copy(review: Mapping[str, Any], collect_box: Mapping[str, Any]) -> dict[str, str]:
    title = str(review.get("title") or collect_box.get("source_title") or "").strip()
    short_copy = str(
        review.get("short_copy")
        or review.get("description")
        or collect_box.get("short_copy")
        or ""
    ).strip()
    return {
        key: value
        for key, value in (("title", title), ("short_copy", short_copy))
        if value
    }


def _commercial_approval_facts(review: Mapping[str, Any]) -> dict[str, Any]:
    category = review.get("category")
    return {
        "cost_cny": review.get("cost_cny"),
        "weight_kg": review.get("weight_kg"),
        "package_cm": list(review.get("package_cm") or ()),
        "selected_sites": sorted(str(value) for value in (review.get("selected_sites") or ())),
        "selected_sku_keys": list(review.get("selected_sku_keys") or ()),
        "category": dict(category) if isinstance(category, Mapping) else category,
        "support_cod": review.get("support_cod"),
        "video_action": str(review.get("video_action") or ""),
    }


def _verified_image_write(content_state: Mapping[str, Any]) -> tuple[bool, list[str]]:
    write = content_state.get("miaoshou_ordered_images_write")
    if not isinstance(write, Mapping):
        write = content_state.get("miaoshou_generated_images_write")
    if not isinstance(write, Mapping):
        return False, []
    verified = bool(write.get("verified")) or str(write.get("status") or "") == "verified"
    values = (
        write.get("ordered_image_urls")
        or write.get("image_urls")
        or write.get("generated_image_urls")
        or []
    )
    urls = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    return verified, urls


def build_release_dashboard(
    *,
    offer_id: object = DEFAULT_OFFER_ID,
    seller_sku: object = DEFAULT_CANDIDATE_SELLER_SKU,
    root: str | Path = ROOT,
    database_path: str | Path | None = None,
    report_store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the complete local release rehearsal without side effects."""
    clean_offer_id = _clean_offer_id(offer_id)
    clean_seller_sku = _clean_seller_sku(seller_sku)
    project_root = Path(root)
    state_path = project_root / "data" / "new_product_workbench" / f"{clean_offer_id}.json"
    state = _read_json(state_path)
    if str(state.get("offer_id") or "").strip() != clean_offer_id:
        raise ValueError("workbench offer identity does not match the requested offer_id")
    review = state.get("review") if isinstance(state.get("review"), Mapping) else {}
    content_state = (
        state.get("content_package")
        if isinstance(state.get("content_package"), Mapping)
        else {}
    )
    collect_box_id = str(content_state.get("collect_box_id") or clean_offer_id).strip()
    if not collect_box_id.isdigit():
        collect_box_id = clean_offer_id
    if collect_box_id != clean_offer_id:
        explicit_subject = str(
            content_state.get("product_id")
            or content_state.get("approval_subject_id")
            or ""
        ).strip()
        if explicit_subject != clean_offer_id:
            raise ValueError(
                "content collect-box identity is not explicitly linked to the requested offer"
            )
    package_dir = project_root / "outputs" / "image_suite_from_miaoshou" / collect_box_id
    review_package = _read_json(package_dir / "review_package.json")
    collect_box = (
        review_package.get("collect_box")
        if isinstance(review_package.get("collect_box"), Mapping)
        else {}
    )
    if str(collect_box.get("detail_id") or "").strip() != collect_box_id:
        raise ValueError("review package collect-box identity does not match its evidence directory")
    suite_plan = (
        review_package.get("plan")
        if isinstance(review_package.get("plan"), Mapping)
        else {}
    )
    audits = _generation_audits(package_dir)
    content_handoff = build_workbench_content_package_handoff(
        product_id=clean_offer_id,
        state=state,
        suite_plan=suite_plan,
        generation_audits=audits,
        copy=_content_copy(review, collect_box),
        package_id=f"content:{clean_offer_id}",
    )

    db_path = Path(database_path or project_root / "data" / "shop.db")
    known_skus = _known_seller_skus(db_path)
    product_row = {
        "product_id": clean_offer_id,
        "seller_sku": clean_seller_sku,
        "title": str(review.get("title") or collect_box.get("source_title") or "").strip(),
        "sku_ids": list(review.get("selected_sku_keys") or ()),
        "platform": "orbit_release_rehearsal",
    }
    simulated_approval = {
        "approval_id": f"simulation:product:{clean_offer_id}:{clean_seller_sku}",
        "package_id": f"product:{clean_offer_id}:{clean_seller_sku}",
        "subject_type": "product",
        "subject_id": clean_offer_id,
        "status": "approved",
        "approved_by": "Kyle (release rehearsal only)",
        "approved_at": str(state.get("updated_at") or "2026-07-25T00:00:00+08:00"),
        "source_reference": f"workbench:{clean_offer_id}:revision:{state.get('_revision', 0)}",
    }
    simulation_state = dict(state)
    simulation_state.pop("product_approval", None)
    approval_preview = preview_product_approval_lock(
        state=simulation_state,
        product_row=product_row,
        content_package=content_handoff.content_package,
        seller_sku=clean_seller_sku,
        known_seller_skus=known_skus,
        user_approved=True,
        approval_fact=simulated_approval,
        expected_revision=int(state.get("_revision") or 0),
        approval_input_facts=_commercial_approval_facts(review),
    )
    publication_plan = (
        build_publication_plan(
            approval_preview.approved_package,
            content_handoff.content_package,
        )
        if approval_preview.approved_package is not None
        else None
    )

    actual_approval = (
        state.get("product_approval")
        if isinstance(state.get("product_approval"), Mapping)
        else {}
    )
    content_approved = (
        content_handoff.content_package.approval is not None
        and content_handoff.content_package.approval.status == "approved"
    )
    simulated_ready = approval_preview.approved_package is not None
    channel_ready = bool(
        publication_plan
        and all(not draft.missing_conditions for draft in publication_plan.drafts)
    )
    expected_approval = (
        approval_preview.state_patch.get("product_approval")
        if isinstance(approval_preview.state_patch.get("product_approval"), Mapping)
        else {}
    )
    required_approval_matches = {
        "status": "approved",
        "subject_type": "product",
        "subject_id": clean_offer_id,
        "seller_sku": clean_seller_sku,
        "content_package_id": content_handoff.content_package.package_id,
        "content_approval_id": (
            content_handoff.content_package.approval.approval_id
            if content_handoff.content_package.approval
            else ""
        ),
        "input_fingerprint": str(expected_approval.get("input_fingerprint") or ""),
    }
    actual_product_approved = (
        simulated_ready
        and bool(required_approval_matches["input_fingerprint"])
        and bool(actual_approval)
        and all(
        str(actual_approval.get(key) or "") == expected
        for key, expected in required_approval_matches.items()
        )
        and all(
        str(actual_approval.get(key) or "").strip()
        for key in ("approval_id", "package_id", "approved_by", "approved_at")
        )
    )
    current_image_urls = list(content_handoff.content_package.image_urls)
    image_write_verified, written_image_urls = _verified_image_write(content_state)
    current_images_written = (
        image_write_verified
        and written_image_urls == current_image_urls
        and bool(current_image_urls)
    )
    actual_blockers: list[str] = []
    if not actual_approval:
        actual_blockers.append("Product approval has not been persisted.")
    elif not actual_product_approved:
        actual_blockers.append(
            "Persisted product approval does not match the current product, SKU, content package, and input fingerprint."
        )
    if not bool(review.get("fields_locked")):
        actual_blockers.append("Workbench commercial fields are not locked.")
    elif str(review.get("seller_sku") or "").strip() != clean_seller_sku:
        actual_blockers.append(
            "Locked workbench Seller SKU does not match the approved candidate SKU."
        )
    if not image_write_verified:
        actual_blockers.append(
            "The current final image set has not been verified as written to Miaoshou."
        )
    elif not current_images_written:
        actual_blockers.append("The previous 11-image Miaoshou write is stale.")

    latest_weekly = latest_weekly_profit_summary(
        report_store_path
        or project_root / "data" / "orbit_platform.db"
    )
    return {
        "ok": True,
        "schema_version": "release-candidate-v1",
        "mode": "rehearsal",
        "safety": {
            "simulation_only": True,
            "publish_enabled": False,
            "external_writes_performed": [],
            "message": "No workbench, database, marketplace, or channel write was performed.",
        },
        "product": {
            "offer_id": clean_offer_id,
            "source_offer_id": str(collect_box.get("source_item_id") or ""),
            "seller_sku_candidate": clean_seller_sku,
            "title": product_row["title"],
            "category": dict(review.get("category") or {}),
            "cost_cny": review.get("cost_cny"),
            "weight_kg": review.get("weight_kg"),
            "package_cm": list(review.get("package_cm") or ()),
            "selected_sites": list(review.get("selected_sites") or ()),
            "selected_sku_keys": list(review.get("selected_sku_keys") or ()),
            "revision": int(state.get("_revision") or 0),
            "actual_product_approved": actual_product_approved,
            "actual_approval": dict(actual_approval),
        },
        "content": {
            "package_id": content_handoff.content_package.package_id,
            "approved": content_approved,
            "approval_status": (
                content_handoff.content_package.approval.status
                if content_handoff.content_package.approval
                else "missing"
            ),
            "image_count": len(content_handoff.asset_lineage),
            "images": [
                {
                    **asdict(row),
                    "position": index,
                }
                for index, row in enumerate(content_handoff.asset_lineage, start=1)
            ],
            "blockers": list(content_handoff.blockers),
            "missing_shot_ids": list(content_handoff.missing_shot_ids),
            "superseded_artifact_ids": list(content_handoff.superseded_artifact_ids),
            "stale_external_write": content_handoff.stale_external_write,
            "current_image_write_verified": current_images_written,
            "written_image_count": len(written_image_urls),
        },
        "approval_rehearsal": {
            "ready": simulated_ready,
            "blockers": list(approval_preview.blockers),
            "state_patch_preview": dict(approval_preview.state_patch),
            "persisted": False,
        },
        "publication_rehearsal": {
            "ready": channel_ready,
            "dry_run": True,
            "approval_required": True,
            "drafts": (
                [
                    {
                        "channel": draft.listing.channel,
                        "listing_id": draft.listing.listing_id,
                        "status": draft.listing.status,
                        "action": draft.action,
                        "missing_conditions": list(draft.missing_conditions),
                    }
                    for draft in publication_plan.drafts
                ]
                if publication_plan
                else []
            ),
        },
        "stages": [
            {"key": "source", "label": "Source facts", "status": "ready"},
            {
                "key": "content",
                "label": "Content & images",
                "status": "ready" if content_approved else "blocked",
            },
            {
                "key": "approval",
                "label": "SKU approval rehearsal",
                "status": "ready" if simulated_ready else "blocked",
            },
            {
                "key": "channels",
                "label": "Channel draft rehearsal",
                "status": "ready" if channel_ready else "blocked",
            },
        ],
        "actual_release_gate": {
            "ready": (
                content_approved
                and simulated_ready
                and actual_product_approved
                and current_images_written
                and str(review.get("seller_sku") or "").strip() == clean_seller_sku
                and not actual_blockers
            ),
            "blockers": actual_blockers,
        },
        "weekly_profit": latest_weekly,
    }


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def summarize_weekly_profit_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    realized = payload.get("realized_by_sku") or []
    estimates = payload.get("estimate_by_sku") or []
    quality = payload.get("quality_issues") or []
    negative = payload.get("negative_profit_skus") or []
    snapshot = payload.get("input_snapshot") if isinstance(payload.get("input_snapshot"), Mapping) else {}
    metadata = (
        snapshot.get("source_metadata")
        if isinstance(snapshot.get("source_metadata"), Mapping)
        else {}
    )
    adapter_issue_counts = (
        metadata.get("adapter_issue_counts")
        if isinstance(metadata.get("adapter_issue_counts"), Mapping)
        else {}
    )
    issue_group_counts = Counter(str(row.get("code") or "unknown") for row in quality)
    affected_row_counts = {
        code: int(adapter_issue_counts.get(code.removeprefix("upstream:"), group_count))
        for code, group_count in issue_group_counts.items()
    }
    blocking_fragments = (
        "missing_quantity",
        "missing_cost",
        "missing_fx",
        "missing_ad_spend",
        "missing_settlement",
        "missing_occurred_at",
    )
    status = str(payload.get("status") or "unknown")
    decision_blockers = [
        code
        for code in issue_group_counts
        if any(fragment in code for fragment in blocking_fragments)
    ]
    if status != "ready":
        decision_blockers.insert(0, f"report_status:{status}")
    decision_blockers = list(dict.fromkeys(decision_blockers))
    totals = {
        field: str(sum((_decimal(row.get(field)) for row in realized), Decimal("0")))
        for field in ("settlement_cny", "cost_cny", "ad_cost_cny", "profit_cny")
    }
    return {
        "available": True,
        "run_id": str(payload.get("run_id") or ""),
        "status": status,
        "period": dict(payload.get("period") or {}),
        "generated_at": str(payload.get("generated_at") or ""),
        "freshness": dict(payload.get("freshness") or {}),
        "totals": totals,
        "realized_bucket_count": len(realized),
        "estimate_bucket_count": len(estimates),
        "negative_profit_skus": list(negative),
        "quality_issues": list(quality),
        "quality_issue_group_count": len(quality),
        "quality_issue_group_counts": dict(sorted(issue_group_counts.items())),
        "quality_affected_row_counts": dict(sorted(affected_row_counts.items())),
        "source_file_count": len(metadata.get("source_files") or []),
        "source_row_counts": dict(metadata.get("adapter_row_counts") or {}),
        "snapshot_id": str(snapshot.get("snapshot_id") or ""),
        "preliminary": any(
            str(row.get("code") or "").endswith("missing_ad_spend")
            for row in quality
        ),
        "decision_usable": not decision_blockers,
        "decision_blockers": decision_blockers,
    }


def latest_weekly_profit_summary(path: str | Path) -> dict[str, Any]:
    rows = ReportRunStore(path).list_report_runs(limit=100)
    weekly = next(
        (
            row
            for row in rows
            if str(row.get("calculation_kind") or "") == "weekly_profit_digest"
        ),
        None,
    )
    if weekly is None:
        return {
            "available": False,
            "status": "not_generated",
            "quality_issues": [],
            "negative_profit_skus": [],
        }
    return summarize_weekly_profit_payload(weekly["payload"])


def build_weekly_profit_rehearsal(
    *,
    period_start: date,
    period_end: date,
    root: str | Path = ROOT,
) -> dict[str, Any]:
    """Recompute one report preview without persisting or notifying."""
    if period_end < period_start:
        raise ValueError("period end must not be before period start")
    if (
        (period_end - period_start).days != 6
        or period_start.weekday() != 0
        or period_end.weekday() != 6
    ):
        raise ValueError("weekly reporting period must be one complete Monday-through-Sunday week")
    preview = build_weekly_profit_preview(
        period_start=period_start,
        period_end=period_end,
        root=root,
    )
    summary = summarize_weekly_profit_payload(preview.report.payload())
    return {
        "ok": True,
        "mode": "rehearsal",
        "persisted": False,
        "notifications_sent": False,
        "external_writes_performed": [],
        "summary": summary,
    }
