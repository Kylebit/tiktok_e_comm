#!/usr/bin/env python3
"""DEPRECATED COMPATIBILITY: direct TikTok readback for incident diagnosis."""
from __future__ import annotations

from typing import Any, Mapping

from _common import add_repo_to_path, dashboard, safe_text
from _readback_cli import run


def readback(
    snapshot: Mapping[str, Any], dispatch: Mapping[str, Any], args: Any
) -> dict[str, Any]:
    platform = snapshot.get("platforms", {}).get("tiktok", {})
    approved_targets = (
        list(platform.get("targets") or []) if isinstance(platform, dict) else []
    )
    current = dashboard(args.base_url, snapshot["identity"]["offer_id"], args.timeout_seconds)
    ledger = _find_latest_tiktok_ledger(current)
    exact_by_target = _exact_draft_readback(current, args)
    outcome_by_target = {
        str(row.get("target_label")): str(row.get("status") or "").upper()
        for row in ledger.get("target_outcomes") or []
        if isinstance(row, dict) and row.get("target_label")
    }
    dispatch_rows = dispatch.get("safe_response", {}).get("targets")
    dispatched_targets: list[str] = []
    for row in dispatch_rows or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("target_label") or "")
        if label in approved_targets and label not in dispatched_targets:
            dispatched_targets.append(label)
    targets = dispatched_targets or approved_targets
    dispatch_by_target = {
        str(row.get("target_label")): str(row.get("outcome") or "").upper()
        for row in dispatch_rows or []
        if isinstance(row, dict) and row.get("target_label")
    }
    rows = []
    for target in targets:
        status = outcome_by_target.get(target)
        exact = exact_by_target.get(target)
        if exact == "READY" and status == "SUCCEEDED":
            verification = "VERIFIED"
        elif exact in {
            "MISMATCH",
            "REPAIR_REQUIRED",
            "READ_REJECTED",
            "PREPARATION_REJECTED",
        }:
            verification = "FAILED"
        elif status == "SUCCEEDED":
            verification = "UNAVAILABLE"
        elif status in {"FAILED", "REJECTED"}:
            verification = "FAILED"
        elif dispatch_by_target.get(target) in {"ACCEPTED", "SUCCEEDED"}:
            verification = "UNAVAILABLE"
        else:
            verification = "NOT_FOUND"
        rows.append({
            "target_label": target,
            "verification": verification,
            "draft_identity_recorded": status is not None,
            "price_category_variant_check": (
                "PASSED" if verification == "VERIFIED" else "UNVERIFIED"
            ),
        })
    verified_count = sum(row["verification"] == "VERIFIED" for row in rows)
    failed_count = sum(row["verification"] in {"FAILED", "NOT_FOUND"} for row in rows)
    complete = (
        dispatch.get("accepted") is True
        and bool(rows)
        and verified_count == len(rows)
    )
    status = "VERIFIED" if complete else (
        "MISMATCH" if failed_count else "UNAVAILABLE"
    )
    return {
        "schema_version": "platform-readback-fact/v1",
        "platform": "tiktok",
        "provider": "miaoshou_collectbox_receipt",
        "verification_scope": "draft identity, SKU, category and price; storefront readback may be unavailable by site",
        "exists": None,
        "verified": complete,
        "complete": complete,
        "status": status,
        "expected_count": len(rows),
        "verified_count": verified_count,
        "mismatch": failed_count > 0,
        "targets": rows,
        "message": safe_text(ledger.get("error") or ""),
        "retry_safe": dispatch.get("write_outcome") == "REJECTED",
    }


def _exact_draft_readback(current: Mapping[str, Any], args: Any) -> dict[str, str]:
    repo = getattr(args, "repo", None)
    if not repo:
        return {}
    add_repo_to_path(repo)
    from domains.channel_operations.tiktok_publisher import (
        TikTokPreWritePreparationError,
    )
    from modules.miaoshou.client import MiaoshouBusinessRejectedError
    from modules.miaoshou.tiktok_publisher import MiaoshouTikTokTransport
    from shared_platform.product_snapshot import build_approved_tiktok_publish_snapshot
    from shared_platform.release_store import default_release_store
    from shared_platform.collectbox_action import CollectBoxActionStore

    release = current.get("release_v1")
    projected_plan = release.get("plan") if isinstance(release, dict) else None
    if not isinstance(projected_plan, dict) or not projected_plan.get("plan_id"):
        return {}
    # The public dashboard intentionally projects a redacted plan payload.  It
    # is enough to identify the plan, but not to rebuild exact category, SKU,
    # price and parcel expectations.  Resolve that identity through the local
    # durable store before any executable provider readback.
    release_store = default_release_store()
    plan = release_store.get_plan(str(projected_plan["plan_id"]))
    store = CollectBoxActionStore(release_store.path)
    contexts = store.internal_tiktok_publish_contexts(plan_id=str(plan["plan_id"]))
    approved = build_approved_tiktok_publish_snapshot(
        plan,
        collectbox_contexts=contexts,
    )
    transport = MiaoshouTikTokTransport()
    results: dict[str, str] = {}
    for target in approved.get("targets") or []:
        if not isinstance(target, Mapping) or not target.get("target_label"):
            continue
        label = str(target["target_label"])
        try:
            draft = transport.read_draft(target)
            results[label] = (
                "READY"
                if transport.post_submit_draft_matches(target, draft)
                else "MISMATCH"
            )
        except MiaoshouBusinessRejectedError:
            results[label] = "READ_REJECTED"
        except TikTokPreWritePreparationError:
            results[label] = "PREPARATION_REJECTED"
        except Exception:
            results[label] = "READ_UNKNOWN"
    return results


def _find_latest_tiktok_ledger(value: object) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if str(node.get("platform") or "").upper() == "TIKTOK" and isinstance(node.get("target_outcomes"), list):
                matches.append(node)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return matches[-1] if matches else {}


if __name__ == "__main__":
    raise SystemExit(run("tiktok", readback))
