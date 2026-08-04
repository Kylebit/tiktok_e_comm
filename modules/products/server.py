"""本地 Web 控制台：页面 + REST API。"""

from __future__ import annotations

import json
import ipaddress
import logging
import mimetypes
import re
import socket
import threading
import time
import hashlib
import math
import os
import unicodedata
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

from core.config import ROOT
from modules.products import costs as cost_mod
from shared_platform.product_snapshot import TIKTOK_PUBLISH_TARGETS
from shared_platform.registry import http_registry

WEB_DIR = ROOT / "web"
DEFAULT_PORT = 8765
IMAGE_CACHE_DIR = ROOT / "data" / "web_image_cache"
PRODUCT_APPROVAL_BODY_LIMIT = 64 * 1024
_READONLY_SHOPEE_RECONCILE_TARGETS = frozenset({"shopee:PH", "shopee:TH"})
_ONECLICK_SHOPEE_MANUAL_REVIEW_TARGETS = frozenset(
    {"shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"}
)
_SHOPEE_GLOBAL_PLAN_PREVIEW_SCHEMA = "shopee-global-plan-preview/v1"
_SHOPEE_GLOBAL_PLAN_APPROVAL_RESPONSE_SCHEMA = (
    "shopee-global-plan-approval-response/v1"
)
_SHOPEE_GLOBAL_PLAN_OBSERVER_REQUEST_SCHEMA = (
    "shopee-global-plan-observer-request/v1"
)
_SHOPEE_GLOBAL_PLAN_POLICY_SCHEMA = (
    "shopee-global-plan-platform-policy/v1"
)
_CHANNEL_CATEGORY_PREVIEW_PATH = (
    "/api/product-workspace/channel-category-decision-preview"
)
_CHANNEL_CATEGORY_APPROVAL_PATH = (
    "/api/product-workspace/channel-category-decision"
)
_READONLY_SHOPEE_RECONCILE_CHECKS = frozenset(
    {
        "seller_sku",
        "model_sku",
        "localized_title",
        "rich_localized_description",
        "price",
        "image_count",
        "all_applicable_logistics",
        "status",
    }
)
_product_approval_lock = threading.Lock()
_release_execution_lock = threading.Lock()
_tiktok_publish_lock = threading.Lock()
_shopee_global_publish_lock = threading.Lock()
_ozon_publish_lock = threading.Lock()
_PLATFORM_PUBLISH_LOGGER = logging.getLogger("product_workspace.platform_publish")
_product_workbench_locks_guard = threading.Lock()
_product_workbench_locks: dict[str, threading.Lock] = {}

# Phase 1 ownership seam. Handler dispatch and every existing URL remain
# unchanged; later route modules can consume this registry during extraction.
HTTP_DOMAIN_REGISTRY = http_registry()


def _product_workspace_view(payload: dict) -> dict:
    """Present governed evidence and durable V1 state as the formal workspace."""
    from shared_platform.product_workflow import (
        assert_no_dead_end,
        project_product_workflow_next_action,
    )

    view_payload = dict(payload)
    view_payload.pop("_source_product_identity", None)
    view_payload.pop("_source_identity_inputs", None)
    view_payload.pop("_sku_lineage", None)
    product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
    listing_copy = (
        dict(payload.get("listing_copy"))
        if isinstance(payload.get("listing_copy"), dict)
        else {}
    )
    if (
        not str(listing_copy.get("shopee_description_en") or "").strip()
        and str(product.get("title") or "").strip()
        and str(product.get("seller_sku_candidate") or "").strip()
    ):
        from modules.shopee.global_copy import build_factual_english_description

        package = list(product.get("package_cm") or ())
        listing_copy["shopee_description_en"] = build_factual_english_description(
            {
                "title": str(product.get("title") or ""),
                "description": "",
                "package_dimensions": {
                    "length": package[0] if len(package) > 0 else None,
                    "width": package[1] if len(package) > 1 else None,
                    "height": package[2] if len(package) > 2 else None,
                },
            },
            str(product.get("seller_sku_candidate") or ""),
            title=str(product.get("title") or ""),
        )
        listing_copy["shopee_description_source"] = (
            "deterministic_verified_facts_fallback"
        )
        view_payload["listing_copy"] = listing_copy
    # The fallback is a presentation aid for legacy v2 drafts. It must not
    # alter the digest of an already-approved immutable ReleasePlan.
    release_v1 = _apply_oneclick_release_authority(_release_v1_view(payload))
    view = {
        **view_payload,
        "schema_version": "product-workspace-v1",
        "mode": "formal_v1",
        "workspace_mode": "formal_v1",
        "approval": payload.get("approval_rehearsal", {}),
        "publication_plan": payload.get("publication_rehearsal", {}),
        "release_v1": release_v1,
    }
    next_action = project_product_workflow_next_action(view)
    assert_no_dead_end(next_action)
    view["workflow_next_action"] = next_action
    return view


def _apply_oneclick_release_authority(release_v1: dict) -> dict:
    """Make the server the sole canonical status/next-action authority."""

    result = dict(release_v1)
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    plan_id = str(plan.get("plan_id") or "")
    if plan_id and result.get("plan_approved") is True:
        from shared_platform.collectbox_action import (
            approved_plan_identity,
            invalid_plan_projection,
            ready_projection,
        )

        try:
            identity = approved_plan_identity(plan)
        except (TypeError, ValueError) as error:
            collectbox_action = invalid_plan_projection(
                plan,
                detail=str(error),
            )
            result["collectbox_action"] = collectbox_action
            result["oneclick_controlplane"] = None
            result["target_recovery_actions"] = []
            result["runnable_target_count"] = 0
            result["publish_ready"] = False
            result["canonical_next_action"] = None
            return result
        plan = {**plan, "targets_digest": identity["targets_digest"]}
        result["plan"] = plan
        collectbox = _collectbox_action_store().status(plan_id=plan_id)
        collectbox_action = _with_collectbox_publishability(
            collectbox or ready_projection(plan)
        )
        result["collectbox_action"] = collectbox_action
        result["oneclick_controlplane"] = None
        result["target_recovery_actions"] = []
        result["runnable_target_count"] = 0
        result["publish_ready"] = False
        result["canonical_next_action"] = collectbox_action.get(
            "canonical_next_action"
        )
        return result
    job = _oneclick_control_store().get_job(plan_id=plan_id) if plan_id else None
    if job:
        job = _project_oneclick_dispatch_capability(job)
    if job:
        actions = [
            {
                "target_label": target["target_label"],
                "target_focus": target.get("next_action_target")
                or target["target_label"],
                "canonical_status": target["status"],
                "action": target["next_action"],
                "runnable": target.get("runnable_now") is True,
            }
            for target in [
                *(job.get("targets") or ()),
                *(job.get("shared_controls") or ()),
            ]
            if target.get("next_action")
        ]
        runnable = [
            action
            for action in actions
            if action["runnable"] is True
        ]
        result["oneclick_controlplane"] = job
        result["target_recovery_actions"] = actions
        result["runnable_target_count"] = int(
            job.get("runnable_target_count", len(runnable))
        )
        result["publish_ready"] = bool(
            job["phase"] == "READY"
            and result["runnable_target_count"] > 0
        )
        result["canonical_next_action"] = job.get(
            "canonical_next_action"
        )
        return result

    actions = []
    for action in result.get("target_recovery_actions") or ():
        if not isinstance(action, dict):
            continue
        target = str(
            action.get("target_label")
            or action.get("target")
            or ""
        )
        projected = dict(action)
        projected["target_focus"] = target or None
        actions.append(projected)
    runnable_count = int(result.get("runnable_target_count") or 0)
    if runnable_count <= 0:
        result["publish_ready"] = False
    result["target_recovery_actions"] = actions
    result["canonical_next_action"] = actions[0] if actions else (
        {
            "action": "refresh_release_state",
            "target_focus": None,
            "runnable": False,
        }
        if result.get("publish_ready") is not True
        else None
    )
    return result


_ONECLICK_RECOVERY_ACTION_PRIORITY = {
    "reconcile_before_any_retry": 10,
    "verify_submission_in_marketplace": 20,
    "retry_exact_zero_write_action": 30,
    "restore_channel_authorization": 40,
    "review_approved_content_facts": 45,
    "review_logistics_policy": 46,
    "approve_sellable_inventory": 50,
    "perform_governed_safe_action": 60,
    "resolve_source_product_identity": 70,
    "resolve_predecessor_sku_lineage": 71,
    "resolve_prerequisite_target": 80,
    "wait_for_channel_capability": 90,
    "wait_for_dependency": 100,
    "wait_for_dispatch_receipt": 110,
    "wait_for_preparation": 120,
    "prepare_batch": 130,
    "wait_for_worker": 140,
}


def _select_canonical_oneclick_action(
    actions: list[dict],
) -> dict | None:
    """Choose one stable server-owned action independent of target ordering."""

    if not actions:
        return None

    def priority(action: dict) -> tuple:
        runnable_rank = 0 if action.get("runnable") is True else 1
        recovery_rank = _ONECLICK_RECOVERY_ACTION_PRIORITY.get(
            str(action.get("action") or ""),
            999,
        )
        return (
            runnable_rank,
            recovery_rank,
            str(action.get("target_focus") or ""),
            str(action.get("target_label") or ""),
        )

    return min(actions, key=priority)


_INITIAL_PRODUCT_REVIEW_FIELDS = (
    "selected_sites",
    "title",
    "category",
    "cost_cny",
    "weight_kg",
    "package_cm",
    "video_action",
    "video_url",
    "support_cod",
    "image_actions",
    "image_order",
    "selected_sku_keys",
    "sku_label_overrides",
    "fx_rates",
)


def _product_workbench_lock(offer_id: str) -> threading.Lock:
    """Return a per-product lock without serializing unrelated queue items."""

    with _product_workbench_locks_guard:
        return _product_workbench_locks.setdefault(offer_id, threading.Lock())


def _collect_product_workspace_locally(data: dict) -> tuple[int, dict]:
    """Read one Miaoshou collect-box item and create its local workbench.

    This boundary intentionally performs only one upstream read and one local
    state write.  It never prepares images, writes Miaoshou, claims a listing,
    or calls any channel publication adapter.
    """

    from modules.sourcing import new_product_workbench as np_mod
    from shared_platform import release_control

    offer_id = str(data.get("offer_id") or "").strip()
    if not offer_id.isdigit() or not 1 <= len(offer_id) <= 32:
        return 400, {
            "ok": False,
            "error_code": "invalid_offer_id",
            "error": "offer_id must contain 1-32 digits",
        }

    with _product_workbench_lock(offer_id):
        state = np_mod.load_state(offer_id)
        current_revision = int(state.get("_revision") or 0)
        if current_revision:
            if str(state.get("offer_id") or "").strip() != offer_id:
                return 409, {
                    "ok": False,
                    "error_code": "workbench_identity_conflict",
                    "error": "existing workbench identity does not match offer_id",
                    "current_revision": current_revision,
                }
            try:
                dashboard = release_control.build_release_dashboard(
                    offer_id=offer_id,
                )
            except (TypeError, ValueError) as error:
                return 409, {
                    "ok": False,
                    "error_code": "existing_workbench_invalid",
                    "error": str(error),
                    "current_revision": current_revision,
                }
            except FileNotFoundError as error:
                return 404, {
                    "ok": False,
                    "error_code": "existing_workbench_evidence_missing",
                    "error": str(error),
                    "current_revision": current_revision,
                }
            except Exception as error:
                return 500, {
                    "ok": False,
                    "error_code": "dashboard_refresh_failed",
                    "error": str(error),
                    "current_revision": current_revision,
                }
            return 200, {
                "ok": True,
                "idempotent": True,
                "persisted": False,
                "source_read_performed": False,
                "local_writes_performed": [],
                "external_writes_performed": [],
                "dashboard": _product_workspace_view(dashboard),
            }

        try:
            preview = np_mod.precollect_preview(offer_id)
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            return 502, {
                "ok": False,
                "error_code": "miaoshou_collect_read_failed",
                "error": str(error),
                "retryable": True,
                "external_writes_performed": [],
            }
        except Exception as error:
            return 502, {
                "ok": False,
                "error_code": "miaoshou_collect_read_failed",
                "error": str(error),
                "retryable": True,
                "external_writes_performed": [],
            }

        if str(preview.get("offer_id") or "").strip() != offer_id:
            return 409, {
                "ok": False,
                "error_code": "miaoshou_collect_identity_mismatch",
                "error": "Miaoshou collect detail identity does not match offer_id",
                "external_writes_performed": [],
            }
        preview_review = preview.get("review")
        if not isinstance(preview_review, dict):
            return 502, {
                "ok": False,
                "error_code": "miaoshou_collect_payload_invalid",
                "error": "Miaoshou collect preview did not return product review facts",
                "external_writes_performed": [],
            }
        initial_review = {
            key: preview_review[key]
            for key in _INITIAL_PRODUCT_REVIEW_FIELDS
            if key in preview_review
        }
        initial_review["fields_locked"] = False
        initial_state = {
            "_revision": 0,
            "review": initial_review,
            "collection": {
                "source": "miaoshou_common_collect_detail",
                "collect_box_id": offer_id,
                "read_only": True,
            },
        }
        try:
            saved = np_mod.save_state(offer_id, initial_state)
        except RuntimeError:
            latest = np_mod.load_state(offer_id)
            if (
                int(latest.get("_revision") or 0) > 0
                and str(latest.get("offer_id") or "").strip() == offer_id
            ):
                try:
                    dashboard = release_control.build_release_dashboard(
                        offer_id=offer_id,
                    )
                except Exception as error:
                    return 500, {
                        "ok": False,
                        "error_code": "dashboard_refresh_failed",
                        "error": str(error),
                        "current_revision": int(latest.get("_revision") or 0),
                    }
                return 200, {
                    "ok": True,
                    "idempotent": True,
                    "persisted": False,
                    "source_read_performed": True,
                    "local_writes_performed": [],
                    "external_writes_performed": [],
                    "dashboard": _product_workspace_view(dashboard),
                }
            return 409, {
                "ok": False,
                "error_code": "state_revision_conflict",
                "error": "workbench state changed while collection was being saved",
                "current_revision": int(latest.get("_revision") or 0),
                "external_writes_performed": [],
            }

        try:
            dashboard = release_control.build_release_dashboard(
                offer_id=offer_id,
            )
        except Exception as error:
            return 500, {
                "ok": False,
                "error_code": "dashboard_refresh_failed_after_collection",
                "error": str(error),
                "current_revision": int(saved.get("_revision") or 0),
                "collection_persisted": True,
                "external_writes_performed": [],
            }
        return 201, {
            "ok": True,
            "idempotent": False,
            "persisted": True,
            "source_read_performed": True,
            "local_writes_performed": ["workbench_state"],
            "external_writes_performed": [],
            "dashboard": _product_workspace_view(dashboard),
        }


def _save_product_workspace_facts_locally(data: dict) -> tuple[int, dict]:
    """Save one unlocked commercial-facts revision; perform no external write."""

    from modules.sourcing import new_product_workbench as np_mod
    from shared_platform import release_control

    offer_id = str(data.get("offer_id") or "").strip()
    expected_revision = data.get("expected_revision")
    if not offer_id.isdigit() or not 1 <= len(offer_id) <= 32:
        return 400, {
            "ok": False,
            "error_code": "invalid_offer_id",
            "error": "offer_id must contain 1-32 digits",
        }
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        return 400, {
            "ok": False,
            "error_code": "invalid_expected_revision",
            "error": "expected_revision must be a positive integer",
        }
    title = str(data.get("title") or "").strip()
    if not title or len(title) > 220:
        return 400, {
            "ok": False,
            "error_code": "invalid_title",
            "error": "title must contain 1-220 characters",
        }

    def positive_number(name: str) -> tuple[float | None, tuple[int, dict] | None]:
        raw = data.get(name)
        if isinstance(raw, bool):
            return None, (
                400,
                {
                    "ok": False,
                    "error_code": f"invalid_{name}",
                    "error": f"{name} must be a positive finite number",
                },
            )
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value) or value <= 0:
            return None, (
                400,
                {
                    "ok": False,
                    "error_code": f"invalid_{name}",
                    "error": f"{name} must be a positive finite number",
                },
            )
        return value, None

    cost_cny, failure = positive_number("cost_cny")
    if failure:
        return failure
    weight_kg, failure = positive_number("weight_kg")
    if failure:
        return failure
    package_raw = data.get("package_cm")
    if not isinstance(package_raw, list) or len(package_raw) != 3:
        return 400, {
            "ok": False,
            "error_code": "invalid_package_cm",
            "error": "package_cm must contain exactly three positive numbers",
        }
    package_cm: list[float] = []
    for raw in package_raw:
        if isinstance(raw, bool):
            value = 0.0
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = 0.0
        if not math.isfinite(value) or value <= 0:
            return 400, {
                "ok": False,
                "error_code": "invalid_package_cm",
                "error": "package_cm must contain exactly three positive numbers",
            }
        package_cm.append(value)
    raw_sku_keys = data.get("selected_sku_keys")
    if not isinstance(raw_sku_keys, list):
        return 400, {
            "ok": False,
            "error_code": "invalid_selected_sku_keys",
            "error": "selected_sku_keys must be a list",
        }
    selected_sku_keys = list(
        dict.fromkeys(str(value or "").strip() for value in raw_sku_keys)
    )
    if any(not value or len(value) > 240 for value in selected_sku_keys):
        return 400, {
            "ok": False,
            "error_code": "invalid_selected_sku_keys",
            "error": "selected_sku_keys contains an invalid source SKU key",
        }
    raw_sku_label_overrides = data.get("sku_label_overrides", {})
    if not isinstance(raw_sku_label_overrides, dict):
        return 400, {
            "ok": False,
            "error_code": "invalid_sku_label_overrides",
            "error": "sku_label_overrides must be an object keyed by source SKU",
        }
    requested_sku_labels: dict[str, str] = {}
    for raw_key, raw_label in raw_sku_label_overrides.items():
        key = str(raw_key or "").strip()
        label = " ".join(str(raw_label or "").split())
        if (
            not key
            or len(key) > 240
            or not label
            or len(label) > 50
            or any(ord(char) < 32 for char in label)
        ):
            return 400, {
                "ok": False,
                "error_code": "invalid_sku_label_overrides",
                "error": (
                    "each edited specification name must contain 1-50 "
                    "printable characters"
                ),
            }
        requested_sku_labels[key] = label

    with _product_workbench_lock(offer_id):
        state = np_mod.load_state(offer_id)
        current_revision = int(state.get("_revision") or 0)
        if current_revision < 1 or str(state.get("offer_id") or "").strip() != offer_id:
            return 404, {
                "ok": False,
                "error_code": "workbench_not_collected",
                "error": "collect this Miaoshou item before editing product facts",
            }
        if current_revision != expected_revision:
            return 409, {
                "ok": False,
                "error_code": "state_revision_conflict",
                "error": "state revision is stale",
                "current_revision": current_revision,
            }
        review = state.get("review")
        if not isinstance(review, dict):
            return 409, {
                "ok": False,
                "error_code": "workbench_review_invalid",
                "error": "state review must be a mapping",
                "current_revision": current_revision,
            }
        approval = state.get("product_approval")
        if bool(review.get("fields_locked")) or (
            isinstance(approval, dict)
            and str(approval.get("status") or "").strip().casefold() == "approved"
        ):
            return 409, {
                "ok": False,
                "error_code": "product_facts_locked",
                "error": "approved product facts are locked; supersede the approval before editing",
                "current_revision": current_revision,
            }

        source = np_mod._source_summary(offer_id)
        source_label_by_key = {
            str(row.get("key") or row.get("name") or "").strip(): str(
                row.get("name") or row.get("key") or ""
            ).strip()
            for row in (source.get("skus") or ())
            if isinstance(row, dict)
            and str(row.get("key") or row.get("name") or "").strip()
        }
        available_sku_keys = set(source_label_by_key)
        unknown_sku_keys = [
            value for value in selected_sku_keys if value not in available_sku_keys
        ]
        if unknown_sku_keys:
            return 400, {
                "ok": False,
                "error_code": "unknown_source_sku",
                "error": "selected_sku_keys contains keys not present in the collected source",
                "unknown_sku_keys": unknown_sku_keys,
            }
        if available_sku_keys and not selected_sku_keys:
            return 400, {
                "ok": False,
                "error_code": "missing_source_sku_selection",
                "error": "select at least one collected source SKU",
            }
        unknown_label_keys = [
            key for key in requested_sku_labels if key not in available_sku_keys
        ]
        if unknown_label_keys:
            return 400, {
                "ok": False,
                "error_code": "unknown_sku_label_override",
                "error": "sku_label_overrides contains keys not present in the source",
                "unknown_sku_keys": unknown_label_keys,
            }
        unselected_label_keys = [
            key for key in requested_sku_labels if key not in selected_sku_keys
        ]
        if unselected_label_keys:
            return 400, {
                "ok": False,
                "error_code": "unselected_sku_label_override",
                "error": "only selected source SKUs may have edited names",
                "unselected_sku_keys": unselected_label_keys,
            }
        effective_sku_labels = [
            requested_sku_labels.get(key) or source_label_by_key.get(key) or key
            for key in selected_sku_keys
        ]
        if len({label.casefold() for label in effective_sku_labels}) != len(
            effective_sku_labels
        ):
            return 400, {
                "ok": False,
                "error_code": "duplicate_effective_sku_label",
                "error": "selected specification names must remain unique",
            }
        sku_label_overrides = {
            key: label
            for key, label in requested_sku_labels.items()
            if label != source_label_by_key.get(key)
        }

        next_state = dict(state)
        next_review = dict(review)
        next_review.update(
            {
                "title": title,
                "cost_cny": cost_cny,
                "weight_kg": weight_kg,
                "package_cm": package_cm,
                "selected_sku_keys": selected_sku_keys,
                "sku_label_overrides": sku_label_overrides,
            }
        )
        next_state["review"] = next_review
        listing_copy = (
            dict(state.get("listing_copy"))
            if isinstance(state.get("listing_copy"), dict)
            else {}
        )
        if listing_copy:
            from domains.content_operations import listing_title_fact_signature

            title_facts = _listing_title_facts(
                np_mod,
                offer_id,
                next_state,
                source=source,
            )
            if listing_copy.get("input_signature") != listing_title_fact_signature(
                title_facts
            ):
                listing_copy["status"] = "superseded_product_facts_changed"
            elif title == str(listing_copy.get("semantic_master_en") or "").strip():
                listing_copy["status"] = "adopted_in_product_facts"
                listing_copy["adopted_title"] = title
            next_state["listing_copy"] = listing_copy
        try:
            saved = np_mod.save_state(offer_id, next_state)
        except RuntimeError:
            latest = np_mod.load_state(offer_id)
            return 409, {
                "ok": False,
                "error_code": "state_revision_conflict",
                "error": "state revision is stale",
                "current_revision": int(latest.get("_revision") or 0),
            }
        try:
            dashboard = release_control.build_release_dashboard(
                offer_id=offer_id,
            )
        except Exception as error:
            return 500, {
                "ok": False,
                "error_code": "dashboard_refresh_failed_after_facts_save",
                "error": str(error),
                "current_revision": int(saved.get("_revision") or 0),
                "facts_persisted": True,
                "external_writes_performed": [],
            }
        return 200, {
            "ok": True,
            "persisted": True,
            "revision": int(saved.get("_revision") or 0),
            "local_writes_performed": ["workbench_state"],
            "external_writes_performed": [],
            "dashboard": _product_workspace_view(dashboard),
        }


def _listing_title_facts(
    np_mod,
    offer_id: str,
    state: dict,
    *,
    source: dict | None = None,
) -> dict:
    """Build the explicit fact input used by the content-domain title model."""

    source = source or np_mod._source_summary(offer_id)
    review = state.get("review") if isinstance(state.get("review"), dict) else {}
    selected = set(str(value) for value in (review.get("selected_sku_keys") or ()))
    label_overrides = (
        review.get("sku_label_overrides")
        if isinstance(review.get("sku_label_overrides"), dict)
        else {}
    )
    selected_skus = [
        {
            "key": str(row.get("key") or row.get("name") or ""),
            "label": str(
                label_overrides.get(str(row.get("key") or row.get("name") or ""))
                or row.get("name")
                or row.get("key")
                or ""
            ),
            "price_cny": row.get("price"),
        }
        for row in (source.get("skus") or ())
        if isinstance(row, dict)
        and str(row.get("key") or row.get("name") or "") in selected
    ]
    return {
        "offer_id": offer_id,
        "source_title_zh": str(source.get("title_source") or "").strip(),
        "category": dict(review.get("category") or {}),
        "cost_cny": review.get("cost_cny"),
        "weight_kg": review.get("weight_kg"),
        "package_cm": list(review.get("package_cm") or ()),
        "selected_skus": selected_skus,
        "verified_attributes": dict(source.get("attributes") or {}),
    }


def _semantic_master_title(value: object) -> str:
    """Return one safe English master title or raise a reviewable error."""

    title = " ".join(str(value or "").split()).strip(" \"'|")
    if not title:
        raise ValueError("semantic_master_en is required")
    if len(title) > 180:
        raise ValueError("semantic_master_en exceeds 180 characters")
    if not any("A" <= char <= "Z" or "a" <= char <= "z" for char in title):
        raise ValueError("semantic_master_en must contain English letters")
    if any("\u3400" <= char <= "\u9fff" for char in title):
        raise ValueError("semantic_master_en must not contain Chinese text")
    if any(
        ord(char) < 32 or 0x1F300 <= ord(char) <= 0x1FAFF
        for char in title
    ):
        raise ValueError("semantic_master_en contains unsupported characters")
    return title


def _adopt_product_workspace_title_candidate(data: dict) -> tuple[int, dict]:
    """Adopt the current EN master and explicitly supersede prior approvals."""

    from domains.content_operations import listing_title_fact_signature
    from modules.sourcing import new_product_workbench as np_mod
    from shared_platform import release_control
    from shared_platform.release_store import (
        ReleaseStoreError,
        default_release_store,
    )

    offer_id = str(data.get("offer_id") or "").strip()
    expected_revision = data.get("expected_revision")
    requested_title = str(data.get("candidate_title") or "").strip()
    requested_signature = str(data.get("input_signature") or "").strip()
    if not offer_id.isdigit() or not 1 <= len(offer_id) <= 32:
        return 400, {
            "ok": False,
            "error_code": "invalid_offer_id",
            "error": "offer_id must contain 1-32 digits",
        }
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        return 400, {
            "ok": False,
            "error_code": "invalid_expected_revision",
            "error": "expected_revision must be a positive integer",
        }
    if data.get("user_approved") is not True:
        return 400, {
            "ok": False,
            "error_code": "explicit_approval_required",
            "error": "explicit user_approved=true is required",
        }
    if str(data.get("approved_by") or "").strip() != "Kyle":
        return 400, {
            "ok": False,
            "error_code": "invalid_approver",
            "error": "approved_by must be Kyle",
        }
    if not requested_title or not requested_signature:
        return 400, {
            "ok": False,
            "error_code": "candidate_identity_required",
            "error": "candidate_title and input_signature are required",
        }

    # Serialize with channel execution so a plan cannot begin an external
    # target while this transition is invalidating that exact plan.
    with _release_execution_lock, _product_workbench_lock(offer_id):
        state = np_mod.load_state(offer_id)
        current_revision = int(state.get("_revision") or 0)
        if current_revision != expected_revision:
            return 409, {
                "ok": False,
                "error_code": "state_revision_conflict",
                "error": "state revision is stale",
                "current_revision": current_revision,
            }
        review = state.get("review")
        if not isinstance(review, dict):
            return 409, {
                "ok": False,
                "error_code": "workbench_review_invalid",
                "error": "state review must be a mapping",
            }
        approval = state.get("product_approval")
        if not (
            isinstance(approval, dict)
            and str(approval.get("status") or "").strip().casefold() == "approved"
            and bool(review.get("fields_locked"))
        ):
            return 409, {
                "ok": False,
                "error_code": "active_product_approval_required",
                "error": (
                    "this transition requires the current product approval "
                    "and locked facts"
                ),
            }
        listing_copy = state.get("listing_copy")
        if not isinstance(listing_copy, dict):
            return 409, {
                "ok": False,
                "error_code": "title_candidate_missing",
                "error": "the current listing_copy candidate is missing",
            }
        stored_signature = str(
            listing_copy.get("input_signature") or ""
        ).strip()
        if requested_signature != stored_signature:
            return 409, {
                "ok": False,
                "error_code": "title_candidate_mismatch",
                "error": "title candidate identity changed; refresh before adopting",
            }
        source = np_mod._source_summary(offer_id)
        current_signature = listing_title_fact_signature(
            _listing_title_facts(
                np_mod,
                offer_id,
                state,
                source=source,
            )
        )
        stored_fact_snapshot = listing_copy.get("fact_snapshot")
        snapshot_signature = (
            listing_title_fact_signature(stored_fact_snapshot)
            if isinstance(stored_fact_snapshot, dict)
            else ""
        )
        candidate_is_current = bool(stored_signature) and (
            stored_signature == current_signature
            or snapshot_signature == current_signature
        )
        if not candidate_is_current:
            return 409, {
                "ok": False,
                "error_code": "title_candidate_stale",
                "error": (
                    "listing_copy input_signature no longer matches the "
                    "current product facts"
                ),
                "current_input_signature": current_signature,
            }
        stored_title = str(
            listing_copy.get("semantic_master_en") or ""
        ).strip()
        if requested_title != stored_title:
            return 409, {
                "ok": False,
                "error_code": "title_candidate_mismatch",
                "error": "semantic_master_en changed; refresh before adopting",
            }
        try:
            adopted_title = _semantic_master_title(stored_title)
        except ValueError as error:
            return 409, {
                "ok": False,
                "error_code": "invalid_semantic_master_en",
                "error": str(error),
            }
        title_changed = adopted_title != str(review.get("title") or "").strip()

        store = default_release_store()
        active_plan = store.active_plan_for_product(offer_id)
        active_plan_id = (
            str(active_plan.get("plan_id") or "").strip()
            if isinstance(active_plan, dict)
            else ""
        )
        prior_superseded_plan_id = str(
            listing_copy.get("superseded_release_plan_id") or ""
        ).strip()
        predecessor_plan_id = active_plan_id or prior_superseded_plan_id
        reason = (
            (
                "Kyle adopted the current semantic_master_en; product facts "
                f"changed from revision {expected_revision}"
            )
            if title_changed
            else (
                "Kyle reaffirmed a refreshed semantic_master_en that already "
                f"matches approved product revision {expected_revision}"
            )
        )
        if active_plan_id:
            try:
                store.supersede_plan(active_plan_id, reason=reason)
            except ReleaseStoreError as error:
                return 409, {
                    "ok": False,
                    "error_code": "release_plan_supersession_failed",
                    "error": str(error),
                }

        superseded_at = datetime.now(timezone.utc).isoformat()
        prior_approval_id = str(approval.get("approval_id") or "").strip()
        next_state = dict(state)
        if title_changed:
            next_review = dict(review)
            next_review.update(
                {
                    "title": adopted_title,
                    "fields_locked": False,
                }
            )
            next_state["review"] = next_review
            next_state["product_approval"] = {
                **approval,
                "status": "superseded",
                "superseded_at": superseded_at,
                "superseded_by": "product_workspace_en_master_adoption",
                "superseded_revision": expected_revision,
                "superseded_fields": ["title"],
                "supersede_reason": reason,
            }
        next_listing_copy = dict(listing_copy)
        next_listing_copy.update(
            {
                "status": "adopted_in_product_facts",
                "adopted_title": adopted_title,
                "adopted_at": superseded_at,
                "adopted_by": "Kyle",
                "adoption_input_signature": stored_signature,
                "superseded_product_approval_id": (
                    prior_approval_id or None
                ) if title_changed else None,
                "superseded_release_plan_id": predecessor_plan_id or None,
                "product_approval_preserved": not title_changed,
            }
        )
        next_state["listing_copy"] = next_listing_copy
        supersessions = list(state.get("commercial_supersessions") or ())
        supersessions.append(
            {
                "source": "product_workspace_en_master_adoption",
                "status": "superseded" if title_changed else "reaffirmed",
                "expected_revision": expected_revision,
                "changed_fields": ["title"] if title_changed else [],
                "reason": reason,
                "superseded_at": superseded_at,
                "prior_approval_id": (
                    prior_approval_id or None
                ) if title_changed else None,
                "preserved_approval_id": (
                    prior_approval_id or None
                ) if not title_changed else None,
                "prior_release_plan_id": predecessor_plan_id or None,
                "adopted_title": adopted_title,
                "input_signature": stored_signature,
                "approved_by": "Kyle",
            }
        )
        next_state["commercial_supersessions"] = supersessions
        try:
            saved = np_mod.save_state(offer_id, next_state)
        except RuntimeError:
            latest = np_mod.load_state(offer_id)
            return 409, {
                "ok": False,
                "error_code": "state_revision_conflict",
                "error": (
                    "state revision changed after the old ReleasePlan was "
                    "safely superseded; refresh before retrying"
                ),
                "current_revision": int(latest.get("_revision") or 0),
                "release_plan_superseded": bool(active_plan_id),
            }

    try:
        dashboard = release_control.build_release_dashboard(offer_id=offer_id)
    except Exception as error:
        return 500, {
            "ok": False,
            "error_code": "dashboard_refresh_failed_after_title_adoption",
            "error": str(error),
            "current_revision": int(saved.get("_revision") or 0),
            "title_adoption_persisted": True,
            "external_writes_performed": [],
        }
    local_writes = ["workbench_state"]
    if active_plan_id:
        local_writes.insert(0, "release_plan_supersession")
    return 200, {
        "ok": True,
        "persisted": True,
        "revision": int(saved.get("_revision") or 0),
        "adopted_title": adopted_title,
        "superseded_product_approval_id": (
            prior_approval_id or None
        ) if title_changed else None,
        "superseded_release_plan_id": predecessor_plan_id or None,
        "product_approval_preserved": not title_changed,
        "next_action": (
            "review_and_reapprove_product_facts"
            if title_changed
            else "create_successor_release_plan"
        ),
        "local_writes_performed": local_writes,
        "external_writes_performed": [],
        "dashboard": _product_workspace_view(dashboard),
    }


def _generate_product_workspace_title_draft(data: dict) -> tuple[int, dict]:
    """Generate and persist model copy candidates; perform no marketplace write."""

    from domains.content_operations import generate_title_candidates
    from domains.content_operations.listing_title_candidates import POLICY_VERSION
    from modules.sourcing import new_product_workbench as np_mod
    from shared_platform import release_control
    from shared_platform.release_store import (
        ReleaseStoreError,
        default_release_store,
    )

    offer_id = str(data.get("offer_id") or "").strip()
    expected_revision = data.get("expected_revision")
    if not offer_id.isdigit() or not 1 <= len(offer_id) <= 32:
        return 400, {"ok": False, "error": "offer_id must contain 1-32 digits"}
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        return 400, {
            "ok": False,
            "error": "expected_revision must be a positive integer",
        }

    with _release_execution_lock, _product_workbench_lock(offer_id):
        state = np_mod.load_state(offer_id)
        current_revision = int(state.get("_revision") or 0)
        if current_revision != expected_revision:
            return 409, {
                "ok": False,
                "error": "state revision is stale",
                "current_revision": current_revision,
            }
        review = state.get("review")
        if not isinstance(review, dict):
            return 409, {"ok": False, "error": "state review must be a mapping"}
        approval = state.get("product_approval")
        locked = bool(review.get("fields_locked"))
        listing_copy = (
            state.get("listing_copy")
            if isinstance(state.get("listing_copy"), dict)
            else {}
        )
        policy_stale = (
            str(listing_copy.get("policy_version") or "") != POLICY_VERSION
        )
        locked_stale_refresh = (
            locked
            and (
                str(listing_copy.get("status") or "").startswith("superseded")
                or policy_stale
            )
            and data.get("refresh_stale_locked_candidate") is True
            and data.get("user_approved") is True
            and str(data.get("approved_by") or "").strip() == "Kyle"
        )
        locked_missing_recovery = (
            locked
            and not str(listing_copy.get("semantic_master_en") or "").strip()
            and data.get("recover_missing_locked_candidate") is True
            and data.get("user_approved") is True
            and str(data.get("approved_by") or "").strip() == "Kyle"
        )
        locked_unadopted_refresh = (
            locked
            and str(listing_copy.get("status") or "")
            == "draft_pending_kyle_review"
            and data.get("replace_unadopted_locked_candidate") is True
            and data.get("user_approved") is True
            and str(data.get("approved_by") or "").strip() == "Kyle"
        )
        locked_candidate_recovery = (
            locked_stale_refresh
            or locked_missing_recovery
            or locked_unadopted_refresh
        )
        if locked and not locked_candidate_recovery:
            return 409, {
                "ok": False,
                "error_code": "locked_title_refresh_requires_kyle_approval",
                "error": (
                    "approved product facts are locked; only an explicitly "
                    "approved refresh or missing-candidate recovery is allowed"
                ),
            }
        if locked_candidate_recovery and not (
            isinstance(approval, dict)
            and str(approval.get("status") or "").strip().casefold() == "approved"
        ):
            return 409, {
                "ok": False,
                "error_code": "active_product_approval_required",
                "error": "locked title refresh requires the active product approval",
            }
        facts = _listing_title_facts(np_mod, offer_id, state)
        try:
            draft = generate_title_candidates(facts)
        except (RuntimeError, TypeError, ValueError) as error:
            return 502, {
                "ok": False,
                "error": str(error),
                "model_request_failed": True,
                "marketplace_writes_performed": [],
            }
        from domains.content_operations import (
            listing_title_fact_signature,
            listing_title_fact_snapshot,
            listing_title_model_input_signature,
        )

        draft = dict(draft)
        draft.setdefault("input_signature", listing_title_fact_signature(facts))
        draft.setdefault("fact_snapshot", listing_title_fact_snapshot(facts))
        draft.setdefault(
            "model_input_signature",
            listing_title_model_input_signature(facts),
        )
        superseded_plan_id = str(
            listing_copy.get("superseded_release_plan_id") or ""
        ).strip()
        if locked_candidate_recovery:
            store = default_release_store()
            active_plan = store.active_plan_for_product(offer_id)
            active_plan_id = (
                str(active_plan.get("plan_id") or "").strip()
                if isinstance(active_plan, dict)
                else ""
            )
            if active_plan_id:
                superseded_plan_id = active_plan_id
                try:
                    store.supersede_plan(
                        active_plan_id,
                        reason=(
                            "Kyle recovered an audited title candidate "
                            f"from locked product revision {expected_revision}"
                        ),
                    )
                except ReleaseStoreError as error:
                    return 409, {
                        "ok": False,
                        "error_code": "release_plan_supersession_failed",
                        "error": str(error),
                    }
            draft["refreshed_while_product_locked"] = True
            draft["refreshed_by"] = "Kyle"
            draft["locked_candidate_recovery"] = (
                "missing"
                if locked_missing_recovery
                else (
                    "unadopted"
                    if locked_unadopted_refresh
                    else "stale"
                )
            )
            draft["superseded_release_plan_id"] = superseded_plan_id or None
        next_state = dict(state)
        next_state["listing_copy"] = draft
        try:
            saved = np_mod.save_state(offer_id, next_state)
        except RuntimeError:
            latest = np_mod.load_state(offer_id)
            return 409, {
                "ok": False,
                "error": "state revision is stale",
                "current_revision": int(latest.get("_revision") or 0),
            }

    try:
        dashboard = release_control.build_release_dashboard(offer_id=offer_id)
    except Exception as error:
        return 500, {
            "ok": False,
            "error": f"title draft persisted but dashboard refresh failed: {error}",
            "current_revision": int(saved.get("_revision") or 0),
        }
    return 200, {
        "ok": True,
        "persisted": True,
        "revision": int(saved.get("_revision") or 0),
        "language_model_request_performed": True,
        "locked_stale_refresh": locked_stale_refresh,
        "locked_unadopted_refresh": locked_unadopted_refresh,
        "locked_missing_recovery": locked_missing_recovery,
        "superseded_release_plan_id": superseded_plan_id or None,
        "marketplace_writes_performed": [],
        "dashboard": _product_workspace_view(dashboard),
    }


def _server_canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_lower_sha256(value: object) -> bool:
    return bool(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_sha256(value: object) -> str:
    if type(value) is str and value.startswith("sha256:"):
        value = value[7:]
    if not _is_lower_sha256(value):
        raise ValueError("value is not a SHA-256 digest")
    return value


def _canonical_decimal_text(value: object) -> str:
    if isinstance(value, bool):
        raise ValueError("boolean is not a decimal")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("value is not a decimal") from error
    if not number.is_finite():
        raise ValueError("decimal must be finite")
    text = format(number.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def _positive_decimal_text(value: object) -> str:
    text = _canonical_decimal_text(value)
    if Decimal(text) <= 0:
        raise ValueError("decimal must be positive")
    return text


def _shopee_global_plan_seed(
    payload: dict,
    *,
    include_category_decision: bool = True,
) -> dict:
    """Derive every non-official candidate fact from server-owned state."""

    from shared_platform.target_scoped_release_contracts import (
        approved_shopee_copy_digest,
        approved_source_image_manifest_digest,
    )

    source_identity = payload.get("source_product_identity")
    sku_lineage = payload.get("sku_lineage")
    reservation = (
        sku_lineage.get("reservation")
        if isinstance(sku_lineage, dict)
        else None
    )
    if (
        not isinstance(source_identity, dict)
        or not isinstance(sku_lineage, dict)
        or not isinstance(reservation, dict)
    ):
        raise ValueError("Shopee global plan lineage evidence is unavailable")
    source_digest = _canonical_sha256(
        source_identity.get("identity_digest")
    )
    lineage_digest = _canonical_sha256(
        reservation.get("reservation_digest")
    )
    lineage_schema = reservation.get("schema_version")
    if type(lineage_schema) is not str or not lineage_schema:
        raise ValueError("Shopee global plan lineage schema is unavailable")

    listing_copy = payload.get("listing_copy")
    product_facts = payload.get("product_facts")
    if not isinstance(listing_copy, dict) or not isinstance(
        product_facts, dict
    ):
        raise ValueError("Shopee global plan copy facts are unavailable")
    title = product_facts.get("title")
    description = listing_copy.get("shopee_description_en")
    if (
        type(title) is not str
        or not title.strip()
        or type(description) is not str
        or not description.strip()
    ):
        raise ValueError("Shopee global description is unavailable")
    copy_digest = approved_shopee_copy_digest(title, description)

    raw_images = payload.get("images")
    if type(raw_images) is not list or not raw_images:
        raise ValueError("Shopee global plan requires approved images")
    source_images: list[dict] = []
    source_urls: list[str] = []
    for expected_position, row in enumerate(
        raw_images, start=1
    ):
        if (
            not isinstance(row, dict)
            or type(row.get("position")) is not int
            or row.get("position") != expected_position
            or type(row.get("image_url")) is not str
            or not row["image_url"].startswith("https://")
        ):
            raise ValueError("Shopee approved image sequence is invalid")
        source_urls.append(row["image_url"])
        source_images.append(
            {
                "source_url": row["image_url"],
                "source_image_digest": _server_canonical_digest(
                    {
                        "schema_version": "approved-content-image/v1",
                        "position": expected_position,
                        "artifact_id": row.get("artifact_id"),
                        "audit_id": row.get("audit_id"),
                        "asset_type": row.get("asset_type"),
                        "decision_source": row.get("decision_source"),
                    }
                ),
            }
        )
    if len(set(source_urls)) != len(source_urls):
        raise ValueError("Shopee approved image URLs must be unique")
    image_digests = [
        row["source_image_digest"] for row in source_images
    ]
    if len(set(image_digests)) != len(image_digests):
        raise ValueError("Shopee approved image identities must be unique")
    if len(source_images) > 9:
        raise ValueError(
            "Shopee global image selection requires explicit approval"
        )
    selected_image_positions = list(range(1, len(source_images) + 1))
    video_urls = payload.get("video_urls")
    if type(video_urls) is not list or any(
        type(value) is not str for value in video_urls
    ):
        raise ValueError("approved content video shape is invalid")
    image_manifest_digest = approved_source_image_manifest_digest(
        source_urls
    )
    content_package_digest = _server_canonical_digest(
        {
            "schema_version": "approved-content-package-binding/v1",
            "content_package_id": payload.get("content_package_id"),
            "content_approval_status": payload.get(
                "content_approval_status"
            ),
            "content_strategy": payload.get("content_strategy"),
            "images": source_images,
            "video_urls": video_urls,
        }
    )

    package = product_facts.get("package_cm")
    weight = product_facts.get("weight_kg")
    if (
        type(package) is not list
        or len(package) != 3
    ):
        raise ValueError("Shopee global parcel facts are unavailable")
    normalized_weight = _positive_decimal_text(weight)
    normalized_package = [
        _positive_decimal_text(value) for value in package
    ]
    parcel_contract_digest = _server_canonical_digest(
        {
            "schema_version": "approved-parcel-binding/v1",
            "weight_kg": normalized_weight,
            "package_cm": normalized_package,
            "product_fingerprint": payload.get("product_fingerprint"),
        }
    )
    parcel = {
        "weight_kg": normalized_weight,
        "length_cm": normalized_package[0],
        "width_cm": normalized_package[1],
        "height_cm": normalized_package[2],
        "contract_digest": parcel_contract_digest,
    }

    raw_targets = payload.get("targets")
    if type(raw_targets) is not list:
        raise ValueError("Shopee target identity is invalid")
    targets = [
        label
        for label in raw_targets
        if type(label) is str and label.startswith("shopee:")
    ]
    selected_pricing = (
        (payload.get("pricing") or {}).get("selected_targets")
        if isinstance(payload.get("pricing"), dict)
        else None
    )
    if not targets or not isinstance(selected_pricing, dict):
        raise ValueError("Shopee target pricing is unavailable")
    bound_target_pricing: dict[str, object] = {}
    for label in targets:
        row = selected_pricing.get(label)
        derived = row.get("derived_preview") if isinstance(row, dict) else None
        price = (
            derived.get("global_original_price_cny")
            if isinstance(derived, dict)
            else None
        )
        _positive_decimal_text(price)
        bound_target_pricing[label] = row
    master_source = (payload.get("pricing") or {}).get(
        "master_price_source"
    )
    if len(targets) == 1:
        master_target_label = targets[0]
    else:
        master_target_key = (
            master_source.get("target_key")
            if isinstance(master_source, dict)
            else None
        )
        if type(master_target_key) is not str or not master_target_key:
            raise ValueError(
                "Shopee global master price source is unavailable"
            )
        matching_targets = [
            label
            for label in targets
            if (
                isinstance(selected_pricing.get(label), dict)
                and isinstance(
                    selected_pricing[label].get("source"), dict
                )
                and selected_pricing[label]["source"].get("target_key")
                == master_target_key
            )
        ]
        if len(matching_targets) != 1:
            raise ValueError(
                "Shopee global master price source is not selected"
            )
        master_target_label = matching_targets[0]
    master_row = selected_pricing[master_target_label]
    master_derived = master_row.get("derived_preview")
    global_original_price = _positive_decimal_text(
        master_derived.get("global_original_price_cny")
        if isinstance(master_derived, dict)
        else None
    )
    pricing_digest = _server_canonical_digest(
        {
            "schema_version": "approved-shopee-target-pricing-binding/v2",
            "targets": bound_target_pricing,
            "master_price_source": master_source,
            "master_target_label": master_target_label,
            "global_original_price_cny": global_original_price,
        }
    )
    target_pricing = {
        "currency": "CNY",
        "global_original_price": global_original_price,
        "contract_digest": pricing_digest,
    }
    category_decision = (
        _category_decision_from_payload(payload)
        if include_category_decision
        else None
    )
    policy_payload = {
        "schema_version": _SHOPEE_GLOBAL_PLAN_POLICY_SCHEMA
    }
    if category_decision is not None:
        policy_payload["category_decision_digest"] = category_decision[
            "decision_digest"
        ]
    policy_digest = _server_canonical_digest(policy_payload)
    result = {
        "source_identity_schema_version": source_identity.get(
            "schema_version"
        ),
        "source_identity_digest": source_digest,
        "sku_lineage_schema_version": lineage_schema,
        "sku_lineage_digest": lineage_digest,
        "content_package_digest": content_package_digest,
        "title": title,
        "description": description,
        "approved_copy_digest": copy_digest,
        "ordered_approved_images": source_images,
        "approved_source_image_manifest_digest": image_manifest_digest,
        "selected_image_positions": selected_image_positions,
        "parcel": parcel,
        "target_pricing": target_pricing,
        "policy_digest": policy_digest,
        "targets": targets,
    }
    if category_decision is not None:
        from shared_platform.channel_category_decisions import (
            category_decision_execution_payload,
        )

        result["category_decision_execution"] = (
            category_decision_execution_payload(category_decision)
        )
    return result


def _category_decision_from_payload(
    payload: dict,
) -> dict | None:
    """Rehydrate the exact internal decision bound by its public digest."""

    from shared_platform.channel_category_decisions import (
        ChannelCategoryDecisionError,
        category_decision_plan_binding,
        rehydrate_category_decision,
    )

    bindings = payload.get("approved_channel_category_decisions")
    records = payload.get("_channel_category_decision_records")
    binding = (
        bindings.get("shopee:GLOBAL")
        if isinstance(bindings, dict)
        else None
    )
    record = (
        records.get("shopee:GLOBAL")
        if isinstance(records, dict)
        else None
    )
    if binding is None and record is None:
        return None
    if not isinstance(binding, dict) or type(record) is not str:
        raise ValueError("Shopee category decision binding is incomplete")
    try:
        decision = rehydrate_category_decision(record)
        if category_decision_plan_binding(decision) != binding:
            raise ChannelCategoryDecisionError(
                "category decision binding drifted"
            )
    except ChannelCategoryDecisionError as error:
        raise ValueError("Shopee category decision is invalid") from error
    return decision


def _channel_category_context(payload: dict) -> dict:
    """Derive the decision context only from current server-owned facts."""

    from shared_platform.channel_category_decisions import (
        OBSERVER_REQUEST_SCHEMA_VERSION,
    )

    seed = _shopee_global_plan_seed(payload)
    return {
        "schema_version": OBSERVER_REQUEST_SCHEMA_VERSION,
        "product_id": str(payload.get("product_id") or ""),
        "product_revision": payload.get("product_revision"),
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "source_identity_digest": seed["source_identity_digest"],
        "sku_lineage_digest": seed["sku_lineage_digest"],
        "approved_copy_digest": seed["approved_copy_digest"],
        "targets_digest": _server_canonical_digest(
            sorted(seed["targets"])
        ),
    }


def _channel_category_creation_seed(payload: dict) -> dict:
    """Derive one deterministic single-SKU create model from plan facts."""

    seed = _shopee_global_plan_seed(
        payload,
        include_category_decision=False,
    )
    lineage = payload.get("sku_lineage")
    assignment = (
        lineage.get("assignment")
        if isinstance(lineage, dict)
        else None
    )
    model_skus = (
        assignment.get("model_skus")
        if isinstance(assignment, dict)
        else None
    )
    if (
        type(model_skus) is not list
        or len(model_skus) != 1
        or not isinstance(model_skus[0], dict)
        or type(model_skus[0].get("model_sku")) is not str
        or not model_skus[0]["model_sku"].strip()
    ):
        raise ValueError("shopee_exact_variant_mapping_required")
    selected_positions = seed["selected_image_positions"]
    if type(selected_positions) is not list or not selected_positions:
        raise ValueError("shopee_approved_image_position_required")
    return {
        "schema_version": "channel-category-creation-seed/v1",
        "sku_lineage_digest": seed["sku_lineage_digest"],
        "model_sku": model_skus[0]["model_sku"],
        "selected_image_position": selected_positions[0],
        "global_original_price_cny": seed["target_pricing"][
            "global_original_price"
        ],
    }


_SHOPEE_GLOBAL_CANDIDATE_FIELDS = frozenset(
    {
        "mode",
        "observation_authority",
        "observation_schema_version",
        "observation_evidence_digest",
        "source_identity_schema_version",
        "source_identity_digest",
        "sku_lineage_schema_version",
        "sku_lineage_digest",
        "content_package_digest",
        "title",
        "description",
        "approved_copy_digest",
        "ordered_approved_images",
        "approved_source_image_manifest_digest",
        "selected_image_positions",
        "parcel",
        "target_pricing",
        "policy_digest",
        "category",
        "attributes",
        "attributes_complete",
        "attribute_tree_digest",
        "brand",
        "seller_stock",
        "location",
        "condition",
        "preorder",
        "variations",
        "variations_complete",
        "models",
        "existing_global_item_id",
        "existing_global_identity_evidence_digest",
    }
)


def _blocked_shopee_global_plan_candidate():
    from shared_platform.shopee_global_plan import (
        build_shopee_global_plan_candidate,
    )

    return build_shopee_global_plan_candidate(
        **{field: None for field in _SHOPEE_GLOBAL_CANDIDATE_FIELDS}
    )


def _shopee_global_plan_matches_local_payload(
    payload: dict,
    current: object,
) -> bool:
    """Verify approved/observed raw facts against local immutable plan facts."""

    if not isinstance(current, dict):
        return False
    try:
        expected = _shopee_global_plan_seed(
            payload,
            include_category_decision=(
                current.get("mode") == "NEW_GLOBAL"
            ),
        )
        expected_binding_subset = {
            "source_identity_schema_version": expected[
                "source_identity_schema_version"
            ],
            "source_identity_digest": expected["source_identity_digest"],
            "sku_lineage_schema_version": expected[
                "sku_lineage_schema_version"
            ],
            "sku_lineage_digest": expected["sku_lineage_digest"],
            "content_package_digest": expected["content_package_digest"],
            "approved_copy_digest": expected["approved_copy_digest"],
            "approved_source_image_manifest_digest": expected[
                "approved_source_image_manifest_digest"
            ],
            "parcel_contract_digest": expected["parcel"]["contract_digest"],
            "target_pricing_digest": expected["target_pricing"][
                "contract_digest"
            ],
            "policy_digest": expected["policy_digest"],
        }
        existing_snapshot = current.get("current_snapshot")
        is_existing_v2 = bool(
            current.get("mode") == "EXISTING_GLOBAL"
            and isinstance(existing_snapshot, dict)
        )
        expected_bindings = (
            {
                **expected_binding_subset,
                "attribute_tree_digest": current[
                    "attribute_tree_digest"
                ],
            }
            if not is_existing_v2
            else None
        )
        expected_copy = {
            "title": unicodedata.normalize("NFC", expected["title"].strip()),
            "description": expected["description"],
            "approved_copy_digest": expected["approved_copy_digest"],
        }
        expected_images = [
            {
                "source_url": row["source_url"],
                "source_image_digest": row["source_image_digest"],
            }
            for row in expected["ordered_approved_images"]
        ]
        assignment = (
            (payload.get("sku_lineage") or {}).get("assignment")
            if isinstance(payload.get("sku_lineage"), dict)
            else None
        )
        model_assignments = (
            assignment.get("model_skus")
            if isinstance(assignment, dict)
            else None
        )
        expected_model_skus = (
            sorted(
                row["model_sku"]
                for row in model_assignments
                if isinstance(row, dict)
                and type(row.get("model_sku")) is str
                and row["model_sku"]
            )
            if type(model_assignments) is list
            else []
        )
        observed_models = (
            existing_snapshot.get("global_model")
            if is_existing_v2
            else current.get("global_model")
        )
        observed_model_skus = (
            sorted(
                row["global_model_sku"]
                for row in observed_models
                if isinstance(row, dict)
                and type(row.get("global_model_sku")) is str
                and row["global_model_sku"]
            )
            if type(observed_models) is list
            else []
        )
        return bool(
            (
                isinstance(current.get("bindings"), dict)
                and all(
                    current.get("bindings", {}).get(key) == value
                    for key, value in expected_binding_subset.items()
                )
                if is_existing_v2
                else current.get("bindings") == expected_bindings
            )
            and current.get("copy") == expected_copy
            and current.get("approved_images") == expected_images
            and current.get("selected_image_positions")
            == expected["selected_image_positions"]
            and current.get("parcel")
            == {
                "weight_kg": _canonical_decimal_text(
                    expected["parcel"]["weight_kg"]
                ),
                "package_cm": {
                    "length": _canonical_decimal_text(
                        expected["parcel"]["length_cm"]
                    ),
                    "width": _canonical_decimal_text(
                        expected["parcel"]["width_cm"]
                    ),
                    "height": _canonical_decimal_text(
                        expected["parcel"]["height_cm"]
                    ),
                },
                "contract_digest": expected["parcel"]["contract_digest"],
            }
            and current.get("pricing")
            == {
                "currency": "CNY",
                "global_original_price": _canonical_decimal_text(
                    expected["target_pricing"]["global_original_price"]
                ),
                "target_pricing_digest": expected["target_pricing"][
                    "contract_digest"
                ],
            }
            and current.get("policy_digest") == expected["policy_digest"]
            and bool(expected_model_skus)
            and observed_model_skus == expected_model_skus
        )
    except (KeyError, TypeError, ValueError):
        return False


def _observe_shopee_global_plan_candidate(payload: dict):
    """Call the channel-owned read-only seam and verify server bindings."""

    import importlib

    from shared_platform.shopee_global_plan import (
        ShopeeGlobalPlanObservationError,
        ShopeeGlobalPlanCandidate,
        build_shopee_global_plan_candidate,
    )
    from shared_platform.channel_category_decisions import (
        decision_matches_global_plan,
    )

    try:
        seed = _shopee_global_plan_seed(payload)
    except (TypeError, ValueError):
        return _blocked_shopee_global_plan_candidate()
    request = {
        "schema_version": _SHOPEE_GLOBAL_PLAN_OBSERVER_REQUEST_SCHEMA,
        "offer_id": payload.get("product_id"),
        "product_revision": payload.get("product_revision"),
        "targets": list(seed["targets"]),
        "source_identity": payload.get("source_product_identity"),
        "sku_lineage": payload.get("sku_lineage"),
        "candidate_seed": {
            key: value for key, value in seed.items() if key != "targets"
        },
    }
    adapter_contract_error = None
    try:
        module = importlib.import_module(
            "domains.channel_operations.oneclick_release_adapters"
        )
        adapter_contract_error = getattr(
            module, "OneClickAdapterInputError", None
        )
        observer = getattr(
            module, "observe_shopee_global_plan_candidate", None
        )
        if not callable(observer):
            return _blocked_shopee_global_plan_candidate()
        observed = observer(request)
        if type(observed) is ShopeeGlobalPlanCandidate:
            candidate = observed
        elif isinstance(observed, dict) and set(observed) == set(
            _SHOPEE_GLOBAL_CANDIDATE_FIELDS
        ):
            candidate = build_shopee_global_plan_candidate(**observed)
        else:
            return _blocked_shopee_global_plan_candidate()
    except ShopeeGlobalPlanObservationError:
        raise
    except Exception as error:
        if (
            isinstance(adapter_contract_error, type)
            and isinstance(error, adapter_contract_error)
        ):
            raise ShopeeGlobalPlanObservationError(
                category="CAPABILITY",
                code="shopee_global_observer_contract_invalid",
            ) from error
        return _blocked_shopee_global_plan_candidate()
    if candidate.status != "READY" or candidate._plan is None:
        return candidate
    category_decision = _category_decision_from_payload(payload)
    candidate_payload = candidate._plan.payload()
    if candidate_payload.get("mode") == "NEW_GLOBAL":
        if category_decision is None:
            raise ShopeeGlobalPlanObservationError(
                category="CAPABILITY",
                code="shopee_category_decision_required",
            )
        if not decision_matches_global_plan(
            category_decision,
            candidate_payload,
        ):
            raise ShopeeGlobalPlanObservationError(
                category="CAPABILITY",
                code="shopee_category_decision_not_applied",
            )
    if not _shopee_global_plan_matches_local_payload(
        payload, candidate_payload
    ):
        return _blocked_shopee_global_plan_candidate()
    return candidate


def _release_plan_payload_from_dashboard(
    dashboard: dict,
    *,
    bind_shopee_global_plan: bool = False,
) -> tuple[dict, list[str]]:
    """Build the exact immutable V1 payload without persisting it.

    Shopee's marketplace-specific Global plan is intentionally not a
    prerequisite for the cross-channel ReleasePlan.  Callers that operate the
    isolated Shopee execution stage may opt in explicitly; ordinary approval,
    preparation, and execution must not let Shopee availability block every
    other selected marketplace.
    """
    from domains.content_operations import release_listing_copy_identity
    from domains.product_operations import resolve_source_product_identity

    product = dashboard.get("product") or {}
    content = dashboard.get("content") or {}
    scope = dashboard.get("publication_scope") or {}
    pricing = dashboard.get("pricing_review") or {}
    omnichannel = dashboard.get("omnichannel_preview") or {}
    actual_approval = product.get("actual_approval") or {}
    blockers: list[str] = []
    if not product.get("actual_product_approved"):
        blockers.append("商品事实尚未由 Kyle 批准并锁定")
    if not content.get("approved"):
        blockers.append("最终内容包尚未批准")
    targets = list(scope.get("selected_labels") or ())
    if not targets:
        blockers.append("尚未选择发布目标")
    if not omnichannel.get("available") or not omnichannel.get("plan_id"):
        blockers.extend(str(value) for value in (omnichannel.get("blockers") or ()))
    for target in omnichannel.get("targets") or ():
        for check in target.get("preflights") or ():
            if check.get("passed") or check.get("code") == "audited_adapter_site":
                continue
            blockers.append(str(check.get("detail") or "渠道依赖预检未通过"))
    if pricing.get("status") != "ready":
        blockers.extend(str(value) for value in (pricing.get("blockers") or ()))
    product_package_id = str(actual_approval.get("package_id") or "").strip()
    if not product_package_id:
        blockers.append("商品审批缺少 product package ID")
    content_package_id = str(content.get("package_id") or "").strip()
    if not content_package_id:
        blockers.append("内容审批缺少 content package ID")

    stored_source_resolution = dashboard.get("_source_product_identity")
    source_identity_inputs = dashboard.get("_source_identity_inputs")
    if (
        not isinstance(stored_source_resolution, dict)
        or not isinstance(source_identity_inputs, dict)
    ):
        blockers.append(
            "BLOCKED_SOURCE_IDENTITY: server-owned identity evidence is missing"
        )
        source_identity_payload = None
    else:
        source_identity_resolution = resolve_source_product_identity(
            collect_box=source_identity_inputs.get("collect_box"),
            precollect=source_identity_inputs.get("precollect"),
            source_record=source_identity_inputs.get("source_record"),
            source_authority=source_identity_inputs.get(
                "source_authority", "1688"
            ),
        )
        recomputed = source_identity_resolution.payload()
        public_source = dashboard.get("source_product_identity")
        public_digest = (
            public_source.get("identity_digest")
            if isinstance(public_source, dict)
            else None
        )
        if (
            recomputed != stored_source_resolution
            or not source_identity_resolution.ready
            or source_identity_resolution.identity is None
            or public_digest
            != source_identity_resolution.identity.identity_digest
        ):
            blockers.extend(
                source_identity_resolution.blockers
                or (
                    "BLOCKED_SOURCE_IDENTITY: identity evidence is stale or malformed",
                )
            )
            source_identity_payload = None
        else:
            source_identity_payload = (
                source_identity_resolution.identity.payload()
            )
    sku_lineage_payload = dashboard.get("_sku_lineage")
    public_sku_lineage = dashboard.get("sku_lineage")
    if (
        not isinstance(sku_lineage_payload, dict)
        or sku_lineage_payload.get("schema_version")
        != "sku-lineage-reservation/v1"
        or sku_lineage_payload.get("status") != "READY"
        or sku_lineage_payload.get("ready") is not True
        or not isinstance(public_sku_lineage, dict)
        or public_sku_lineage.get("lineage_mode")
        != sku_lineage_payload.get("lineage_mode")
        or public_sku_lineage.get("assignment")
        != sku_lineage_payload.get("assignment")
    ):
        blockers.extend(
            list(
                (sku_lineage_payload or {}).get("blockers")
                if isinstance(sku_lineage_payload, dict)
                else ()
            )
            or ["BLOCKED_SKU_LINEAGE: lineage evidence is missing or stale"]
        )
        sku_lineage_payload = None
    elif (
        sku_lineage_payload.get("lineage_mode")
        == "INHERITED_PREDECESSOR"
        and (
            not isinstance(sku_lineage_payload.get("assignment"), dict)
            or sku_lineage_payload["assignment"].get("seller_sku")
            != str(product.get("seller_sku_candidate") or "")
            or not isinstance(sku_lineage_payload.get("reservation"), dict)
        )
    ):
        blockers.append(
            "BLOCKED_SKU_LINEAGE: inherited assignment/reservation drifted"
        )
        sku_lineage_payload = None

    target_pricing = pricing.get("target_pricing") or {}
    selected_target_pricing = {
        label: dict(target_pricing.get(label) or {})
        for label in targets
    }
    images = [
        {
            "position": row.get("position"),
            "image_url": row.get("image_url"),
            "artifact_id": row.get("artifact_id"),
            "audit_id": row.get("audit_id"),
            "asset_type": row.get("asset_type"),
            "decision_source": row.get("decision_source"),
        }
        for row in (content.get("images") or ())
    ]
    plan_id = str(omnichannel.get("plan_id") or "").strip()
    listing_copy_source = (
        dashboard.get("listing_copy")
        if isinstance(dashboard.get("listing_copy"), dict)
        else {}
    )
    listing_copy_identity, listing_copy_blockers = release_listing_copy_identity(
        listing_copy_source,
        approved_product_title=product.get("title"),
        current_input_signature=listing_copy_source.get(
            "current_input_signature"
        ),
        target_labels=targets,
    )
    blockers.extend(listing_copy_blockers)
    shopee_description = str(
        listing_copy_source.get("shopee_description_en") or ""
    ).strip()
    payload = {
        "plan_id": plan_id,
        "product_id": str(product.get("offer_id") or "").strip(),
        "seller_sku": str(product.get("seller_sku_candidate") or "").strip(),
        "product_package_id": product_package_id,
        "content_package_id": content_package_id,
        "source_product_identity": source_identity_payload,
        "sku_lineage": sku_lineage_payload,
        "targets": targets,
        "product_revision": int(product.get("revision") or 0),
        "product_approval_id": str(actual_approval.get("approval_id") or ""),
        "product_fingerprint": str(actual_approval.get("input_fingerprint") or ""),
        "content_approval_status": str(content.get("approval_status") or ""),
        "content_strategy": str(content.get("strategy") or ""),
        "product_facts": {
            "title": product.get("title"),
            "source_title_zh": str(product.get("source_title_zh") or ""),
            "category": dict(product.get("category") or {}),
            "cost_cny": product.get("cost_cny"),
            "weight_kg": product.get("weight_kg"),
            "package_cm": list(product.get("package_cm") or ()),
            "selected_sku_keys": list(product.get("selected_sku_keys") or ()),
            "sku_label_overrides": dict(
                product.get("sku_label_overrides") or {}
            ),
            "selected_skus": [
                {
                    "key": str(row.get("key") or ""),
                    "label": str(row.get("label") or ""),
                    "price_cny": row.get("price_cny"),
                    "model_sku": row.get("model_sku"),
                }
                for row in (product.get("source_skus") or ())
                if isinstance(row, dict)
                and str(row.get("key") or "")
                in set(product.get("selected_sku_keys") or ())
            ],
        },
        "listing_copy": {
            **listing_copy_identity,
            "shopee_description_en": shopee_description,
        },
        "images": images,
        "video_urls": list(content.get("video_urls") or ()),
        "pricing": {
            "schema_version": pricing.get("schema_version"),
            "selected_targets": selected_target_pricing,
            "master_price_source": dict(
                pricing.get("master_price_source") or {}
            ),
            "workbench_exchange_rates": dict(
                pricing.get("workbench_exchange_rates") or {}
            ),
            "shopee_exchange_rates": dict(
                pricing.get("shopee_exchange_rates") or {}
            ),
            "ozon_exchange_rates": dict(
                pricing.get("ozon_exchange_rates") or {}
            ),
        },
        "omnichannel_scope_digest": (
            (omnichannel.get("approval_summary") or {}).get(
                "approval_scope_digest"
            )
        ),
    }
    tiktok_targets = tuple(
        label
        for label in targets
        if type(label) is str and label.startswith("tiktok:")
    )
    if tiktok_targets:
        from modules.miaoshou.oneclick_release import (
            approved_tiktok_category_decisions,
        )

        tiktok_category_decisions = approved_tiktok_category_decisions(
            payload["product_facts"].get("category"),
            targets=tiktok_targets,
        )
        if tiktok_category_decisions is None:
            blockers.append(
                "BLOCKED_TIKTOK_CATEGORY: approved product category has no mapping"
            )
        else:
            payload["approved_tiktok_category_decisions"] = (
                tiktok_category_decisions
            )
    has_shopee_target = any(
        type(label) is str and label.startswith("shopee:")
        for label in targets
    )
    if has_shopee_target and not blockers:
        from shared_platform.channel_category_decisions import (
            ChannelCategoryDecisionError,
            category_decision_plan_binding,
        )
        from shared_platform.release_store import (
            ImmutableReleaseError,
            default_release_store,
        )

        try:
            store = default_release_store()
            stored_category = store.channel_category_decision(
                product_id=payload["product_id"],
                product_revision=payload["product_revision"],
                channel="shopee",
                mode="NEW_GLOBAL",
            )
            if stored_category is not None:
                category_context = _channel_category_context(payload)
                expected_context_digest = _server_canonical_digest(
                    category_context
                )
                if (
                    stored_category["decision"]["context_digest"]
                    != expected_context_digest
                ):
                    raise ChannelCategoryDecisionError(
                        "stored category context drifted"
                    )
                decision = stored_category["decision"]
                payload["approved_channel_category_decisions"] = {
                    "shopee:GLOBAL": category_decision_plan_binding(
                        decision
                    )
                }
                payload["_channel_category_decision_records"] = {
                    "shopee:GLOBAL": stored_category["record_json"]
                }
        except (
            ChannelCategoryDecisionError,
            ImmutableReleaseError,
            TypeError,
            ValueError,
        ):
            blockers.append(
                "select_channel_category: stored Shopee category "
                "decision is invalid or stale"
            )
    if any(
        label
        in {
            "tiktok:LH_PH",
            "tiktok:LH_MY",
            "tiktok:LH_TH",
            "tiktok:LH_VN",
            "shopee:PH",
            "shopee:MY",
            "shopee:TH",
            "shopee:VN",
        }
        for label in targets
    ):
        from shared_platform.postpublish_promotions import (
            build_approved_postpublish_promotion_policy,
        )

        # Kyle approved this server-owned, versioned policy on 2026-07-30.
        # It is derived here for every *new* immutable plan.  Existing stored
        # plans are never rewritten and browser/dashboard fields cannot
        # inject, remove, or override it.
        payload["approved_postpublish_promotion_policy"] = (
            build_approved_postpublish_promotion_policy(
                approval_reference=(
                    "Kyle-20260730-existing-ongoing-direct-discount"
                ),
            )
        )
    if (
        bind_shopee_global_plan
        and has_shopee_target
        and not blockers
    ):
        from shared_platform.release_store import (
            ImmutableReleaseError,
            default_release_store,
        )
        from shared_platform.shopee_global_plan import (
            ShopeeGlobalPlanContractError,
        )

        try:
            stored = default_release_store().shopee_global_plan_approval(
                product_id=payload["product_id"],
                product_revision=payload["product_revision"],
            )
            if not stored:
                raise ShopeeGlobalPlanContractError(
                    "current approved Shopee global plan was not found"
                )
            approved = stored["approved"]
            raw_plan = approved._plan.payload()
            if not _shopee_global_plan_matches_local_payload(
                payload, raw_plan
            ):
                raise ShopeeGlobalPlanContractError(
                    "approved Shopee global plan drifted from local facts"
                )
            compact = {
                "schema_version": approved.schema_version,
                "mode": approved.mode,
                "candidate_digest": approved.candidate_digest,
                "approved_plan_digest": approved.approved_plan_digest,
                "selected_image_positions": list(
                    raw_plan["selected_image_positions"]
                ),
                "selected_source_image_manifest_digest": raw_plan[
                    "selected_source_image_manifest_digest"
                ],
                "record_digest": stored["record_digest"],
            }
            if (
                set(compact)
                != {
                    "schema_version",
                    "mode",
                    "candidate_digest",
                    "approved_plan_digest",
                    "selected_image_positions",
                    "selected_source_image_manifest_digest",
                    "record_digest",
                }
                or any(
                    not _is_lower_sha256(compact[field])
                    for field in (
                        "candidate_digest",
                        "approved_plan_digest",
                        "selected_source_image_manifest_digest",
                        "record_digest",
                    )
                )
            ):
                raise ShopeeGlobalPlanContractError(
                    "approved Shopee global plan projection is invalid"
                )
            payload["approved_shopee_global_plan"] = compact
            payload["_approved_shopee_global_plan_record"] = stored[
                "record_json"
            ]
        except (
            ImmutableReleaseError,
            ShopeeGlobalPlanContractError,
            TypeError,
            ValueError,
        ):
            blockers.append(
                "review_shopee_global_plan: current exact Shopee global "
                "plan approval is required"
            )
    return payload, list(dict.fromkeys(value for value in blockers if value))


def _observe_channel_category_options(
    payload: dict,
    *,
    attribute_selection: dict | None = None,
) -> dict:
    """Call the optional channel-owned read-only observer and validate it."""

    import importlib

    from shared_platform.channel_category_decisions import (
        ChannelCategoryDecisionError,
        blocked_category_options,
        build_category_options,
        attribute_selection_execution_payload,
        category_decision_execution_payload,
    )

    context = _channel_category_context(payload)
    seed = _shopee_global_plan_seed(payload)
    decision = _category_decision_from_payload(payload)
    try:
        creation_seed = _channel_category_creation_seed(payload)
    except ValueError as error:
        code = str(error)
        if code == "shopee_exact_variant_mapping_required":
            return blocked_category_options(
                context=context,
                reason_code=code,
                reason_category="CONTENT",
                next_action="complete_shopee_variant_mapping",
            )
        return blocked_category_options(
            context=context,
            reason_code=code,
            reason_category="CONTENT",
            next_action="review_approved_content_facts",
        )
    request = {
        "schema_version": "channel-category-observer-request/v2",
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "context": context,
        "approved_title": seed["title"],
        "approved_title_digest": hashlib.sha256(
            unicodedata.normalize("NFC", seed["title"].strip()).encode(
                "utf-8"
            )
        ).hexdigest(),
        "current_selection": (
            category_decision_execution_payload(decision)
            if decision is not None
            else None
        ),
        "current_attribute_selection": (
            attribute_selection_execution_payload(
                attribute_selection
            )
            if attribute_selection is not None
            else None
        ),
    }
    try:
        module = importlib.import_module(
            "domains.channel_operations.oneclick_release_adapters"
        )
        observer = getattr(
            module, "observe_channel_category_options", None
        )
        if not callable(observer):
            return blocked_category_options(
                context=context,
                reason_code=(
                    "official_channel_category_observer_unavailable"
                ),
            )
        return build_category_options(
            observer(request),
            context=context,
            creation_seed=creation_seed,
        )
    except ChannelCategoryDecisionError:
        return blocked_category_options(
            context=context,
            reason_code="official_channel_category_shape_invalid",
        )
    except Exception:
        return blocked_category_options(
            context=context,
            reason_code="official_channel_category_read_failed",
        )


def _channel_category_preview_projection(
    *,
    payload: dict,
    snapshot: dict,
    attribute_selection: dict | None = None,
) -> dict:
    from shared_platform.channel_category_decisions import (
        OPTIONS_SCHEMA_VERSION,
        PREVIEW_SCHEMA_VERSION,
        public_options_projection,
    )
    from shared_platform.release_store import default_release_store

    attribute_summary = (
        {
            "selection_digest": attribute_selection[
                "selection_digest"
            ],
            "category_identity_digest": attribute_selection[
                "category_identity_digest"
            ],
            "attribute_tree_digest": attribute_selection[
                "attribute_tree_digest"
            ],
            "selection_count": attribute_selection[
                "selection_count"
            ],
            "approved_by": "Kyle",
        }
        if attribute_selection is not None
        else None
    )
    if snapshot.get("schema_version") != OPTIONS_SCHEMA_VERSION or (
        snapshot.get("status") == "BLOCKED_CAPABILITY"
    ):
        if attribute_summary is not None:
            return {
                "ok": True,
                "schema_version": PREVIEW_SCHEMA_VERSION,
                "offer_id": payload["product_id"],
                "product_revision": payload["product_revision"],
                "target_label": "shopee:GLOBAL",
                "mode": "NEW_GLOBAL",
                "status": "RECHECK_REQUIRED",
                "options_digest": attribute_selection[
                    "options_digest"
                ],
                "recommendation": None,
                "options": [],
                "brand_options": [],
                "location_options": [],
                "creation_fact_option": None,
                "selection": None,
                "attribute_selection": attribute_summary,
                "blocker": {
                    "category": "CAPABILITY",
                    "code": (
                        "official_category_attribute_recheck_required"
                    ),
                },
                "next_action": {
                    "action": "recheck_channel_category_attributes",
                    "target_focus": "shopee:GLOBAL",
                },
                "external_writes_performed": [],
            }
        return {
            "ok": True,
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "offer_id": payload["product_id"],
            "product_revision": payload["product_revision"],
            "target_label": "shopee:GLOBAL",
            "mode": "NEW_GLOBAL",
            "status": "BLOCKED_CAPABILITY",
            "options_digest": None,
            "recommendation": None,
            "options": [],
            "brand_options": [],
            "location_options": [],
            "creation_fact_option": None,
            "selection": None,
            "attribute_selection": None,
            "blocker": dict(
                snapshot.get("reason")
                or {
                    "category": "CAPABILITY",
                    "code": "official_channel_category_unavailable",
                }
            ),
            "next_action": dict(
                snapshot.get("next_action")
                or {
                    "action": "wait_for_channel_capability",
                    "target_focus": "shopee:GLOBAL",
                }
            ),
            "external_writes_performed": [],
        }
    current = default_release_store().channel_category_decision(
        product_id=payload["product_id"],
        product_revision=payload["product_revision"],
        channel="shopee",
        mode="NEW_GLOBAL",
        context_digest=snapshot["context_digest"],
    )
    projection = public_options_projection(
        snapshot,
        decision=(current or {}).get("decision"),
    )
    if attribute_summary is not None and projection["selection"] is None:
        projection = {
            **projection,
            "status": "RECHECK_REQUIRED",
            "attribute_selection": attribute_summary,
            "blocker": {
                "category": "CAPABILITY",
                "code": "official_category_attribute_recheck_required",
            },
            "next_action": {
                "action": "recheck_channel_category_attributes",
                "target_focus": "shopee:GLOBAL",
            },
        }
    return {
        "ok": True,
        **projection,
        "attribute_selection": (
            projection.get("attribute_selection")
            or attribute_summary
        ),
        "offer_id": payload["product_id"],
        "product_revision": payload["product_revision"],
        "external_writes_performed": [],
    }


def _active_channel_category_attribute_selection(
    payload: dict,
) -> dict | None:
    from shared_platform.channel_category_decisions import digest_json
    from shared_platform.release_store import default_release_store

    context = _channel_category_context(payload)
    stored = (
        default_release_store()
        .channel_category_attribute_selection(
            product_id=payload["product_id"],
            product_revision=payload["product_revision"],
            channel="shopee",
            mode="NEW_GLOBAL",
            context_digest=digest_json(context),
        )
    )
    return (stored or {}).get("selection")


def _finalize_channel_category_decision(
    *,
    payload: dict,
    snapshot: dict,
    attribute_selection: dict,
) -> dict:
    from shared_platform.channel_category_decisions import (
        approve_category_decision,
        serialize_category_decision,
    )
    from shared_platform.release_store import default_release_store

    decision = approve_category_decision(
        snapshot,
        product_id=payload["product_id"],
        product_revision=payload["product_revision"],
        selected_category_identity_digest=attribute_selection[
            "category_identity_digest"
        ],
        selected_brand_identity_digest=attribute_selection[
            "selected_brand_identity_digest"
        ],
        selected_location_identity_digest=attribute_selection[
            "selected_location_identity_digest"
        ],
        selected_creation_fact_identity_digest=attribute_selection[
            "selected_creation_fact_identity_digest"
        ],
        attribute_selection_digest=attribute_selection[
            "selection_digest"
        ],
        approved_by="Kyle",
        confirm_channel_category_selection=True,
        confirm_seller_stock_quantity=True,
        confirm_condition_and_preorder=True,
    )
    return default_release_store().persist_channel_category_decision(
        serialize_category_decision(decision)
    )


def _preview_channel_category_decision(
    *,
    offer_id: object,
    target_label: object,
) -> tuple[int, dict]:
    """Return official alternatives and the current explicit local selection."""

    from shared_platform import release_control

    clean_offer_id = str(offer_id or "").strip()
    if (
        type(offer_id) is not str
        or not clean_offer_id.isascii()
        or not clean_offer_id.isdigit()
        or not 1 <= len(clean_offer_id) <= 32
        or target_label != "shopee:GLOBAL"
    ):
        return 400, {
            "ok": False,
            "error": (
                "offer_id and target_label=shopee:GLOBAL are required"
            ),
            "external_writes_performed": [],
        }
    try:
        dashboard = release_control.build_release_dashboard(
            offer_id=clean_offer_id
        )
        payload, blockers = _release_plan_payload_from_dashboard(
            dashboard,
            bind_shopee_global_plan=False,
        )
        if not any(
            type(label) is str and label.startswith("shopee:")
            for label in payload.get("targets") or ()
        ):
            raise ValueError("current release scope has no Shopee target")
        if blockers:
            raise ValueError(blockers[0])
        # A finalized decision and its earlier attribute-intent row may both
        # remain append-only in the store.  They are alternative observer
        # inputs: once the exact decision exists, revalidate that decision
        # alone.  Passing both makes the strict channel observer reject an
        # otherwise valid saved decision and traps the UI in RECHECK_REQUIRED.
        current_decision = _category_decision_from_payload(payload)
        attribute_selection = (
            None
            if current_decision is not None
            else _active_channel_category_attribute_selection(payload)
        )
        snapshot = _observe_channel_category_options(
            payload,
            attribute_selection=attribute_selection,
        )
        if attribute_selection is not None:
            from shared_platform.channel_category_decisions import (
                attribute_selection_matches_options,
            )

            if attribute_selection_matches_options(
                snapshot,
                attribute_selection,
            ):
                _finalize_channel_category_decision(
                    payload=payload,
                    snapshot=snapshot,
                    attribute_selection=attribute_selection,
                )
        return 200, _channel_category_preview_projection(
            payload=payload,
            snapshot=snapshot,
            attribute_selection=attribute_selection,
        )
    except FileNotFoundError as error:
        return 404, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }
    except (TypeError, ValueError) as error:
        return 409, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }


def _approve_channel_category_decision_locally(
    data: dict,
) -> tuple[int, dict]:
    """Persist one exact offered selection; never call a marketplace write."""

    from shared_platform import release_control
    from shared_platform.channel_category_decisions import (
        ChannelCategoryDecisionError,
        attribute_selection_matches_options,
        digest_json,
        resolve_required_attribute_selections,
        serialize_attribute_selection,
    )
    from shared_platform.release_store import (
        ImmutableReleaseError,
        ReleaseStoreError,
        default_release_store,
    )

    expected_fields = {
        "offer_id",
        "target_label",
        "expected_product_revision",
        "expected_options_digest",
        "selected_category_identity_digest",
        "selected_brand_identity_digest",
        "selected_location_identity_digest",
        "selected_creation_fact_identity_digest",
        "approved_by",
        "confirm_channel_category_selection",
        "confirm_seller_stock_quantity",
        "confirm_condition_and_preorder",
        "required_attribute_selections",
        "confirm_required_attribute_selections",
    }
    if set(data) != expected_fields:
        return 400, {
            "ok": False,
            "error": "channel category selection fields are invalid",
            "external_writes_performed": [],
        }
    if (
        type(data["offer_id"]) is not str
        or not data["offer_id"].isascii()
        or not data["offer_id"].isdigit()
        or not 1 <= len(data["offer_id"]) <= 32
        or data["target_label"] != "shopee:GLOBAL"
        or type(data["expected_product_revision"]) is not int
        or data["expected_product_revision"] < 0
        or data["approved_by"] != "Kyle"
        or data["confirm_channel_category_selection"] is not True
        or data["confirm_seller_stock_quantity"] is not True
        or data["confirm_condition_and_preorder"] is not True
        or data["confirm_required_attribute_selections"] is not True
        or type(data["required_attribute_selections"]) is not list
    ):
        return 400, {
            "ok": False,
            "error": "channel category selection identity is invalid",
            "external_writes_performed": [],
        }
    try:
        dashboard = release_control.build_release_dashboard(
            offer_id=data["offer_id"]
        )
        payload, blockers = _release_plan_payload_from_dashboard(
            dashboard,
            bind_shopee_global_plan=False,
        )
        if blockers:
            raise ChannelCategoryDecisionError(blockers[0])
        if (
            payload["product_revision"]
            != data["expected_product_revision"]
        ):
            raise ChannelCategoryDecisionError(
                "product revision changed; refresh category options"
            )
        approval_request_digest = digest_json(data)
        active_attribute_selection = (
            _active_channel_category_attribute_selection(payload)
        )
        if (
            active_attribute_selection is not None
            and active_attribute_selection[
                "approval_request_digest"
            ]
            == approval_request_digest
        ):
            rechecked = _observe_channel_category_options(
                payload,
                attribute_selection=active_attribute_selection,
            )
            if attribute_selection_matches_options(
                rechecked,
                active_attribute_selection,
            ):
                _finalize_channel_category_decision(
                    payload=payload,
                    snapshot=rechecked,
                    attribute_selection=active_attribute_selection,
                )
            projection = _channel_category_preview_projection(
                payload=payload,
                snapshot=rechecked,
                attribute_selection=active_attribute_selection,
            )
            return 200, {
                **projection,
                "persisted": True,
                "created": False,
            }
        snapshot = _observe_channel_category_options(payload)
        if snapshot.get("status") == "BLOCKED_CAPABILITY":
            raise ChannelCategoryDecisionError(
                (snapshot.get("reason") or {}).get("code")
                or "official category options are unavailable"
            )
        if (
            type(data["expected_options_digest"]) is not str
            or data["expected_options_digest"]
            != snapshot["options_digest"]
        ):
            raise ChannelCategoryDecisionError(
                "category options changed; refresh before selection"
            )
        attribute_selection = resolve_required_attribute_selections(
            snapshot,
            selected_category_identity_digest=data[
                "selected_category_identity_digest"
            ],
            selected_brand_identity_digest=data[
                "selected_brand_identity_digest"
            ],
            selected_location_identity_digest=data[
                "selected_location_identity_digest"
            ],
            selected_creation_fact_identity_digest=data[
                "selected_creation_fact_identity_digest"
            ],
            required_attribute_selections=data[
                "required_attribute_selections"
            ],
            approval_request_digest=approval_request_digest,
            approved_by="Kyle",
            confirm_channel_category_selection=True,
            confirm_seller_stock_quantity=True,
            confirm_condition_and_preorder=True,
            confirm_required_attribute_selections=True,
        )
        draft = (
            default_release_store()
            .persist_channel_category_attribute_selection(
                serialize_attribute_selection(attribute_selection)
            )
        )
        rechecked = _observe_channel_category_options(
            payload,
            attribute_selection=attribute_selection,
        )
        if not attribute_selection_matches_options(
            rechecked,
            attribute_selection,
        ):
            projection = _channel_category_preview_projection(
                payload=payload,
                snapshot=rechecked,
                attribute_selection=attribute_selection,
            )
            return 200, {
                **projection,
                "persisted": True,
                "created": draft["created"],
            }
        stored = _finalize_channel_category_decision(
            payload=payload,
            snapshot=rechecked,
            attribute_selection=attribute_selection,
        )
        projection = _channel_category_preview_projection(
            payload=payload,
            snapshot=rechecked,
            attribute_selection=attribute_selection,
        )
    except FileNotFoundError as error:
        return 404, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }
    except (
        ChannelCategoryDecisionError,
        ImmutableReleaseError,
        ReleaseStoreError,
        TypeError,
        ValueError,
    ) as error:
        return 409, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }
    return 200, {
        **projection,
        "persisted": True,
        "created": stored["created"],
    }


def _shopee_global_plan_preview_for_dashboard(
    dashboard: dict,
) -> tuple[dict, dict, object, dict | None]:
    """Return the current base payload, candidate, and exact approval."""

    from shared_platform.release_store import (
        ImmutableReleaseError,
        default_release_store,
    )
    from shared_platform.shopee_global_plan import (
        ShopeeGlobalPlanContractError,
        ShopeeGlobalPlanObservationError,
        validate_approved_shopee_global_plan,
    )

    payload, blockers = _release_plan_payload_from_dashboard(
        dashboard,
        bind_shopee_global_plan=False,
    )
    if not any(
        type(label) is str and label.startswith("shopee:")
        for label in (payload.get("targets") or ())
    ):
        raise ValueError("current release scope has no Shopee target")
    store = default_release_store()
    try:
        candidate = _observe_shopee_global_plan_candidate(payload)
    except ShopeeGlobalPlanObservationError as failure:
        latest = None
        try:
            latest = store.shopee_global_plan_approval(
                product_id=payload["product_id"],
            )
        except (ImmutableReleaseError, TypeError, ValueError):
            pass
        return payload, {"blockers": blockers}, failure, latest
    current: dict | None = None
    if candidate.status == "READY" and not blockers:
        try:
            current = store.shopee_global_plan_approval(
                product_id=payload["product_id"],
                product_revision=payload["product_revision"],
                candidate_digest=candidate.candidate_digest,
            )
            if current:
                validate_approved_shopee_global_plan(
                    current["approved"], candidate
                )
        except (
            ImmutableReleaseError,
            ShopeeGlobalPlanContractError,
            TypeError,
            ValueError,
        ):
            current = None
    latest = None
    try:
        latest = store.shopee_global_plan_approval(
            product_id=payload["product_id"],
        )
    except (ImmutableReleaseError, TypeError, ValueError):
        latest = None
    return payload, {"blockers": blockers}, candidate, current or latest


def _preview_shopee_global_plan(
    offer_id: object,
) -> tuple[int, dict]:
    """Build one pure/read-only redacted Shopee global plan preview."""

    from shared_platform import release_control
    from shared_platform.shopee_global_plan import (
        ShopeeGlobalPlanContractError,
        ShopeeGlobalPlanObservationError,
        validate_approved_shopee_global_plan,
    )

    clean_offer_id = str(offer_id or "").strip()
    if not clean_offer_id.isdigit() or not 1 <= len(clean_offer_id) <= 32:
        return 400, {
            "ok": False,
            "error": "offer_id must contain 1-32 digits",
            "external_writes_performed": [],
        }
    try:
        dashboard = release_control.build_release_dashboard(
            offer_id=clean_offer_id,
        )
        payload, state, candidate, approval_row = (
            _shopee_global_plan_preview_for_dashboard(dashboard)
        )
    except FileNotFoundError as error:
        return 404, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }
    except (TypeError, ValueError) as error:
        return 409, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }
    approval = approval_row["approved"] if approval_row else None
    if isinstance(candidate, ShopeeGlobalPlanObservationError):
        return 200, {
            "ok": True,
            "schema_version": _SHOPEE_GLOBAL_PLAN_PREVIEW_SCHEMA,
            "offer_id": payload["product_id"],
            "product_revision": payload["product_revision"],
            "candidate": candidate.public_projection(),
            "approval": (
                approval.public_projection() if approval is not None else None
            ),
            "approval_current": False,
            "external_writes_performed": [],
        }
    approval_current = False
    if approval is not None and not state["blockers"]:
        try:
            validate_approved_shopee_global_plan(approval, candidate)
            approval_current = bool(
                approval_row["product_revision"]
                == payload["product_revision"]
            )
        except ShopeeGlobalPlanContractError:
            approval_current = False
    return 200, {
        "ok": True,
        "schema_version": _SHOPEE_GLOBAL_PLAN_PREVIEW_SCHEMA,
        "offer_id": payload["product_id"],
        "product_revision": payload["product_revision"],
        "candidate": candidate.public_projection(),
        "approval": (
            approval.public_projection() if approval is not None else None
        ),
        "approval_current": approval_current,
        "external_writes_performed": [],
    }


def _approve_shopee_global_plan_locally(
    data: dict,
) -> tuple[int, dict]:
    """Persist Kyle's current candidate approval without marketplace writes."""

    from shared_platform import release_control
    from shared_platform.release_store import (
        ImmutableReleaseError,
        ReleaseStoreError,
        default_release_store,
    )
    from shared_platform.shopee_global_plan import (
        ShopeeGlobalPlanApprovalError,
        ShopeeGlobalPlanContractError,
        ShopeeGlobalPlanObservationError,
        approve_shopee_global_plan,
        serialize_approved_shopee_global_plan,
    )

    expected_fields = {
        "offer_id",
        "expected_product_revision",
        "expected_candidate_digest",
        "approved_by",
        "confirm_approved_shopee_global_plan",
    }
    if set(data) != expected_fields:
        return 400, {
            "ok": False,
            "error": "Shopee global plan approval fields are invalid",
            "external_writes_performed": [],
        }
    clean_offer_id = data.get("offer_id")
    if (
        type(clean_offer_id) is not str
        or not clean_offer_id.isdigit()
        or not 1 <= len(clean_offer_id) <= 32
    ):
        return 400, {
            "ok": False,
            "error": "offer_id must contain 1-32 digits",
            "external_writes_performed": [],
        }
    expected_revision = data.get("expected_product_revision")
    if type(expected_revision) is not int or expected_revision < 0:
        return 400, {
            "ok": False,
            "error": "expected_product_revision must be a non-negative int",
            "external_writes_performed": [],
        }
    if data.get("approved_by") != "Kyle":
        return 400, {
            "ok": False,
            "error": "approved_by must be Kyle",
            "external_writes_performed": [],
        }
    if data.get("confirm_approved_shopee_global_plan") is not True:
        return 400, {
            "ok": False,
            "error": (
                "literal confirm_approved_shopee_global_plan=true is required"
            ),
            "external_writes_performed": [],
        }
    try:
        dashboard = release_control.build_release_dashboard(
            offer_id=clean_offer_id,
        )
        payload, state, candidate, _approval_row = (
            _shopee_global_plan_preview_for_dashboard(dashboard)
        )
    except FileNotFoundError as error:
        return 404, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }
    except (TypeError, ValueError) as error:
        return 409, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }
    if expected_revision != payload["product_revision"]:
        return 409, {
            "ok": False,
            "error": "product revision changed; refresh the candidate",
            "external_writes_performed": [],
        }
    if isinstance(candidate, ShopeeGlobalPlanObservationError):
        return 409, {
            "ok": False,
            "error": candidate.code,
            "reason": {
                "category": candidate.category,
                "code": candidate.code,
            },
            "canonical_next_action": {
                "action": (
                    "restore_channel_authorization"
                    if candidate.category == "AUTH"
                    else "wait_for_channel_capability"
                ),
                "target_focus": "shopee:GLOBAL",
            },
            "external_writes_performed": [],
        }
    if state["blockers"]:
        return 409, {
            "ok": False,
            "error": state["blockers"][0],
            "blockers": state["blockers"],
            "external_writes_performed": [],
        }
    if (
        type(data.get("expected_candidate_digest")) is not str
        or data["expected_candidate_digest"] != candidate.candidate_digest
    ):
        return 409, {
            "ok": False,
            "error": "Shopee global plan candidate changed; refresh first",
            "external_writes_performed": [],
        }
    try:
        approved = approve_shopee_global_plan(
            candidate,
            approved_by="Kyle",
            confirm_approved_shopee_global_plan=True,
            expected_candidate_digest=data["expected_candidate_digest"],
        )
        serialized = serialize_approved_shopee_global_plan(approved)
        seed = _shopee_global_plan_seed(payload)
        stored = default_release_store().persist_shopee_global_plan_approval(
            product_id=payload["product_id"],
            product_revision=payload["product_revision"],
            source_identity_digest=seed["source_identity_digest"],
            sku_lineage_digest=seed["sku_lineage_digest"],
            serialized_record=serialized,
        )
    except (
        ImmutableReleaseError,
        ReleaseStoreError,
        ShopeeGlobalPlanApprovalError,
        ShopeeGlobalPlanContractError,
        TypeError,
        ValueError,
    ) as error:
        return 409, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }
    return 200, {
        "ok": True,
        "persisted": True,
        "schema_version": _SHOPEE_GLOBAL_PLAN_APPROVAL_RESPONSE_SCHEMA,
        "offer_id": payload["product_id"],
        "product_revision": payload["product_revision"],
        "approval": approved.public_projection(),
        "record_digest": stored["record_digest"],
        "external_writes_performed": [],
    }


def _immutable_listing_copy_preflight(payload: dict) -> list[str]:
    """Validate only the approved immutable copy before any run mutation."""

    from domains.content_operations import release_listing_copy_identity

    listing_copy = (
        payload.get("listing_copy")
        if isinstance(payload.get("listing_copy"), dict)
        else {}
    )
    identity, blockers = release_listing_copy_identity(
        listing_copy,
        approved_product_title=(payload.get("product_facts") or {}).get("title"),
        current_input_signature=listing_copy.get("input_signature"),
        target_labels=payload.get("targets") or (),
    )
    expected_digest = str(
        listing_copy.get("shopee_description_digest") or ""
    ).strip()
    if expected_digest != identity.get("shopee_description_digest"):
        blockers.append(
            "immutable Shopee description digest does not match its approved text"
        )
    for field in (
        "schema_version",
        "status",
        "provider",
        "policy_version",
        "model",
        "input_signature",
        "semantic_master_en",
        "candidates",
    ):
        if listing_copy.get(field) != identity.get(field):
            blockers.append(
                f"immutable listing copy identity is not normalized: {field}"
            )
    return list(dict.fromkeys(blockers))


def _approved_plan_matches_current_payload(
    persisted_plan: dict,
    current_preview: dict,
) -> bool:
    """Compare immutable business scope while ignoring state-container churn.

    ``product_revision`` is the workbench document revision, not a commercial
    facts revision. Recording Miaoshou/API evidence advances it even though
    the approved fingerprint, packages, facts, images, copy, pricing and
    target scope remain identical. Every business-bearing field stays in the
    comparison; only that operational counter is excluded.
    """

    persisted_payload = dict(persisted_plan.get("payload") or {})
    current_payload = dict(current_preview.get("payload") or {})
    persisted_payload.pop("product_revision", None)
    current_payload.pop("product_revision", None)
    if (
        "approved_tiktok_category_decisions" not in persisted_payload
        and "approved_tiktok_category_decisions" in current_payload
    ):
        # Plans approved before the TikTok category-binding contract was
        # introduced can be upgraded in memory only when the exact binding is
        # deterministically recoverable from that immutable legacy payload.
        # Never accept a present-but-different stored binding, and never use
        # the mutable current dashboard as the source of compatibility facts.
        from modules.miaoshou.oneclick_release import (
            approved_tiktok_category_decisions,
        )

        product_facts = persisted_payload.get("product_facts")
        targets = persisted_payload.get("targets")
        if isinstance(product_facts, dict) and isinstance(targets, list):
            recovered = approved_tiktok_category_decisions(
                product_facts.get("category"),
                targets=tuple(targets),
            )
            if recovered == current_payload.get(
                "approved_tiktok_category_decisions"
            ):
                persisted_payload["approved_tiktok_category_decisions"] = (
                    recovered
                )
    return persisted_payload == current_payload


def _release_adapter_blockers(
    dashboard: dict,
    *,
    selected_labels: list[str],
    registry: dict,
) -> list[dict[str, str]]:
    """Validate the complete target/adapter registry without executing it."""

    target_rows = (
        (dashboard.get("omnichannel_preview") or {}).get("targets") or ()
    )
    rows_by_label: dict[str, list[dict]] = {}
    for target in target_rows:
        if not isinstance(target, dict):
            continue
        label = f"{target.get('channel')}:{target.get('site')}"
        rows_by_label.setdefault(label, []).append(target)

    blockers: list[dict[str, str]] = []
    for label in selected_labels:
        rows = rows_by_label.get(label) or []
        if len(rows) != 1:
            blockers.append(
                {
                    "target": label,
                    "code": "target_registry_identity_mismatch",
                    "detail": (
                        "selected target must have exactly one immutable "
                        "omnichannel registry row"
                    ),
                }
            )
            continue
        target = rows[0]
        for check in target.get("preflights") or ():
            if (
                check.get("code") == "audited_adapter_site"
                and not check.get("passed")
            ):
                blockers.append(
                    {
                        "target": label,
                        "code": "audited_adapter_site",
                        "detail": str(
                            check.get("detail")
                            or "target adapter/site audit is not approved"
                        ),
                    }
                )
        adapter_name = str(target.get("adapter") or "")
        registration = registry.get(adapter_name)
        if not registration or not registration.executable:
            blockers.append(
                {
                    "target": label,
                    "code": (
                        registration.blocker.code
                        if registration and registration.blocker
                        else "adapter_not_registered"
                    ),
                    "detail": (
                        registration.blocker.detail
                        if registration and registration.blocker
                        else "unified V1 adapter is not registered"
                    ),
                }
            )
    return blockers


def _verified_common_evidence_blockers(
    run: dict | None,
    payload: dict,
    *,
    store=None,
) -> list[str]:
    """Require exact durable COMMON provenance before any channel target."""

    common = next(
        (
            row
            for row in ((run or {}).get("targets") or ())
            if row.get("target_label") == "miaoshou:COMMON"
        ),
        None,
    )
    if not common or common.get("status") != "SUCCEEDED":
        return ["Miaoshou COMMON must succeed with verified readback first"]
    expected_product_id = str(payload.get("product_id") or "")
    blockers: list[str] = []
    if str((run or {}).get("plan_id") or "") != str(
        payload.get("plan_id") or ""
    ):
        blockers.append("Miaoshou COMMON run does not belong to immutable plan")
    if str(common.get("external_id") or "") != expected_product_id:
        blockers.append("Miaoshou COMMON external_id does not match product_id")
    readback = (
        common.get("readback")
        if isinstance(common.get("readback"), dict)
        else {}
    )
    evidence = (
        readback.get("evidence")
        if isinstance(readback.get("evidence"), dict)
        else {}
    )
    if evidence.get("verified") is not True:
        blockers.append("Miaoshou COMMON lacks verified readback evidence")
    if (
        not str(readback.get("evidence_digest") or "").strip()
        or not str(readback.get("verified_at") or "").strip()
    ):
        blockers.append("Miaoshou COMMON durable readback receipt is incomplete")
    elif hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest() != readback.get("evidence_digest"):
        blockers.append("Miaoshou COMMON readback evidence digest is invalid")
    if str(evidence.get("offer_id") or "") != expected_product_id:
        blockers.append("Miaoshou COMMON readback offer_id does not match product_id")
    if not str(evidence.get("source") or "").strip():
        blockers.append("Miaoshou COMMON readback source is missing")
    checks = evidence.get("checks")
    if (
        not isinstance(checks, dict)
        or not checks
        or any(value is not True for value in checks.values())
    ):
        blockers.append("Miaoshou COMMON readback checks are incomplete")
    expected_image_count = len(payload.get("images") or ())
    if int(evidence.get("image_count") or -1) != expected_image_count:
        blockers.append("Miaoshou COMMON readback image count does not match plan")
    if evidence.get("mode") == "readback_reuse_no_write":
        predecessor = (
            evidence.get("predecessor")
            if isinstance(evidence.get("predecessor"), dict)
            else {}
        )
        if evidence.get("external_writes_performed") != []:
            blockers.append("COMMON reuse evidence contains an external write")
        if (
            not str(predecessor.get("plan_id") or "")
            or not str(predecessor.get("run_id") or "")
            or not str(predecessor.get("payload_digest") or "")
            or not str(predecessor.get("common_readback_evidence_digest") or "")
            or not str(predecessor.get("common_readback_verified_at") or "")
            or predecessor.get("common_status") != "SUCCEEDED"
            or str(predecessor.get("common_external_id") or "")
            != expected_product_id
        ):
            blockers.append("COMMON reuse predecessor provenance is incomplete")
        if store is not None and predecessor:
            durable_plan = store.get_plan(str(predecessor.get("plan_id") or ""))
            durable_run = store.get_run(str(predecessor.get("run_id") or ""))
            durable_common = next(
                (
                    row
                    for row in ((durable_run or {}).get("targets") or ())
                    if row.get("target_label") == "miaoshou:COMMON"
                ),
                None,
            )
            durable_readback = (
                (durable_common or {}).get("readback")
                if isinstance((durable_common or {}).get("readback"), dict)
                else {}
            )
            if (
                not durable_plan
                or durable_plan.get("payload_digest")
                != predecessor.get("payload_digest")
                or (durable_run or {}).get("plan_id")
                != predecessor.get("plan_id")
                or (durable_run or {}).get("run_id")
                != predecessor.get("run_id")
                or (durable_common or {}).get("status") != "SUCCEEDED"
                or str((durable_common or {}).get("external_id") or "")
                != str(predecessor.get("common_external_id") or "")
                or durable_readback.get("evidence_digest")
                != predecessor.get("common_readback_evidence_digest")
                or durable_readback.get("verified_at")
                != predecessor.get("common_readback_verified_at")
            ):
                blockers.append(
                    "COMMON reuse provenance does not match durable predecessor"
                )
    return blockers


def _release_execution_readonly_gate(
    data: dict,
    *,
    store,
) -> tuple[dict | None, tuple[int, dict] | None]:
    """Rebuild and validate every immutable execution input without writes."""

    from modules.products.release_adapters import production_adapter_registry

    dashboard, failure = _release_dashboard_for_request(data)
    if failure:
        return None, failure
    assert dashboard is not None
    current_payload, blockers = _release_plan_payload_from_dashboard(dashboard)
    actual_gate = dashboard.get("actual_release_gate") or {}
    plan_id = str(data.get("plan_id") or "").strip()
    token = str(data.get("confirmation_token") or "").strip()
    try:
        preview = store.preview_plan(current_payload)
    except (TypeError, ValueError) as error:
        blockers.append(str(error))
        preview = {}
    plan = store.get_plan(plan_id)
    plan_identity_exact = bool(
        plan
        and plan_id == str(preview.get("plan_id") or "")
        and plan.get("status") == "APPROVED"
        and token == plan.get("confirmation_token")
        and _approved_plan_matches_current_payload(plan, preview)
    )
    if not plan_identity_exact:
        blockers.append(
            "approved ReleasePlan no longer matches current immutable facts"
        )
    if plan:
        blockers.extend(
            _immutable_listing_copy_preflight(plan.get("payload") or {})
        )
    registry = production_adapter_registry()
    adapter_blockers = _release_adapter_blockers(
        dashboard,
        selected_labels=list(current_payload.get("targets") or ()),
        registry=registry,
    )
    run = (
        store.get_run(f"release-run:{plan['payload_digest'][:24]}")
        if plan
        else None
    )
    predecessor_run = _release_predecessor_evidence_run(store, plan)
    common_blockers = (
        _verified_common_evidence_blockers(
            run,
            plan.get("payload") or {},
            store=store,
        )
        if plan
        else ["Miaoshou COMMON must succeed with verified readback first"]
    )
    blockers.extend(common_blockers)

    # ``actual_release_gate`` is a pre-COMMON presentation gate.  Once the
    # exact approved plan has a digest-verified durable COMMON readback, that
    # canonical receipt supersedes stale legacy image-sync observations.  The
    # current immutable payload comparison above still protects every business
    # field, so this does not weaken the release authorization boundary.
    canonical_common_ready = bool(plan_identity_exact and not common_blockers)
    if not canonical_common_ready and not actual_gate.get("ready"):
        blockers.extend(
            str(value)
            for value in (
                actual_gate.get("blockers")
                or ["actual release gate is not ready"]
            )
        )
    blockers = list(dict.fromkeys(value for value in blockers if value))
    if blockers or adapter_blockers:
        return None, (
            409,
            {
                "ok": False,
                "error": "release execution preflight is blocked",
                "blockers": blockers,
                "adapter_blockers": adapter_blockers,
                "external_writes_performed": [],
                "run": run,
            },
        )
    return {
        "dashboard": dashboard,
        "payload": current_payload,
        "plan": plan,
        "run": run,
        "predecessor_run": predecessor_run,
        "registry": registry,
        "target_rows": (
            (dashboard.get("omnichannel_preview") or {}).get("targets") or ()
        ),
    }, None


def _target_scoped_adapter_module():
    """Load the channel-owned seam only when a dedicated action is requested."""

    import importlib

    return importlib.import_module(
        "domains.channel_operations.target_scoped_retry_adapters"
    )


def _target_scoped_request_from_context(
    *,
    gate: dict,
    context: dict,
):
    from shared_platform.target_scoped_release_contracts import (
        TargetScopedOperationRequest,
    )

    plan = gate["plan"] or {}
    payload = plan.get("payload") or {}
    return TargetScopedOperationRequest(
        plan_id=str(plan.get("plan_id") or ""),
        confirmation_token=str(plan.get("confirmation_token") or ""),
        approval_scope_digest=str(
            payload.get("omnichannel_scope_digest") or ""
        ),
        product_id=str(plan.get("product_id") or ""),
        seller_sku=str(plan.get("seller_sku") or ""),
        product_package_id=str(plan.get("product_package_id") or ""),
        content_package_id=str(plan.get("content_package_id") or ""),
        run_id=str(context.get("run_id") or ""),
        target_label=str(context.get("target_label") or ""),
        operation_kind=str(context.get("operation_kind") or ""),
        product_revision=context.get("product_revision"),
        payload_digest=str(context.get("payload_digest") or ""),
        planned_command=dict(context.get("planned_command") or {}),
        planned_command_digest=str(
            context.get("planned_command_digest") or ""
        ),
        preflight_digest=str(context.get("preflight_digest") or ""),
        failure_attempt=context.get("failure_attempt"),
        failure_digest=str(context.get("failure_digest") or ""),
        target_idempotency_key=str(
            context.get("target_idempotency_key") or ""
        ),
        approved_by="Kyle",
    )


def _target_scoped_action_gate(
    data: dict,
    *,
    store,
    derive_plan: bool,
) -> tuple[dict | None, tuple[int, dict] | None]:
    """Resolve one exact active plan/target without mutating release state."""

    from shared_platform.target_scoped_release_contracts import (
        TargetScopedCommandUnavailable,
        TargetScopedContractError,
        operation_kind_for_target,
        planned_target_command,
    )

    offer_id = str(data.get("offer_id") or "").strip()
    target_label = str(data.get("target_label") or "").strip()
    if not offer_id.isdigit() or not 1 <= len(offer_id) <= 32:
        return None, (
            400,
            {
                "ok": False,
                "error": "offer_id must contain 1-32 digits",
                "external_writes_performed": [],
            },
        )
    try:
        operation_kind = operation_kind_for_target(target_label)
    except TargetScopedContractError as error:
        return None, (
            409,
            {
                "ok": False,
                "code": "target_scoped_action_not_supported",
                "error": str(error),
                "external_writes_performed": [],
            },
        )

    gate_data = dict(data)
    if derive_plan:
        active = store.active_plan_for_product(offer_id)
        approval = (active or {}).get("approval") or {}
        if (
            not active
            or active.get("status") != "APPROVED"
            or approval.get("status") != "APPROVED"
            or approval.get("approved_by") != "Kyle"
            or approval.get("user_approved") is not True
        ):
            return None, (
                409,
                {
                    "ok": False,
                    "code": "active_release_plan_required",
                    "error": (
                        "target-scoped action requires the active "
                        "Kyle-approved ReleasePlan"
                    ),
                    "external_writes_performed": [],
                },
            )
        gate_data.update(
            {
                "offer_id": offer_id,
                "seller_sku": active.get("seller_sku"),
                "publication_targets": list(active.get("targets") or ()),
                "plan_id": active.get("plan_id"),
                "confirmation_token": active.get("confirmation_token"),
            }
        )

    gate, failure = _release_execution_readonly_gate(
        gate_data,
        store=store,
    )
    if failure:
        return None, failure
    assert gate is not None
    plan = gate.get("plan") or {}
    run = gate.get("run")
    if (
        not run
        or target_label not in (plan.get("targets") or ())
        or str((plan.get("payload") or {}).get("product_id") or "")
        != offer_id
    ):
        return None, (
            409,
            {
                "ok": False,
                "code": "target_scoped_identity_mismatch",
                "error": (
                    "target-scoped action does not match the active plan run"
                ),
                "external_writes_performed": [],
            },
        )
    existing = store.get_target_scoped_operation(
        run_id=str(run.get("run_id") or ""),
        target_label=target_label,
    )
    if existing:
        try:
            _current_command, current_command_digest = (
                planned_target_command(
                    plan.get("payload") or {},
                    target_label=target_label,
                )
            )
        except TargetScopedCommandUnavailable as error:
            return None, (
                409,
                {
                    "ok": False,
                    "code": error.code,
                    "error": str(error),
                    "available": False,
                    "external_writes_performed": [],
                    "run": run,
                },
            )
        stored_request = existing.get("request") or {}
        if (
            stored_request.get("planned_command_digest")
            != current_command_digest
            or stored_request.get("payload_digest")
            != plan.get("payload_digest")
        ):
            return None, (
                409,
                {
                    "ok": False,
                    "code": "target_scoped_contract_stale",
                    "error": (
                        "stored target-scoped proof/operation identity uses "
                        "an obsolete server-derived command contract"
                    ),
                    "available": False,
                    "operation_status": existing.get("status"),
                    "external_writes_performed": [],
                },
            )
        return {
            "gate": gate,
            "operation_kind": operation_kind,
            "existing_operation": existing,
            "request": None,
            "context": None,
            "gate_data": gate_data,
        }, None
    try:
        context = store.target_scoped_action_context(
            plan_id=str(plan.get("plan_id") or ""),
            target_label=target_label,
        )
        request = _target_scoped_request_from_context(
            gate=gate,
            context=context,
        )
    except TargetScopedCommandUnavailable as error:
        return None, (
            409,
            {
                "ok": False,
                "code": error.code,
                "error": str(error),
                "available": False,
                "external_writes_performed": [],
                "run": run,
            },
        )
    except (TypeError, ValueError, RuntimeError) as error:
        return None, (
            409,
            {
                "ok": False,
                "code": "target_scoped_action_blocked",
                "error": str(error),
                "external_writes_performed": [],
                "run": run,
            },
        )
    if not context.get("eligible"):
        return None, (
            409,
            {
                "ok": False,
                "code": "target_scoped_action_blocked",
                "error": "target is not eligible for a scoped action",
                "blockers": list(context.get("blockers") or ()),
                "external_writes_performed": [],
                "run": run,
            },
        )
    return {
        "gate": gate,
        "operation_kind": operation_kind,
        "existing_operation": None,
        "request": request,
        "context": context,
        "gate_data": gate_data,
    }, None


def _target_scoped_existing_operation_response(
    *,
    operation: dict,
    data: dict | None = None,
    plan: dict | None = None,
) -> tuple[int, dict]:
    """Return an exact replay or a zero-call terminal response."""

    import hashlib

    status = str(operation.get("status") or "")
    request = operation.get("request") or {}
    if data is not None and plan is not None:
        token = str(data.get("confirmation_token") or "")
        expected = {
            "plan_id": str(data.get("plan_id") or ""),
            "target_label": str(data.get("target_label") or ""),
            "product_revision": data.get("expected_revision"),
            "failure_attempt": data.get("failure_attempt"),
            "payload_digest": str(data.get("payload_digest") or ""),
            "planned_command_digest": str(
                data.get("planned_command_digest") or ""
            ),
            "preflight_digest": str(data.get("preflight_digest") or ""),
            "approved_by": str(data.get("approved_by") or ""),
            "confirmation_token_digest": hashlib.sha256(
                token.encode("utf-8")
            ).hexdigest(),
        }
        actual = {field: request.get(field) for field in expected}
        if (
            actual != expected
            or str(data.get("proof_digest") or "")
            != str(operation.get("proof_digest") or "")
            or token != str(plan.get("confirmation_token") or "")
        ):
            return 409, {
                "ok": False,
                "code": "target_scoped_replay_identity_mismatch",
                "error": "target-scoped replay identity does not match",
                "external_writes_performed": [],
            }
    if status == "SUCCEEDED":
        return 200, {
            "ok": True,
            "idempotent": True,
            "operation_kind": operation.get("operation_kind"),
            "target_label": operation.get("target_label"),
            "operation_status": status,
            "external_writes_performed": [],
        }
    return 409, {
        "ok": False,
        "code": (
            "target_scoped_action_running"
            if status == "RUNNING"
            else "target_scoped_action_terminal"
        ),
        "error": (
            "target-scoped action is already running"
            if status == "RUNNING"
            else (
                "target-scoped action is terminal; obtain a new governed "
                "proof before any further action"
            )
        ),
        "operation_status": status,
        "target_label": operation.get("target_label"),
        "external_writes_performed": [],
    }


def _preview_target_scoped_release_action(
    *,
    offer_id: str,
    target_label: str,
) -> tuple[int, dict]:
    """Build one redacted, write-free preview with no token refresh."""

    from shared_platform.release_store import default_release_store
    from shared_platform.target_scoped_release_contracts import (
        OfficialTargetProof,
        TargetScopedContractError,
    )

    store = default_release_store()
    resolved, failure = _target_scoped_action_gate(
        {"offer_id": offer_id, "target_label": target_label},
        store=store,
        derive_plan=True,
    )
    if failure:
        return failure
    assert resolved is not None
    existing = resolved.get("existing_operation")
    if existing:
        status, payload = _target_scoped_existing_operation_response(
            operation=existing
        )
        payload.update(
            {
                "preview": True,
                "available": False,
                "external_writes_performed": [],
            }
        )
        return status, payload
    request = resolved["request"]
    try:
        adapter = _target_scoped_adapter_module()
        raw_proof = adapter.build_official_target_proof(
            request,
            allow_refresh=False,
        )
        proof = OfficialTargetProof.from_value(
            raw_proof,
            request=request,
        )
    except ModuleNotFoundError:
        return 503, {
            "ok": False,
            "code": "target_scoped_adapter_unavailable",
            "error": "channel target-scoped proof provider is unavailable",
            "external_writes_performed": [],
        }
    except (TargetScopedContractError, TypeError, ValueError, RuntimeError) as error:
        return 409, {
            "ok": False,
            "code": "official_target_proof_failed",
            "error": str(error),
            "external_writes_performed": [],
        }
    return 200, {
        "ok": True,
        "preview": True,
        "available": True,
        "target_label": request.target_label,
        "operation_kind": request.operation_kind,
        "plan_id": request.plan_id,
        "expected_revision": request.product_revision,
        "payload_digest": request.payload_digest,
        "planned_command_digest": request.planned_command_digest,
        "preflight_digest": request.preflight_digest,
        "proof_digest": proof.proof_digest,
        "failure_attempt": request.failure_attempt,
        "summary": dict(proof.redacted_summary),
        "external_writes_performed": [],
    }


def _target_scoped_exception_result(error: Exception):
    """Convert an adapter exception into truthful fail-closed evidence."""

    from shared_platform.target_scoped_release_contracts import (
        TargetScopedOperationResult,
    )

    source = getattr(error, "external_write_evidence", None)
    evidence = dict(source) if isinstance(source, dict) else {}
    writes = [
        str(value)
        for value in (evidence.get("external_writes_performed") or ())
        if str(value)
    ]
    evidence.update(
        {
            "external_writes_performed": writes,
            "durable_state_uncertain": (
                evidence.get("pre_submit_failure") is not True
            ),
            "reconciliation_required": (
                evidence.get("pre_submit_failure") is not True
            ),
        }
    )
    return TargetScopedOperationResult.from_value(
        {
            "succeeded": False,
            "readback_verified": False,
            "detail": str(error) or type(error).__name__,
            "external_reference": getattr(
                error, "external_reference", None
            ),
            "submission_accepted": (
                evidence.get("submission_accepted") is True
            ),
            "evidence": evidence,
        }
    )


def _execute_target_scoped_release_action(data: dict) -> tuple[int, dict]:
    """Execute exactly one proof-bound target action; never loop or retry."""

    from shared_platform.release_store import (
        ImmutableReleaseError,
        ReleaseAuthorizationError,
        ReleaseStoreError,
        default_release_store,
    )
    from shared_platform.target_scoped_release_contracts import (
        OfficialTargetProof,
        TargetScopedContractError,
        TargetScopedOperationResult,
    )

    if data.get("confirm_target_scoped_action") is not True:
        return 400, {
            "ok": False,
            "error": "literal confirm_target_scoped_action=true is required",
            "external_writes_performed": [],
        }
    if str(data.get("approved_by") or "").strip() != "Kyle":
        return 400, {
            "ok": False,
            "error": "approved_by must be Kyle",
            "external_writes_performed": [],
        }
    if "planned_command" in data:
        return 400, {
            "ok": False,
            "code": "client_command_override_forbidden",
            "error": (
                "client may echo planned_command_digest but cannot provide "
                "or override the server-owned planned command"
            ),
            "external_writes_performed": [],
        }
    required_text = (
        "target_label",
        "plan_id",
        "confirmation_token",
        "payload_digest",
        "planned_command_digest",
        "preflight_digest",
        "proof_digest",
    )
    if any(not str(data.get(field) or "").strip() for field in required_text):
        return 400, {
            "ok": False,
            "error": (
                "target_label, plan_id, confirmation_token, payload_digest, "
                "planned_command_digest, preflight_digest and proof_digest "
                "are required"
            ),
            "external_writes_performed": [],
        }
    expected_revision = data.get("expected_revision")
    failure_attempt = data.get("failure_attempt")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        return 400, {
            "ok": False,
            "error": "expected_revision must be a non-negative integer",
            "external_writes_performed": [],
        }
    if (
        isinstance(failure_attempt, bool)
        or not isinstance(failure_attempt, int)
        or failure_attempt < 0
    ):
        return 400, {
            "ok": False,
            "error": "failure_attempt must be a non-negative integer",
            "external_writes_performed": [],
        }

    store = default_release_store()
    target_label = str(data.get("target_label") or "").strip()
    offer_id = str(data.get("offer_id") or "").strip()
    with _release_execution_lock, _product_workbench_lock(offer_id):
        resolved, failure = _target_scoped_action_gate(
            data,
            store=store,
            derive_plan=False,
        )
        if failure:
            return failure
        assert resolved is not None
        gate = resolved["gate"]
        plan = gate["plan"] or {}
        existing = resolved.get("existing_operation")
        if existing:
            return _target_scoped_existing_operation_response(
                operation=existing,
                data=data,
                plan=plan,
            )
        request = resolved["request"]
        if (
            expected_revision != request.product_revision
            or failure_attempt != request.failure_attempt
            or str(data.get("payload_digest") or "")
            != request.payload_digest
            or str(data.get("planned_command_digest") or "")
            != request.planned_command_digest
            or str(data.get("preflight_digest") or "")
            != request.preflight_digest
            or str(data.get("plan_id") or "") != request.plan_id
            or str(data.get("confirmation_token") or "")
            != request.confirmation_token
        ):
            return 409, {
                "ok": False,
                "code": "target_scoped_request_drift",
                "error": (
                    "target-scoped request no longer matches the active "
                    "plan/failure preflight"
                ),
                "external_writes_performed": [],
            }
        try:
            adapter = _target_scoped_adapter_module()
            raw_proof = adapter.build_official_target_proof(
                request,
                allow_refresh=False,
            )
            proof = OfficialTargetProof.from_value(
                raw_proof,
                request=request,
            )
        except ModuleNotFoundError:
            return 503, {
                "ok": False,
                "code": "target_scoped_adapter_unavailable",
                "error": "channel target-scoped adapter is unavailable",
                "external_writes_performed": [],
            }
        except (TargetScopedContractError, TypeError, ValueError, RuntimeError) as error:
            return 409, {
                "ok": False,
                "code": "official_target_proof_failed",
                "error": str(error),
                "external_writes_performed": [],
            }
        if proof.proof_digest != str(data.get("proof_digest") or ""):
            return 409, {
                "ok": False,
                "code": "official_target_proof_drift",
                "error": "official target proof changed before dispatch",
                "external_writes_performed": [],
            }
        try:
            claim = store.claim_target_scoped_operation(
                request=request,
                proof=proof,
            )
        except (
            ImmutableReleaseError,
            ReleaseAuthorizationError,
            ReleaseStoreError,
            TargetScopedContractError,
            TypeError,
            ValueError,
        ) as error:
            return 409, {
                "ok": False,
                "code": "target_scoped_claim_rejected",
                "error": str(error),
                "external_writes_performed": [],
            }
        if claim.get("action") == "already_succeeded":
            return _target_scoped_existing_operation_response(
                operation=claim["operation"],
                data=data,
                plan=plan,
            )
        operation = claim["operation"]
        operation_digest = str(operation["operation_digest"])
        try:
            raw_result = adapter.execute_target_scoped_operation(
                request,
                proof,
            )
            result = TargetScopedOperationResult.from_value(raw_result)
        except Exception as error:
            try:
                result = _target_scoped_exception_result(error)
            except Exception:
                result = TargetScopedOperationResult.from_value(
                    {
                        "succeeded": False,
                        "readback_verified": False,
                        "detail": type(error).__name__,
                        "external_reference": getattr(
                            error, "external_reference", None
                        ),
                        "submission_accepted": False,
                        "evidence": {
                            "external_writes_performed": list(
                                (
                                    getattr(
                                        error,
                                        "external_write_evidence",
                                        {},
                                    )
                                    or {}
                                ).get(
                                    "external_writes_performed",
                                    [],
                                )
                            ),
                            "durable_state_uncertain": True,
                            "reconciliation_required": True,
                        },
                    }
                )

        try:
            if result.outcome == "SUCCEEDED":
                run = store.record_target_scoped_success(
                    operation_digest,
                    result=result,
                )
                status = 200
                code = "target_scoped_action_succeeded"
            elif result.outcome == "FAILED_PRE_SUBMIT":
                run = store.record_target_scoped_pre_submit_failure(
                    operation_digest,
                    result=result,
                )
                status = 409
                code = "target_scoped_pre_submit_failure"
            else:
                run = store.record_target_scoped_reconciliation(
                    operation_digest,
                    result=result,
                )
                status = 409
                code = "target_scoped_reconciliation_required"
        except Exception as receipt_error:
            latest = store.get_target_scoped_operation(
                run_id=request.run_id,
                target_label=request.target_label,
            )
            if (latest or {}).get("status") == "SUCCEEDED":
                return 200, {
                    "ok": True,
                    "idempotent": False,
                    "code": "target_scoped_action_succeeded",
                    "target_label": request.target_label,
                    "operation_kind": request.operation_kind,
                    "operation_status": "SUCCEEDED",
                    "external_writes_performed": (
                        result.external_writes_performed
                    ),
                    "durable_receipt_recovered": True,
                    "run": store.get_run(request.run_id),
                }
            uncertain = TargetScopedOperationResult.from_value(
                {
                    "succeeded": False,
                    "readback_verified": False,
                    "detail": (
                        "external outcome is durable-state uncertain after "
                        f"receipt failure: {type(receipt_error).__name__}"
                    ),
                    "external_reference": result.external_reference,
                    "submission_accepted": result.submission_accepted,
                    "evidence": {
                        **dict(result.evidence),
                        "external_writes_performed": (
                            result.external_writes_performed
                        ),
                        "durable_state_uncertain": True,
                        "reconciliation_required": True,
                    },
                }
            )
            try:
                run = store.record_target_scoped_reconciliation(
                    operation_digest,
                    result=uncertain,
                )
            except Exception as reconciliation_error:
                return 500, {
                    "ok": False,
                    "code": "target_scoped_durable_receipt_uncertain",
                    "error": (
                        "target-scoped external outcome could not be "
                        "durably reconciled"
                    ),
                    "receipt_error": type(receipt_error).__name__,
                    "reconciliation_error": type(
                        reconciliation_error
                    ).__name__,
                    "durable_state_uncertain": True,
                    "reconciliation_required": True,
                    "target_label": request.target_label,
                    "external_writes_performed": (
                        uncertain.external_writes_performed
                    ),
                }
            result = uncertain
            status = 409
            code = "target_scoped_reconciliation_required"

        return status, {
            "ok": status == 200,
            "idempotent": False,
            "code": code,
            "target_label": request.target_label,
            "operation_kind": request.operation_kind,
            "operation_status": result.outcome,
            "detail": result.detail,
            "external_writes_performed": result.external_writes_performed,
            "durable_state_uncertain": bool(
                result.evidence.get("durable_state_uncertain")
            ),
            "reconciliation_required": (
                result.outcome == "RECONCILIATION_REQUIRED"
            ),
            "run": run,
        }


_TARGET_SCOPED_RECONCILIATION_TARGETS = frozenset(
    {"shopee:MY", "shopee:VN"}
)


def _target_scoped_reconciliation_gate(
    data: dict,
    *,
    store,
    derive_plan: bool,
) -> tuple[dict | None, tuple[int, dict] | None]:
    """Resolve one existing ambiguous operation without changing state."""

    offer_id = str(data.get("offer_id") or "").strip()
    target_label = str(data.get("target_label") or "").strip()
    if (
        not offer_id.isdigit()
        or not 1 <= len(offer_id) <= 32
        or target_label not in _TARGET_SCOPED_RECONCILIATION_TARGETS
    ):
        return None, (
            400,
            {
                "ok": False,
                "code": "target_scoped_reconciliation_not_supported",
                "error": (
                    "valid offer_id and Shopee MY/VN target are required"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            },
        )
    gate_data = dict(data)
    if derive_plan:
        active = store.active_plan_for_product(offer_id)
        approval = (active or {}).get("approval") or {}
        if (
            not active
            or active.get("status") != "APPROVED"
            or approval.get("status") != "APPROVED"
            or approval.get("approved_by") != "Kyle"
            or approval.get("user_approved") is not True
        ):
            return None, (
                409,
                {
                    "ok": False,
                    "code": "active_release_plan_required",
                    "error": (
                        "GET-only close requires the active "
                        "Kyle-approved ReleasePlan"
                    ),
                    "external_writes_performed": [],
                    "state_mutations_performed": [],
                },
            )
        gate_data.update(
            {
                "offer_id": offer_id,
                "seller_sku": active.get("seller_sku"),
                "publication_targets": list(active.get("targets") or ()),
                "plan_id": active.get("plan_id"),
                "confirmation_token": active.get("confirmation_token"),
            }
        )
    gate, failure = _release_execution_readonly_gate(
        gate_data,
        store=store,
    )
    if failure:
        status, response = failure
        return None, (
            status,
            {
                "ok": False,
                "code": "target_scoped_reconciliation_gate_blocked",
                "error": str(
                    response.get("error")
                    or "GET-only reconciliation gate is blocked"
                ),
                "blockers": list(response.get("blockers") or ()),
                "adapter_blockers": list(
                    response.get("adapter_blockers") or ()
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            },
        )
    assert gate is not None
    dashboard = gate["dashboard"]
    plan = gate.get("plan") or {}
    run = gate.get("run") or {}
    revision = (dashboard.get("product") or {}).get("revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or (plan.get("payload") or {}).get("product_revision")
        != revision
        or str(plan.get("product_id") or "") != offer_id
        or target_label not in list(plan.get("targets") or ())
        or str(run.get("plan_id") or "")
        != str(plan.get("plan_id") or "")
    ):
        return None, (
            409,
            {
                "ok": False,
                "code": "target_scoped_reconciliation_identity_mismatch",
                "error": (
                    "active plan/run/revision does not match the target"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            },
        )
    try:
        context = store.target_scoped_reconciliation_context(
            plan_id=str(plan.get("plan_id") or ""),
            target_label=target_label,
        )
    except (TypeError, ValueError, RuntimeError) as error:
        return None, (
            409,
            {
                "ok": False,
                "code": "target_scoped_reconciliation_blocked",
                "error": str(error),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            },
        )
    if not context.get("eligible"):
        return None, (
            409,
            {
                "ok": False,
                "code": "target_scoped_reconciliation_blocked",
                "error": (
                    "existing target-scoped operation is not eligible "
                    "for GET-only close"
                ),
                "blockers": list(context.get("blockers") or ()),
                "operation_status": (
                    (context.get("operation") or {}).get("status")
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            },
        )
    return {
        "gate": gate,
        "gate_data": gate_data,
        "plan": plan,
        "run": run,
        "revision": revision,
        "context": context,
        "request": context["reconciliation_request"],
    }, None


def _preview_target_scoped_reconciliation(
    *,
    offer_id: str,
    target_label: str,
) -> tuple[int, dict]:
    """Build a redacted official GET-only proof without persistence."""

    from shared_platform.release_store import default_release_store
    from shared_platform.target_scoped_release_contracts import (
        OfficialTargetReconciliationProof,
        TargetScopedContractError,
    )

    store = default_release_store()
    with _release_execution_lock:
        resolved, failure = _target_scoped_reconciliation_gate(
            {"offer_id": offer_id, "target_label": target_label},
            store=store,
            derive_plan=True,
        )
        if failure:
            return failure
        assert resolved is not None
        context = resolved["context"]
        request = resolved["request"]
        operation = context["operation"]
        if context.get("already_succeeded"):
            result = operation.get("result") or {}
            return 200, {
                "ok": True,
                "preview": True,
                "available": False,
                "idempotent": True,
                "mode": "official_get_only_durable_close",
                "target_label": request.operation_request.target_label,
                "operation_status": "SUCCEEDED",
                "operation_digest": request.operation_digest,
                "reconciliation_proof_digest": result.get(
                    "reconciliation_proof_digest"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        try:
            adapter = _target_scoped_adapter_module()
            raw_proof = (
                adapter.build_official_target_reconciliation_proof(
                    request,
                    allow_refresh=False,
                )
            )
            proof = OfficialTargetReconciliationProof.from_value(
                raw_proof,
                request=request,
            )
        except (AttributeError, ModuleNotFoundError):
            return 503, {
                "ok": False,
                "code": "target_scoped_reconciliation_adapter_unavailable",
                "error": (
                    "channel GET-only reconciliation provider is unavailable"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        except (
            TargetScopedContractError,
            TypeError,
            ValueError,
            RuntimeError,
        ) as error:
            return 409, {
                "ok": False,
                "code": "official_reconciliation_proof_failed",
                "error": str(error),
                "operation_status": operation.get("status"),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        base = request.operation_request
        return 200, {
            "ok": True,
            "preview": True,
            "available": True,
            "mode": request.reconciliation_mode,
            "target_label": base.target_label,
            "operation_kind": base.operation_kind,
            "operation_status": operation.get("status"),
            "plan_id": base.plan_id,
            "run_id": base.run_id,
            "confirmation_token_digest": (
                base.confirmation_token_digest
            ),
            "expected_revision": base.product_revision,
            "payload_digest": base.payload_digest,
            "planned_command_digest": base.planned_command_digest,
            "preflight_digest": base.preflight_digest,
            "failure_attempt": base.failure_attempt,
            "operation_digest": request.operation_digest,
            "operation_proof_digest": request.operation_proof_digest,
            "prior_result_digest": request.prior_result_digest,
            "external_identity_digest": (
                request.external_identity_digest
            ),
            "original_proof_evidence_digest": (
                request.original_proof_evidence_digest
            ),
            "selected_logistics_count": (
                request.original_proof_evidence[
                    "selected_logistics_count"
                ]
            ),
            "global_item_identity_digest": (
                request.global_item_identity_digest
            ),
            "reconciliation_request_digest": request.request_digest,
            "reconciliation_proof_digest": proof.proof_digest,
            "publication_targets": list(request.publication_targets),
            "summary": dict(proof.redacted_summary),
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }


def _execute_target_scoped_reconciliation(
    data: dict,
) -> tuple[int, dict]:
    """Re-read official facts and atomically close one ambiguous operation."""

    from shared_platform.release_store import default_release_store
    from shared_platform.target_scoped_release_contracts import (
        OfficialTargetReconciliationProof,
        TargetScopedContractError,
        TargetScopedOperationResult,
    )

    if (
        data.get("confirm_target_scoped_reconciliation") is not True
        or data.get("approved_by") != "Kyle"
    ):
        return 400, {
            "ok": False,
            "code": "target_scoped_reconciliation_consent_required",
            "error": (
                "literal confirm_target_scoped_reconciliation=true "
                "and approved_by=Kyle are required"
            ),
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    offer_id = str(data.get("offer_id") or "").strip()
    target_label = str(data.get("target_label") or "").strip()
    if (
        isinstance(data.get("expected_revision"), bool)
        or not isinstance(data.get("expected_revision"), int)
        or not isinstance(data.get("publication_targets"), list)
    ):
        return 400, {
            "ok": False,
            "code": "target_scoped_reconciliation_identity_required",
            "error": (
                "integer expected_revision and full publication_targets "
                "are required"
            ),
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    required_text = (
        "seller_sku",
        "plan_id",
        "run_id",
        "confirmation_token",
        "payload_digest",
        "planned_command_digest",
        "preflight_digest",
        "operation_digest",
        "operation_proof_digest",
        "prior_result_digest",
        "external_identity_digest",
        "original_proof_evidence_digest",
        "global_item_identity_digest",
        "reconciliation_request_digest",
        "reconciliation_proof_digest",
    )
    if any(not str(data.get(field) or "").strip() for field in required_text):
        return 400, {
            "ok": False,
            "code": "target_scoped_reconciliation_identity_required",
            "error": "complete immutable reconciliation identity is required",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    store = default_release_store()
    with _release_execution_lock, _product_workbench_lock(offer_id):
        resolved, failure = _target_scoped_reconciliation_gate(
            data,
            store=store,
            derive_plan=False,
        )
        if failure:
            return failure
        assert resolved is not None
        request = resolved["request"]
        base = request.operation_request
        expected = {
            "seller_sku": base.seller_sku,
            "plan_id": base.plan_id,
            "run_id": base.run_id,
            "confirmation_token": base.confirmation_token,
            "expected_revision": base.product_revision,
            "payload_digest": base.payload_digest,
            "planned_command_digest": base.planned_command_digest,
            "preflight_digest": base.preflight_digest,
            "failure_attempt": base.failure_attempt,
            "operation_digest": request.operation_digest,
            "operation_proof_digest": request.operation_proof_digest,
            "prior_result_digest": request.prior_result_digest,
            "external_identity_digest": (
                request.external_identity_digest
            ),
            "original_proof_evidence_digest": (
                request.original_proof_evidence_digest
            ),
            "global_item_identity_digest": (
                request.global_item_identity_digest
            ),
            "reconciliation_request_digest": request.request_digest,
            "publication_targets": list(request.publication_targets),
        }
        actual = {field: data.get(field) for field in expected}
        if actual != expected:
            return 409, {
                "ok": False,
                "code": "target_scoped_reconciliation_request_drift",
                "error": (
                    "reconciliation request no longer matches the active "
                    "plan/operation identity"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        context = resolved["context"]
        operation = context["operation"]
        if context.get("already_succeeded"):
            stored = operation.get("result") or {}
            if (
                data.get("reconciliation_proof_digest")
                != stored.get("reconciliation_proof_digest")
            ):
                return 409, {
                    "ok": False,
                    "code": "target_scoped_reconciliation_replay_drift",
                    "error": "stored reconciliation proof identity differs",
                    "external_writes_performed": [],
                    "state_mutations_performed": [],
                }
            return 200, {
                "ok": True,
                "idempotent": True,
                "code": "target_scoped_reconciliation_succeeded",
                "mode": request.reconciliation_mode,
                "target_label": base.target_label,
                "operation_status": "SUCCEEDED",
                "external_writes_performed": [],
                "state_mutations_performed": [],
                "run": resolved["run"],
            }
        try:
            adapter = _target_scoped_adapter_module()
            raw_proof = (
                adapter.build_official_target_reconciliation_proof(
                    request,
                    allow_refresh=False,
                )
            )
            proof = OfficialTargetReconciliationProof.from_value(
                raw_proof,
                request=request,
            )
        except (AttributeError, ModuleNotFoundError):
            return 503, {
                "ok": False,
                "code": "target_scoped_reconciliation_adapter_unavailable",
                "error": (
                    "channel GET-only reconciliation provider is unavailable"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        except (
            TargetScopedContractError,
            TypeError,
            ValueError,
            RuntimeError,
        ) as error:
            return 409, {
                "ok": False,
                "code": "official_reconciliation_proof_failed",
                "error": str(error),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        if proof.proof_digest != data.get(
            "reconciliation_proof_digest"
        ):
            return 409, {
                "ok": False,
                "code": "official_reconciliation_proof_drift",
                "error": (
                    "official GET-only reconciliation proof changed"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        try:
            raw_result = adapter.reconcile_target_scoped_operation(
                request,
                proof,
            )
            result = TargetScopedOperationResult.from_value(raw_result)
        except (AttributeError, ModuleNotFoundError):
            return 503, {
                "ok": False,
                "code": "target_scoped_reconciliation_adapter_unavailable",
                "error": "channel GET-only reconcile seam is unavailable",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        except Exception as error:
            return 409, {
                "ok": False,
                "code": "official_reconciliation_failed",
                "error": (
                    "official GET-only reconciliation failed; "
                    f"durable state is unchanged ({type(error).__name__})"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        if (
            result.outcome != "SUCCEEDED"
            or result.external_reference != request.external_id
            or result.external_writes_performed != []
            or result.evidence.get("reconciliation_mode")
            != "official_get_only_durable_close"
        ):
            return 409, {
                "ok": False,
                "code": "official_reconciliation_not_exact",
                "error": (
                    "official GET-only evidence is not exact; "
                    "durable state remains unchanged"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        try:
            completed = store.record_target_scoped_reconciled_success(
                request=request,
                proof=proof,
                result=result,
            )
        except Exception as receipt_error:
            try:
                latest = store.get_target_scoped_operation(
                    run_id=base.run_id,
                    target_label=base.target_label,
                )
            except Exception as recovery_error:
                return 500, {
                    "ok": False,
                    "code": (
                        "target_scoped_reconciliation_durable_state_uncertain"
                    ),
                    "error": (
                        "local durable close and recovery read both failed"
                    ),
                    "receipt_error": type(receipt_error).__name__,
                    "recovery_error": type(recovery_error).__name__,
                    "durable_state_uncertain": True,
                    "external_writes_performed": [],
                    "state_mutations_performed": [
                        "unknown:local_durable_close"
                    ],
                }
            if (latest or {}).get("status") == "SUCCEEDED":
                return 200, {
                    "ok": True,
                    "idempotent": False,
                    "code": "target_scoped_reconciliation_succeeded",
                    "mode": request.reconciliation_mode,
                    "target_label": base.target_label,
                    "operation_status": "SUCCEEDED",
                    "durable_receipt_recovered": True,
                    "external_writes_performed": [],
                    "state_mutations_performed": [
                        "release_target_scoped_operation:SUCCEEDED",
                        "release_target:SUCCEEDED",
                        "release_run:refreshed",
                    ],
                    "run": store.get_run(base.run_id),
                }
            return 502, {
                "ok": False,
                "code": "target_scoped_reconciliation_close_failed",
                "error": (
                    "official GET matched, but local durable close failed; "
                    "retry the GET-only close"
                ),
                "receipt_error": type(receipt_error).__name__,
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        return 200, {
            "ok": True,
            "idempotent": False,
            "code": "target_scoped_reconciliation_succeeded",
            "mode": request.reconciliation_mode,
            "target_label": base.target_label,
            "operation_status": "SUCCEEDED",
            "summary": dict(proof.redacted_summary),
            "external_writes_performed": [],
            "state_mutations_performed": [
                "release_target_scoped_operation:SUCCEEDED",
                "release_target:SUCCEEDED",
                "release_run:refreshed",
            ],
            "run": completed,
        }


def _store_release_progress(run: object) -> dict:
    """Project storefront progress without counting preparation as publication."""

    targets = (
        list(run.get("targets") or ())
        if isinstance(run, dict)
        else []
    )
    storefront_targets = [
        row
        for row in targets
        if isinstance(row, dict)
        and row.get("target_label") != "miaoshou:COMMON"
    ]
    counts = {
        "storefront_total": len(storefront_targets),
        "published_verified": 0,
        "submitted_waiting_verification": 0,
        "draft_waiting_verification": 0,
        "draft_version_conflict": 0,
        "not_started": 0,
        "running": 0,
        "other_blocked": 0,
    }
    for target in storefront_targets:
        status = str(target.get("status") or "")
        if status in {"SUCCEEDED", "MANUALLY_VERIFIED"}:
            counts["published_verified"] += 1
            continue
        if status == "SUBMITTED_UNVERIFIED":
            counts["submitted_waiting_verification"] += 1
            continue
        if status == "PENDING" and int(target.get("attempts") or 0) == 0:
            counts["not_started"] += 1
            continue
        if status == "RUNNING":
            counts["running"] += 1
            continue
        evidence = (
            ((target.get("latest_failure_evidence") or {}).get("evidence"))
            or (
                ((target.get("failure_events") or [{}])[-1]).get("evidence")
                if isinstance(target.get("failure_events"), list)
                and target.get("failure_events")
                and isinstance((target.get("failure_events") or [{}])[-1], dict)
                else {}
            )
            or {}
        )
        writes = {
            str(value)
            for value in (
                evidence.get("external_writes_performed")
                if isinstance(evidence, dict)
                else ()
            )
            or ()
            if str(value)
        }
        draft_writes = {
            "miaoshou:tiktok_detail:create",
            "miaoshou:tiktok_shop:claim",
            "miaoshou:tiktok_detail:update",
        }
        publish_dispatched = (
            "miaoshou:tiktok_publish:submission" in writes
            or (
                isinstance(evidence, dict)
                and (
                    evidence.get("submission_accepted") is True
                    or evidence.get("publish_dispatched") is True
                )
            )
        )
        if (
            str(target.get("target_label") or "").startswith("tiktok:")
            and writes.intersection(draft_writes)
            and not publish_dispatched
        ):
            detail = str(target.get("error") or "").casefold()
            if (
                "产品数据发生变动" in str(target.get("error") or "")
                or "version" in detail
                or "conflict" in detail
            ):
                counts["draft_version_conflict"] += 1
            else:
                counts["draft_waiting_verification"] += 1
            continue
        counts["other_blocked"] += 1
    return {
        "schema_version": "storefront-release-progress/v1",
        **counts,
    }


def _release_plan_recovery_actions(
    dashboard: dict,
    blockers: list[str],
) -> list[dict[str, object]]:
    """Expose an existing safe next step whenever plan approval is blocked."""

    if not blockers:
        return []
    listing_copy = (
        dashboard.get("listing_copy")
        if isinstance(dashboard.get("listing_copy"), dict)
        else {}
    )
    normalized = [str(value or "").casefold() for value in blockers]
    if any("review_shopee_global_plan:" in value for value in normalized):
        return [
            {
                "code": "review_shopee_global_plan",
                "label": "核对并批准 Shopee 全球商品方案",
                "detail": (
                    "系统将重新读取当前官方候选；只有 Kyle 对当前精确"
                    "候选完成批准后，ReleasePlan 才会开放。"
                ),
                "next_codes": ["review_shopee_global_plan"],
                "marketplace_writes_performed": [],
            }
        ]
    stale_copy = (
        listing_copy.get("status") == "superseded_product_facts_changed"
        or any("listing copy input signature is stale" in value for value in normalized)
    )
    adoption_required = any(
        "listing copy must be adopted" in value for value in normalized
    )
    if stale_copy:
        return [
            {
                "code": "refresh_listing_copy",
                "label": "重新生成平台文案",
                "detail": (
                    "商品事实或所选规格在上次采用文案后发生了变化。"
                    "请按当前已批准事实重新生成候选，再由 Kyle 明确采用 EN MASTER。"
                ),
                "next_codes": ["refresh_listing_copy", "adopt_listing_copy"],
                "marketplace_writes_performed": [],
            }
        ]
    if adoption_required:
        return [
            {
                "code": "adopt_listing_copy",
                "label": "去采用当前 EN MASTER",
                "detail": (
                    "平台文案候选已经生成，但尚未绑定到当前商品事实。"
                    "采用后再重新核对并批准发布计划。"
                ),
                "next_codes": ["adopt_listing_copy"],
                "marketplace_writes_performed": [],
            }
        ]
    return [
        {
            "code": "refresh_release_state",
            "label": "重新检查并定位未完成步骤",
            "detail": (
                "系统会重新读取当前商品状态，并在相应区域保留具体阻断原因；"
                "不会批准、同步或发布。"
            ),
            "next_codes": ["refresh_release_state"],
            "marketplace_writes_performed": [],
        }
    ]


def _public_release_plan_projection(plan: object) -> object:
    """Remove server-internal execution facts from a public plan projection."""

    if not isinstance(plan, dict):
        return plan
    public = {
        key: value
        for key, value in plan.items()
        if key not in {"seller_sku", "sku_key", "payload"}
    }
    payload = plan.get("payload")
    if isinstance(payload, dict):
        safe_payload = {
            "product_revision": payload.get("product_revision"),
            "content_package_id": payload.get("content_package_id"),
            "targets": list(payload.get("targets") or ()),
        }
        approved = payload.get("approved_shopee_global_plan")
        if isinstance(approved, dict):
            safe_payload["approved_shopee_global_plan"] = dict(approved)
        category_decisions = payload.get(
            "approved_channel_category_decisions"
        )
        if isinstance(category_decisions, dict):
            safe_payload["approved_channel_category_decisions"] = {
                str(target): dict(binding)
                for target, binding in category_decisions.items()
                if isinstance(binding, dict)
            }
        public["payload"] = safe_payload
    return public


def _release_v1_view(dashboard: dict) -> dict:
    from modules.products.release_adapters import production_adapter_registry
    from shared_platform.release_store import default_release_store

    payload, blockers = _release_plan_payload_from_dashboard(dashboard)
    store = default_release_store()
    product_id = str(
        (dashboard.get("product") or {}).get("offer_id")
        or payload.get("product_id")
        or ""
    )

    def historical_view() -> dict | None:
        active = store.active_plan_for_product(product_id) if product_id else None
        if not active:
            return None
        historical_run = store.get_run(
            f"release-run:{active['payload_digest'][:24]}"
        )
        common = next(
            (
                row
                for row in ((historical_run or {}).get("targets") or ())
                if row.get("target_label") == "miaoshou:COMMON"
            ),
            None,
        )
        approved = bool(
            active.get("status") == "APPROVED"
            and (active.get("approval") or {}).get("status") == "APPROVED"
        )
        historical_predecessor_run = _release_predecessor_evidence_run(
            store,
            active,
        )
        target_recovery_actions = _release_target_recovery_actions(
            historical_run,
            predecessor_run=historical_predecessor_run,
            target_rows=(
                (dashboard.get("omnichannel_preview") or {}).get(
                    "targets"
                )
                or ()
            ),
        )
        return {
            "eligible_for_plan_approval": False,
            "blockers": blockers,
            "recovery_actions": _release_plan_recovery_actions(
                dashboard,
                blockers,
            ),
            "plan": _public_release_plan_projection(active),
            "plan_persisted": True,
            "plan_approved": approved,
            "run": historical_run,
            "storefront_progress": _store_release_progress(
                historical_run
            ),
            "common_overwrite_review": store.get_common_overwrite_review(
                active["plan_id"]
            ),
            "miaoshou_prepared": bool(
                common and common.get("status") == "SUCCEEDED"
            ),
            "adapter_blockers": [],
            "publish_ready": False,
            "target_recovery_actions": target_recovery_actions,
            "runnable_target_count": sum(
                action.get("runnable") is True
                for action in target_recovery_actions
            ),
            "historical": True,
        }

    if not payload.get("plan_id"):
        return historical_view() or {
            "eligible_for_plan_approval": False,
            "blockers": blockers,
            "recovery_actions": _release_plan_recovery_actions(
                dashboard,
                blockers,
            ),
            "plan": None,
            "run": None,
            "miaoshou_prepared": False,
            "publish_ready": False,
        }
    try:
        preview = store.preview_plan(payload)
    except (TypeError, ValueError) as error:
        blockers = list(dict.fromkeys([*blockers, str(error)]))
        return historical_view() or {
            "eligible_for_plan_approval": False,
            "blockers": blockers,
            "recovery_actions": _release_plan_recovery_actions(
                dashboard,
                blockers,
            ),
            "plan": None,
            "run": None,
            "miaoshou_prepared": False,
            "publish_ready": False,
        }
    persisted = store.get_plan(preview["plan_id"])
    plan = persisted or preview
    run = (
        store.get_run(f"release-run:{plan['payload_digest'][:24]}")
        if persisted
        else None
    )
    miaoshou_target = next(
        (
            row
            for row in ((run or {}).get("targets") or ())
            if row.get("target_label") == "miaoshou:COMMON"
        ),
        None,
    )
    adapter_blockers = [
        str(check.get("detail") or "渠道适配器未通过审计")
        for target in ((dashboard.get("omnichannel_preview") or {}).get("targets") or ())
        for check in (target.get("preflights") or ())
        if check.get("code") == "audited_adapter_site" and not check.get("passed")
    ]
    registry = production_adapter_registry()
    for target in ((dashboard.get("omnichannel_preview") or {}).get("targets") or ()):
        registration = registry.get(str(target.get("adapter") or ""))
        if not registration or not registration.executable:
            detail = (
                registration.blocker.detail
                if registration and registration.blocker
                else f"{target.get('channel')}:{target.get('site')} 尚无统一发布适配器"
            )
            adapter_blockers.append(str(detail))
    approved = bool(
        persisted
        and persisted.get("status") == "APPROVED"
        and (persisted.get("approval") or {}).get("status") == "APPROVED"
    )
    miaoshou_prepared = bool(
        miaoshou_target and miaoshou_target.get("status") == "SUCCEEDED"
    )
    common_evidence_blockers = (
        _verified_common_evidence_blockers(
            run,
            plan.get("payload") or {},
            store=store,
        )
        if approved
        else ["approved ReleasePlan is required"]
    )
    canonical_common_ready = bool(approved and not common_evidence_blockers)
    common_overwrite_review = (
        store.get_common_overwrite_review(plan["plan_id"])
        if persisted
        else None
    )
    predecessor_run = (
        _release_predecessor_evidence_run(store, plan)
        if persisted
        else None
    )
    target_recovery_actions = _release_target_recovery_actions(
        run,
        predecessor_run=predecessor_run,
        target_rows=(
            (dashboard.get("omnichannel_preview") or {}).get("targets")
            or ()
        ),
        registry=registry,
    )
    return {
        "eligible_for_plan_approval": not blockers,
        "blockers": blockers,
        "recovery_actions": _release_plan_recovery_actions(
            dashboard,
            blockers,
        ),
        "plan": _public_release_plan_projection(plan),
        "plan_persisted": bool(persisted),
        "plan_approved": approved,
        "run": run,
        "storefront_progress": _store_release_progress(run),
        "common_overwrite_review": common_overwrite_review,
        "miaoshou_prepared": miaoshou_prepared,
        "canonical_common_ready": canonical_common_ready,
        "common_evidence_blockers": common_evidence_blockers,
        "release_preflight_authority": (
            "canonical_common_readback"
            if canonical_common_ready
            else "pre_common_release_gate"
        ),
        "adapter_blockers": list(dict.fromkeys(adapter_blockers)),
        "target_recovery_actions": target_recovery_actions,
        "runnable_target_count": sum(
            action.get("runnable") is True
            for action in target_recovery_actions
        ),
        "publish_ready": bool(
            canonical_common_ready
            and not adapter_blockers
        ),
    }


def _approve_product_workspace_locally(data: dict) -> tuple[int, dict]:
    """Serialize local SKU reservation, preview, revision check, and save."""

    with _product_approval_lock:
        return _approve_product_workspace_locally_locked(data)


def _approve_product_workspace_locally_locked(data: dict) -> tuple[int, dict]:
    """Persist one explicit, revision-checked local product approval.

    The governed dashboard performs the current content-package validation and
    read-only catalog uniqueness check through
    ``preview_product_approval_lock``.  This function only applies the exact
    review lock and approval fact returned by that gate.
    """
    from modules.sourcing import new_product_workbench as np_mod
    from shared_platform import release_control

    offer_id = str(data.get("offer_id") or "").strip()
    requested_seller_sku = str(data.get("seller_sku") or "").strip()
    approved_by = str(data.get("approved_by") or "").strip()
    expected_revision = data.get("expected_revision")
    if not offer_id.isdigit() or not 1 <= len(offer_id) <= 32:
        return 400, {"ok": False, "error": "offer_id must contain 1-32 digits"}
    if (
        requested_seller_sku
        and (
            not requested_seller_sku.isdigit()
            or not 1 <= len(requested_seller_sku) <= 32
        )
    ):
        return 400, {"ok": False, "error": "seller_sku must contain 1-32 digits"}
    if data.get("user_approved") is not True:
        return 400, {
            "ok": False,
            "error": "explicit user_approved=true is required",
        }
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        return 400, {
            "ok": False,
            "error": "expected_revision must be a non-negative integer",
        }
    if approved_by != "Kyle":
        return 400, {
            "ok": False,
            "error": "approved_by must be Kyle for this local approval surface",
        }

    try:
        state = np_mod.load_state(offer_id)
    except (FileNotFoundError, ValueError) as error:
        return 404, {"ok": False, "error": str(error)}
    current_revision = int(state.get("_revision") or 0)
    if current_revision != expected_revision:
        return 409, {
            "ok": False,
            "error": "state revision is stale",
            "current_revision": current_revision,
        }

    try:
        dashboard = release_control.build_release_dashboard(
            offer_id=offer_id,
        )
    except FileNotFoundError as error:
        return 404, {"ok": False, "error": str(error)}
    except (TypeError, ValueError) as error:
        return 400, {"ok": False, "error": str(error)}
    except Exception as error:
        return 500, {"ok": False, "error": str(error)}

    dashboard_revision = int((dashboard.get("product") or {}).get("revision") or 0)
    if dashboard_revision != expected_revision:
        return 409, {
            "ok": False,
            "error": "state revision is stale",
            "current_revision": dashboard_revision,
        }
    seller_sku = str(
        (dashboard.get("product") or {}).get("seller_sku_candidate") or ""
    ).strip()
    if not seller_sku:
        return 409, {
            "ok": False,
            "error": "automatic Seller SKU allocation did not return a candidate",
        }
    if requested_seller_sku and requested_seller_sku != seller_sku:
        return 409, {
            "ok": False,
            "error": "automatic Seller SKU candidate changed; refresh before approval",
            "seller_sku": seller_sku,
        }
    preview = dashboard.get("approval_rehearsal") or dashboard.get("approval") or {}
    preview_patch = preview.get("state_patch_preview") or {}
    proposed_approval = preview_patch.get("product_approval") or {}
    approval_warnings = [
        str(value).strip()
        for value in (preview.get("warnings") or ())
        if str(value).strip()
    ]
    if not bool(preview.get("ready")) or not proposed_approval:
        return 409, {
            "ok": False,
            "error": "product approval preview is not ready",
            "blockers": list(preview.get("blockers") or ()),
        }

    current_product = dashboard.get("product") or {}
    if (
        bool(current_product.get("actual_product_approved"))
        and bool((state.get("review") or {}).get("fields_locked"))
        and str((state.get("review") or {}).get("seller_sku") or "") == seller_sku
    ):
        return 200, {
            "ok": True,
            "idempotent": True,
            "persisted": False,
            "external_writes_performed": [],
            "dashboard": _product_workspace_view(dashboard),
        }

    fingerprint = str(proposed_approval.get("input_fingerprint") or "").strip()
    if not fingerprint:
        return 409, {
            "ok": False,
            "error": "approval preview is missing its input fingerprint",
        }
    approved_at = datetime.now(timezone.utc).isoformat()
    approval_id = (
        f"product-approval:{offer_id}:{seller_sku}:{fingerprint[:16]}"
    )
    product_approval = {
        **dict(proposed_approval),
        "approval_id": approval_id,
        "package_id": f"product:{offer_id}:{seller_sku}",
        "status": "approved",
        "subject_type": "product",
        "subject_id": offer_id,
        "seller_sku": seller_sku,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "approval_warnings_acknowledged": approval_warnings,
        "source_reference": (
            f"workbench:{offer_id}:revision:{expected_revision}"
        ),
    }

    next_state = dict(state)
    review = state.get("review")
    if not isinstance(review, dict):
        return 409, {"ok": False, "error": "state review must be a mapping"}
    next_review = dict(review)
    next_review["seller_sku"] = seller_sku
    next_review["fields_locked"] = True
    next_state["review"] = next_review
    next_state["product_approval"] = product_approval
    try:
        np_mod.save_state(offer_id, next_state)
    except RuntimeError:
        latest = np_mod.load_state(offer_id)
        return 409, {
            "ok": False,
            "error": "state revision is stale",
            "current_revision": int(latest.get("_revision") or 0),
        }

    try:
        updated = release_control.build_release_dashboard(
            offer_id=offer_id,
        )
    except Exception as error:
        return 500, {
            "ok": False,
            "error": f"approval was saved but dashboard refresh failed: {error}",
            "approval_id": approval_id,
        }
    return 200, {
        "ok": True,
        "idempotent": False,
        "persisted": True,
        "approval_id": approval_id,
        "approval_warnings_acknowledged": approval_warnings,
        "external_writes_performed": [],
        "dashboard": _product_workspace_view(updated),
    }


def _release_dashboard_for_request(data: dict) -> tuple[dict | None, tuple[int, dict] | None]:
    from shared_platform import release_control

    offer_id = str(data.get("offer_id") or "").strip()
    requested_seller_sku = str(data.get("seller_sku") or "").strip()
    targets = data.get("publication_targets")
    if not offer_id.isdigit() or not 1 <= len(offer_id) <= 32:
        return None, (400, {"ok": False, "error": "offer_id must contain 1-32 digits"})
    if (
        requested_seller_sku
        and (
            not requested_seller_sku.isdigit()
            or not 1 <= len(requested_seller_sku) <= 32
        )
    ):
        return None, (400, {"ok": False, "error": "seller_sku must contain 1-32 digits"})
    if isinstance(targets, (str, bytes)) or not isinstance(targets, list):
        return None, (
            400,
            {"ok": False, "error": "publication_targets must be a list"},
        )
    try:
        dashboard = release_control.build_release_dashboard(
            offer_id=offer_id,
            publication_targets=targets,
        )
    except FileNotFoundError as error:
        return None, (404, {"ok": False, "error": str(error)})
    except (TypeError, ValueError) as error:
        return None, (400, {"ok": False, "error": str(error)})
    except Exception as error:
        return None, (500, {"ok": False, "error": str(error)})
    current_seller_sku = str(
        (dashboard.get("product") or {}).get("seller_sku_candidate") or ""
    ).strip()
    if requested_seller_sku and requested_seller_sku != current_seller_sku:
        return None, (
            409,
            {
                "ok": False,
                "error": "automatic Seller SKU candidate changed; refresh before continuing",
                "seller_sku": current_seller_sku,
            },
        )
    return dashboard, None


def _reconcile_existing_shopee_target_readonly(
    *,
    offer_id: str,
    target_label: str,
) -> tuple[int, dict]:
    """Prove one existing Shopee result without exposing release authority.

    The client supplies only public target identity. The server resolves every
    authorization field from the current approved immutable plan and durable
    run, while the adapter repeats the same validation before official GET
    readback. No run or target result is recorded by this endpoint.
    """

    from domains.channel_operations.release_executor import AdapterExecutionRequest
    from modules.products.release_adapters import (
        reconcile_existing_shopee_target,
    )
    from shared_platform.release_store import default_release_store

    clean_offer_id = str(offer_id or "").strip()
    clean_target = str(target_label or "").strip()
    if not clean_offer_id.isdigit() or not 1 <= len(clean_offer_id) <= 32:
        return 400, {
            "ok": False,
            "error": "offer_id must contain 1-32 digits",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    if clean_target not in _READONLY_SHOPEE_RECONCILE_TARGETS:
        return 400, {
            "ok": False,
            "error": "read-only reconciliation target is not allowed",
            "allowed_targets": sorted(_READONLY_SHOPEE_RECONCILE_TARGETS),
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }

    store = default_release_store()
    plan = store.active_plan_for_product(clean_offer_id)
    approval = (plan or {}).get("approval") or {}
    payload = (plan or {}).get("payload") or {}
    plan_targets = list((plan or {}).get("targets") or ())
    payload_targets = list(payload.get("targets") or ())
    scope_digest = str(payload.get("omnichannel_scope_digest") or "").strip()
    plan_is_current_and_approved = bool(
        plan
        and plan.get("status") == "APPROVED"
        and str(plan.get("product_id") or "") == clean_offer_id
        and str(payload.get("product_id") or "") == clean_offer_id
        and str(payload.get("plan_id") or "") == str(plan.get("plan_id") or "")
        and clean_target in plan_targets
        and clean_target in payload_targets
        and approval.get("status") == "APPROVED"
        and str(approval.get("approved_by") or "") == "Kyle"
        and bool(approval.get("user_approved"))
        and str(approval.get("plan_id") or "") == str(plan.get("plan_id") or "")
        and str(approval.get("payload_digest") or "")
        == str(plan.get("payload_digest") or "")
        and str(approval.get("confirmation_token") or "")
        == str(plan.get("confirmation_token") or "")
        and scope_digest
    )
    if not plan_is_current_and_approved:
        return 409, {
            "ok": False,
            "error": (
                "current approved immutable ReleasePlan does not authorize "
                "this read-only reconciliation"
            ),
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }

    assert plan is not None
    run_id = f"release-run:{str(plan['payload_digest'])[:24]}"
    run = store.get_run(run_id)
    if (
        not run
        or str(run.get("run_id") or "") != run_id
        or str(run.get("plan_id") or "") != str(plan.get("plan_id") or "")
        or str(run.get("approval_id") or "") != str(approval.get("approval_id") or "")
    ):
        return 409, {
            "ok": False,
            "error": "durable release run does not match the approved plan",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    matching_targets = [
        row
        for row in (run.get("targets") or ())
        if str(row.get("target_label") or "") == clean_target
    ]
    if len(matching_targets) != 1:
        return 409, {
            "ok": False,
            "error": "durable release target identity is missing or ambiguous",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    target = matching_targets[0]
    if str(target.get("status") or "") != "FAILED":
        return 409, {
            "ok": False,
            "error": "read-only reconciliation requires a FAILED durable target",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    external_id = str(target.get("external_id") or "").strip()
    failure_evidence_present = bool(
        target.get("latest_failure_evidence")
        or target.get("failure_events")
    )
    if not external_id:
        return 409, {
            "ok": False,
            "error": "read-only reconciliation requires the recorded external_id",
            "failure_evidence_present": failure_evidence_present,
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    idempotency_key = str(target.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return 409, {
            "ok": False,
            "error": "durable release target idempotency identity is missing",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }

    channel, site = clean_target.split(":", 1)
    request = AdapterExecutionRequest(
        plan_id=str(plan["plan_id"]),
        confirmation_token=str(plan["confirmation_token"]),
        approval_scope_digest=scope_digest,
        product_id=str(plan["product_id"]),
        seller_sku=str(plan["seller_sku"]),
        product_package_id=str(plan["product_package_id"]),
        content_package_id=str(plan["content_package_id"]),
        channel=channel,
        site=site,
        target_label=clean_target,
        idempotency_key=idempotency_key,
    )
    try:
        result = reconcile_existing_shopee_target(request)
    except RuntimeError:
        return 409, {
            "ok": False,
            "error": "official read-only Shopee reconciliation is unavailable",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    except Exception:
        return 502, {
            "ok": False,
            "error": "official read-only Shopee reconciliation failed",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }

    evidence = (
        dict(result.readback_evidence)
        if isinstance(result.readback_evidence, dict)
        else {}
    )
    checks = evidence.get("checks")
    exact_checks = bool(
        isinstance(checks, dict)
        and set(checks) == _READONLY_SHOPEE_RECONCILE_CHECKS
        and all(isinstance(value, bool) for value in checks.values())
    )
    evidence_verified = bool(evidence.get("verified"))
    computed_verified = bool(exact_checks and all(checks.values()))
    evidence_is_exact = bool(
        exact_checks
        and evidence.get("source") == "official_shopee_partner_api"
        and evidence.get("authentication_mode") == "existing_token_only"
        and evidence.get("reconciliation_mode") == "read_only_existing_item"
        and str(evidence.get("region") or "") == site
        and str(evidence.get("item_id") or "") == external_id
        and list(evidence.get("external_writes_performed") or ()) == []
        and str(result.external_reference or "") == external_id
        and evidence_verified == computed_verified
        and bool(result.succeeded) == computed_verified
        and bool(result.readback_verified) == computed_verified
    )
    if not evidence_is_exact:
        return 502, {
            "ok": False,
            "error": "official readback evidence did not satisfy the exact contract",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }

    logistics = list(evidence.get("logistics") or ())
    disabled_logistics = list(evidence.get("disabled_logistics") or ())
    return 200, {
        "ok": True,
        "mode": "read_only_existing_target",
        "verified": computed_verified,
        "plan_id": str(plan["plan_id"]),
        "run_id": run_id,
        "target_label": clean_target,
        "external_id": external_id,
        "failure_evidence_present": failure_evidence_present,
        "detail": str(result.detail or ""),
        "evidence": {
            "source": evidence["source"],
            "authentication_mode": evidence["authentication_mode"],
            "reconciliation_mode": evidence["reconciliation_mode"],
            "region": site,
            "item_id": external_id,
            "checks": dict(checks),
            "description_length": int(evidence.get("description_length") or 0),
            "image_count": int(evidence.get("image_count") or 0),
            "logistics_count": len(logistics),
            "disabled_logistics_count": len(disabled_logistics),
            "listing_status": str(evidence.get("status") or ""),
            "price_issues": [
                str(value)
                for value in (evidence.get("price_issues") or ())
                if str(value)
            ],
        },
        "external_writes_performed": [],
        "state_mutations_performed": [],
    }


def _shopee_price_repair_status_view(
    run: dict | None,
    *,
    target_label: str,
) -> dict:
    """Expose only non-sensitive repair lifecycle fields."""

    target = next(
        (
            row
            for row in ((run or {}).get("targets") or ())
            if str(row.get("target_label") or "") == target_label
        ),
        {},
    )
    repair = (
        target.get("repair")
        if isinstance(target.get("repair"), dict)
        else {}
    )
    return {
        "run_status": str((run or {}).get("status") or ""),
        "target": {
            "target_label": target_label,
            "status": str(target.get("status") or ""),
            "attempts": int(target.get("attempts") or 0),
            "repair_status": str(repair.get("status") or ""),
        },
    }


def _preview_existing_shopee_target_price(
    *,
    offer_id: str,
    target_label: str,
) -> tuple[int, dict]:
    """Return a sanitized, no-write confirmation identity for one repair."""

    from domains.channel_operations.release_executor import AdapterExecutionRequest
    from modules.products.release_adapters import preflight_shopee_price_repair
    from shared_platform.release_store import default_release_store

    clean_offer_id = str(offer_id or "").strip()
    clean_target = str(target_label or "").strip()
    if (
        not clean_offer_id.isdigit()
        or not 1 <= len(clean_offer_id) <= 32
        or clean_target not in _READONLY_SHOPEE_RECONCILE_TARGETS
    ):
        return 400, {
            "ok": False,
            "error": "valid offer_id and Shopee PH/TH target are required",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    store = default_release_store()
    with _release_execution_lock:
        plan = store.active_plan_for_product(clean_offer_id)
        if not plan:
            return 409, {
                "ok": False,
                "error": "current approved immutable ReleasePlan was not found",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        request_data = {
            "offer_id": clean_offer_id,
            "seller_sku": str(plan.get("seller_sku") or ""),
            "publication_targets": list(plan.get("targets") or ()),
            "plan_id": str(plan.get("plan_id") or ""),
            "confirmation_token": str(plan.get("confirmation_token") or ""),
        }
        gate, failure = _release_execution_readonly_gate(
            request_data,
            store=store,
        )
        if failure:
            return failure
        assert gate is not None
        dashboard = gate["dashboard"]
        plan = gate["plan"]
        run = gate["run"]
        if not plan or not run:
            return 409, {
                "ok": False,
                "error": "current release execution identity is incomplete",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        revision = int((dashboard.get("product") or {}).get("revision") or 0)
        if (
            revision < 1
            or int((plan.get("payload") or {}).get("product_revision") or 0)
            != revision
        ):
            return 409, {
                "ok": False,
                "error": "current product revision differs from immutable plan",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        matches = [
            row
            for row in (run.get("targets") or ())
            if str(row.get("target_label") or "") == clean_target
        ]
        if len(matches) != 1:
            return 409, {
                "ok": False,
                "error": "price repair target identity is missing or ambiguous",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        target = matches[0]
        if target.get("repair"):
            return 409, {
                "ok": False,
                "error": "price repair was already claimed",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        if (
            target.get("status") != "FAILED"
            or not str(target.get("external_id") or "").strip()
            or not str(target.get("idempotency_key") or "").strip()
        ):
            return 409, {
                "ok": False,
                "error": "price repair requires one exact FAILED external item",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        channel, site = clean_target.split(":", 1)
        request = AdapterExecutionRequest(
            plan_id=str(plan["plan_id"]),
            confirmation_token=str(plan["confirmation_token"]),
            approval_scope_digest=str(
                (plan.get("payload") or {}).get("omnichannel_scope_digest")
                or ""
            ),
            product_id=str(plan["product_id"]),
            seller_sku=str(plan["seller_sku"]),
            product_package_id=str(plan["product_package_id"]),
            content_package_id=str(plan["content_package_id"]),
            channel=channel,
            site=site,
            target_label=clean_target,
            idempotency_key=str(target["idempotency_key"]),
        )
        try:
            preview = preflight_shopee_price_repair(request)
        except Exception as error:
            return 409, {
                "ok": False,
                "error": f"Shopee price repair preflight blocked: {error}",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        return 200, {
            "ok": True,
            "repair_allowed": True,
            "plan_id": str(plan["plan_id"]),
            "target_label": clean_target,
            "expected_revision": revision,
            "payload_digest": str(plan["payload_digest"]),
            "preflight_digest": str(
                preview["operation"]["preflight_digest"]
            ),
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }


def _preview_shopee_price_reconciliation(
    *,
    offer_id: str,
    target_label: str,
) -> tuple[int, dict]:
    """Return the exact durable identity for a later GET-only close POST."""

    from shared_platform.release_store import default_release_store

    clean_offer_id = str(offer_id or "").strip()
    clean_target = str(target_label or "").strip()
    if (
        not clean_offer_id.isdigit()
        or not 1 <= len(clean_offer_id) <= 32
        or clean_target not in _READONLY_SHOPEE_RECONCILE_TARGETS
    ):
        return 400, {
            "ok": False,
            "error": "valid offer_id and Shopee PH/TH target are required",
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }
    store = default_release_store()
    with _release_execution_lock:
        plan = store.active_plan_for_product(clean_offer_id)
        if not plan:
            return 409, {
                "ok": False,
                "error": "current approved immutable ReleasePlan was not found",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        request_data = {
            "offer_id": clean_offer_id,
            "seller_sku": str(plan.get("seller_sku") or ""),
            "publication_targets": list(plan.get("targets") or ()),
            "plan_id": str(plan.get("plan_id") or ""),
            "confirmation_token": str(plan.get("confirmation_token") or ""),
        }
        gate, failure = _release_execution_readonly_gate(
            request_data,
            store=store,
        )
        if failure:
            return failure
        assert gate is not None
        dashboard = gate["dashboard"]
        plan = gate["plan"]
        run = gate["run"]
        if not plan or not run:
            return 409, {
                "ok": False,
                "error": "current release execution identity is incomplete",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        revision = int((dashboard.get("product") or {}).get("revision") or 0)
        if (
            revision < 1
            or int((plan.get("payload") or {}).get("product_revision") or 0)
            != revision
        ):
            return 409, {
                "ok": False,
                "error": "current product revision differs from immutable plan",
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        target = next(
            (
                row
                for row in (run.get("targets") or ())
                if str(row.get("target_label") or "") == clean_target
            ),
            None,
        )
        repair = (target or {}).get("repair") or {}
        if (
            not target
            or target.get("status") != "RECONCILIATION_REQUIRED"
            or repair.get("status") != "RECONCILIATION_REQUIRED"
        ):
            return 409, {
                "ok": False,
                "error": (
                    "GET-only close requires one exact "
                    "RECONCILIATION_REQUIRED repair"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        context = store.target_repair_reconciliation_context(
            run_id=str(run["run_id"]),
            target_label=clean_target,
            plan_id=str(plan["plan_id"]),
            expected_revision=revision,
            payload_digest=str(plan["payload_digest"]),
            preflight_digest="",
        )
        if not context:
            return 409, {
                "ok": False,
                "error": (
                    "durable repair identity or truthful prior write "
                    "evidence is incomplete"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        return 200, {
            "ok": True,
            "reconciliation_allowed": True,
            "mode": "official_get_only_durable_close",
            "plan_id": str(plan["plan_id"]),
            "target_label": clean_target,
            "expected_revision": revision,
            "payload_digest": str(plan["payload_digest"]),
            "preflight_digest": str(
                context["operation"].get("preflight_digest") or ""
            ),
            "operation_digest": str(context["operation_digest"]),
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }


def _reconcile_existing_shopee_price_repair(
    data: dict,
) -> tuple[int, dict]:
    """GET-only official readback followed by one local durable close."""

    from domains.channel_operations.release_executor import AdapterExecutionRequest
    from modules.products.release_adapters import reconcile_shopee_price_repair
    from shared_platform.release_store import (
        ReleaseAuthorizationError,
        ReleaseStoreError,
        default_release_store,
    )

    if (
        data.get("confirm_shopee_price_reconciliation") is not True
        or data.get("approved_by") != "Kyle"
    ):
        return 400, {
            "ok": False,
            "error": (
                "price reconciliation requires "
                "confirm_shopee_price_reconciliation=true "
                "and approved_by=Kyle"
            ),
            "external_writes_performed": [],
        }
    plan_id = str(data.get("plan_id") or "").strip()
    token = str(data.get("confirmation_token") or "").strip()
    payload_digest = str(data.get("payload_digest") or "").strip()
    preflight_digest = str(data.get("preflight_digest") or "").strip()
    operation_digest = str(data.get("operation_digest") or "").strip()
    offer_id = str(data.get("offer_id") or "").strip()
    target_label = str(data.get("target_label") or "").strip()
    try:
        expected_revision = int(data.get("expected_revision"))
    except (TypeError, ValueError):
        expected_revision = 0
    if (
        not offer_id.isdigit()
        or not 1 <= len(offer_id) <= 32
        or target_label not in _READONLY_SHOPEE_RECONCILE_TARGETS
        or expected_revision < 1
        or not all(
            (
                plan_id,
                token,
                payload_digest,
                preflight_digest,
                operation_digest,
            )
        )
    ):
        return 400, {
            "ok": False,
            "error": (
                "exact offer, target, plan, token, revision, payload, "
                "preflight and operation identity are required"
            ),
            "external_writes_performed": [],
        }

    store = default_release_store()
    with _release_execution_lock, _product_workbench_lock(offer_id):
        gate, failure = _release_execution_readonly_gate(data, store=store)
        if failure:
            status, response = failure
            return status, {
                "ok": False,
                "error": str(
                    response.get("error")
                    or "Shopee price reconciliation gate is blocked"
                ),
                "blockers": list(response.get("blockers") or ()),
                "adapter_blockers": list(
                    response.get("adapter_blockers") or ()
                ),
                "external_writes_performed": [],
            }
        assert gate is not None
        dashboard = gate["dashboard"]
        plan = gate["plan"]
        run = gate["run"]
        payload = gate["payload"]
        if (
            not plan
            or not run
            or str(plan.get("plan_id") or "") != plan_id
            or str(plan.get("confirmation_token") or "") != token
            or str(plan.get("payload_digest") or "") != payload_digest
            or int((dashboard.get("product") or {}).get("revision") or 0)
            != expected_revision
            or int((plan.get("payload") or {}).get("product_revision") or 0)
            != expected_revision
        ):
            return 409, {
                "ok": False,
                "error": "price reconciliation plan/token/revision is stale",
                "external_writes_performed": [],
            }
        approval = plan.get("approval") or {}
        if (
            approval.get("status") != "APPROVED"
            or approval.get("approved_by") != "Kyle"
            or approval.get("user_approved") is not True
        ):
            return 409, {
                "ok": False,
                "error": (
                    "price reconciliation requires the active Kyle approval"
                ),
                "external_writes_performed": [],
            }
        matches = [
            row
            for row in (run.get("targets") or ())
            if str(row.get("target_label") or "") == target_label
        ]
        if len(matches) != 1:
            return 409, {
                "ok": False,
                "error": (
                    "price reconciliation target identity is missing "
                    "or ambiguous"
                ),
                "external_writes_performed": [],
            }
        target = matches[0]
        repair = target.get("repair") or {}
        confirmation = store.target_repair_confirmation_matches(
            run_id=str(run["run_id"]),
            target_label=target_label,
            plan_id=plan_id,
            expected_revision=expected_revision,
            payload_digest=payload_digest,
            preflight_digest=preflight_digest,
        )
        if (
            not confirmation
            or confirmation.get("matches") is not True
            or confirmation.get("operation_digest") != operation_digest
        ):
            return 409, {
                "ok": False,
                "error": "price reconciliation confirmation identity changed",
                "external_writes_performed": [],
            }
        if repair.get("status") == "SUCCEEDED":
            return 200, {
                "ok": True,
                "idempotent": True,
                "target": target_label,
                "external_writes_performed": [],
                "state_mutations_performed": [],
                "repair_status": _shopee_price_repair_status_view(
                    run,
                    target_label=target_label,
                ),
            }
        if (
            target.get("status") != "RECONCILIATION_REQUIRED"
            or repair.get("status") != "RECONCILIATION_REQUIRED"
        ):
            return 409, {
                "ok": False,
                "error": (
                    "price reconciliation requires one exact "
                    "RECONCILIATION_REQUIRED repair"
                ),
                "external_writes_performed": [],
            }
        context = store.target_repair_reconciliation_context(
            run_id=str(run["run_id"]),
            target_label=target_label,
            plan_id=plan_id,
            expected_revision=expected_revision,
            payload_digest=payload_digest,
            preflight_digest=preflight_digest,
            operation_digest=operation_digest,
        )
        if not context:
            return 409, {
                "ok": False,
                "error": (
                    "truthful prior write or immutable operation "
                    "identity is incomplete"
                ),
                "external_writes_performed": [],
            }
        channel, site = target_label.split(":", 1)
        request = AdapterExecutionRequest(
            plan_id=plan_id,
            confirmation_token=token,
            approval_scope_digest=str(
                (plan.get("payload") or {}).get(
                    "omnichannel_scope_digest"
                )
                or payload.get("omnichannel_scope_digest")
                or ""
            ),
            product_id=str(plan["product_id"]),
            seller_sku=str(plan["seller_sku"]),
            product_package_id=str(plan["product_package_id"]),
            content_package_id=str(plan["content_package_id"]),
            channel=channel,
            site=site,
            target_label=target_label,
            idempotency_key=str(target.get("idempotency_key") or ""),
        )
        try:
            result = reconcile_shopee_price_repair(
                request,
                operation=dict(context["operation"]),
            )
        except Exception as error:
            return 409, {
                "ok": False,
                "error": (
                    "official GET-only price reconciliation failed; "
                    f"durable state is unchanged ({type(error).__name__})"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        evidence = dict(result.readback_evidence or {})
        if (
            not result.succeeded
            or not result.readback_verified
            or evidence.get("external_writes_performed") != []
        ):
            return 409, {
                "ok": False,
                "error": (
                    "official GET-only readback is not exact; "
                    "durable state remains unchanged"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        try:
            completed = store.record_target_repair_reconciled_success(
                operation_digest,
                readback_evidence=evidence,
            )
        except (
            ReleaseAuthorizationError,
            ReleaseStoreError,
            ValueError,
        ) as error:
            return 409, {
                "ok": False,
                "error": str(error),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        except Exception as error:
            return 502, {
                "ok": False,
                "error": (
                    "official GET matched, but the local durable close "
                    f"failed ({type(error).__name__}); retry GET-only close"
                ),
                "external_writes_performed": [],
                "state_mutations_performed": [],
            }
        return 200, {
            "ok": True,
            "idempotent": False,
            "target": target_label,
            "write_status": "verified",
            "listing_price_verified": True,
            "derived_price_status": str(
                evidence.get("derived_price_status") or "warning"
            ),
            "profit_status": str(
                evidence.get("profit_status") or "unverified"
            ),
            "external_writes_performed": [],
            "state_mutations_performed": [
                "release_target_repair:SUCCEEDED",
                "release_target:SUCCEEDED",
                "release_run:refreshed",
            ],
            "repair_status": _shopee_price_repair_status_view(
                completed,
                target_label=target_label,
            ),
        }


def _repair_existing_shopee_target_price(data: dict) -> tuple[int, dict]:
    """Run one governed PH/TH original-price repair with no retry window."""

    from domains.channel_operations.release_executor import AdapterExecutionRequest
    from modules.products.release_adapters import (
        ReleaseAdapterWriteVerificationError,
        execute_shopee_price_repair,
        preflight_shopee_price_repair,
    )
    from shared_platform.release_store import (
        ImmutableReleaseError,
        ReleaseAuthorizationError,
        ReleaseStoreError,
        default_release_store,
    )

    if (
        data.get("confirm_shopee_price_repair") is not True
        or data.get("approved_by") != "Kyle"
    ):
        return 400, {
            "ok": False,
            "error": (
                "price repair requires "
                "confirm_shopee_price_repair=true and approved_by=Kyle"
            ),
            "external_writes_performed": [],
        }
    plan_id = str(data.get("plan_id") or "").strip()
    token = str(data.get("confirmation_token") or "").strip()
    requested_payload_digest = str(data.get("payload_digest") or "").strip()
    requested_preflight_digest = str(
        data.get("preflight_digest") or ""
    ).strip()
    offer_id = str(data.get("offer_id") or "").strip()
    target_label = str(data.get("target_label") or "").strip()
    if not offer_id.isdigit() or not 1 <= len(offer_id) <= 32:
        return 400, {
            "ok": False,
            "error": "offer_id must contain 1-32 digits",
            "external_writes_performed": [],
        }
    if target_label not in _READONLY_SHOPEE_RECONCILE_TARGETS:
        return 400, {
            "ok": False,
            "error": "Shopee price repair target is not allowed",
            "allowed_targets": sorted(_READONLY_SHOPEE_RECONCILE_TARGETS),
            "external_writes_performed": [],
        }
    try:
        expected_revision = int(data.get("expected_revision"))
    except (TypeError, ValueError):
        expected_revision = 0
    if (
        expected_revision < 1
        or not plan_id
        or not token
        or not requested_payload_digest
        or not requested_preflight_digest
    ):
        return 400, {
            "ok": False,
            "error": (
                "exact plan_id, confirmation_token, expected_revision, "
                "payload_digest and preflight_digest are required"
            ),
            "external_writes_performed": [],
        }

    store = default_release_store()
    with _release_execution_lock, _product_workbench_lock(offer_id):
        gate, failure = _release_execution_readonly_gate(data, store=store)
        if failure:
            status, response = failure
            return status, {
                "ok": False,
                "error": str(
                    response.get("error")
                    or "Shopee price repair execution gate is blocked"
                ),
                "blockers": [
                    str(value)
                    for value in (response.get("blockers") or ())
                ],
                "adapter_blockers": [
                    {
                        "target": str(row.get("target") or ""),
                        "code": str(row.get("code") or ""),
                        "detail": str(row.get("detail") or ""),
                    }
                    for row in (response.get("adapter_blockers") or ())
                    if isinstance(row, dict)
                ],
                "external_writes_performed": [],
            }
        assert gate is not None
        dashboard = gate["dashboard"]
        plan = gate["plan"]
        run = gate["run"]
        payload = gate["payload"]
        if (
            not plan
            or not run
            or str(plan.get("plan_id") or "") != plan_id
            or str(plan.get("confirmation_token") or "") != token
            or str(plan.get("payload_digest") or "")
            != requested_payload_digest
            or int((dashboard.get("product") or {}).get("revision") or 0)
            != expected_revision
            or int((plan.get("payload") or {}).get("product_revision") or 0)
            != expected_revision
        ):
            return 409, {
                "ok": False,
                "error": "price repair plan/token/revision is stale",
                "external_writes_performed": [],
            }
        approval = plan.get("approval") or {}
        if (
            approval.get("status") != "APPROVED"
            or approval.get("approved_by") != "Kyle"
            or approval.get("user_approved") is not True
        ):
            return 409, {
                "ok": False,
                "error": "price repair requires the active Kyle approval",
                "external_writes_performed": [],
            }
        matches = [
            row
            for row in (run.get("targets") or ())
            if str(row.get("target_label") or "") == target_label
        ]
        if len(matches) != 1:
            return 409, {
                "ok": False,
                "error": "price repair target identity is missing or ambiguous",
                "external_writes_performed": [],
            }
        target = matches[0]
        if target.get("repair"):
            repair = target["repair"]
            repeat = store.target_repair_confirmation_matches(
                run_id=str(run["run_id"]),
                target_label=target_label,
                plan_id=plan_id,
                expected_revision=expected_revision,
                payload_digest=requested_payload_digest,
                preflight_digest=requested_preflight_digest,
            )
            if not repeat or repeat.get("matches") is not True:
                return 409, {
                    "ok": False,
                    "error": "price repair confirmation identity does not match",
                    "external_writes_performed": [],
                    "repair_status": _shopee_price_repair_status_view(
                        run,
                        target_label=target_label,
                    ),
                }
            if repair.get("status") == "SUCCEEDED":
                return 200, {
                    "ok": True,
                    "idempotent": True,
                    "target": target_label,
                    "external_writes_performed": [],
                    "repair_status": _shopee_price_repair_status_view(
                        run,
                        target_label=target_label,
                    ),
                }
            return 409, {
                "ok": False,
                "error": (
                    "price repair is already running or requires reconciliation"
                ),
                "external_writes_performed": [],
                "repair_status": _shopee_price_repair_status_view(
                    run,
                    target_label=target_label,
                ),
            }
        external_id = str(target.get("external_id") or "").strip()
        idempotency_key = str(target.get("idempotency_key") or "").strip()
        if (
            target.get("status") != "FAILED"
            or not external_id
            or not idempotency_key
        ):
            return 409, {
                "ok": False,
                "error": "price repair requires one exact FAILED external item",
                "external_writes_performed": [],
            }
        channel, site = target_label.split(":", 1)
        request = AdapterExecutionRequest(
            plan_id=plan_id,
            confirmation_token=token,
            approval_scope_digest=str(
                (plan.get("payload") or {}).get("omnichannel_scope_digest")
                or payload.get("omnichannel_scope_digest")
                or ""
            ),
            product_id=str(plan["product_id"]),
            seller_sku=str(plan["seller_sku"]),
            product_package_id=str(plan["product_package_id"]),
            content_package_id=str(plan["content_package_id"]),
            channel=channel,
            site=site,
            target_label=target_label,
            idempotency_key=idempotency_key,
        )
        try:
            preview = preflight_shopee_price_repair(request)
        except Exception as error:
            return 409, {
                "ok": False,
                "error": f"Shopee price repair preflight blocked: {error}",
                "external_writes_performed": [],
            }
        if (
            str(preview["operation"].get("preflight_digest") or "")
            != requested_preflight_digest
        ):
            return 409, {
                "ok": False,
                "error": "Shopee price repair preview is stale",
                "external_writes_performed": [],
            }
        operation = {
            **dict(preview["operation"]),
            "expected_revision": expected_revision,
            "payload_digest": requested_payload_digest,
        }
        try:
            claim = store.claim_failed_target_repair(
                plan_id=plan_id,
                run_id=str(run["run_id"]),
                target_label=target_label,
                external_id=external_id,
                operation=operation,
            )
        except (
            ImmutableReleaseError,
            ReleaseAuthorizationError,
            ReleaseStoreError,
            ValueError,
        ) as error:
            return 409, {
                "ok": False,
                "error": str(error),
                "external_writes_performed": [],
            }
        if claim["action"] == "already_succeeded":
            latest = store.get_run(str(run["run_id"]))
            return 200, {
                "ok": True,
                "idempotent": True,
                "target": target_label,
                "external_writes_performed": [],
                "repair_status": _shopee_price_repair_status_view(
                    latest,
                    target_label=target_label,
                ),
            }
        operation_digest = str(claim["operation_digest"])

        def reconciliation_response(
            *,
            detail: str,
            evidence: dict,
            durable_state_uncertain: bool,
        ) -> tuple[int, dict]:
            truthful_writes = list(
                evidence.get("external_writes_performed") or ()
            )
            evidence = {
                **evidence,
                "reconciliation_required": True,
                "durable_state_uncertain": durable_state_uncertain,
                "external_writes_performed": truthful_writes,
            }
            try:
                reconciled = store.record_target_repair_reconciliation(
                    operation_digest,
                    error=detail,
                    evidence=evidence,
                )
            except Exception:
                latest = None
                try:
                    latest = store.get_run(str(run["run_id"]))
                except Exception:
                    latest = None
                return 502, {
                    "ok": False,
                    "error": (
                        "price repair durable reconciliation could not be "
                        "recorded; do not repeat the POST"
                    ),
                    "reconciliation_required": True,
                    "durable_state_uncertain": True,
                    "external_writes_performed": truthful_writes,
                    "repair_status": _shopee_price_repair_status_view(
                        latest,
                        target_label=target_label,
                    ),
                }
            return 409, {
                "ok": False,
                "error": detail,
                "reconciliation_required": True,
                "durable_state_uncertain": durable_state_uncertain,
                "external_writes_performed": truthful_writes,
                "repair_status": _shopee_price_repair_status_view(
                    reconciled,
                    target_label=target_label,
                ),
            }

        try:
            result = execute_shopee_price_repair(
                request,
                expected_preflight_digest=str(
                    preview["operation"]["preflight_digest"]
                ),
            )
            if not result.succeeded or not result.readback_verified:
                raise RuntimeError("Shopee price repair readback is not exact")
        except ReleaseAdapterWriteVerificationError as error:
            evidence = dict(error.external_write_evidence or {})
            return reconciliation_response(
                detail=str(error),
                evidence=evidence,
                durable_state_uncertain=True,
            )
        except Exception as error:
            return reconciliation_response(
                detail=(
                    "price repair stopped after claim; manual reconciliation "
                    f"is required: {error}"
                ),
                evidence={
                    "verified": False,
                    "error_type": type(error).__name__,
                    "external_writes_performed": [],
                },
                durable_state_uncertain=False,
            )
        try:
            completed = store.record_target_repair_success(
                operation_digest,
                readback_evidence=dict(result.readback_evidence or {}),
            )
        except Exception as error:
            return reconciliation_response(
                detail=(
                    "Shopee update_price and exact official readback succeeded, "
                    "but the durable success receipt failed"
                ),
                evidence={
                    "verified": True,
                    "error_type": type(error).__name__,
                    "external_writes_performed": ["shopee:update_price"],
                },
                durable_state_uncertain=True,
            )
        return 200, {
            "ok": True,
            "idempotent": False,
            "target": target_label,
            "external_writes_performed": ["shopee:update_price"],
            "repair_status": _shopee_price_repair_status_view(
                completed,
                target_label=target_label,
            ),
        }


def _approve_release_plan_locally(data: dict) -> tuple[int, dict]:
    """Persist the exact plan and Kyle approval; perform no external action."""
    from shared_platform.release_store import (
        ReleaseAuthorizationError,
        ReleaseStoreError,
        SkuReservationConflict,
        default_release_store,
    )

    if data.get("user_approved") is not True:
        return 400, {
            "ok": False,
            "error": "explicit user_approved=true is required",
        }
    if str(data.get("approved_by") or "").strip() != "Kyle":
        return 400, {"ok": False, "error": "approved_by must be Kyle"}
    dashboard, failure = _release_dashboard_for_request(data)
    if failure:
        return failure
    assert dashboard is not None
    plan_payload, blockers = _release_plan_payload_from_dashboard(dashboard)
    if blockers:
        current_dashboard = _product_workspace_view(dashboard)
        return 409, {
            "ok": False,
            "error_code": "release_plan_not_ready",
            "error": blockers[0],
            "blockers": blockers,
            "dashboard": current_dashboard,
            "external_writes_performed": [],
        }
    store = default_release_store()
    preview = store.preview_plan(plan_payload)
    requested_plan_id = str(data.get("plan_id") or "").strip()
    requested_token = str(data.get("confirmation_token") or "").strip()
    if requested_plan_id != preview["plan_id"]:
        return 409, {
            "ok": False,
            "error": "release plan identity changed; refresh before approval",
            "current_plan_id": preview["plan_id"],
        }
    if requested_token != preview["confirmation_token"]:
        return 409, {
            "ok": False,
            "error": "release confirmation token changed; refresh before approval",
        }
    try:
        existing = store.get_plan(preview["plan_id"])
        active = store.active_plan_for_product(preview["product_id"])
        if existing is None:
            explicit_predecessor_id = str(
                (dashboard.get("listing_copy") or {}).get(
                    "superseded_release_plan_id"
                )
                or ""
            ).strip()
            if active and active["plan_id"] != preview["plan_id"]:
                predecessor = active["plan_id"]
            elif explicit_predecessor_id:
                predecessor = explicit_predecessor_id
            else:
                unlinked = store.latest_unlinked_common_predecessor(
                    product_id=preview["product_id"],
                    seller_sku=preview["seller_sku"],
                )
                predecessor = (
                    str((unlinked or {}).get("plan_id") or "").strip()
                    or None
                )
            store.create_plan(
                plan_payload,
                supersedes_plan_id=predecessor,
            )
        approval = store.approve_plan(
            preview["plan_id"],
            approved_by="Kyle",
            user_approved=True,
            confirmation_token=preview["confirmation_token"],
        )
    except (ReleaseAuthorizationError, SkuReservationConflict, ReleaseStoreError) as error:
        return 409, {"ok": False, "error": str(error)}
    updated = _product_workspace_view(dashboard)
    return 200, {
        "ok": True,
        "persisted": True,
        "external_writes_performed": [],
        "approval": approval,
        "dashboard": updated,
    }


def _release_reconciliation_required(
    run: dict,
    target_labels: list[str],
    *,
    external_writes_performed: list[str] | None = None,
) -> tuple[int, dict]:
    """Fail closed when a begun target has no durable terminal receipt."""

    return 409, {
        "ok": False,
        "code": "reconciliation_required",
        "error": (
            "a release target is still RUNNING; its external outcome must be "
            "reconciled before any retry"
        ),
        "reconciliation_required": True,
        "blocked_targets": list(target_labels),
        "external_writes_performed": list(
            external_writes_performed or ()
        ),
        "run": run,
    }


def _uncertain_adapter_receipt_response(
    *,
    store,
    run: dict,
    label: str,
    result,
    error: Exception,
    prior_external_writes: list[str],
) -> tuple[int, dict]:
    """Persist fail-closed evidence after an external success lost its receipt."""

    result_evidence = dict(result.readback_evidence or {})
    detected_writes = [
        str(value)
        for value in (
            result_evidence.get("external_writes_performed")
            or [label]
        )
        if str(value)
    ]
    uncertain_evidence = {
        **result_evidence,
        "verified": bool(result.readback_verified),
        "submission_accepted": bool(result.submission_accepted),
        "durable_state_uncertain": True,
        "durable_receipt_error": str(error),
        "external_writes_performed": detected_writes,
    }
    failure_record_error = ""
    try:
        store.record_target_failure(
            run["run_id"],
            label,
            error=(
                "external action completed but durable terminal receipt "
                f"failed: {error}"
            ),
            external_id=result.external_reference,
            failure_evidence=uncertain_evidence,
        )
    except Exception as record_error:
        failure_record_error = str(record_error)
    response = {
        "ok": False,
        "code": "reconciliation_required",
        "error": (
            "external action completed but its durable terminal receipt "
            "is uncertain"
        ),
        "detail": str(error),
        "blocked_target": label,
        "blocked_targets": [label],
        "external_reference": result.external_reference,
        "readback_evidence": result_evidence,
        "durable_state_uncertain": True,
        "reconciliation_required": True,
        "external_writes_performed": [
            *prior_external_writes,
            *detected_writes,
        ],
        "run": store.get_run(run["run_id"]) or run,
    }
    if failure_record_error:
        response["run_record_error"] = failure_record_error
    return 409, response


def _adapter_result_has_external_outcome(result) -> bool:
    if result is None:
        return False
    evidence = dict(result.readback_evidence or {})
    return bool(
        result.external_reference
        or result.submission_accepted
        or (result.succeeded and result.readback_verified)
        or evidence.get("external_writes_performed")
    )


def _prepare_miaoshou_release(
    data: dict,
    *,
    _overwrite_lock_held: bool = False,
) -> tuple[int, dict]:
    """Execute only the approved common-draft write and verified readback."""
    from modules.products.release_adapters import (
        MiaoshouDraftVerificationError,
        miaoshou_common_overwrite_review,
        readback_miaoshou_common,
        write_miaoshou_common_from_plan,
    )
    from shared_platform.release_store import (
        ReleaseAuthorizationError,
        ReleaseStoreError,
        default_release_store,
    )

    reuse_readback = data.get("reuse_miaoshou_readback") is True
    confirm_write = data.get("confirm_miaoshou_write") is True
    confirm_overwrite = data.get("confirm_miaoshou_overwrite") is True
    if confirm_overwrite and not _overwrite_lock_held:
        with _release_execution_lock:
            return _prepare_miaoshou_release(
                data,
                _overwrite_lock_held=True,
            )
    if not reuse_readback and not confirm_write:
        return 400, {
            "ok": False,
            "error": (
                "explicit reuse_miaoshou_readback=true or "
                "confirm_miaoshou_write=true is required"
            ),
        }
    dashboard, failure = _release_dashboard_for_request(data)
    if failure:
        return failure
    assert dashboard is not None
    plan_payload, blockers = _release_plan_payload_from_dashboard(dashboard)
    if blockers:
        return 409, {
            "ok": False,
            "error": "current release facts no longer match an approvable plan",
            "blockers": blockers,
        }
    store = default_release_store()
    preview = store.preview_plan(plan_payload)
    plan_id = str(data.get("plan_id") or "").strip()
    token = str(data.get("confirmation_token") or "").strip()
    plan = store.get_plan(plan_id)
    if (
        plan_id != preview["plan_id"]
        or not plan
        or not _approved_plan_matches_current_payload(plan, preview)
        or plan.get("status") != "APPROVED"
        or token != plan.get("confirmation_token")
    ):
        return 409, {
            "ok": False,
            "error": "approved ReleasePlan no longer matches current facts",
            "external_writes_performed": [],
        }
    if confirm_overwrite:
        try:
            supplied_revision = int(data.get("expected_revision"))
        except (TypeError, ValueError):
            supplied_revision = -1
        expected_revision = int(
            (plan.get("payload") or {}).get("product_revision") or -1
        )
        overwrite_contract_errors = []
        if confirm_write is not True:
            overwrite_contract_errors.append(
                "confirm_miaoshou_write=true is required"
            )
        if str(data.get("approved_by") or "").strip() != "Kyle":
            overwrite_contract_errors.append("approved_by must be Kyle")
        if supplied_revision != expected_revision:
            overwrite_contract_errors.append(
                "expected revision does not match the immutable ReleasePlan"
            )
        if str(data.get("payload_digest") or "").strip() != str(
            plan.get("payload_digest") or ""
        ):
            overwrite_contract_errors.append(
                "payload digest does not match the immutable ReleasePlan"
            )
        if overwrite_contract_errors:
            return 409, {
                "ok": False,
                "error": "explicit COMMON overwrite contract was not satisfied",
                "blockers": overwrite_contract_errors,
                "external_writes_performed": [],
            }
    copy_blockers = _immutable_listing_copy_preflight(plan.get("payload") or {})
    if copy_blockers:
        return 409, {
            "ok": False,
            "error": "approved ReleasePlan has invalid immutable listing copy",
            "blockers": copy_blockers,
            "external_writes_performed": [],
        }
    if "miaoshou:COMMON" not in (plan.get("targets") or ()):
        return 409, {
            "ok": False,
            "error": "Miaoshou COMMON must be selected before draft preparation",
        }
    predecessor = store.predecessor_plan_for(plan_id)
    predecessor_run = (
        store.get_run(f"release-run:{predecessor['payload_digest'][:24]}")
        if predecessor
        else None
    )
    predecessor_common = next(
        (
            row
            for row in ((predecessor_run or {}).get("targets") or ())
            if row.get("target_label") == "miaoshou:COMMON"
        ),
        None,
    )
    predecessor_has_common = bool(
        predecessor_common
        and predecessor_common.get("status") == "SUCCEEDED"
    )
    predecessor_blockers: list[str] = []
    if predecessor and not predecessor_has_common:
        predecessor_blockers.append(
            "linked predecessor has no successful COMMON readback"
        )
    if predecessor_has_common:
        predecessor_blockers.extend(
            _verified_common_evidence_blockers(
                predecessor_run,
                (predecessor or {}).get("payload") or {},
                store=store,
            )
        )
        predecessor_payload = (predecessor or {}).get("payload") or {}
        successor_payload = plan.get("payload") or {}
        for field in ("product_id", "seller_sku"):
            if str(predecessor_payload.get(field) or "") != str(
                successor_payload.get(field) or ""
            ):
                predecessor_blockers.append(
                    f"COMMON predecessor {field} does not match successor"
                )
        predecessor_facts = predecessor_payload.get("product_facts") or {}
        successor_facts = successor_payload.get("product_facts") or {}
        for field in ("source_offer_id", "selected_sku_keys"):
            if predecessor_facts.get(field) != successor_facts.get(field):
                predecessor_blockers.append(
                    f"COMMON predecessor source binding changed: {field}"
                )
    if predecessor_blockers:
        return 409, {
            "ok": False,
            "error": "COMMON predecessor evidence is not safe to reuse",
            "blockers": list(dict.fromkeys(predecessor_blockers)),
            "external_writes_performed": [],
        }
    if reuse_readback and not predecessor_has_common:
        return 409, {
            "ok": False,
            "error": "COMMON readback reuse requires a verified predecessor success",
            "external_writes_performed": [],
        }
    if confirm_overwrite and not predecessor_has_common:
        return 409, {
            "ok": False,
            "error": (
                "COMMON overwrite requires a verified predecessor binding; "
                "use the normal preparation action for a first draft"
            ),
            "external_writes_performed": [],
        }
    if predecessor_has_common:
        # The existing UI sends confirm_miaoshou_write=true.  A successor must
        # nevertheless prove equality by readback before any possible edit.
        reuse_readback = True
    if reuse_readback:
        existing_run = store.get_run(
            f"release-run:{plan['payload_digest'][:24]}"
        )
        existing_common = next(
            (
                row
                for row in ((existing_run or {}).get("targets") or ())
                if row.get("target_label") == "miaoshou:COMMON"
            ),
            None,
        )
        if existing_common and existing_common.get("status") == "SUCCEEDED":
            existing_blockers = _verified_common_evidence_blockers(
                existing_run,
                plan.get("payload") or {},
                store=store,
            )
            if existing_blockers:
                return 409, {
                    "ok": False,
                    "error": "existing successor COMMON evidence is incomplete",
                    "blockers": existing_blockers,
                    "external_writes_performed": [],
                }
            return 200, {
                "ok": True,
                "idempotent": True,
                "external_writes_performed": [],
                "run": existing_run,
                "dashboard": _product_workspace_view(dashboard),
            }
        try:
            readback = readback_miaoshou_common(plan.get("payload") or {})
        except Exception as error:
            return 502, {
                "ok": False,
                "error": str(error),
                "mode": "readback_reuse_no_write",
                "external_writes_performed": [],
            }
        if not readback.get("verified"):
            review = miaoshou_common_overwrite_review(
                plan.get("payload") or {},
                readback,
                plan_id=plan["plan_id"],
                confirmation_token=plan["confirmation_token"],
                payload_digest=plan["payload_digest"],
                expected_revision=int(
                    (plan.get("payload") or {}).get("product_revision") or -1
                ),
            )
            try:
                review = store.record_common_overwrite_review(
                    plan["plan_id"],
                    review,
                )
            except ReleaseStoreError as error:
                return 409, {
                    "ok": False,
                    "error": str(error),
                    "external_writes_performed": [],
                }
            requested_review_digest = str(
                data.get("overwrite_review_digest") or ""
            ).strip()
            if (
                requested_review_digest
                and requested_review_digest != review.get("review_digest")
            ):
                return 409, {
                    "ok": False,
                    "error": (
                        "COMMON overwrite review changed; inspect the latest "
                        "redacted diff before confirming"
                    ),
                    "common_overwrite_review": review,
                    "dashboard": _product_workspace_view(dashboard),
                    "external_writes_performed": [],
                }
            if not confirm_overwrite:
                return 409, {
                    "ok": False,
                    "error": (
                        "existing Miaoshou COMMON differs from the immutable "
                        "successor ReleasePlan"
                    ),
                    "mode": "readback_reuse_no_write",
                    "common_overwrite_review": review,
                    "dashboard": _product_workspace_view(dashboard),
                    "external_writes_performed": [],
                    "overwrite_requires": {
                        "confirm_miaoshou_overwrite": True,
                        "approved_by": "Kyle",
                        "plan_id": plan["plan_id"],
                        "confirmation_token": plan["confirmation_token"],
                        "expected_revision": review["expected_revision"],
                        "payload_digest": plan["payload_digest"],
                    },
                }
            if not review.get("overwrite_allowed"):
                return 409, {
                    "ok": False,
                    "error": (
                        "existing COMMON identity or field scope is not safe "
                        "for an automated overwrite"
                    ),
                    "common_overwrite_review": review,
                    "dashboard": _product_workspace_view(dashboard),
                    "external_writes_performed": [],
                }
            existing_run = store.get_run(
                f"release-run:{plan['payload_digest'][:24]}"
            )
            if existing_run:
                return 409, {
                    "ok": False,
                    "error": (
                        "COMMON overwrite is disabled after a release run exists"
                    ),
                    "common_overwrite_review": review,
                    "run": existing_run,
                    "external_writes_performed": [],
                }
            reuse_readback = False
            overwrite_guard = review
        else:
            overwrite_guard = None
        if reuse_readback:
            predecessor = store.predecessor_plan_for(plan_id)
            predecessor_run = (
                store.get_run(
                    f"release-run:{predecessor['payload_digest'][:24]}"
                )
                if predecessor
                else None
            )
            predecessor_common = next(
                (
                    row
                    for row in ((predecessor_run or {}).get("targets") or ())
                    if row.get("target_label") == "miaoshou:COMMON"
                ),
                None,
            )
            reuse_evidence = {
                **readback,
                "mode": "readback_reuse_no_write",
                "predecessor": {
                    "plan_id": (predecessor or {}).get("plan_id"),
                    "run_id": (predecessor_run or {}).get("run_id"),
                    "payload_digest": (predecessor or {}).get(
                        "payload_digest"
                    ),
                    "common_external_id": (
                        (predecessor_common or {}).get("external_id")
                    ),
                    "common_status": (predecessor_common or {}).get("status"),
                    "common_readback_evidence_digest": (
                        ((predecessor_common or {}).get("readback") or {}).get(
                            "evidence_digest"
                        )
                    ),
                    "common_readback_verified_at": (
                        ((predecessor_common or {}).get("readback") or {}).get(
                            "verified_at"
                        )
                    ),
                },
                "external_writes_performed": [],
            }
            try:
                run = store.start_run(plan_id)
                target = next(
                    row
                    for row in run["targets"]
                    if row["target_label"] == "miaoshou:COMMON"
                )
                if target["status"] == "RUNNING":
                    return _release_reconciliation_required(
                        run,
                        ["miaoshou:COMMON"],
                    )
                if target["status"] == "FAILED":
                    run = store.retry_failed_targets(
                        run["run_id"],
                        ["miaoshou:COMMON"],
                    )
                store.begin_target(run["run_id"], "miaoshou:COMMON")
                store.record_target_success(
                    run["run_id"],
                    "miaoshou:COMMON",
                    external_id=str(plan_payload["product_id"]),
                    readback_evidence=reuse_evidence,
                )
                store.resolve_common_overwrite_review(plan_id)
            except (ReleaseAuthorizationError, ReleaseStoreError, StopIteration) as error:
                return 409, {
                    "ok": False,
                    "error": str(error),
                    "external_writes_performed": [],
                }
            return 200, {
                "ok": True,
                "idempotent": False,
                "mode": "readback_reuse_no_write",
                "external_writes_performed": [],
                "result": reuse_evidence,
                "run": store.get_run(run["run_id"]),
                "dashboard": _product_workspace_view(dashboard),
            }
    else:
        overwrite_guard = None
    try:
        run = store.start_run(plan_id)
        target = next(
            row
            for row in run["targets"]
            if row["target_label"] == "miaoshou:COMMON"
        )
        if target["status"] == "SUCCEEDED":
            return 200, {
                "ok": True,
                "idempotent": True,
                "external_writes_performed": [],
                "run": run,
                "dashboard": _product_workspace_view(dashboard),
            }
        if target["status"] == "RUNNING":
            return _release_reconciliation_required(
                run,
                ["miaoshou:COMMON"],
            )
        if target["status"] == "FAILED":
            failure_evidence = (
                (target.get("latest_failure_evidence") or {}).get(
                    "evidence"
                )
                or {}
            )
            if target.get("external_id") or failure_evidence.get(
                "external_writes_performed"
            ):
                reconciliation_eligible = bool(
                    target.get("external_id")
                    and failure_evidence.get("save_accepted") is True
                    and failure_evidence.get("verified") is False
                    and failure_evidence.get("external_writes_performed")
                    == ["miaoshou:COMMON:immutable_plan_write"]
                )
                if not reconciliation_eligible:
                    return 409, {
                        "ok": False,
                        "error": (
                            "COMMON already records a failed external write; "
                            "automatic retry is disabled"
                        ),
                        "external_writes_performed": [],
                        "run": run,
                    }
                try:
                    readback = readback_miaoshou_common(
                        plan.get("payload") or {}
                    )
                except Exception as error:
                    return 502, {
                        "ok": False,
                        "error": str(error),
                        "mode": "readback_reconciliation_no_write",
                        "reconciliation_required": True,
                        "external_writes_performed": [],
                        "run": run,
                    }
                if not readback.get("verified"):
                    return 409, {
                        "ok": False,
                        "error": (
                            "COMMON official readback still differs from the "
                            "immutable ReleasePlan"
                        ),
                        "mode": "readback_reconciliation_no_write",
                        "failed_checks": [
                            str(name)
                            for name, passed in (
                                readback.get("checks") or {}
                            ).items()
                            if passed is not True
                        ],
                        "reconciliation_required": True,
                        "external_writes_performed": [],
                        "run": run,
                    }
                reconciliation_evidence = {
                    "source": "miaoshou_open_api",
                    "verified": True,
                    "mode": "readback_reconciliation_no_write",
                    "offer_id": str(plan_payload["product_id"]),
                    "collect_box_detail_id": int(plan_payload["product_id"]),
                    "checks": dict(readback.get("checks") or {}),
                    "spec_label_application": dict(
                        readback.get("spec_label_application") or {}
                    ),
                    "image_count": int(readback.get("image_count") or 0),
                    "external_writes_performed": [],
                }
                try:
                    run = store.record_common_reconciled_success(
                        run["run_id"],
                        external_id=str(target.get("external_id") or ""),
                        readback_evidence=reconciliation_evidence,
                    )
                except (
                    ReleaseAuthorizationError,
                    ReleaseStoreError,
                    ValueError,
                ) as error:
                    return 409, {
                        "ok": False,
                        "error": str(error),
                        "mode": "readback_reconciliation_no_write",
                        "reconciliation_required": True,
                        "external_writes_performed": [],
                        "run": store.get_run(run["run_id"]) or run,
                    }
                refreshed, refresh_failure = _release_dashboard_for_request(
                    data
                )
                if refresh_failure:
                    refreshed = dashboard
                return 200, {
                    "ok": True,
                    "idempotent": False,
                    "mode": "readback_reconciliation_no_write",
                    "external_writes_performed": [],
                    "result": reconciliation_evidence,
                    "run": run,
                    "dashboard": _product_workspace_view(
                        refreshed or dashboard
                    ),
                }
            run = store.retry_failed_targets(
                run["run_id"],
                ["miaoshou:COMMON"],
            )
        store.begin_target(run["run_id"], "miaoshou:COMMON")
    except (ReleaseAuthorizationError, ReleaseStoreError, StopIteration) as error:
        return 409, {"ok": False, "error": str(error)}

    result = None
    common_readback_evidence = None
    try:
        if overwrite_guard is None:
            result = write_miaoshou_common_from_plan(plan.get("payload") or {})
        else:
            result = write_miaoshou_common_from_plan(
                plan.get("payload") or {},
                overwrite_guard=overwrite_guard,
            )
        if not result.get("written_to_miaoshou") or not result.get("verified"):
            failed_checks = [
                str(name)
                for name, passed in (result.get("checks") or {}).items()
                if not passed
            ]
            detail = ", ".join(failed_checks) or "unknown fields"
            raise MiaoshouDraftVerificationError(
                (
                    "Miaoshou COMMON write was accepted but readback did not "
                    "verify every approved field: "
                    + detail
                ),
                external_reference=str(
                    result.get("offer_id") or plan_payload["product_id"]
                ),
                evidence={
                    **dict(result),
                    "verified": False,
                    "save_accepted": bool(
                        result.get("written_to_miaoshou")
                    ),
                    "external_writes_performed": list(
                        result.get("external_writes_performed")
                        or ["miaoshou:COMMON:immutable_plan_write"]
                    ),
                },
            )
        common_readback_evidence = {
            "source": "miaoshou_open_api",
            "verified": True,
            "offer_id": str(
                result.get("offer_id") or plan_payload["product_id"]
            ),
            "collect_box_detail_id": result.get("detail_id"),
            "checks": dict(result.get("checks") or {}),
            "spec_label_application": dict(
                result.get("spec_label_application") or {}
            ),
            "image_count": len(
                ((result.get("draft") or {}).get("imgUrls") or ())
            ),
            "external_writes_performed": list(
                result.get("external_writes_performed")
                or ["miaoshou:COMMON:immutable_plan_write"]
            ),
        }
        store.record_target_success(
            run["run_id"],
            "miaoshou:COMMON",
            external_id=str(result.get("offer_id") or plan_payload["product_id"]),
            readback_evidence=common_readback_evidence,
        )
        if overwrite_guard is not None:
            store.resolve_common_overwrite_review(plan_id)
    except Exception as error:
        store_record_error = ""
        failure_evidence = getattr(error, "external_write_evidence", None)
        external_reference = getattr(error, "external_reference", None)
        if (
            not isinstance(failure_evidence, dict)
            and isinstance(result, dict)
            and result.get("written_to_miaoshou")
        ):
            external_reference = str(
                result.get("offer_id") or plan_payload["product_id"]
            )
            failure_evidence = {
                **dict(common_readback_evidence or {}),
                "source": "miaoshou_open_api",
                "verified": bool(result.get("verified")),
                "save_accepted": True,
                "durable_state_uncertain": True,
                "durable_receipt_error": str(error),
                "external_writes_performed": list(
                    result.get("external_writes_performed")
                    or ["miaoshou:COMMON:immutable_plan_write"]
                ),
            }
        try:
            store.record_target_failure(
                run["run_id"],
                "miaoshou:COMMON",
                error=str(error),
                external_id=external_reference,
                failure_evidence=failure_evidence,
            )
        except Exception as record_error:
            store_record_error = str(record_error)
        payload = {
            "ok": False,
            "error": str(error),
            "run": store.get_run(run["run_id"]),
            "external_reference": external_reference,
            "readback_evidence": (
                dict(common_readback_evidence)
                if isinstance(common_readback_evidence, dict)
                else None
            ),
            "durable_state_uncertain": bool(
                isinstance(failure_evidence, dict)
                and failure_evidence.get("durable_state_uncertain")
            ),
            "reconciliation_required": bool(
                isinstance(failure_evidence, dict)
                and (
                    failure_evidence.get("durable_state_uncertain")
                    or failure_evidence.get("external_writes_performed")
                )
            ),
            "external_writes_performed": list(
                (
                    failure_evidence.get("external_writes_performed")
                    if isinstance(failure_evidence, dict)
                    else ()
                )
                or ()
            ),
            "dashboard": _product_workspace_view(dashboard),
        }
        if store_record_error:
            payload["run_record_error"] = store_record_error
        return 502, payload
    refreshed, refresh_failure = _release_dashboard_for_request(data)
    if refresh_failure:
        refreshed = dashboard
    return 200, {
        "ok": True,
        "idempotent": False,
        "external_writes_performed": ["miaoshou:COMMON:draft_write_and_readback"],
        "result": result,
        "run": store.get_run(run["run_id"]),
        "dashboard": _product_workspace_view(refreshed or dashboard),
    }


_GENERIC_TIKTOK_SAFE_RETRY_LABELS = frozenset(
    {
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
        "tiktok:MX",
        "tiktok:GB",
        "tiktok:HB_PH",
        "tiktok:HB_MY",
        "tiktok:HB_TH",
        "tiktok:HB_VN",
    }
)
_GENERIC_TIKTOK_SAFE_RETRY_MARKERS = (
    "persisted miaoshou claim lacks",
    "miaoshou tiktok has no target draft",
    "target draft missing",
)


def _zero_write_pre_submit_evidence(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    nested = value.get("evidence")
    evidence = nested if isinstance(nested, dict) else value
    writes = evidence.get("external_writes_performed")
    return (
        evidence.get("pre_submit_failure") is True
        and evidence.get("submission_accepted") is False
        and isinstance(writes, list)
        and not writes
    )


def _generic_tiktok_safe_retry_target(target: dict) -> bool:
    """Recognize only truthful zero-write TikTok pre-submit failures."""

    label = str(target.get("target_label") or "")
    if (
        label not in _GENERIC_TIKTOK_SAFE_RETRY_LABELS
        or target.get("status") != "FAILED"
        or target.get("external_id")
        or target.get("submission")
        or target.get("readback")
        or target.get("repair")
        or target.get("external_writes_performed")
    ):
        return False
    failure_events = target.get("failure_events")
    if failure_events is None:
        failure_events = []
    if not isinstance(failure_events, list):
        return False
    structured_safe_evidence = False
    for event in failure_events:
        if not isinstance(event, dict):
            return False
        evidence = event.get("evidence")
        if evidence in (None, {}):
            continue
        if not _zero_write_pre_submit_evidence(evidence):
            return False
        structured_safe_evidence = True
    latest = target.get("latest_failure_evidence")
    if isinstance(latest, dict) and latest.get("evidence") not in (None, {}):
        if not _zero_write_pre_submit_evidence(latest):
            return False
        structured_safe_evidence = True
    if structured_safe_evidence:
        return True
    detail = str(target.get("error") or "").casefold()
    return any(
        marker in detail for marker in _GENERIC_TIKTOK_SAFE_RETRY_MARKERS
    )


def _release_target_dependencies(
    target_label: str,
    statuses: dict[str, object],
) -> tuple[str, ...]:
    """Return only genuine same-run execution dependencies.

    TikTok site drafts are derived from the governed Miaoshou COMMON record.
    Shopee and Ozon consume the immutable ReleasePlan independently; coupling
    them to a same-region TikTok result caused pristine targets to be skipped
    whenever a TikTok draft hit a version conflict.
    """

    channel = str(target_label or "").split(":", 1)[0]
    if channel == "tiktok":
        return ("miaoshou:COMMON",)
    return ()


def _release_predecessor_evidence_run(store, plan: dict | None) -> dict | None:
    """Fold durable target evidence across the full predecessor chain.

    A second successor must not make an older accepted write disappear merely
    because its immediate predecessor never attempted that target.  Keep the
    nearest row for ordinary status context, but prefer any older row carrying
    an external outcome.  The projection is read-only and is used only to
    prevent automatic resubmission.
    """

    from shared_platform.target_recovery import classify_target_recovery

    current = plan if isinstance(plan, dict) else None
    seen_plan_ids: set[str] = set()
    evidence_by_label: dict[str, dict] = {}
    for _depth in range(100):
        current_id = str((current or {}).get("plan_id") or "").strip()
        if not current_id or current_id in seen_plan_ids:
            break
        seen_plan_ids.add(current_id)
        predecessor = store.predecessor_plan_for(current_id)
        if not predecessor:
            break
        digest = str(predecessor.get("payload_digest") or "")
        predecessor_run = (
            store.get_run(f"release-run:{digest[:24]}")
            if digest
            else None
        )
        for row in (predecessor_run or {}).get("targets") or ():
            if not isinstance(row, dict):
                continue
            label = str(row.get("target_label") or "")
            if not label or label == "miaoshou:COMMON":
                continue
            candidate_has_evidence = (
                classify_target_recovery(row).get(
                    "prior_write_evidence"
                )
                is True
            )
            existing = evidence_by_label.get(label)
            existing_has_evidence = bool(
                existing
                and classify_target_recovery(existing).get(
                    "prior_write_evidence"
                )
                is True
            )
            if existing is None or (
                candidate_has_evidence and not existing_has_evidence
            ):
                evidence_by_label[label] = row
        current = predecessor
    return (
        {"targets": list(evidence_by_label.values())}
        if evidence_by_label
        else None
    )


def _release_target_recovery_actions(
    run: dict | None,
    *,
    predecessor_run: dict | None = None,
    target_rows: object = (),
    registry: dict | None = None,
) -> list[dict]:
    """Project one channel-neutral action for every physical target."""

    from modules.products.release_adapters import production_adapter_registry
    from shared_platform.target_recovery import project_run_recovery_actions

    rows = (run or {}).get("targets") or ()
    active_registry = registry or production_adapter_registry()
    predecessor_recovery_labels = {
        f"{row.get('channel')}:{row.get('site')}"
        for row in (
            target_rows if isinstance(target_rows, (list, tuple)) else ()
        )
        if isinstance(row, dict)
        and (
            registration := active_registry.get(
                str(row.get("adapter") or "")
            )
        )
        is not None
        and registration.supports_predecessor_recovery
    }
    first_attempt_blocked_labels = {
        f"{row.get('channel')}:{row.get('site')}"
        for row in (
            target_rows if isinstance(target_rows, (list, tuple)) else ()
        )
        if isinstance(row, dict)
        and (
            registration := active_registry.get(
                str(row.get("adapter") or "")
            )
        )
        is not None
        and not registration.supports_automatic_first_attempt
    }
    safe_retry_labels = {
        str(row.get("target_label") or "")
        for row in rows
        if isinstance(row, dict) and _generic_tiktok_safe_retry_target(row)
    }
    return project_run_recovery_actions(
        rows,
        safe_retry_labels=safe_retry_labels,
        predecessor_recovery_labels=predecessor_recovery_labels,
        first_attempt_blocked_labels=first_attempt_blocked_labels,
        predecessor_targets=(predecessor_run or {}).get("targets") or (),
    )


_oneclick_worker_guard = threading.Lock()
_oneclick_worker_wake = threading.Event()
_oneclick_worker_jobs: set[str] = set()
_oneclick_worker_thread: threading.Thread | None = None


def _oneclick_adapter_registry() -> dict:
    """Load only the channel-owned typed registry; never import a client."""

    import importlib

    try:
        module = importlib.import_module(
            "domains.channel_operations.oneclick_release_adapters"
        )
    except ModuleNotFoundError:
        return {}
    provider = getattr(module, "production_adapter_registry", None)
    if not callable(provider):
        return {}
    registry = provider()
    try:
        return dict(registry)
    except (TypeError, ValueError):
        return {}


def _oneclick_control_store():
    from shared_platform.oneclick_release_controlplane import OneClickReleaseStore
    from shared_platform.release_store import default_release_store

    return OneClickReleaseStore(default_release_store().path)


def _oneclick_dispatch_enabled() -> bool:
    return _oneclick_dispatch_capability()["enabled"] is True


def _oneclick_dispatch_capability() -> dict:
    raw = os.environ.get("ORBIT_ONECLICK_EXTERNAL_DISPATCH")
    if raw is None or raw == "":
        enabled = True
        source = "server_default"
        reason_code = "oneclick_dispatch_enabled_by_default"
    elif raw.casefold() in {"1", "true", "yes", "on", "enabled"}:
        enabled = True
        source = "explicit_environment"
        reason_code = "oneclick_dispatch_explicitly_enabled"
    elif raw.casefold() in {"0", "false", "no", "off", "disabled"}:
        enabled = False
        source = "explicit_environment"
        reason_code = "oneclick_dispatch_explicitly_disabled"
    else:
        enabled = False
        source = "invalid_environment_fail_closed"
        reason_code = "oneclick_dispatch_configuration_invalid"
    return {
        "schema_version": "oneclick-dispatch-capability/v1",
        "enabled": enabled,
        "source": source,
        "reason_code": reason_code,
        "next_action": None if enabled else "enable_oneclick_dispatch",
    }


def _project_oneclick_dispatch_capability(job: dict) -> dict:
    projected = dict(job)
    capability = _oneclick_dispatch_capability()
    projected["dispatch_capability"] = capability
    if capability["enabled"] is True:
        return _attach_oneclick_canonical_action(projected)
    def disable_runnable(values):
        disabled = []
        for target_value in values or ():
            target = dict(target_value)
            if (
                target.get("runnable_now") is True
                or target.get("classification") == "PREPARE_PENDING"
            ):
                target.update(
                    {
                        "classification": "BLOCKED_CAPABILITY",
                        "status": "BLOCKED_CAPABILITY",
                        "runnable_now": False,
                        "next_action": "enable_oneclick_dispatch",
                        "next_action_target": None,
                        "reason": {
                            "category": "CAPABILITY",
                            "scope": "TARGET",
                            "code": "oneclick_dispatch_disabled",
                            "summary_code": "channel_capability_status",
                            "detail_digest": hashlib.sha256(
                                b"oneclick_dispatch_disabled"
                            ).hexdigest(),
                        },
                    }
                )
            disabled.append(target)
        return disabled

    targets = disable_runnable(projected.get("targets"))
    projected["shared_controls"] = disable_runnable(
        projected.get("shared_controls")
    )
    projected["targets"] = targets
    projected["phase"] = (
        "BLOCKED" if projected.get("phase") == "READY" else projected.get("phase")
    )
    projected["runnable_target_count"] = 0
    projected["preparation_pending_count"] = 0
    projected["prepare_pending"] = []
    projected["start_allowed"] = False
    summary = dict(projected.get("summary") or {})
    newly_blocked = [
        target["target_label"]
        for target in targets
        if target.get("next_action") == "enable_oneclick_dispatch"
    ]
    summary["will_dispatch"] = []
    summary["manual_after_submit"] = []
    summary["blocked"] = list(
        dict.fromkeys([*(summary.get("blocked") or ()), *newly_blocked])
    )
    projected["summary"] = summary
    return _attach_oneclick_canonical_action(projected)


def _attach_oneclick_canonical_action(projected: dict) -> dict:
    """Attach the sole server-owned next action to every public projection."""

    result = dict(projected)
    capability = result.get("dispatch_capability") or {}
    if capability.get("enabled") is False:
        result["canonical_next_action"] = {
            "target_label": None,
            "target_focus": None,
            "canonical_status": "BLOCKED_CAPABILITY",
            "action": "enable_oneclick_dispatch",
            "runnable": False,
        }
        return result
    actions = [
        {
            "target_label": target["target_label"],
            "target_focus": target.get("next_action_target")
            or target["target_label"],
            "canonical_status": target["status"],
            "action": target["next_action"],
            "runnable": target.get("runnable_now") is True,
        }
        for target in [
            *(result.get("targets") or ()),
            *(result.get("shared_controls") or ()),
        ]
        if isinstance(target, dict) and target.get("next_action")
    ]
    result["canonical_next_action"] = _select_canonical_oneclick_action(
        actions
    )
    return result


def _oneclick_worker_loop() -> None:
    from shared_platform.oneclick_release_controlplane import OneClickReleaseWorker

    store = _oneclick_control_store()
    worker = OneClickReleaseWorker(
        store,
        _oneclick_adapter_registry,
        dispatch_enabled=_oneclick_dispatch_enabled,
    )
    worker.recover()
    _consume_oneclick_outcome_receipts(store)
    with _oneclick_worker_guard:
        _oneclick_worker_jobs.update(store.resumable_job_ids())
        if _oneclick_worker_jobs:
            _oneclick_worker_wake.set()
    while True:
        _oneclick_worker_wake.wait(timeout=5.0)
        _oneclick_worker_wake.clear()
        with _oneclick_worker_guard:
            job_ids = tuple(_oneclick_worker_jobs)
        for job_id in job_ids:
            try:
                progressed = worker.advance_once(job_id)
                _consume_oneclick_outcome_receipts(store)
                job = store.get_job(job_id=job_id)
            except Exception:
                progressed = False
                try:
                    store.record_systemic_stop(
                        job_id,
                        RuntimeError(
                            "one-click worker stopped after an unexpected "
                            "internal error"
                        ),
                    )
                except Exception:
                    pass
                job = store.get_job(job_id=job_id)
            phase = str((job or {}).get("phase") or "")
            if progressed and phase in {"PENDING", "PREPARING", "READY", "RUNNING"}:
                _oneclick_worker_wake.set()
                continue
            if phase not in {"PENDING", "PREPARING", "READY", "RUNNING"}:
                with _oneclick_worker_guard:
                    _oneclick_worker_jobs.discard(job_id)


def _consume_oneclick_outcome_receipts(store) -> None:
    """Normalize redacted receipts through 05 without affecting release state."""

    import importlib

    try:
        module = importlib.import_module(
            "domains.data_operations.release_outcomes"
        )
        adapter = getattr(module, "adapt_release_outcome_receipt")
        contract_error = getattr(
            module,
            "ReleaseOutcomeContractError",
            (),
        )
    except (ImportError, AttributeError):
        return
    if not callable(adapter):
        return
    for pending in store.pending_outcome_receipts(limit=50):
        try:
            fact = adapter(pending["receipt"])
            payload = fact.payload()
            fact_digest = payload.get("fact_digest")
            store.record_outcome_consumer_result(
                job_id=pending["job_id"],
                target_label=pending["target_label"],
                attempt=pending["attempt"],
                receipt_digest=pending["receipt_digest"],
                fact_digest=fact_digest,
                error_code=None,
            )
        except Exception as error:
            code = (
                "release_outcome_contract_rejected"
                if isinstance(contract_error, type)
                and isinstance(error, contract_error)
                else "release_outcome_consumer_failed"
            )
            try:
                store.record_outcome_consumer_result(
                    job_id=pending["job_id"],
                    target_label=pending["target_label"],
                    attempt=pending["attempt"],
                    receipt_digest=pending["receipt_digest"],
                    fact_digest=None,
                    error_code=code,
                )
            except Exception:
                # Consumer persistence is observational only.  The canonical
                # release target and one-click terminal state remain final.
                pass
    resolution_adapter = getattr(
        module,
        "adapt_release_outcome_manual_acceptance",
        None,
    )
    if not callable(resolution_adapter):
        # 05 must merge this append-only resolution with the original
        # receipt.  It must never be sent through the ordinary outcome
        # adapter as a second publication sample.
        return
    for pending in store.pending_manual_acceptance_resolutions(limit=50):
        try:
            fact = resolution_adapter(pending["resolution"])
            payload = fact.payload()
            store.record_manual_acceptance_consumer_result(
                job_id=pending["job_id"],
                target_label=pending["target_label"],
                attempt=pending["attempt"],
                resolution_digest=pending["resolution_digest"],
                fact_digest=payload.get("fact_digest"),
                error_code=None,
            )
        except Exception as error:
            code = (
                "release_outcome_manual_acceptance_contract_rejected"
                if isinstance(contract_error, type)
                and isinstance(error, contract_error)
                else "release_outcome_manual_acceptance_consumer_failed"
            )
            try:
                store.record_manual_acceptance_consumer_result(
                    job_id=pending["job_id"],
                    target_label=pending["target_label"],
                    attempt=pending["attempt"],
                    resolution_digest=pending["resolution_digest"],
                    fact_digest=None,
                    error_code=code,
                )
            except Exception:
                pass


def _start_oneclick_background_worker() -> None:
    global _oneclick_worker_thread

    with _oneclick_worker_guard:
        if _oneclick_worker_thread and _oneclick_worker_thread.is_alive():
            return
        _oneclick_worker_thread = threading.Thread(
            target=_oneclick_worker_loop,
            name="orbit-oneclick-release-worker",
            daemon=True,
        )
        _oneclick_worker_thread.start()


def _wake_oneclick_worker(job_id: str) -> None:
    _start_oneclick_background_worker()
    with _oneclick_worker_guard:
        _oneclick_worker_jobs.add(job_id)
    _oneclick_worker_wake.set()


def _collectbox_action_store():
    from shared_platform.collectbox_action import CollectBoxActionStore
    from shared_platform.release_store import default_release_store

    return CollectBoxActionStore(default_release_store().path)


def _tiktok_collectbox_publish_proof_available(plan: dict) -> bool:
    targets = tuple(
        target
        for target in plan.get("targets", ())
        if isinstance(target, str) and target.startswith("tiktok:")
    )
    if not targets:
        return False
    try:
        contexts = _collectbox_action_store().internal_tiktok_publish_contexts(
            plan_id=str(plan.get("plan_id") or "")
        )
    except (TypeError, ValueError):
        return False
    # One exact target proof is sufficient to start the isolated TikTok
    # batch. Missing target proofs remain per-target blockers so a GB-only
    # gap cannot prevent the other five approved drafts from publishing.
    return any(target in contexts for target in targets)


def _collectbox_action_timing():
    return time.monotonic, time.sleep


def _recover_collectbox_actions() -> int:
    return _collectbox_action_store().recover_interrupted()


def _collectbox_platform_adapter():
    import importlib

    module = importlib.import_module(
        "domains.channel_operations.collectbox_action_adapters"
    )
    adapter = getattr(module, "execute_collectbox_platform", None)
    if not callable(adapter):
        raise RuntimeError("collect-box channel adapter is unavailable")
    return adapter


def _collectbox_public_error(
    *,
    category: str,
    code: str,
    detail: str,
) -> dict[str, str]:
    return {
        "category": category,
        "code": code,
        "detail_digest": hashlib.sha256(
            detail.encode("utf-8")
        ).hexdigest(),
    }


def _collectbox_common_detail_id(context: dict) -> str:
    # In this workflow the approved plan's product_id is the exact Miaoshou
    # commonCollectBoxDetailId.  The review-package collect-box evidence is
    # optional corroboration; source_offer_id is a different business identity
    # and must never be substituted here.
    plan = context.get("plan") if isinstance(context.get("plan"), dict) else {}
    plan_detail_id = plan.get("product_id")
    if type(plan_detail_id) not in {str, int} or isinstance(
        plan_detail_id, bool
    ):
        raise ValueError("approved plan common collect-box identity is missing")
    clean = str(plan_detail_id)
    if (
        not clean.isascii()
        or not clean.isdigit()
        or (len(clean) > 1 and clean.startswith("0"))
        or not 1 <= len(clean) <= 32
        or int(clean) <= 0
    ):
        raise ValueError("approved plan common collect-box identity is invalid")
    inputs = (context.get("dashboard") or {}).get(
        "_source_identity_inputs"
    )
    collect_box = (
        inputs.get("collect_box") if isinstance(inputs, dict) else None
    )
    detail_id = (
        collect_box.get("detail_id")
        if isinstance(collect_box, dict)
        else None
    )
    if detail_id is not None and detail_id != "":
        if type(detail_id) not in {str, int} or isinstance(detail_id, bool):
            raise ValueError("collect-box corroborating identity is invalid")
        corroborating = str(detail_id)
        if (
            not corroborating.isascii()
            or not corroborating.isdigit()
            or (
                len(corroborating) > 1
                and corroborating.startswith("0")
            )
            or int(corroborating) <= 0
        ):
            raise ValueError("collect-box corroborating identity is invalid")
        if corroborating != clean:
            raise ValueError(
                "collect-box corroborating identity conflicts with approved plan"
            )
    return clean


def _collectbox_identity_context(
    data: dict,
    *,
    require_token: bool,
) -> tuple[dict | None, tuple[int, dict] | None]:
    from shared_platform.collectbox_action import approved_plan_identity

    request_data = dict(data)
    if request_data.get("publication_targets") is None:
        from shared_platform.release_store import default_release_store

        stored_plan = default_release_store().get_plan(
            str(request_data.get("plan_id") or "").strip()
        )
        if (
            isinstance(stored_plan, dict)
            and str(stored_plan.get("product_id") or "")
            == str(request_data.get("offer_id") or "")
            and isinstance(stored_plan.get("targets"), list)
        ):
            request_data["publication_targets"] = list(
                stored_plan["targets"]
            )
    context, failure = _oneclick_approved_context(
        request_data,
        require_token=require_token,
    )
    if failure:
        return None, failure
    assert context is not None
    try:
        identity = approved_plan_identity(context["plan"])
        common_detail_id = _collectbox_common_detail_id(context)
    except (TypeError, ValueError) as error:
        return None, (
            409,
            {
                "schema_version": "collectbox-action-status/v1",
                "ok": False,
                "persisted": False,
                "error": _collectbox_public_error(
                    category="IDENTITY",
                    code="collectbox_source_identity_unavailable",
                    detail=str(error),
                ),
                "external_writes_performed": [],
                "external_write_count": 0,
            },
        )
    return {
        **context,
        "approved_plan_identity": identity,
        "common_collect_box_detail_id": common_detail_id,
    }, None


def _preview_collectbox_action(data: dict) -> tuple[int, dict]:
    from shared_platform.collectbox_action import (
        common_collectbox_identity_digest,
    )

    context, failure = _collectbox_identity_context(
        data,
        require_token=False,
    )
    if failure:
        return failure
    assert context is not None
    identity = context["approved_plan_identity"]
    projection = _with_collectbox_publishability(
        _collectbox_action_store().preview(
            plan=context["plan"],
            common_collectbox_identity_digest=(
                common_collectbox_identity_digest(
                    identity["plan_id"],
                    context["common_collect_box_detail_id"],
                )
            ),
        )
    )
    return 200, _with_collectbox_publishability(projection)


def _collectbox_action_status(data: dict) -> tuple[int, dict]:
    context, failure = _collectbox_identity_context(
        data,
        require_token=False,
    )
    if failure:
        return failure
    assert context is not None
    identity = context["approved_plan_identity"]
    projection = _collectbox_action_store().status(
        plan_id=identity["plan_id"]
    )
    if projection is None:
        return 404, {
            "schema_version": "collectbox-action-status/v1",
            "ok": False,
            "persisted": False,
            "error": _collectbox_public_error(
                category="NOT_FOUND",
                code="collectbox_action_not_found",
                detail="collect-box action has not been started",
            ),
            "external_writes_performed": [],
            "external_write_count": 0,
        }
    public_identity = {
        key: identity[key]
        for key in (
            "plan_id",
            "product_revision",
            "payload_digest",
            "targets_digest",
        )
    }
    if projection["approved_plan"] != public_identity:
        return 409, {
            "schema_version": "collectbox-action-status/v1",
            "ok": False,
            "persisted": True,
            "error": _collectbox_public_error(
                category="IDENTITY",
                code="collectbox_action_plan_identity_drift",
                detail="persisted collect-box action identity drifted",
            ),
            "external_writes_performed": [],
            "external_write_count": 0,
        }
    return 200, _with_collectbox_publishability(projection)


def _start_collectbox_action(data: dict) -> tuple[int, dict]:
    forbidden = {
        "commoncollectboxdetailid",
        "common_collect_box_detail_id",
        "platform_detail_id",
        "platform_receipt",
        "adapter_command",
    }
    if any(str(key).casefold() in forbidden for key in data):
        return 400, {
            "schema_version": "collectbox-action-status/v1",
            "ok": False,
            "persisted": False,
            "error": _collectbox_public_error(
                category="VALIDATION",
                code="client_collectbox_override_forbidden",
                detail="client cannot supply collect-box identity or receipts",
            ),
            "external_writes_performed": [],
            "external_write_count": 0,
        }
    if (
        data.get("confirm_collectbox_action") is not True
        or data.get("approved_by") != "Kyle"
    ):
        return 400, {
            "schema_version": "collectbox-action-status/v1",
            "ok": False,
            "persisted": False,
            "error": _collectbox_public_error(
                category="VALIDATION",
                code="collectbox_explicit_confirmation_required",
                detail=(
                    "literal confirm_collectbox_action=true and "
                    "approved_by=Kyle are required"
                ),
            ),
            "external_writes_performed": [],
            "external_write_count": 0,
        }
    restart_existing = data.get("restart_collectbox_action", False)
    if type(restart_existing) is not bool or (
        restart_existing is False
        and "reimport_request_id" in data
    ):
        return 400, {
            "schema_version": "collectbox-action-status/v1",
            "ok": False,
            "persisted": False,
            "error": _collectbox_public_error(
                category="VALIDATION",
                code="collectbox_restart_request_invalid",
                detail=(
                    "restart_collectbox_action must be a literal boolean; "
                    "reimport_request_id is only valid for a restart"
                ),
            ),
            "external_writes_performed": [],
            "external_write_count": 0,
        }
    context, failure = _collectbox_identity_context(
        data,
        require_token=True,
    )
    if failure:
        return failure
    assert context is not None
    identity = context["approved_plan_identity"]
    if (
        data.get("product_revision") != identity["product_revision"]
        or data.get("payload_digest") != identity["payload_digest"]
        or data.get("targets_digest") != identity["targets_digest"]
        or data.get("plan_id") != identity["plan_id"]
        or str(data.get("offer_id") or "") != identity["offer_id"]
    ):
        return 409, {
            "schema_version": "collectbox-action-status/v1",
            "ok": False,
            "persisted": False,
            "error": _collectbox_public_error(
                category="IDENTITY",
                code="collectbox_approved_plan_echo_mismatch",
                detail="collect-box approved plan echo is stale",
            ),
            "external_writes_performed": [],
            "external_write_count": 0,
        }
    try:
        adapter = _collectbox_platform_adapter()
    except (ImportError, RuntimeError) as error:
        return 503, {
            "schema_version": "collectbox-action-status/v1",
            "ok": False,
            "persisted": False,
            "error": _collectbox_public_error(
                category="CAPABILITY",
                code="collectbox_channel_adapter_unavailable",
                detail=str(error),
            ),
            "external_writes_performed": [],
            "external_write_count": 0,
        }
    now, wait_for_spacing = _collectbox_action_timing()
    try:
        projection = _collectbox_action_store().start(
            plan=context["plan"],
            common_collect_box_detail_id=(
                context["common_collect_box_detail_id"]
            ),
            adapter=adapter,
            now=now,
            wait=wait_for_spacing,
            restart_existing=restart_existing,
            restart_request_id=data.get("reimport_request_id"),
        )
    except (TypeError, ValueError) as error:
        return 409, {
            "schema_version": "collectbox-action-status/v1",
            "ok": False,
            "persisted": False,
            "error": _collectbox_public_error(
                category="IDENTITY",
                code="collectbox_action_identity_conflict",
                detail=str(error),
            ),
            "external_writes_performed": [],
            "external_write_count": 0,
        }
    return 200, _with_collectbox_publishability(projection)


def _oneclick_approved_context(
    data: dict,
    *,
    require_token: bool = True,
) -> tuple[dict | None, tuple[int, dict] | None]:
    """Rebuild the exact approved plan without mutating a run or job."""

    from shared_platform.release_store import default_release_store

    store = default_release_store()
    request_data = dict(data)
    if not require_token and request_data.get("publication_targets") is None:
        # GET preview deliberately accepts only the public plan identity.
        # Rehydrate the full target set from the immutable server-owned plan;
        # never require or trust a browser-provided target list at this seam.
        preview_plan_id = str(request_data.get("plan_id") or "").strip()
        preview_offer_id = str(request_data.get("offer_id") or "").strip()
        preview_plan = store.get_plan(preview_plan_id)
        if (
            isinstance(preview_plan, dict)
            and str(preview_plan.get("product_id") or "") == preview_offer_id
            and isinstance(preview_plan.get("targets"), list)
        ):
            request_data["publication_targets"] = list(
                preview_plan["targets"]
            )
    dashboard, failure = _release_dashboard_for_request(request_data)
    if failure:
        return None, failure
    assert dashboard is not None
    plan_id = str(data.get("plan_id") or "").strip()
    plan = store.get_plan(plan_id)
    stored_payload = (
        plan.get("payload")
        if isinstance(plan, dict) and isinstance(plan.get("payload"), dict)
        else {}
    )
    legacy_shopee_global_binding = (
        "approved_shopee_global_plan" in stored_payload
        or "_approved_shopee_global_plan_record" in stored_payload
    )
    payload, _current_candidate_blockers = _release_plan_payload_from_dashboard(
        dashboard,
        bind_shopee_global_plan=legacy_shopee_global_binding,
    )
    # An APPROVED plan is the immutable execution authority.  The current
    # dashboard may already have moved on to a newer content/category
    # candidate, so its authoring-time blockers must not re-block execution of
    # the approved snapshot.  Exact plan/token/payload and SKU reservation
    # identity are still checked below.
    blockers: list[str] = []
    try:
        preview = store.preview_plan(payload)
    except (TypeError, ValueError) as error:
        blockers = [*blockers, str(error)]
        preview = {}
    token = str(data.get("confirmation_token") or "").strip()
    approval = (plan or {}).get("approval") or {}
    if (
        not plan
        or plan_id != str(preview.get("plan_id") or "")
        or plan.get("status") != "APPROVED"
        or approval.get("status") != "APPROVED"
        or approval.get("approved_by") != "Kyle"
        or (require_token and token != plan.get("confirmation_token"))
        or not _approved_plan_matches_current_payload(plan, preview)
    ):
        blockers = [
            *blockers,
            "approved ReleasePlan no longer matches current immutable facts",
        ]
    reservation = (plan or {}).get("sku_reservation")
    source_reservation = (plan or {}).get("source_sku_reservation")
    if not (
        (
            isinstance(reservation, dict)
            and reservation.get("status") == "ACTIVE"
            and reservation.get("plan_id") == plan_id
            and reservation.get("seller_sku") == (plan or {}).get(
                "seller_sku"
            )
        )
        or (
            isinstance(source_reservation, dict)
            and source_reservation.get("status") == "ACTIVE"
            and (source_reservation.get("assignment") or {}).get(
                "seller_sku"
            )
            == (plan or {}).get("seller_sku")
        )
    ):
        blockers = [
            *blockers,
            "predecessor SKU reservation conflicts with the active plan",
        ]
    blockers = list(dict.fromkeys(str(value) for value in blockers if value))
    if blockers:
        return None, (
            409,
            {
                "ok": False,
                "error": "one-click batch preparation is blocked",
                "blockers": blockers,
                "external_writes_performed": [],
                "canonical_next_action": {
                    "action": "resolve_plan_or_source_identity",
                    "target_focus": None,
                },
            },
        )
    return {
        "dashboard": dashboard,
        "payload": payload,
        "plan": plan,
        "store": store,
    }, None


def _preview_oneclick_release(data: dict) -> tuple[int, dict]:
    from shared_platform.oneclick_release_controlplane import (
        SystemicIdentityError,
        build_batch_preview,
        preview_run_for_plan,
    )

    context, failure = _oneclick_approved_context(data, require_token=False)
    if failure:
        return failure
    assert context is not None
    plan = context["plan"]
    store = context["store"]
    run = store.get_run(f"release-run:{plan['payload_digest'][:24]}")
    preview_run = run or preview_run_for_plan(plan)
    try:
        preview = build_batch_preview(
            plan=plan,
            run=preview_run,
            product_revision=int(context["payload"]["product_revision"]),
            registry=_oneclick_adapter_registry(),
        )
    except SystemicIdentityError as error:
        return 409, {
            "ok": False,
            "error": str(error),
            "external_writes_performed": [],
        }
    preview = _project_oneclick_dispatch_capability(
        {
            **preview,
            "summary": {
                "will_dispatch": preview.get("will_dispatch") or [],
                "manual_after_submit": (
                    preview.get("manual_after_submit") or []
                ),
                "blocked": preview.get("blocked") or [],
                "already_terminal": (
                    preview.get("already_terminal") or []
                ),
            },
        }
    )
    preview["will_dispatch"] = preview["summary"]["will_dispatch"]
    preview["manual_after_submit"] = preview["summary"][
        "manual_after_submit"
    ]
    preview["blocked"] = preview["summary"]["blocked"]
    return 200, {
        "ok": True,
        "persisted": False,
        "external_writes_performed": [],
        "preview": preview,
    }


def _oneclick_release_status(data: dict) -> tuple[int, dict]:
    job = _oneclick_control_store().get_job(
        job_id=str(data.get("job_id") or "").strip() or None,
        plan_id=str(data.get("plan_id") or "").strip() or None,
    )
    if not job:
        return 404, {"ok": False, "error": "one-click release job was not found"}
    return 200, {
        "ok": True,
        "job": _project_oneclick_dispatch_capability(job),
    }


def _oneclick_scope_target_labels(
    job: dict,
    batch_scope: str,
) -> tuple[str, ...]:
    rows = [
        *(job.get("shared_controls") or []),
        *(job.get("targets") or []),
    ]
    labels = [
        str(row.get("target_label") or "")
        for row in rows
        if isinstance(row, dict)
    ]
    if batch_scope == "TIKTOK":
        return tuple(label for label in labels if label.startswith("tiktok:"))
    if batch_scope == "SHOPEE_GLOBAL":
        return tuple(label for label in labels if label == "shopee:GLOBAL")
    if batch_scope == "OZON":
        return tuple(label for label in labels if label == "ozon:RU")
    return ()


def _collectbox_platform_succeeded(plan_id: str, platform: str) -> bool:
    projection = _collectbox_action_store().status(plan_id=plan_id)
    action = projection.get("action") if isinstance(projection, dict) else None
    rows = action.get("platforms") if isinstance(action, dict) else None
    return any(
        isinstance(row, dict)
        and row.get("platform") == platform
        and _collectbox_platform_row_publishable(row, platform)
        for row in (rows or [])
    )


def _collectbox_platform_row_publishable(row: dict, platform: str) -> bool:
    from shared_platform.collectbox_action import CollectBoxTargetOutcome

    if row.get("status") == "SUCCEEDED":
        return True
    if platform != "TIKTOK" or row.get("status") not in {
        "PARTIAL_FAILED",
        "RECONCILIATION_REQUIRED",
    }:
        return False
    outcomes = row.get("target_outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        return False
    expected_keys = {
        "target_label",
        "status",
        "error_code",
        "detail_digest",
    }
    seen: set[str] = set()
    gb_terminal = False
    for outcome in outcomes:
        if not isinstance(outcome, dict) or set(outcome) != expected_keys:
            return False
        try:
            typed_outcome = CollectBoxTargetOutcome(**outcome)
        except (TypeError, ValueError):
            return False
        target_label = typed_outcome.target_label
        status = typed_outcome.status
        if (
            target_label not in _GENERIC_TIKTOK_SAFE_RETRY_LABELS
            or target_label in seen
        ):
            return False
        seen.add(target_label)
        if target_label != "tiktok:GB":
            if status != "SUCCEEDED":
                return False
            continue
        if status in {"SUCCEEDED", "REPAIRED_SUCCEEDED"}:
            gb_terminal = True
            continue
        if status != "FAILED":
            return False
        gb_terminal = True
    return gb_terminal


def _with_collectbox_publishability(projection: dict) -> dict:
    if not isinstance(projection, dict):
        return projection
    action = projection.get("action")
    platforms = action.get("platforms") if isinstance(action, dict) else None
    if not isinstance(platforms, list) or not platforms:
        return projection
    public_platforms = [
        {
            **row,
            "publishable": _collectbox_platform_row_publishable(
                row,
                str(row.get("platform") or ""),
            ),
        }
        if isinstance(row, dict)
        else row
        for row in platforms
    ]
    return {
        **projection,
        "action": {**action, "platforms": public_platforms},
    }


_MIAOSHOU_PLATFORM_SUCCESS_STATUSES = {
    "SUCCEEDED",
    "SUBMITTED_UNVERIFIED",
    "SUCCEEDED_MANUAL_REVIEW",
}
_MIAOSHOU_PLATFORM_NAMES = {
    "TIKTOK": "TikTok",
    "SHOPEE_GLOBAL": "Shopee 全球商品",
    "OZON": "Ozon",
}


def _complete_oneclick_platform_batch(
    *,
    control_store,
    job_id: str,
    target_labels: tuple[str, ...],
    batch_scope: str,
) -> tuple[int, dict]:
    """Run one explicit platform batch and return its final Miaoshou result.

    The HTTP request is the product boundary: the browser does not poll the
    internal control-plane ledger and does not interpret reconciliation or
    manual-acceptance states.  Those durable facts may remain available for
    diagnostics, but a Miaoshou-accepted submission is a successful button
    result for this stage of the product.
    """

    from shared_platform.oneclick_release_controlplane import (
        OneClickReleaseWorker,
    )

    display_name = _MIAOSHOU_PLATFORM_NAMES[batch_scope]
    worker = OneClickReleaseWorker(
        control_store,
        _oneclick_adapter_registry,
        dispatch_enabled=_oneclick_dispatch_enabled,
    )
    # Preparation and dispatch are separate transitions.  This bound is
    # deliberately derived from the selected platform scope and contains no
    # sleep/retry loop against the external API.
    transition_limit = max(16, (len(target_labels) * 6) + 8)
    try:
        for _ in range(transition_limit):
            if not worker.advance_once(job_id):
                break
        _consume_oneclick_outcome_receipts(control_store)
        job = control_store.get_job(job_id=job_id)
    except Exception:
        return 500, {
            "schema_version": "miaoshou-platform-publish-result/v1",
            "ok": False,
            "platform": batch_scope,
            "success": False,
            "message": f"{display_name} 发布失败",
            "target_count": len(target_labels),
            "successful_target_count": 0,
            "failed_targets": list(target_labels),
            "retryable": True,
        }

    rows = {
        str(row.get("target_label") or ""): row
        for row in (
            *((job or {}).get("shared_controls") or ()),
            *((job or {}).get("targets") or ()),
        )
        if str(row.get("target_label") or "") in target_labels
    }
    successful = [
        label
        for label in target_labels
        if (rows.get(label) or {}).get("status")
        in _MIAOSHOU_PLATFORM_SUCCESS_STATUSES
    ]
    failed = [label for label in target_labels if label not in successful]
    result = {
        "schema_version": "miaoshou-platform-publish-result/v1",
        "ok": not failed,
        "platform": batch_scope,
        "success": not failed,
        "message": (
            f"{display_name} 发布成功"
            if not failed
            else f"{display_name} 发布失败"
        ),
        "target_count": len(target_labels),
        "successful_target_count": len(successful),
        "failed_targets": failed,
        "retryable": True,
    }
    # Business rejection is still a completed request/response interaction.
    # Keep HTTP 200 and let the explicit ``success`` field drive the four-state
    # UI, avoiding browser-level transport noise for a normal vendor refusal.
    return 200, result


def _start_oneclick_release(
    data: dict,
    *,
    batch_scope: str = "TIKTOK",
    require_collectbox_platform: str | None = "TIKTOK",
) -> tuple[int, dict]:
    """Create/wake one durable job and return without channel I/O."""

    if data.get("confirm_publish") is not True:
        return 400, {
            "ok": False,
            "error": "explicit confirm_publish=true is required",
        }
    context, failure = _oneclick_approved_context(data)
    if failure:
        return failure
    assert context is not None
    if (
        require_collectbox_platform
        and not _collectbox_platform_succeeded(
            context["plan"]["plan_id"],
            require_collectbox_platform,
        )
    ):
        return 409, {
            "ok": False,
            "error": _collectbox_public_error(
                category="CAPABILITY",
                code="step1_collectbox_required",
                detail=(
                    f"complete the {require_collectbox_platform} collect-box "
                    "action before starting this platform"
                ),
            ),
            "external_writes_performed": [],
            "canonical_next_action": {
                "action": "start_collectbox_action",
                "target_focus": None,
            },
        }
    if (
        require_collectbox_platform == "TIKTOK"
        and not _tiktok_collectbox_publish_proof_available(context["plan"])
    ):
        return 409, {
            "ok": False,
            "error": _collectbox_public_error(
                category="CAPABILITY",
                code="step1_collectbox_publish_proof_required",
                detail=(
                    "reimport TikTok collect-box drafts to persist exact "
                    "per-target publication identities"
                ),
            ),
            "external_writes_performed": [],
            "canonical_next_action": {
                "action": "restart_collectbox_action",
                "target_focus": None,
            },
        }
    with _release_execution_lock:
        context, failure = _oneclick_approved_context(data)
        if failure:
            return failure
        assert context is not None
        plan = context["plan"]
        if (
            require_collectbox_platform
            and not _collectbox_platform_succeeded(
                plan["plan_id"],
                require_collectbox_platform,
            )
        ):
            return 409, {
                "ok": False,
                "error": _collectbox_public_error(
                    category="CAPABILITY",
                    code="step1_collectbox_required",
                    detail=(
                        f"complete the {require_collectbox_platform} "
                        "collect-box action before starting this platform"
                    ),
                ),
                "external_writes_performed": [],
            }
        if (
            require_collectbox_platform == "TIKTOK"
            and not _tiktok_collectbox_publish_proof_available(plan)
        ):
            return 409, {
                "ok": False,
                "error": _collectbox_public_error(
                    category="CAPABILITY",
                    code="step1_collectbox_publish_proof_required",
                    detail=(
                        "TikTok collect-box publication proof changed before "
                        "the release batch was created"
                    ),
                ),
                "external_writes_performed": [],
            }
        run = context["store"].start_run(plan["plan_id"])
        registry = _oneclick_adapter_registry()
        control_store = _oneclick_control_store()
        job = control_store.ensure_job(
            plan=context["store"].get_plan(plan["plan_id"]),
            run=run,
            product_revision=int(context["payload"]["product_revision"]),
            registry=registry,
        )
        target_labels = _oneclick_scope_target_labels(job, batch_scope)
        if not target_labels:
            return 409, {
                "ok": False,
                "error": {
                    "category": "CAPABILITY",
                    "code": "legacy_oneclick_job_requires_successor",
                    "detail_digest": _server_canonical_digest(
                        {
                            "batch_scope": batch_scope,
                            "plan_id": plan["plan_id"],
                        }
                    ),
                },
                "external_writes_performed": [],
                "canonical_next_action": {
                    "action": "create_platform_successor_job",
                    "target_focus": None,
                },
            }
        dispatch_capability = _oneclick_dispatch_capability()
        job = control_store.set_dispatch_capability(
            job["job_id"],
            enabled=dispatch_capability["enabled"],
        )
        job = control_store.start_explicit_batch(
            job["job_id"],
            target_labels=target_labels,
        )
        active_scope = tuple(job.get("batch_scope_targets") or ())
        if active_scope != target_labels:
            return 409, {
                "ok": False,
                "error": {
                    "category": "CAPABILITY",
                    "code": "platform_dispatch_in_progress",
                    "detail_digest": _server_canonical_digest(
                        {
                            "requested_scope": list(target_labels),
                            "active_scope": list(active_scope),
                            "job_id": job["job_id"],
                        }
                    ),
                },
                "batch_scope": batch_scope,
                "external_writes_performed": [],
                "canonical_next_action": {
                    "action": "wait_for_platform_dispatch",
                    "target_focus": (
                        active_scope[0] if active_scope else None
                    ),
                },
            }
        job_id = str(job["job_id"])
        # Serialize use of the shared durable ledger, but do not reject a
        # second platform.  A concurrent button waits here and then starts its
        # own fresh explicit batch; it never inherits the first platform's
        # result or receives platform_dispatch_in_progress.
        return _complete_oneclick_platform_batch(
            control_store=control_store,
            job_id=job_id,
            target_labels=target_labels,
            batch_scope=batch_scope,
        )


_INDEPENDENT_TIKTOK_TARGETS = TIKTOK_PUBLISH_TARGETS


def _tiktok_release_store():
    from shared_platform.release_store import default_release_store

    return default_release_store()


def _tiktok_publisher():
    from modules.miaoshou.tiktok_publisher import production_tiktok_publisher

    return production_tiktok_publisher()


def _tiktok_snapshot_failure(detail: str) -> tuple[int, dict]:
    return 409, {
        "schema_version": "miaoshou-platform-publish-result/v1",
        "ok": False,
        "success": False,
        "platform": "TIKTOK",
        "message": "TikTok 发布失败：批准快照或妙手草稿身份不完整",
        "error": {
            "category": "IDENTITY",
            "code": "tiktok_approved_snapshot_invalid",
            "detail_digest": _server_canonical_digest(detail),
        },
        "external_write_count": 0,
        "target_count": len(_INDEPENDENT_TIKTOK_TARGETS),
        "successful_target_count": 0,
        "failed_targets": list(_INDEPENDENT_TIKTOK_TARGETS),
        "retryable": True,
    }


def _build_approved_tiktok_publish_snapshot(data: dict) -> dict:
    from shared_platform.collectbox_action import approved_plan_identity
    from shared_platform.product_snapshot import (
        build_approved_tiktok_publish_snapshot,
    )

    if type(data.get("plan_id")) is not str or not data["plan_id"].strip():
        raise ValueError("plan_id is required")
    plan = _tiktok_release_store().get_plan(data["plan_id"].strip())
    identity = approved_plan_identity(plan)
    request_identity = {
        "plan_id": data.get("plan_id"),
        "offer_id": str(data.get("offer_id")),
        "product_revision": data.get("product_revision"),
        "payload_digest": data.get("payload_digest"),
        "targets_digest": data.get("targets_digest"),
    }
    if request_identity != identity:
        raise ValueError("request does not match the approved plan identity")
    if (
        type(data.get("confirmation_token")) is not str
        or data["confirmation_token"] != plan.get("confirmation_token")
        or data.get("publication_targets") != plan.get("targets")
    ):
        raise ValueError("request does not match the approved plan authority")
    contexts = _collectbox_action_store().internal_tiktok_publish_contexts(
        plan_id=identity["plan_id"]
    )
    return build_approved_tiktok_publish_snapshot(
        plan,
        collectbox_contexts=contexts,
    )


def _safe_tiktok_provider_field(value: object, fallback: str) -> str:
    if type(value) is not str or not value.strip():
        return fallback
    return _safe_platform_publish_error(RuntimeError(value.strip()))


def _project_tiktok_publish_receipt(
    *, snapshot: dict, receipt: object
) -> tuple[bool, dict]:
    if not isinstance(receipt, dict):
        raise ValueError("TikTok publisher returned a non-mapping receipt")
    if (
        receipt.get("schema_version") != "tiktok-publish-receipt/v1"
        or receipt.get("offer_id") != snapshot["offer_id"]
        or receipt.get("plan_id") != snapshot["plan_id"]
        or receipt.get("snapshot_digest") != _server_canonical_digest(snapshot)
    ):
        raise ValueError("TikTok publisher receipt identity drifted")
    rows = receipt.get("targets")
    if not isinstance(rows, list) or [
        row.get("target_label") if isinstance(row, dict) else None for row in rows
    ] != list(_INDEPENDENT_TIKTOK_TARGETS):
        raise ValueError("TikTok publisher receipt targets drifted")
    counts = {"ACCEPTED": 0, "REJECTED": 0, "UNKNOWN": 0, "NOT_ATTEMPTED": 0}
    public_rows = []
    external_write_counts: list[int | None] = []
    write_request_counts: list[int] = []
    for row in rows:
        outcome = row.get("outcome")
        if outcome not in counts:
            raise ValueError("TikTok publisher receipt outcome is invalid")
        external_write_count = row.get("external_write_count")
        write_request_count = row.get("write_request_count")
        if (
            (
                external_write_count is not None
                and (
                    type(external_write_count) is not int
                    or external_write_count < 0
                )
            )
            or type(write_request_count) is not int
            or write_request_count < 0
        ):
            raise ValueError("TikTok publisher write counts are invalid")
        counts[outcome] += 1
        external_write_counts.append(external_write_count)
        write_request_counts.append(write_request_count)
        public_row = {
            "target_label": row["target_label"],
            "outcome": outcome,
            "provider_code": _safe_tiktok_provider_field(
                row.get("provider_code"), "provider_result_unavailable"
            ),
            "provider_reason": _safe_tiktok_provider_field(
                row.get("provider_reason"), "妙手未返回具体原因"
            ),
            "external_write_count": external_write_count,
            "write_request_count": write_request_count,
        }
        public_rows.append(public_row)
        _PLATFORM_PUBLISH_LOGGER.info(
            "platform_publish_target platform=TIKTOK offer_id=%s "
            "target=%s outcome=%s provider_code=%s provider_reason=%s "
            "write_request_count=%s external_write_count=%s",
            snapshot["offer_id"],
            public_row["target_label"],
            public_row["outcome"],
            public_row["provider_code"],
            public_row["provider_reason"],
            public_row["write_request_count"],
            public_row["external_write_count"],
        )
    expected_counts = {
        "accepted_target_count": counts["ACCEPTED"],
        "rejected_target_count": counts["REJECTED"],
        "unknown_target_count": counts["UNKNOWN"],
        "not_attempted_target_count": counts["NOT_ATTEMPTED"],
    }
    if any(receipt.get(key) != value for key, value in expected_counts.items()):
        raise ValueError("TikTok publisher receipt counts drifted")
    success = counts["ACCEPTED"] == len(_INDEPENDENT_TIKTOK_TARGETS)
    failed = [
        row["target_label"] for row in public_rows if row["outcome"] != "ACCEPTED"
    ]
    first_failure = next(
        (row for row in public_rows if row["outcome"] != "ACCEPTED"), None
    )
    message = "TikTok 发布成功"
    error = None
    if first_failure is not None:
        message = f"TikTok 发布未全部成功：{first_failure['provider_reason']}"
        error = {
            "category": "PROVIDER",
            "code": "tiktok_target_not_accepted",
            "provider_code": first_failure["provider_code"],
            "provider_reason": first_failure["provider_reason"],
            "detail_digest": _server_canonical_digest(first_failure),
        }
    return success, {
        "schema_version": "miaoshou-platform-publish-result/v1",
        "ok": success,
        "success": success,
        "platform": "TIKTOK",
        "message": message,
        "target_count": len(_INDEPENDENT_TIKTOK_TARGETS),
        "successful_target_count": counts["ACCEPTED"],
        "accepted_target_count": counts["ACCEPTED"],
        "rejected_target_count": counts["REJECTED"],
        "unknown_target_count": counts["UNKNOWN"],
        "not_attempted_target_count": counts["NOT_ATTEMPTED"],
        "failed_targets": failed,
        "write_request_count": sum(write_request_counts),
        "external_write_count": (
            sum(external_write_counts)
            if all(value is not None for value in external_write_counts)
            else None
        ),
        "retryable": True,
        "targets": public_rows,
        **({"error": error} if error is not None else {}),
    }


def _start_tiktok_release(data: dict) -> tuple[int, dict]:
    if data.get("confirm_publish") is not True:
        return 400, {
            "ok": False,
            "success": False,
            "platform": "TIKTOK",
            "message": "TikTok 发布失败：需要明确确认发布",
            "retryable": True,
        }
    try:
        snapshot = _build_approved_tiktok_publish_snapshot(data)
    except (TypeError, ValueError) as error:
        return _tiktok_snapshot_failure(str(error))
    try:
        publisher = _tiktok_publisher()
    except (ImportError, RuntimeError) as error:
        return 503, {
            "ok": False,
            "success": False,
            "platform": "TIKTOK",
            "message": "TikTok 发布失败：独立发布器暂不可用",
            "error": {
                "category": "CAPABILITY",
                "code": "tiktok_publisher_unavailable",
                "detail_digest": _server_canonical_digest(str(error)),
            },
            "external_write_count": 0,
            "retryable": True,
        }
    with _tiktok_publish_lock:
        try:
            receipt = publisher.publish(snapshot)
            success, result = _project_tiktok_publish_receipt(
                snapshot=snapshot,
                receipt=receipt,
            )
        except Exception as error:
            reason = _safe_platform_publish_error(error)
            _PLATFORM_PUBLISH_LOGGER.warning(
                "platform_publish_failed platform=TIKTOK offer_id=%s reason=%s",
                snapshot["offer_id"],
                reason,
            )
            return 200, {
                "schema_version": "miaoshou-platform-publish-result/v1",
                "ok": False,
                "success": False,
                "platform": "TIKTOK",
                "message": f"TikTok 发布失败：{reason}",
                "target_count": len(_INDEPENDENT_TIKTOK_TARGETS),
                "successful_target_count": 0,
                "failed_targets": list(_INDEPENDENT_TIKTOK_TARGETS),
                "retryable": True,
            }
    return 200, result


def _approved_shopee_global_publish_facts(payload: dict) -> dict:
    seller_sku = payload.get("seller_sku")
    listing_copy = payload.get("listing_copy")
    pricing = payload.get("pricing")
    if (
        not isinstance(seller_sku, str)
        or not seller_sku.strip()
        or not isinstance(listing_copy, dict)
        or not isinstance(pricing, dict)
    ):
        raise ValueError("approved Shopee global facts are incomplete")
    candidates = [
        row
        for row in listing_copy.get("candidates") or []
        if isinstance(row, dict)
        and str(row.get("channel") or "").lower() == "shopee"
        and str(row.get("site") or "").upper() in {"CNSC", "GLOBAL"}
        and row.get("policy_check") == "passed"
    ]
    description = listing_copy.get("shopee_description_en")
    if (
        len(candidates) != 1
        or not isinstance(candidates[0].get("title"), str)
        or not candidates[0]["title"].strip()
        or not isinstance(description, str)
        or not description.strip()
    ):
        raise ValueError("approved Shopee global copy is not exact")
    source = pricing.get("master_price_source")
    selected = pricing.get("selected_targets")
    if not isinstance(source, dict) or not isinstance(selected, dict):
        raise ValueError("approved Shopee global price source is unavailable")
    region = source.get("region")
    target_key = source.get("target_key")
    selected_target = selected.get(f"shopee:{region}")
    if not isinstance(selected_target, dict):
        raise ValueError("approved Shopee global price target is unavailable")
    selected_source = selected_target.get("source")
    derived = selected_target.get("derived_preview")
    try:
        global_price = Decimal(str(derived.get("global_original_price_cny")))
    except (AttributeError, InvalidOperation, TypeError, ValueError):
        raise ValueError("approved Shopee global price is invalid") from None
    if (
        not isinstance(region, str)
        or region not in {"PH", "MY", "TH", "VN"}
        or not isinstance(target_key, str)
        or not target_key
        or not isinstance(selected_source, dict)
        or selected_source.get("target_key") != target_key
        or not global_price.is_finite()
        or global_price <= 0
    ):
        raise ValueError("approved Shopee global price binding is invalid")
    return {
        "seller_sku": seller_sku.strip(),
        "region": region,
        "title": candidates[0]["title"].strip(),
        # Preserve the approved description byte-for-byte.
        "description": description,
        "global_original_price_cny": float(global_price),
    }


def _approved_ozon_publish_facts(payload: dict) -> dict:
    seller_sku = payload.get("seller_sku")
    product_facts = payload.get("product_facts")
    listing_copy = payload.get("listing_copy")
    images = payload.get("images")
    pricing = payload.get("pricing")
    if (
        not isinstance(seller_sku, str)
        or not seller_sku.strip()
        or not isinstance(product_facts, dict)
        or not isinstance(listing_copy, dict)
        or not isinstance(images, list)
        or not isinstance(pricing, dict)
    ):
        raise ValueError("approved Ozon facts are incomplete")
    candidates = [
        row
        for row in listing_copy.get("candidates") or []
        if isinstance(row, dict)
        and str(row.get("channel") or "").lower() == "ozon"
        and str(row.get("site") or "").upper() == "RU"
        and row.get("policy_check") == "passed"
    ]
    package = product_facts.get("package_cm")
    selected = pricing.get("selected_targets")
    target = selected.get("ozon:RU") if isinstance(selected, dict) else None
    derived = target.get("derived_preview") if isinstance(target, dict) else None
    try:
        size = (float(package[0]), float(package[1]))
        price = int(Decimal(str(derived.get("price_cny"))))
        old_price = int(Decimal(str(derived.get("old_price_cny"))))
    except (IndexError, InvalidOperation, TypeError, ValueError, AttributeError):
        raise ValueError("approved Ozon size or price is invalid") from None
    ordered_images = sorted(
        (
            row
            for row in images
            if isinstance(row, dict)
            and isinstance(row.get("position"), int)
            and isinstance(row.get("image_url"), str)
            and row["image_url"].startswith("https://")
        ),
        key=lambda row: row["position"],
    )
    if (
        len(candidates) != 1
        or not isinstance(candidates[0].get("title"), str)
        or not candidates[0]["title"].strip()
        or len(ordered_images) != len(images)
        or len({row["position"] for row in ordered_images}) != len(images)
        or any(value <= 0 or not math.isfinite(value) for value in size)
        or price <= 0
        or old_price <= price
    ):
        raise ValueError("approved Ozon publication facts are invalid")
    return {
        "seller_sku": seller_sku.strip(),
        "title": candidates[0]["title"].strip(),
        "size": size,
        "price": price,
        "old_price": old_price,
        "images": [row["image_url"] for row in ordered_images],
    }


def _safe_platform_publish_error(error: Exception) -> str:
    """Return a useful reason without exposing credentials or raw URLs."""

    detail = " ".join(str(error).split())
    detail = re.sub(r'''https?://[^\s"'<>]+''', "[url]", detail)
    detail = re.sub(
        r'''(?i)(["']?(?:access[_-]?token|refresh[_-]?token|partner[_-]?key|api[_-]?key|client[_-]?secret|secret|signature|token|key)["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,}\]]+)''',
        r'\1"[redacted]"',
        detail,
    )
    detail = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,}\]]+",
        r"\1[redacted]",
        detail,
    )
    detail = re.sub(
        r"(?i)(bearer\s+)[^\s,}\]]+",
        r"\1[redacted]",
        detail,
    )
    return (detail or type(error).__name__)[:300]


def _start_shopee_global_release(data: dict) -> tuple[int, dict]:
    """Create or update only the approved Shopee CNSC global product."""

    if data.get("confirm_publish") is not True:
        return 400, {
            "ok": False,
            "success": False,
            "platform": "SHOPEE_GLOBAL",
            "message": "Shopee 全球商品发布失败：需要确认发布",
            "retryable": True,
        }
    context, failure = _oneclick_approved_context(data)
    if failure:
        return failure
    try:
        facts = _approved_shopee_global_publish_facts(context["payload"])
    except (TypeError, ValueError) as error:
        return 409, {
            "ok": False,
            "success": False,
            "platform": "SHOPEE_GLOBAL",
            "message": f"Shopee 全球商品发布失败：{_safe_platform_publish_error(error)}",
            "retryable": True,
        }
    with _shopee_global_publish_lock:
        refreshed, failure = _oneclick_approved_context(data)
        if failure:
            return failure
        try:
            current_facts = _approved_shopee_global_publish_facts(
                refreshed["payload"]
            )
            if current_facts != facts:
                raise ValueError("approved Shopee global facts changed before dispatch")
            from modules.shopee.publish import publish_match_key

            result = publish_match_key(
                facts["seller_sku"],
                facts["region"],
                dry_run=False,
                global_only=True,
                publish_shops=False,
                title_override=facts["title"],
                description_override=facts["description"],
                global_original_price_cny_override=(
                    facts["global_original_price_cny"]
                ),
            )
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError("Miaoshou/Shopee did not accept the global product")
        except Exception as error:
            reason = _safe_platform_publish_error(error)
            _PLATFORM_PUBLISH_LOGGER.warning(
                "platform_publish_failed platform=SHOPEE_GLOBAL offer_id=%s reason=%s",
                str(data.get("offer_id") or ""),
                reason,
            )
            return 409, {
                "schema_version": "official-platform-publish-result/v1",
                "ok": False,
                "success": False,
                "platform": "SHOPEE_GLOBAL",
                "message": f"Shopee 全球商品发布失败：{reason}",
                "target_count": 1,
                "successful_target_count": 0,
                "failed_targets": ["shopee:GLOBAL"],
                "retryable": True,
            }
    return 200, {
        "schema_version": "official-platform-publish-result/v1",
        "ok": True,
        "success": True,
        "platform": "SHOPEE_GLOBAL",
        "message": "Shopee 全球商品发布成功",
        "target_count": 1,
        "successful_target_count": 1,
        "failed_targets": [],
        "retryable": True,
    }


def _start_ozon_release(data: dict) -> tuple[int, dict]:
    """Submit only the approved Ozon product through the official API path."""

    if data.get("confirm_publish") is not True:
        return 400, {
            "ok": False,
            "success": False,
            "platform": "OZON",
            "message": "Ozon 发布失败：需要确认发布",
            "retryable": True,
        }
    context, failure = _oneclick_approved_context(data)
    if failure:
        return failure
    try:
        facts = _approved_ozon_publish_facts(context["payload"])
    except (TypeError, ValueError) as error:
        return 409, {
            "ok": False,
            "success": False,
            "platform": "OZON",
            "message": f"Ozon 发布失败：{_safe_platform_publish_error(error)}",
            "retryable": True,
        }
    with _ozon_publish_lock:
        refreshed, failure = _oneclick_approved_context(data)
        if failure:
            return failure
        try:
            current_facts = _approved_ozon_publish_facts(refreshed["payload"])
            if current_facts != facts:
                raise ValueError("approved Ozon facts changed before dispatch")
            from modules.ozon.migrate_batch import migrate_one

            result = migrate_one(
                facts["seller_sku"],
                allow_deepseek=False,
                title_candidate=facts["title"],
                product_size_cm=facts["size"],
                quantity=1,
                price_cny_override=facts["price"],
                old_price_cny_override=facts["old_price"],
                price_source_override="approved_release_plan",
                price_label_override="ozon:RU",
                image_urls_override=facts["images"],
                process_images=False,
                wait_for_import=False,
                skip_rich_content=True,
                skip_mapping_write=True,
            )
            accepted = (
                isinstance(result, dict)
                and not result.get("errors")
                and (
                    result.get("ok") is True
                    or (
                        bool(result.get("task_id"))
                        and result.get("import_dispatch_outcome") == "accepted"
                    )
                )
            )
            if not accepted:
                raise RuntimeError("Ozon official API did not accept the import")
        except Exception as error:
            reason = _safe_platform_publish_error(error)
            _PLATFORM_PUBLISH_LOGGER.warning(
                "platform_publish_failed platform=OZON offer_id=%s reason=%s",
                str(data.get("offer_id") or ""),
                reason,
            )
            return 409, {
                "schema_version": "official-platform-publish-result/v1",
                "ok": False,
                "success": False,
                "platform": "OZON",
                "message": f"Ozon 发布失败：{reason}",
                "target_count": 1,
                "successful_target_count": 0,
                "failed_targets": ["ozon:RU"],
                "retryable": True,
            }
    return 200, {
        "schema_version": "official-platform-publish-result/v1",
        "ok": True,
        "success": True,
        "platform": "OZON",
        "message": "Ozon API 已接受发布",
        "target_count": 1,
        "successful_target_count": 1,
        "failed_targets": [],
        "retryable": True,
    }
def _publish_selected_release(data: dict) -> tuple[int, dict]:
    """Execute the approved plan once through durable per-target adapters."""
    from domains.channel_operations.release_executor import AdapterExecutionRequest
    from shared_platform.release_store import (
        ReleaseAuthorizationError,
        ReleaseStoreError,
        default_release_store,
    )

    if data.get("confirm_publish") is not True:
        return 400, {
            "ok": False,
            "error": "explicit confirm_publish=true is required",
        }
    store = default_release_store()
    plan_id = str(data.get("plan_id") or "").strip()
    token = str(data.get("confirmation_token") or "").strip()
    gate, failure = _release_execution_readonly_gate(data, store=store)
    if failure:
        return failure
    assert gate is not None

    with _release_execution_lock:
        # Repeat the whole pure gate under the execution lock before retry,
        # recovery, begin_target, or any adapter call can mutate durable state.
        gate, failure = _release_execution_readonly_gate(data, store=store)
        if failure:
            return failure
        assert gate is not None
        dashboard = gate["dashboard"]
        plan_payload = gate["payload"]
        run = gate["run"]
        predecessor_run = gate.get("predecessor_run")
        registry = gate["registry"]
        target_rows = gate["target_rows"]
        assert run is not None
        run = store.get_run(run["run_id"]) or run
        interrupted = [
            str(row.get("target_label") or "")
            for row in (run.get("targets") or ())
            if row.get("status") == "RUNNING"
        ]
        if interrupted:
            return _release_reconciliation_required(
                run,
                interrupted,
            )
        failed_targets = [
            row
            for row in (run.get("targets") or ())
            if row.get("status") in {"FAILED", "RECONCILIATION_REQUIRED"}
        ]
        if failed_targets:
            safe_retry_targets = [
                str(row.get("target_label") or "")
                for row in failed_targets
                if _generic_tiktok_safe_retry_target(row)
            ]
            if safe_retry_targets:
                try:
                    run = store.retry_failed_targets(
                        run["run_id"],
                        safe_retry_targets,
                    )
                except (ReleaseAuthorizationError, ReleaseStoreError) as error:
                    return 409, {
                        "ok": False,
                        "code": "safe_retry_authorization_rejected",
                        "error": "safe TikTok retry authorization was rejected",
                        "detail": str(error),
                        "blocked_targets": safe_retry_targets,
                        "external_writes_performed": [],
                        "run": store.get_run(run["run_id"]) or run,
                    }

        recovery_actions = _release_target_recovery_actions(
            run,
            predecessor_run=predecessor_run,
            target_rows=target_rows,
            registry=registry,
        )
        if not any(action.get("runnable") is True for action in recovery_actions):
            if run.get("status") in {
                "SUCCEEDED",
                "COMPLETED_WITH_MANUAL_VERIFICATION",
                "AWAITING_MANUAL_VERIFICATION",
            }:
                return 200, {
                    "ok": True,
                    "completed": run.get("status")
                    in {"SUCCEEDED", "COMPLETED_WITH_MANUAL_VERIFICATION"},
                    "partial": False,
                    "awaiting_manual_verification": (
                        run.get("status") == "AWAITING_MANUAL_VERIFICATION"
                    ),
                    "message": "release run is already terminal; no target was resubmitted",
                    "external_writes_performed": [],
                    "target_recovery_actions": recovery_actions,
                    "run": run,
                    "dashboard": _product_workspace_view(dashboard),
                }
            return 409, {
                "ok": False,
                "code": "no_runnable_release_targets",
                "error": (
                    "no target is eligible for a first attempt or an exact "
                    "zero-write safe retry"
                ),
                "external_writes_performed": [],
                "target_recovery_actions": recovery_actions,
                "blocked_targets": [
                    action["target_label"]
                    for action in recovery_actions
                    if action.get("action_kind") not in {"TERMINAL"}
                ],
                "run": run,
            }

        target_by_label = {
            f"{target.get('channel')}:{target.get('site')}": target
            for target in target_rows
        }
        channel_order = {"miaoshou": 0, "tiktok": 1, "shopee": 2, "ozon": 3}
        ordered = sorted(
            (run.get("targets") or ()),
            key=lambda row: (
                channel_order.get(
                    str(row.get("target_label") or "").split(":", 1)[0],
                    99,
                ),
                str(row.get("target_label") or ""),
            ),
        )
        external_writes: list[str] = []
        recovery_action_by_label = {
            str(action.get("target_label") or ""): action
            for action in recovery_actions
        }
        for durable_target in ordered:
            label = str(durable_target.get("target_label") or "")
            if (
                label == "miaoshou:COMMON"
                or durable_target.get("status")
                in {"SUCCEEDED", "SUBMITTED_UNVERIFIED", "MANUALLY_VERIFIED"}
                or (
                    recovery_action_by_label.get(label, {}).get("runnable")
                    is not True
                )
            ):
                continue
            channel, site = label.split(":", 1)
            current_run = store.get_run(run["run_id"]) or run
            statuses = {
                row["target_label"]: row.get("status")
                for row in (current_run.get("targets") or ())
            }
            dependencies = _release_target_dependencies(label, statuses)
            if dependencies and not all(
                statuses.get(dependency) == "SUCCEEDED"
                for dependency in dependencies
            ):
                continue

            # An adapter can advance an operational workbench revision, but it
            # must never silently carry a plan across commercial/input drift or
            # supersession. Rebuild the exact read-only gate before each begin.
            fresh_gate, fresh_failure = _release_execution_readonly_gate(
                data,
                store=store,
            )
            if fresh_failure:
                status, response = fresh_failure
                blocked = dict(response)
                blocked.update(
                    {
                        "blocked_target": label,
                        "external_writes_performed": list(external_writes),
                        "run": store.get_run(run["run_id"]) or run,
                    }
                )
                return status, blocked
            assert fresh_gate is not None
            dashboard = fresh_gate["dashboard"]
            plan_payload = fresh_gate["payload"]
            registry = fresh_gate["registry"]
            target_by_label = {
                f"{target.get('channel')}:{target.get('site')}": target
                for target in fresh_gate["target_rows"]
            }
            current_run = fresh_gate["run"] or run
            current_target = next(
                (
                    row
                    for row in (current_run.get("targets") or ())
                    if row.get("target_label") == label
                ),
                None,
            )
            if not current_target or current_target.get("status") != "PENDING":
                continue
            plan_target = target_by_label.get(label) or {}
            registration = registry.get(str(plan_target.get("adapter") or ""))
            if not registration or not registration.executable:
                continue
            result = None
            try:
                store.begin_target(run["run_id"], label)
                request = AdapterExecutionRequest(
                    plan_id=plan_id,
                    confirmation_token=token,
                    approval_scope_digest=str(
                        plan_payload.get("omnichannel_scope_digest") or ""
                    ),
                    product_id=str(plan_payload["product_id"]),
                    seller_sku=str(plan_payload["seller_sku"]),
                    product_package_id=str(plan_payload["product_package_id"]),
                    content_package_id=str(plan_payload["content_package_id"]),
                    channel=channel,
                    site=site,
                    target_label=label,
                    idempotency_key=str(durable_target["idempotency_key"]),
                )
                result = registration.execute(request)  # type: ignore[misc]
                if result.succeeded and result.readback_verified:
                    store.record_target_success(
                        run["run_id"],
                        label,
                        external_id=result.external_reference,
                        readback_evidence=result.readback_evidence,
                    )
                    external_writes.append(label)
                elif (
                    result.succeeded
                    and result.submission_accepted
                    and result.external_reference
                    and result.readback_evidence
                ):
                    store.record_target_submission(
                        run["run_id"],
                        label,
                        external_id=result.external_reference,
                        submission_evidence=result.readback_evidence,
                        detail=result.detail,
                    )
                    external_writes.append(label)
                else:
                    failure_evidence = (
                        dict(result.readback_evidence)
                        if isinstance(result.readback_evidence, dict)
                        and result.readback_evidence
                        else None
                    )
                    store.record_target_failure(
                        run["run_id"],
                        label,
                        error=result.detail,
                        external_id=result.external_reference,
                        failure_evidence=failure_evidence,
                    )
                    if failure_evidence:
                        external_writes.extend(
                            str(value)
                            for value in (
                                failure_evidence.get(
                                    "external_writes_performed"
                                )
                                or ()
                            )
                            if str(value)
                        )
            except (ReleaseAuthorizationError, ReleaseStoreError) as error:
                if _adapter_result_has_external_outcome(result):
                    return _uncertain_adapter_receipt_response(
                        store=store,
                        run=run,
                        label=label,
                        result=result,
                        error=error,
                        prior_external_writes=external_writes,
                    )
                return 409, {
                    "ok": False,
                    "error": "release execution stopped by durable authorization",
                    "detail": str(error),
                    "blocked_target": label,
                    "external_writes_performed": list(external_writes),
                    "run": store.get_run(run["run_id"]) or run,
                }
            except Exception as error:
                if _adapter_result_has_external_outcome(result):
                    return _uncertain_adapter_receipt_response(
                        store=store,
                        run=run,
                        label=label,
                        result=result,
                        error=error,
                        prior_external_writes=external_writes,
                    )
                failure_evidence = getattr(
                    error,
                    "external_write_evidence",
                    None,
                )
                external_reference = getattr(
                    error,
                    "external_reference",
                    None,
                )
                detected_writes = [
                    str(value)
                    for value in (
                        (
                            failure_evidence.get(
                                "external_writes_performed"
                            )
                            if isinstance(failure_evidence, dict)
                            else ()
                        )
                        or ()
                    )
                    if str(value)
                ]
                try:
                    store.record_target_failure(
                        run["run_id"],
                        label,
                        error=str(error),
                        external_id=external_reference,
                        failure_evidence=failure_evidence,
                    )
                except (ReleaseAuthorizationError, ReleaseStoreError) as record_error:
                    return 409, {
                        "ok": False,
                        "error": "adapter failed and durable failure receipt was rejected",
                        "adapter_error": str(error),
                        "record_error": str(record_error),
                        "blocked_target": label,
                        "external_writes_performed": [
                            *external_writes,
                            *detected_writes,
                        ],
                        "run": store.get_run(run["run_id"]) or run,
                    }
                except Exception as record_error:
                    return 500, {
                        "ok": False,
                        "error": "adapter failed and durable failure receipt could not be saved",
                        "adapter_error": str(error),
                        "record_error": str(record_error),
                        "blocked_target": label,
                        "external_writes_performed": [
                            *external_writes,
                            *detected_writes,
                        ],
                        "run": store.get_run(run["run_id"]) or run,
                    }
                external_writes.extend(detected_writes)

        final_run = store.get_run(run["run_id"]) or run
        try:
            from shared_platform import release_control

            refreshed_dashboard = release_control.build_release_dashboard(
                offer_id=str(plan_payload["product_id"]),
                publication_targets=list(plan_payload["targets"]),
            )
        except Exception:
            refreshed_dashboard = dashboard
        complete = final_run.get("status") in {
            "SUCCEEDED",
            "COMPLETED_WITH_MANUAL_VERIFICATION",
        }
        awaiting_manual = (
            final_run.get("status") == "AWAITING_MANUAL_VERIFICATION"
        )
        return 200, {
            "ok": True,
            "completed": complete,
            "partial": not complete and not awaiting_manual,
            "awaiting_manual_verification": awaiting_manual,
            "message": (
                "all selected targets succeeded with verified readback"
                if complete
                else (
                    "all executable submissions finished; API-less targets await Kyle verification"
                    if awaiting_manual
                    else "some selected targets still require retry or verified readback"
                )
            ),
            "external_writes_performed": external_writes,
            "target_recovery_actions": _release_target_recovery_actions(
                final_run,
                predecessor_run=predecessor_run,
                target_rows=target_rows,
                registry=registry,
            ),
            "run": final_run,
            "dashboard": _product_workspace_view(refreshed_dashboard),
        }


def _manually_verify_release_target(data: dict) -> tuple[int, dict]:
    """Close the exact pending manual contract for a one-click target."""

    from shared_platform.release_store import (
        ImmutableReleaseError,
        ReleaseAuthorizationError,
        ReleaseStoreError,
        default_release_store,
    )
    from shared_platform.oneclick_release_controlplane import (
        AdapterContractError,
        OneClickReleaseStore,
        SystemicIdentityError,
    )

    if data.get("user_verified") is not True:
        return 400, {
            "ok": False,
            "error": "explicit user_verified=true is required",
        }
    if str(data.get("verified_by") or "").strip() != "Kyle":
        return 400, {"ok": False, "error": "verified_by must be Kyle"}
    dashboard, failure = _release_dashboard_for_request(data)
    if failure:
        return failure
    assert dashboard is not None
    plan_payload, blockers = _release_plan_payload_from_dashboard(dashboard)
    if blockers:
        return 409, {
            "ok": False,
            "error": "current release facts no longer match the approved plan",
            "blockers": blockers,
        }
    store = default_release_store()
    preview = store.preview_plan(plan_payload)
    plan_id = str(data.get("plan_id") or "").strip()
    token = str(data.get("confirmation_token") or "").strip()
    plan = store.get_plan(plan_id)
    if (
        plan_id != preview["plan_id"]
        or not plan
        or not _approved_plan_matches_current_payload(plan, preview)
        or plan.get("status") != "APPROVED"
        or token != plan.get("confirmation_token")
    ):
        return 409, {
            "ok": False,
            "error": "approved ReleasePlan no longer matches current facts",
        }
    target_label = str(data.get("target_label") or "").strip()
    api_less_targets = {
        "tiktok:HB_PH",
        "tiktok:HB_MY",
        "tiktok:HB_TH",
        "tiktok:HB_VN",
        "tiktok:MX",
        "tiktok:GB",
    }
    if target_label not in (plan.get("targets") or ()):
        return 400, {
            "ok": False,
            "error": "target is not an approved ReleasePlan destination",
        }
    run_id = f"release-run:{plan['payload_digest'][:24]}"
    try:
        oneclick = OneClickReleaseStore(store.path)
        oneclick_job = oneclick.get_job(plan_id=plan_id)
        if oneclick_job:
            oneclick_target = next(
                (
                    row
                    for row in oneclick_job.get("targets", ())
                    if row.get("target_label") == target_label
                ),
                None,
            )
            if not isinstance(oneclick_target, dict):
                raise SystemicIdentityError(
                    "one-click manual acceptance target was not found"
                )
            checks = (
                data.get("checks")
                if isinstance(data.get("checks"), dict)
                else {}
            )
            if oneclick_target.get("status") == "SUBMITTED_UNVERIFIED":
                if target_label not in api_less_targets:
                    raise SystemicIdentityError(
                        "submitted-unverified target is not an approved API-less destination"
                    )
                marketplace_product_id = str(
                    data.get("marketplace_product_id") or ""
                ).strip()
                if (
                    not marketplace_product_id
                    or len(marketplace_product_id) > 128
                    or any(
                        character.isspace()
                        for character in marketplace_product_id
                    )
                ):
                    return 400, {
                        "ok": False,
                        "error": (
                            "marketplace_product_id must be a non-empty ID "
                            "without spaces"
                        ),
                    }
                evidence = {
                    "source": "kyle_marketplace_console_inspection",
                    "marketplace_product_id": marketplace_product_id,
                    "identity_matches": checks.get("identity_matches") is True,
                    "seller_sku_matches": (
                        checks.get("seller_sku_matches") is True
                    ),
                    "single_listing_for_sku": (
                        checks.get("single_listing_for_sku") is True
                    ),
                    "title_matches": checks.get("title_matches") is True,
                    "price_matches": checks.get("price_matches") is True,
                    "images_match": checks.get("images_match") is True,
                    "logistics_match": checks.get("logistics_match") is True,
                }
            elif oneclick_target.get("status") == "SUCCEEDED_MANUAL_REVIEW":
                if target_label not in _ONECLICK_SHOPEE_MANUAL_REVIEW_TARGETS:
                    raise SystemicIdentityError(
                        "verified-warning acceptance is limited to Shopee targets"
                    )
                if "marketplace_product_id" in data:
                    return 400, {
                        "ok": False,
                        "error": (
                            "verified Shopee warning acceptance must not "
                            "provide marketplace_product_id"
                        ),
                    }
                if (
                    data.get("manual_review_accepted") is not True
                    or "checks" in data
                ):
                    return 400, {
                        "ok": False,
                        "error": (
                            "verified Shopee warning acceptance requires "
                            "manual_review_accepted=true without API-less checks"
                        ),
                    }
                result = oneclick_target.get("result")
                outcome = oneclick_target.get("outcome_receipt")
                if (
                    not isinstance(result, dict)
                    or not isinstance(outcome, dict)
                    or not isinstance(result.get("observation_digests"), list)
                    or not result["observation_digests"]
                ):
                    raise SystemicIdentityError(
                        "verified Shopee warning evidence is unavailable"
                    )
                observation_digests = sorted(
                    result["observation_digests"]
                )
                observation_echo = data.get(
                    "observation_evidence_digest"
                )
                if (
                    type(observation_echo) is not str
                    or observation_echo != observation_digests[0]
                ):
                    return 409, {
                        "ok": False,
                        "error": (
                            "verified Shopee observation evidence no longer "
                            "matches the current receipt"
                        ),
                    }
                job_id = str(oneclick_job.get("job_id") or "")
                result_digest = result.get("evidence_digest")
                outcome_digest = outcome.get("receipt_digest")
                if (
                    len(job_id) == 0
                    or not isinstance(result_digest, str)
                    or len(result_digest) != 64
                    or not isinstance(outcome_digest, str)
                    or len(outcome_digest) != 64
                ):
                    raise SystemicIdentityError(
                        "verified Shopee warning identity is invalid"
                    )
                evidence = {
                    "source": "kyle_verified_shopee_observation_review",
                    "manual_review_accepted": True,
                    "observation_evidence_digest": observation_echo,
                    "job_identity_digest": hashlib.sha256(
                        job_id.encode("utf-8")
                    ).hexdigest(),
                    "result_evidence_digest": result_digest,
                    "readback_evidence_digest": result_digest,
                    "outcome_receipt_digest": outcome_digest,
                    "observation_evidence_digests": observation_digests,
                }
            else:
                raise SystemicIdentityError(
                    "one-click target is not awaiting a supported manual acceptance"
                )
            oneclick.record_manual_acceptance(
                run_id=run_id,
                target_label=target_label,
                verified_by="Kyle",
                user_verified=True,
                verification_evidence=evidence,
            )
            current_run = store.get_run(run_id)
            if not current_run:
                raise ReleaseStoreError(
                    "manual verification run disappeared after commit"
                )
            target = next(
                (
                    row
                    for row in current_run["targets"]
                    if row["target_label"] == target_label
                ),
                None,
            )
            if not target:
                raise ReleaseStoreError(
                    "manual verification target disappeared after commit"
                )
        else:
            if target_label not in api_less_targets:
                return 400, {
                    "ok": False,
                    "error": (
                        "legacy manual verification is limited to approved "
                        "API-less TikTok destinations"
                    ),
                }
            marketplace_product_id = str(
                data.get("marketplace_product_id") or ""
            ).strip()
            if (
                not marketplace_product_id
                or len(marketplace_product_id) > 128
                or any(
                    character.isspace()
                    for character in marketplace_product_id
                )
            ):
                return 400, {
                    "ok": False,
                    "error": (
                        "marketplace_product_id must be a non-empty ID "
                        "without spaces"
                    ),
                }
            checks = (
                data.get("checks")
                if isinstance(data.get("checks"), dict)
                else {}
            )
            evidence = {
                "source": "kyle_marketplace_console_inspection",
                "marketplace_product_id": marketplace_product_id,
                "identity_matches": checks.get("identity_matches") is True,
                "seller_sku_matches": checks.get("seller_sku_matches") is True,
                "single_listing_for_sku": (
                    checks.get("single_listing_for_sku") is True
                ),
                "title_matches": checks.get("title_matches") is True,
                "price_matches": checks.get("price_matches") is True,
                "images_match": checks.get("images_match") is True,
                "logistics_match": checks.get("logistics_match") is True,
            }
            target = store.record_manual_verification(
                run_id,
                target_label,
                verified_by="Kyle",
                user_verified=True,
                verification_evidence=evidence,
            )
    except (
        AdapterContractError,
        ValueError,
        ImmutableReleaseError,
        ReleaseAuthorizationError,
        ReleaseStoreError,
        SystemicIdentityError,
    ) as error:
        return 409, {"ok": False, "error": str(error)}
    refreshed, refresh_failure = _release_dashboard_for_request(data)
    if refresh_failure:
        refreshed = dashboard
    return 200, {
        "ok": True,
        "external_writes_performed": [],
        "target": target,
        "run": store.get_run(run_id),
        "dashboard": _product_workspace_view(refreshed or dashboard),
    }


_scan_lock = threading.Lock()
_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_push_lock = threading.Lock()
_push_job: dict = {
    "running": False,
    "message": "",
    "ok_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "errors": [],
    "error": None,
}

_promo_scan_lock = threading.Lock()
_promo_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_promo_push_lock = threading.Lock()
_promo_push_job: dict = {
    "running": False,
    "message": "",
    "ok_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "errors": [],
    "error": None,
}

_deact_scan_lock = threading.Lock()
_deact_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_deact_push_lock = threading.Lock()
_deact_push_job: dict = {
    "running": False,
    "message": "",
    "ok_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "errors": [],
    "error": None,
}

_mx_publish_lock = threading.Lock()
_mx_publish_job: dict = {
    "running": False,
    "message": "",
    "token": "",
    "match_key": "",
    "error": None,
    "result": None,
}

_uk_publish_lock = threading.Lock()
_uk_publish_job: dict = {
    "running": False,
    "message": "",
    "token": "",
    "match_key": "",
    "error": None,
    "result": None,
}

_analytics_sync_lock = threading.Lock()
_analytics_sync_job: dict = {
    "running": False,
    "message": "",
    "total": 0,
    "by_segment": {},
    "error": None,
}

_image_scan_lock = threading.Lock()
_image_scan_job: dict = {
    "running": False,
    "message": "",
    "count": 0,
    "error": None,
}

_catalog_sync_lock = threading.Lock()
_catalog_sync_job: dict = {
    "running": False,
    "run_id": None,
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "",
    "percent": 0,
    "phase": "",
    "mode": "fast",
    "baseline": None,
    "after": None,
    "backup": None,
    "result": None,
    "error": None,
}

_shopee_sync_lock = threading.Lock()
_shopee_sync_job: dict = {
    "running": False,
    "message": "",
    "mode": "",
    "region": "",
    "match_key": "",
    "match_keys": [],
    "result": None,
    "error": None,
}

_sourcing_build_lock = threading.Lock()
_sourcing_build_job: dict = {
    "running": False,
    "message": "",
    "offer_id": "",
    "error": None,
    "result": None,
}

_photoroom_showcase_lock = threading.Lock()
_photoroom_showcase_job: dict = {
    "running": False,
    "message": "",
    "offer_id": "",
    "error": None,
    "result": None,
}

_dewatermark_lock = threading.Lock()
_dewatermark_job: dict = {
    "running": False,
    "message": "",
    "offer_id": "",
    "error": None,
    "result": None,
}


def _run_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    mode: str = "velocity",
) -> None:
    global _scan_job
    try:
        from modules.products import titles as title_mod

        if mode == "analytics":
            _scan_job["message"] = "同步 Analytics，随后 AI 生成标题+详情..."
            n = title_mod.scan_analytics_high_interest(
                limit=limit,
                region=region,
                build_html=False,
                quiet=True,
            )
        else:
            _scan_job["message"] = "正在统计动销，随后 AI 生成标题..."
            n = title_mod.scan_low_velocity(
                days=days,
                max_units=max_units,
                limit=limit,
                region=region,
                build_html=False,
                quiet=True,
            )
        _scan_job.update(
            running=False,
            message=f"完成，共 {n} 条待确认",
            count=n,
            error=None,
        )
    except Exception as e:
        _scan_job.update(running=False, message="", error=str(e))


def _start_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    mode: str = "velocity",
) -> tuple[bool, str]:
    with _scan_lock:
        if _scan_job["running"]:
            return False, "已有扫描任务在进行中，请稍候"
        _scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(
        target=_run_scan,
        args=(days, max_units, limit, region, mode),
        daemon=True,
    )
    t.start()
    return True, "已开始扫描"


def _scan_status() -> dict:
    with _scan_lock:
        return dict(_scan_job)


def _run_push(items: list[dict]) -> None:
    global _push_job
    from modules.products import titles as title_mod

    try:
        edits = [{
            "product_id": it.get("product_id"),
            "shop_cipher": it.get("shop_cipher"),
            "new_title": it.get("new_title"),
            "new_description": it.get("new_description"),
        } for it in items]
        title_mod.save_edits(edits)
        ids = [int(it["id"]) for it in items if it.get("id")]
        total = len(ids)
        _push_job["message"] = f"正在推送 0/{total}..."
        result = title_mod.push_approved(ids if ids else None)
        _push_job.update(
            running=False,
            message=(
                f"完成：成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}"
            ),
            ok_count=result["ok"],
            fail_count=result["fail"],
            skip_count=result["skip"],
            errors=result["errors"][:10],
            error=None,
        )
    except Exception as e:
        _push_job.update(running=False, message="", error=str(e))


def _start_push(items: list[dict]) -> tuple[bool, str]:
    if not items:
        return False, "没有可推送的条目"
    with _push_lock:
        if _push_job["running"]:
            return False, "已有推送任务在进行中，请稍候"
        _push_job.update(
            running=True,
            message="启动中...",
            ok_count=0,
            fail_count=0,
            skip_count=0,
            errors=[],
            error=None,
        )
    t = threading.Thread(target=_run_push, args=(items,), daemon=True)
    t.start()
    return True, "已开始推送"


def _push_status() -> dict:
    with _push_lock:
        return dict(_push_job)


def _run_promo_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    scope: str,
    mode: str = "velocity",
) -> None:
    global _promo_scan_job
    try:
        from modules.products import promotions as promo_mod

        if mode == "analytics":
            _promo_scan_job["message"] = "同步 Analytics A 类，生成促销建议..."
            n = promo_mod.scan_analytics_high_interest(
                limit=limit,
                region=region,
                scope=scope,
                quiet=True,
            )
        else:
            _promo_scan_job["message"] = "正在统计动销并拉取促销活动..."
            n = promo_mod.scan_low_velocity(
                days=days,
                max_units=max_units,
                limit=limit,
                region=region,
                scope=scope,
                quiet=True,
            )
        _promo_scan_job.update(
            running=False,
            message=f"完成，共 {n} 条待确认",
            count=n,
            error=None,
        )
    except Exception as e:
        _promo_scan_job.update(running=False, message="", error=str(e))


def _start_promo_scan(
    days: int,
    max_units: int,
    limit: int,
    region: str | None,
    scope: str = "adjust",
    mode: str = "velocity",
) -> tuple[bool, str]:
    with _promo_scan_lock:
        if _promo_scan_job["running"]:
            return False, "已有扫描任务在进行中，请稍候"
        _promo_scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(
        target=_run_promo_scan,
        args=(days, max_units, limit, region, scope, mode),
        daemon=True,
    )
    t.start()
    return True, "已开始扫描"


def _promo_scan_status() -> dict:
    with _promo_scan_lock:
        return dict(_promo_scan_job)


def _run_promo_push(items: list[dict]) -> None:
    global _promo_push_job
    from modules.products import promotions as promo_mod

    try:
        edits = [{
            "product_id": it.get("product_id"),
            "shop_cipher": it.get("shop_cipher"),
            "new_discount": it.get("new_discount"),
            "flash_price": it.get("flash_price"),
            "promo_price": it.get("promo_price"),
            "action": it.get("action"),
        } for it in items]
        promo_mod.save_edits(edits)
        ids = [int(it["id"]) for it in items if it.get("id")]
        total = len(ids)
        _promo_push_job["message"] = f"正在推送 0/{total}..."
        result = promo_mod.push_approved(ids if ids else None)
        _promo_push_job.update(
            running=False,
            message=(
                f"完成：成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}"
            ),
            ok_count=result["ok"],
            fail_count=result["fail"],
            skip_count=result["skip"],
            errors=result["errors"][:10],
            error=None,
        )
    except Exception as e:
        _promo_push_job.update(running=False, message="", error=str(e))


def _start_promo_push(items: list[dict]) -> tuple[bool, str]:
    if not items:
        return False, "没有可推送的条目"
    with _promo_push_lock:
        if _promo_push_job["running"]:
            return False, "已有推送任务在进行中，请稍候"
        _promo_push_job.update(
            running=True,
            message="启动中...",
            ok_count=0,
            fail_count=0,
            skip_count=0,
            errors=[],
            error=None,
        )
    t = threading.Thread(target=_run_promo_push, args=(items,), daemon=True)
    t.start()
    return True, "已开始推送"


def _promo_push_status() -> dict:
    with _promo_push_lock:
        return dict(_promo_push_job)


def _run_analytics_sync(region: str | None) -> None:
    global _analytics_sync_job
    try:
        from modules.products import analytics as analytics_mod

        _analytics_sync_job["message"] = "正在拉取各站 Analytics..."
        result = analytics_mod.sync_all(region=region, quiet=True)
        _analytics_sync_job.update(
            running=False,
            message=f"完成，共 {result['total']} 条",
            total=result["total"],
            by_segment=result.get("by_segment") or {},
            error=None,
        )
    except Exception as e:
        _analytics_sync_job.update(running=False, message="", error=str(e))


def _start_analytics_sync(region: str | None) -> tuple[bool, str]:
    with _analytics_sync_lock:
        if _analytics_sync_job["running"]:
            return False, "已有 Analytics 同步任务在进行中"
        _analytics_sync_job.update(
            running=True, message="启动中...", total=0, by_segment={}, error=None
        )
    t = threading.Thread(target=_run_analytics_sync, args=(region,), daemon=True)
    t.start()
    return True, "已开始同步"


def _analytics_sync_status() -> dict:
    with _analytics_sync_lock:
        return dict(_analytics_sync_job)


def _run_deact_scan(limit: int, region: str | None) -> None:
    global _deact_scan_job
    try:
        from modules.products import deactivate as deact_mod

        _deact_scan_job["message"] = "同步 Analytics 并筛选下架候选..."
        n = deact_mod.scan_candidates(region=region, limit=limit, quiet=True)
        _deact_scan_job.update(
            running=False,
            message=f"完成，共 {n} 条待确认",
            count=n,
            error=None,
        )
    except Exception as e:
        _deact_scan_job.update(running=False, message="", error=str(e))


def _start_deact_scan(limit: int, region: str | None) -> tuple[bool, str]:
    with _deact_scan_lock:
        if _deact_scan_job["running"]:
            return False, "已有扫描任务在进行中，请稍候"
        _deact_scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(target=_run_deact_scan, args=(limit, region), daemon=True)
    t.start()
    return True, "已开始扫描"


def _deact_scan_status() -> dict:
    with _deact_scan_lock:
        return dict(_deact_scan_job)


def _run_deact_push(items: list[dict]) -> None:
    global _deact_push_job
    from modules.products import deactivate as deact_mod

    try:
        ids = [int(it["id"]) for it in items if it.get("id")]
        total = len(ids)
        _deact_push_job["message"] = f"正在下架 0/{total}..."
        result = deact_mod.push_approved(ids if ids else None)
        _deact_push_job.update(
            running=False,
            message=(
                f"完成：成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}"
            ),
            ok_count=result["ok"],
            fail_count=result["fail"],
            skip_count=result["skip"],
            errors=result["errors"][:10],
            error=None,
        )
    except Exception as e:
        _deact_push_job.update(running=False, message="", error=str(e))


def _start_deact_push(items: list[dict]) -> tuple[bool, str]:
    if not items:
        return False, "没有可下架的条目"
    with _deact_push_lock:
        if _deact_push_job["running"]:
            return False, "已有下架任务在进行中，请稍候"
        _deact_push_job.update(
            running=True,
            message="启动中...",
            ok_count=0,
            fail_count=0,
            skip_count=0,
            errors=[],
            error=None,
        )
    t = threading.Thread(target=_run_deact_push, args=(items,), daemon=True)
    t.start()
    return True, "已开始下架"


def _deact_push_status() -> dict:
    with _deact_push_lock:
        return dict(_deact_push_job)


def _run_mx_publish(token: str) -> None:
    global _mx_publish_job
    from modules.miaoshou import mx_web_approval as mx_web

    try:
        _mx_publish_job["message"] = "正在 claim + publish…"
        result = mx_web.publish_token(token)
        _mx_publish_job.update(
            running=False,
            message=f"✅ {result['match_key']} 上架完成 · {result['list_price_ceil_mxn']} MXN",
            match_key=result.get("match_key") or "",
            result=result,
            error=None,
        )
    except Exception as e:
        _mx_publish_job.update(running=False, message="", error=str(e), result=None)


def _start_mx_publish(token: str) -> tuple[bool, str]:
    token = (token or "").strip()
    if not token:
        return False, "缺少 token"
    with _mx_publish_lock:
        if _mx_publish_job.get("running"):
            return False, "已有上架任务进行中"
        _mx_publish_job.update(
            running=True,
            message="排队中…",
            token=token,
            match_key="",
            error=None,
            result=None,
        )
    threading.Thread(target=_run_mx_publish, args=(token,), daemon=True).start()
    return True, "started"


def _mx_publish_status() -> dict:
    with _mx_publish_lock:
        return dict(_mx_publish_job)


def _run_uk_publish(token: str) -> None:
    global _uk_publish_job
    from modules.miaoshou import uk_web_approval as uk_web

    try:
        _uk_publish_job["message"] = "正在 claim + publish…"
        result = uk_web.publish_token(token)
        _uk_publish_job.update(
            running=False,
            message=f"✅ {result['match_key']} 上架完成 · £{result['list_price_ceil_gbp']}",
            match_key=result.get("match_key") or "",
            result=result,
            error=None,
        )
    except Exception as e:
        _uk_publish_job.update(running=False, message="", error=str(e), result=None)


def _start_uk_publish(token: str) -> tuple[bool, str]:
    token = (token or "").strip()
    if not token:
        return False, "缺少 token"
    with _uk_publish_lock:
        if _uk_publish_job.get("running"):
            return False, "已有上架任务进行中"
        _uk_publish_job.update(
            running=True,
            message="排队中…",
            token=token,
            match_key="",
            error=None,
            result=None,
        )
    threading.Thread(target=_run_uk_publish, args=(token,), daemon=True).start()
    return True, "started"


def _uk_publish_status() -> dict:
    with _uk_publish_lock:
        return dict(_uk_publish_job)


def _run_shopee_sync(mode: str, payload: dict) -> None:
    global _shopee_sync_job
    from modules.catalog import shopee_push as sp_push

    try:
        with _shopee_sync_lock:
            _shopee_sync_job["message"] = (
                "正在同步整组到 Shopee..."
                if mode == "group"
                else "正在同步 TikTok 到 Shopee..."
            )
        if mode == "group":
            result = sp_push.sync_tk_group_to_shopee(
                payload.get("match_keys") or [],
                region=str(payload.get("region") or "PH").upper(),
            )
        else:
            result = sp_push.sync_tk_to_shopee_global(
                str(payload.get("match_key") or "").strip(),
                region=str(payload.get("region") or "PH").upper(),
            )
        with _shopee_sync_lock:
            _shopee_sync_job.update(
                running=False,
                message=result.get("message") or "Shopee 同步完成",
                result=result,
                error=None,
            )
    except Exception as e:
        with _shopee_sync_lock:
            _shopee_sync_job.update(
                running=False,
                message="",
                result=None,
                error=str(e),
            )


def _start_shopee_sync(mode: str, payload: dict) -> tuple[bool, str]:
    region = str(payload.get("region") or "PH").upper()
    match_key = str(payload.get("match_key") or "").strip()
    raw_keys = payload.get("match_keys") or []
    if isinstance(raw_keys, str):
        match_keys = [x.strip() for x in raw_keys.replace(";", ",").split(",") if x.strip()]
    else:
        match_keys = [str(x).strip() for x in raw_keys if str(x).strip()]
    if mode == "group":
        if len(match_keys) < 2:
            return False, "整组同步至少需要 2 个对齐码"
    else:
        if not match_key:
            return False, "缺少 match_key"
    with _shopee_sync_lock:
        if _shopee_sync_job.get("running"):
            return False, "已有 Shopee 同步任务正在进行中"
        _shopee_sync_job.update(
            running=True,
            message="排队中...",
            mode=mode,
            region=region,
            match_key=match_key,
            match_keys=match_keys,
            result=None,
            error=None,
        )
    threading.Thread(
        target=_run_shopee_sync,
        args=(mode, {"region": region, "match_key": match_key, "match_keys": match_keys}),
        daemon=True,
    ).start()
    return True, "started"


def _shopee_sync_status() -> dict:
    with _shopee_sync_lock:
        return dict(_shopee_sync_job)


def _run_image_scan(
    limit: int,
    region: str | None,
    variants: int,
    mode: str = "b_class",
    product_items: list[dict] | None = None,
    main_recipe_ids: list[str] | None = None,
    custom_scenes: list[dict] | None = None,
    include_default_scenes: bool = False,
    explore_recipe_ids: list[str] | None = None,
) -> None:
    global _image_scan_job
    try:
        from modules.products import images as image_mod

        if mode in ("manual", "explore") and product_items:
            label = "探索方案" if mode == "explore" else "选定商品"
            _image_scan_job["message"] = f"为 {len(product_items)} 个{label}生成图片..."
            n = image_mod.generate_for_products(
                product_items,
                main_recipe_ids=[] if mode == "explore" else main_recipe_ids,
                custom_scenes=custom_scenes,
                include_default_scenes=include_default_scenes,
                explore_recipe_ids=explore_recipe_ids,
                use_explore_recipes=(mode == "explore" and not explore_recipe_ids),
                quiet=True,
            )
        else:
            _image_scan_job["message"] = "同步 Analytics B 类，随后生成主图+场景..."
            n = image_mod.scan_b_class(
                limit=limit, region=region, variants=variants, quiet=True
            )
        _image_scan_job.update(
            running=False,
            message=f"完成，共 {n} 个商品已生成候选",
            count=n,
            error=None,
        )
    except Exception as e:
        _image_scan_job.update(running=False, message="", error=str(e))


def _start_image_scan(
    limit: int,
    region: str | None,
    variants: int,
    mode: str = "b_class",
    product_items: list[dict] | None = None,
    main_recipe_ids: list[str] | None = None,
    custom_scenes: list[dict] | None = None,
    include_default_scenes: bool = False,
    explore_recipe_ids: list[str] | None = None,
) -> tuple[bool, str]:
    with _image_scan_lock:
        if _image_scan_job["running"]:
            return False, "已有主图生成任务在进行中"
        _image_scan_job.update(running=True, message="启动中...", count=0, error=None)
    t = threading.Thread(
        target=_run_image_scan,
        args=(
            limit, region, variants, mode, product_items,
            main_recipe_ids, custom_scenes, include_default_scenes, explore_recipe_ids,
        ),
        daemon=True,
    )
    t.start()
    return True, "已开始生成"


def _run_sourcing_build(
    offer_id: str,
    *,
    plan_version: str = "v2",
    skip_slots: bool = False,
    skip_images: bool = False,
) -> None:
    global _sourcing_build_job
    try:
        from modules.sourcing import pipeline as sourcing_mod

        def progress(msg: str) -> None:
            _sourcing_build_job["message"] = msg

        draft = sourcing_mod.build_draft(
            offer_id,
            progress=progress,
            plan_version=plan_version,
            skip_slots=skip_slots,
            skip_images=skip_images,
        )
        _sourcing_build_job.update(
            running=False,
            message="构建完成",
            offer_id=offer_id,
            error=None,
            result={
                "offer_id": offer_id,
                "plan_version": plan_version,
                "errors": draft.get("errors") or [],
            },
        )
    except Exception as e:
        _sourcing_build_job.update(
            running=False, message="", error=str(e), result=None
        )


def _start_sourcing_build(
    offer_id: str,
    *,
    plan_version: str = "v2",
    skip_slots: bool = False,
    skip_images: bool = False,
) -> tuple[bool, str]:
    oid = (offer_id or "").strip()
    if not oid:
        return False, "缺少 offer_id"
    with _sourcing_build_lock:
        if _sourcing_build_job["running"]:
            return False, "已有选品构建任务在进行中"
        _sourcing_build_job.update(
            running=True,
            message="启动中…",
            offer_id=oid,
            error=None,
            result=None,
        )
    t = threading.Thread(
        target=_run_sourcing_build,
        args=(oid,),
        kwargs={
            "plan_version": plan_version,
            "skip_slots": skip_slots,
            "skip_images": skip_images,
        },
        daemon=True,
    )
    t.start()
    return True, "已开始构建"


def _sourcing_build_status() -> dict:
    with _sourcing_build_lock:
        return dict(_sourcing_build_job)


def _run_photoroom_showcase(offer_id: str) -> None:
    global _photoroom_showcase_job
    try:
        from modules.sourcing import photoroom_showcase as showcase_mod

        def progress(msg: str) -> None:
            _photoroom_showcase_job["message"] = msg

        manifest = showcase_mod.build_showcase(offer_id, progress=progress)
        _photoroom_showcase_job.update(
            running=False,
            message="试跑完成",
            offer_id=offer_id,
            error=None,
            result=manifest.get("summary"),
        )
    except Exception as e:
        _photoroom_showcase_job.update(
            running=False, message="", error=str(e), result=None
        )


def _start_photoroom_showcase(offer_id: str) -> tuple[bool, str]:
    oid = (offer_id or "").strip()
    if not oid:
        return False, "缺少 offer_id"
    with _photoroom_showcase_lock:
        if _photoroom_showcase_job["running"]:
            return False, "Photoroom 试跑进行中，请稍候"
        _photoroom_showcase_job.update(
            running=True,
            message="准备中…",
            offer_id=oid,
            error=None,
            result=None,
        )
    t = threading.Thread(target=_run_photoroom_showcase, args=(oid,), daemon=True)
    t.start()
    return True, "已开始 Photoroom 全能力试跑"


def _photoroom_showcase_status() -> dict:
    with _photoroom_showcase_lock:
        return dict(_photoroom_showcase_job)


def _run_dewatermark_batch(offer_id: str) -> None:
    global _dewatermark_job
    try:
        from modules.sourcing import image_workbench as wb_mod

        def progress(msg: str) -> None:
            _dewatermark_job["message"] = msg

        wb = wb_mod.batch_dewatermark(offer_id, progress=progress, replace_final=True)
        _dewatermark_job.update(
            running=False,
            message="去水印完成",
            offer_id=offer_id,
            error=None,
            result={"main": len(wb.get("final", {}).get("tiktok_main") or [])},
        )
    except Exception as e:
        _dewatermark_job.update(running=False, message="", error=str(e), result=None)


def _start_dewatermark_batch(offer_id: str) -> tuple[bool, str]:
    oid = (offer_id or "").strip()
    if not oid:
        return False, "缺少 offer_id"
    with _dewatermark_lock:
        if _dewatermark_job["running"]:
            return False, "去水印任务进行中"
        _dewatermark_job.update(
            running=True, message="准备中…", offer_id=oid, error=None, result=None
        )
    t = threading.Thread(target=_run_dewatermark_batch, args=(oid,), daemon=True)
    t.start()
    return True, "已开始批量去水印"


def _dewatermark_status() -> dict:
    with _dewatermark_lock:
        return dict(_dewatermark_job)


def _catalog_database_baseline(mode: str) -> dict:
    """Build a read-only database snapshot bound to the requested sync mode."""
    from core.database_maintenance import inspect_database
    from core.db import connect_readonly

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"fast", "full"}:
        raise ValueError("mode must be fast or full")
    health = inspect_database(full_integrity=True)
    health_payload = health.payload()
    content_digest = hashlib.sha256()
    with connect_readonly(health.path) as connection:
        for statement in connection.iterdump():
            content_digest.update(statement.encode("utf-8"))
            content_digest.update(b"\n")
        def quality_count(sql: str) -> int | None:
            try:
                return int(connection.execute(sql).fetchone()[0] or 0)
            except Exception:
                return None

        quality_metrics = {
            "same_shop_duplicate_seller_sku_groups": quality_count(
                """
                SELECT COUNT(*) FROM (
                    SELECT shop_cipher, seller_sku
                    FROM products
                    WHERE TRIM(COALESCE(seller_sku, '')) != ''
                    GROUP BY shop_cipher, seller_sku
                    HAVING COUNT(*) > 1
                )
                """
            ),
            "tiktok_rows_without_direct_cost": quality_count(
                """
                SELECT COUNT(*)
                FROM products p
                LEFT JOIN sku_costs c ON c.sku_id = p.sku_id
                WHERE c.sku_id IS NULL
                """
            ),
            "logistics_rows_without_tiktok_tail4": quality_count(
                """
                SELECT COUNT(*)
                FROM sku_logistics_weights w
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM products p
                    WHERE p.seller_sku GLOB '[0-9]*'
                      AND SUBSTR(PRINTF('%04d', CAST(p.seller_sku AS INTEGER)), -4)
                          = SUBSTR(PRINTF('%04d', CAST(w.seller_sku AS INTEGER)), -4)
                )
                """
            ),
            "shopee_nonpositive_price_rows": quality_count(
                """
                SELECT COUNT(*)
                FROM shopee_products
                WHERE price IS NOT NULL AND price <= 0
                """
            ),
        }
    content_sha256 = content_digest.hexdigest()
    quality_issue_count = sum(
        1 for value in quality_metrics.values() if value not in (None, 0)
    )
    fingerprint_input = {
        "mode": normalized_mode,
        "content_sha256": content_sha256,
        "size_bytes": health.size_bytes,
        "wal_size_bytes": health.wal_size_bytes,
        "shm_size_bytes": health.shm_size_bytes,
        "journal_mode": health.journal_mode,
        "page_size": health.page_size,
        "page_count": health.page_count,
        "freelist_count": health.freelist_count,
        "user_version": health.user_version,
        "row_counts": health.row_counts,
        "quick_check": health.quick_check,
        "integrity_check": health.integrity_check,
        "foreign_key_violation_count": health.foreign_key_violation_count,
    }
    snapshot_id = "sha256:" + hashlib.sha256(
        json.dumps(
            fingerprint_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "mode": normalized_mode,
        "snapshot_id": snapshot_id,
        "content_sha256": content_sha256,
        "row_counts": dict(health.row_counts),
        "integrity": {
            "ok": health.ok,
            "quick_check": list(health.quick_check),
            "integrity_check": list(health.integrity_check or ()),
            "foreign_key_violation_count": health.foreign_key_violation_count,
        },
        "business_quality": {
            "status": "needs_review" if quality_issue_count else "ready",
            "issue_metric_count": quality_issue_count,
            "metrics": quality_metrics,
        },
        "backup_required": True,
        "database": health_payload,
    }


def _catalog_sync_result_parts(result: dict) -> list[str]:
    parts: list[str] = []
    tk = result.get("tiktok") or {}
    if tk.get("skus") is not None and not tk.get("error"):
        parts.append(f"TK {int(tk.get('skus') or 0)} SKU")
    sp = result.get("shopee") or {}
    if sp.get("skus") is not None and not sp.get("error"):
        parts.append(f"Shopee {int(sp.get('skus') or 0)} SKU")
    elif sp.get("skipped"):
        parts.append("Shopee 跳过")
    oz = result.get("ozon") or {}
    if oz.get("offers") is not None and not oz.get("error"):
        parts.append(f"Ozon {int(oz.get('offers') or 0)} 商品")
    lw = result.get("logistics_weights") or {}
    if lw.get("skus") and not lw.get("error"):
        parts.append(f"重量 {int(lw.get('skus') or 0)}")
    return parts


def _catalog_sync_audit_payload(job: dict) -> dict:
    return {
        key: value
        for key, value in job.items()
        if key
        in {
            "running",
            "run_id",
            "status",
            "started_at",
            "finished_at",
            "message",
            "percent",
            "phase",
            "mode",
            "baseline",
            "after",
            "backup",
            "result",
            "error",
        }
    }


def _write_catalog_sync_audit_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _persist_catalog_sync_status(job: dict | None = None) -> None:
    payload = _catalog_sync_audit_payload(job or _catalog_sync_job)
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return
    base = ROOT / "backups" / "catalog_sync"
    _write_catalog_sync_audit_file(base / run_id / "run.json", payload)
    _write_catalog_sync_audit_file(base / "latest.json", payload)


def _load_latest_catalog_sync_status() -> dict | None:
    path = ROOT / "backups" / "catalog_sync" / "latest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("run_id") else None


def _run_catalog_sync() -> None:
    global _catalog_sync_job
    try:
        from modules.catalog.sync import run_catalog_sync

        with _catalog_sync_lock:
            mode = str(_catalog_sync_job.get("mode") or "fast")

        def on_state(state: dict) -> None:
            with _catalog_sync_lock:
                _catalog_sync_job["message"] = state.get("message") or ""
                _catalog_sync_job["percent"] = int(state.get("percent") or 0)
                _catalog_sync_job["phase"] = state.get("phase") or ""

        def on_progress(msg: str) -> None:
            with _catalog_sync_lock:
                _catalog_sync_job["message"] = msg

        result = run_catalog_sync(
            on_progress=on_progress,
            on_state=on_state,
            mode=mode,
        )
        errors = [str(item) for item in (result.get("errors") or []) if str(item)]
        parts = _catalog_sync_result_parts(result)
        status = "partial" if errors and parts else ("failed" if errors else "success")
        summary = " · ".join(parts) or (
            "同步完成" if status == "success" else "未完成任何目录同步"
        )
        if errors:
            summary += f"（{'部分失败' if status == 'partial' else '失败'}: {'; '.join(errors)}）"
        try:
            after = _catalog_database_baseline(mode)
        except Exception as error:
            after = None
            errors.append(f"同步后数据库检查失败: {error}")
            status = "partial" if parts else "failed"
            summary += f"（同步后检查失败: {error}）"
        with _catalog_sync_lock:
            _catalog_sync_job.update(
                running=False,
                status=status,
                finished_at=datetime.now(timezone.utc).isoformat(),
                message=summary,
                percent=100 if status in {"success", "partial"} else 0,
                phase="done" if status in {"success", "partial"} else "failed",
                after=after,
                result=result,
                error="; ".join(errors) if errors else None,
            )
            completed_job = dict(_catalog_sync_job)
        _persist_catalog_sync_status(completed_job)
    except Exception as error:
        with _catalog_sync_lock:
            _catalog_sync_job.update(
                running=False,
                status="failed",
                finished_at=datetime.now(timezone.utc).isoformat(),
                message=f"同步失败: {error}",
                percent=0,
                phase="failed",
                after=None,
                result=None,
                error=str(error),
            )
            failed_job = dict(_catalog_sync_job)
        _persist_catalog_sync_status(failed_job)


def _start_catalog_sync(
    *,
    mode: str,
    expected_snapshot_id: str,
    confirm_catalog_update: bool,
) -> tuple[bool, str, int, dict]:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"fast", "full"}:
        return False, "mode must be fast or full", 400, {}
    if confirm_catalog_update is not True:
        return False, "需要显式 confirm_catalog_update=true", 400, {}
    expected = str(expected_snapshot_id or "").strip()
    if not expected:
        return False, "缺少 expected_snapshot_id，请先执行目录预检", 400, {}

    from core.database_maintenance import backup_database

    with _catalog_sync_lock:
        if _catalog_sync_job["running"]:
            return False, "已有同步任务在进行中，请稍候", 409, {}
        try:
            baseline = _catalog_database_baseline(normalized_mode)
        except Exception as error:
            return False, f"目录预检失败: {error}", 500, {}
        if baseline["snapshot_id"] != expected:
            return (
                False,
                "目录快照已变化，请重新预检后再确认",
                409,
                {"current_preview": baseline},
            )
        run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + uuid4().hex[:8]
        )
        destination = ROOT / "backups" / "catalog_sync" / run_id / "shop.db"
        try:
            backup = backup_database(destination)
        except Exception as error:
            return False, f"目录备份失败，已阻止同步: {error}", 500, {}
        started_at = datetime.now(timezone.utc).isoformat()
        _catalog_sync_job.update(
            running=True,
            run_id=run_id,
            status="running",
            started_at=started_at,
            finished_at=None,
            message="WAL 安全备份已验证，正在启动同步…",
            percent=0,
            phase="tokens",
            mode=normalized_mode,
            baseline=baseline,
            after=None,
            backup=backup.payload(),
            result=None,
            error=None,
        )
        started_job = dict(_catalog_sync_job)
    _persist_catalog_sync_status(started_job)
    thread = threading.Thread(target=_run_catalog_sync, daemon=True)
    thread.start()
    return (
        True,
        "目录预检与备份已通过，已开始同步",
        202,
        {
            "run_id": run_id,
            "status": "running",
            "baseline": baseline,
            "backup": backup.payload(),
        },
    )


def _catalog_sync_status() -> dict:
    with _catalog_sync_lock:
        current = dict(_catalog_sync_job)
    if not current.get("run_id") and not current.get("running"):
        persisted = _load_latest_catalog_sync_status()
        if persisted:
            return {**current, **persisted, "running": False}
    return current


def _image_scan_status() -> dict:
    with _image_scan_lock:
        return dict(_image_scan_job)


def _api_status() -> dict:
    from core import auth
    from modules.products import titles as title_mod
    from modules.products import promotions as promo_mod
    from modules.products import deactivate as deact_mod
    from modules.products import images as image_mod

    def safe_count(label: str, fn) -> tuple[int, str | None]:
        try:
            return len(fn()), None
        except Exception as e:
            return 0, f"{label}: {e}"

    try:
        tok = auth.load_token()
        access_exp = auth.access_expires_at(tok)
        refresh_exp = auth.refresh_expires_at(tok)
        pending, w_titles = safe_count("titles", lambda: title_mod.load_queue("pending"))
        pending_promos, w_promos = safe_count("promotions", lambda: promo_mod.load_queue("pending"))
        pending_deact, w_deact = safe_count("deactivate", lambda: deact_mod.load_queue("pending"))
        pending_images, w_images = safe_count("images", image_mod.load_active_queue)
        from modules.miaoshou import mx_web_approval as mx_web
        from modules.miaoshou import uk_web_approval as uk_web

        pending_mx = len(mx_web.list_cards(status="pending"))
        pending_uk = len(uk_web.list_cards(status="pending"))
        warnings = [x for x in (w_titles, w_promos, w_deact, w_images) if x]
        return {
            "ok": True,
            "seller_name": tok.get("seller_name"),
            "access_expires": access_exp.isoformat() if access_exp else None,
            "refresh_expires": refresh_exp.isoformat() if refresh_exp else None,
            "pending_titles": pending,
            "pending_promos": pending_promos,
            "pending_deactivate": pending_deact,
            "pending_images": pending_images,
            "pending_mx": pending_mx,
            "pending_uk": pending_uk,
            "warnings": warnings,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _cache_ext(content_type: str, url_path: str) -> str:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    ext = mimetypes.guess_extension(ctype) or Path(url_path).suffix
    if not ext or len(ext) > 8:
        ext = ".jpg"
    return ext


def _image_cache_path(url: str, content_type: str = "") -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    ext = _cache_ext(content_type, urlparse(url).path)
    return IMAGE_CACHE_DIR / f"{digest}{ext}"


def _validate_remote_image_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("only public http/https image URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("image URLs must not include credentials")
    if parsed.port not in (None, 80, 443):
        raise ValueError("image URLs must use port 80 or 443")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as exc:
        raise ValueError("image host could not be resolved") from exc
    if not addresses:
        raise ValueError("image host could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("private or local image hosts are not allowed")


class _NoRemoteImageRedirects(urllib.request.HTTPRedirectHandler):
    """Fail closed so a public image URL cannot redirect into a private host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_REMOTE_IMAGE_OPENER = urllib.request.build_opener(_NoRemoteImageRedirects())


def _download_remote_image(url: str) -> tuple[Path, str]:
    parsed = urlparse(url)
    _validate_remote_image_url(url)

    cached = _image_cache_path(url)
    for existing in IMAGE_CACHE_DIR.glob(cached.stem + ".*"):
        if existing.is_file() and existing.stat().st_size > 0:
            ctype = mimetypes.guess_type(str(existing))[0] or "image/jpeg"
            if ctype == "image/svg+xml":
                raise ValueError("SVG images are not allowed")
            return existing, ctype

    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/png,image/jpeg,image/*;q=0.8",
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        },
    )
    for attempt in range(2):
        try:
            with _REMOTE_IMAGE_OPENER.open(req, timeout=8) as resp:
                content_type = resp.headers.get("Content-Type") or "image/jpeg"
                if not content_type.lower().startswith("image/"):
                    raise ValueError(f"remote URL is not an image: {content_type}")
                if content_type.split(";", 1)[0].strip().lower() == "image/svg+xml":
                    raise ValueError("SVG images are not allowed")
                data = resp.read(12 * 1024 * 1024)
            break
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt:
                raise
            time.sleep(0.2)
    fp = _image_cache_path(url, content_type)
    fp.write_bytes(data)
    return fp, content_type


def _preview_image_urls(payload: dict, limit: int = 18) -> list[str]:
    urls: list[str] = []

    def add(value):
        if not value:
            return
        text = str(value).strip()
        if text and text not in urls:
            urls.append(text)

    review = payload.get("review") if isinstance(payload, dict) else {}
    for img in (review or {}).get("overseas_image_candidates") or []:
        if len(urls) >= limit:
            break
        if isinstance(img, dict):
            add(img.get("url"))
    for img in (review or {}).get("image_actions") or []:
        if len(urls) >= limit:
            break
        if isinstance(img, dict):
            add(img.get("url"))
    return urls[:limit]


def _warm_preview_image_cache(payload: dict) -> None:
    urls = _preview_image_urls(payload)
    if not urls:
        return
    pool = ThreadPoolExecutor(max_workers=min(8, len(urls)))
    try:
        futures = [pool.submit(_download_remote_image, url) for url in urls]
        wait(futures, timeout=18)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _placeholder_image_bytes(message: str = "image unavailable") -> bytes:
    safe = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="480" viewBox="0 0 480 480">'
        '<rect width="480" height="480" fill="#f1f5f9"/>'
        '<rect x="72" y="96" width="336" height="240" rx="12" fill="#e2e8f0"/>'
        '<circle cx="168" cy="176" r="36" fill="#cbd5e1"/>'
        '<path d="M112 304l84-84 62 62 44-44 66 66z" fill="#cbd5e1"/>'
        f'<text x="240" y="382" text-anchor="middle" font-family="Arial, sans-serif" '
        f'font-size="22" fill="#64748b">{safe}</text>'
        '</svg>'
    ).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _module_moved(self, name: str, url: str):
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name} moved</title>
  <style>
    body {{ font-family: Arial, sans-serif; background:#f8fafc; color:#0f172a; margin:0; }}
    main {{ max-width: 760px; margin: 64px auto; background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:28px; }}
    h1 {{ margin:0 0 12px; font-size:28px; }}
    p {{ line-height:1.6; color:#475569; }}
    a {{ color:#2563eb; text-decoration:none; }}
    code {{ background:#f1f5f9; padding:2px 6px; border-radius:6px; }}
  </style>
</head>
<body>
  <main>
    <h1>{name} has moved</h1>
    <p>This module is no longer hosted inside <strong>Orbit OS</strong>.</p>
    <p>Please open it from its standalone service: <a href="{url}">{url}</a></p>
    <p>Old compatibility entry is now retired so the modules can run independently.</p>
  </main>
</body>
</html>""".encode("utf-8")
        return self._bytes(410, html, "text/html; charset=utf-8")

    def _redirect(self, location: str, *, code: int = 302):
        body = (
            "<!DOCTYPE html><html><head><meta charset=\"UTF-8\">"
            f"<meta http-equiv=\"refresh\" content=\"0;url={location}\">"
            "</head><body></body></html>"
        ).encode("utf-8")
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _bytes(
        self,
        code: int,
        data: bytes,
        content_type: str,
        *,
        filename: str | None = None,
    ):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{quote(filename)}"',
            )
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, *, cache_seconds: int | None = None):
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype.startswith("text/") and "charset=" not in ctype:
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if path.name in {
            "index.html",
            "release.html",
            "product_workspace.html",
            "ai_image_studio.html",
            "profit_center.html",
        }:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' https: data:; "
                "style-src 'self'; script-src 'self'; connect-src 'self'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            self.send_header("Cache-Control", "no-store")
        if cache_seconds is not None:
            self.send_header("Cache-Control", f"public, max-age={cache_seconds}")
        self.end_headers()
        self.wfile.write(data)

    def _image_placeholder(self, message: str = "image unavailable"):
        data = _placeholder_image_bytes(message)
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if not length:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _handle_product_flow_proxy(self, method: str) -> bool:
        """Expose the Treasury workflow behind Orbit's same-origin product API."""
        parsed = urlparse(self.path)
        prefix = "/api/product-flow/"
        if not parsed.path.startswith(prefix):
            return False
        action = parsed.path[len(prefix) :].strip("/")
        allowed_get = {
            "preview",
            "content-report",
            "content-image",
        }
        allowed_post = {
            "review",
            "content-package/prepare",
            "content-package/vision-proposal",
            "content-package/review",
            "content-package/source-only/review",
            "content-package/suite-images-preflight",
            "content-package/remaining-images-generate",
            "content-package/miaoshou-images/commit",
            "content-package/generated-image/decision",
        }
        allowed = allowed_get if method == "GET" else allowed_post
        if action not in allowed:
            self._json(404, {"ok": False, "error": "product-flow action is not registered"})
            return True

        target = f"http://127.0.0.1:8766/api/new-product/{action}"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        body = None
        if method == "POST":
            origin = (self.headers.get("Origin") or "").strip()
            if origin:
                expected_port = int(self.server.server_address[1])
                expected_origins = {
                    f"http://127.0.0.1:{expected_port}",
                    f"http://localhost:{expected_port}",
                }
                if origin not in expected_origins:
                    self._json(403, {"ok": False, "error": "cross-origin product-flow write rejected"})
                    return True
            content_type = (self.headers.get("Content-Type") or "").lower()
            if "application/json" not in content_type:
                self._json(415, {"ok": False, "error": "product-flow writes require application/json"})
                return True
            length = int(self.headers.get("Content-Length", 0))
            if length > 2 * 1024 * 1024:
                self._json(413, {"ok": False, "error": "request body is too large"})
                return True
            body = self.rfile.read(length) if length else b"{}"
        request = urllib.request.Request(
            target,
            data=body,
            method=method,
            headers={
                "Accept": self.headers.get("Accept") or "*/*",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        opener = urllib.request.build_opener(_NoRemoteImageRedirects())
        try:
            with opener.open(request, timeout=120) as response:
                data = response.read(64 * 1024 * 1024)
                content_type = response.headers.get("Content-Type") or "application/octet-stream"
                if action == "content-report" and "text/html" in content_type:
                    data = data.replace(b"/api/new-product/", b"/api/product-flow/")
                self.send_response(response.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as error:
            data = error.read(4 * 1024 * 1024)
            self.send_response(error.code)
            self.send_header(
                "Content-Type",
                error.headers.get("Content-Type") or "application/json; charset=utf-8",
            )
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        except (urllib.error.URLError, TimeoutError, OSError):
            self._json(
                503,
                {
                    "ok": False,
                    "error": "product workflow service is unavailable",
                },
            )
        return True

    def _handle_ozon_proxy(self, method: str) -> bool:
        path = urlparse(self.path).path
        if not path.startswith("/api/ozon/"):
            return False
        subpath = path[len("/api/ozon/") :].split("?")[0]
        query = urlparse(self.path).query
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

        # 商品目录驱动的待搬运 / 草稿（覆盖 ozon webapp 旧 tk_sku_map 逻辑）
        try:
            if method == "GET" and subpath == "unmigrated":
                from modules.ozon.catalog_source import list_unmigrated_from_catalog

                return self._json(200, list_unmigrated_from_catalog())
            if method == "GET" and subpath.startswith("draft/"):
                from urllib.parse import unquote

                from modules.ozon.catalog_draft import build_draft

                seller_sku = unquote(subpath[len("draft/") :])
                draft = build_draft(seller_sku)
                if draft.get("error") and not draft.get("draft_title"):
                    return self._json(404, draft)
                return self._json(200, draft)
            if method == "GET" and subpath == "category_options":
                from modules.ozon.category_match import load_category_options
                from modules.ozon.migrate_attrs import BUILTIN_TYPE_PROFILES
                from modules.ozon.tk_category_map import load_map

                tp = {str(k): v for k, v in BUILTIN_TYPE_PROFILES.items()}
                tp.update(load_map().get("type_profiles") or {})
                return self._json(200, {"options": load_category_options(), "type_profiles": tp})
            # 待审草稿队列：agent 生成好存这里，前端打开 /ozon 加载成待审卡片
            if method == "GET" and subpath == "pending_drafts":
                from modules.ozon.pending_drafts import list_pending

                return self._json(200, {"drafts": list_pending()})
            if method == "POST" and subpath == "pending_drafts":
                from modules.ozon.pending_drafts import save_pending

                payload = json.loads((body or b"{}").decode("utf-8") or "{}")
                return self._json(200, {"saved": save_pending(payload)})
            if method == "POST" and subpath == "pending_drafts/delete":
                from modules.ozon.pending_drafts import delete_pending

                payload = json.loads((body or b"{}").decode("utf-8") or "{}")
                ok = delete_pending(payload.get("seller_sku") or "")
                return self._json(200, {"deleted": ok})
            # 忽略某产品：记入已忽略并从待搬运列表永久排除
            if method == "POST" and subpath == "dismiss":
                from modules.ozon.pending_drafts import add_dismissed

                payload = json.loads((body or b"{}").decode("utf-8") or "{}")
                rec = add_dismissed(
                    payload.get("seller_sku") or "",
                    payload.get("tk_id") or "",
                    payload.get("reason") or "",
                )
                return self._json(200, {"dismissed": rec})
            if method == "GET" and subpath == "dismissed":
                from modules.ozon.pending_drafts import list_dismissed

                return self._json(200, {"dismissed": list_dismissed()})
            # Ozon 真实结算汇总（佣金/物流费/广告费拆解），供定价参考
            if method == "GET" and subpath == "settlement_summary":
                from modules.ozon.settlement import build_settlement_summary

                q = parse_qs(query or "")
                months_back = int((q.get("months") or ["3"])[0])
                weeks = q.get("weeks") or q.get("weeks_back")
                weeks_back = int(weeks[0]) if weeks else None
                only_settled = (q.get("only_settled") or ["1"])[0] in ("1", "true", "True")
                force_fx = (q.get("refresh_fx") or ["0"])[0] in ("1", "true", "True")
                return self._json(
                    200,
                    build_settlement_summary(
                        months_back,
                        only_settled,
                        weeks_back=weeks_back,
                        force_fx_refresh=force_fx,
                    ),
                )
            # Ozon 利润分析：真实生效价(含弹性提升折扣) + 保最低利润率的min_price草稿
            if method == "GET" and subpath == "profit_table":
                from modules.ozon.profit_analysis import build_profit_table
                from modules.ozon.pending_drafts import dismissed_offer_ids

                q = parse_qs(query or "")
                target_margin = float((q.get("target_margin") or ["0.05"])[0])
                excluded = dismissed_offer_ids()
                return self._json(200, build_profit_table(target_margin, excluded_offer_ids=excluded))
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return True

        from modules.ozon.webapp_bridge import proxy_request

        try:
            status, data, ctype = proxy_request(method, subpath, query=query or None, body=body)
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return True
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if path in (
            "/new-product",
            "/new-product.html",
            "/product-workspace",
            "/product-workspace.html",
        ):
            return self._file(WEB_DIR / "product_workspace.html")
        if path in (
            "/new-product/images",
            "/new-product/images.html",
            "/ai-image-studio",
            "/ai-image-studio.html",
            "/ai-images",
            "/ai-images.html",
        ):
            return self._file(WEB_DIR / "ai_image_studio.html")
        if path in ("/new-product-legacy", "/new-product-legacy.html"):
            return self._module_moved("Orbit Treasury", "http://127.0.0.1:8766/")
        if path in ("/workbench", "/workbench.html"):
            return self._file(WEB_DIR / "workbench.html")
        if path in ("/ozon", "/ozon.html", "/rus", "/rus.html"):
            return self._module_moved("Orbit Rus", "http://127.0.0.1:8767/")
        if self._handle_product_flow_proxy("GET"):
            return
        if path.startswith("/api/new-product/"):
            return self._json(410, {"ok": False, "error": "Orbit Treasury moved to http://127.0.0.1:8766/"})
        if self._handle_ozon_proxy("GET"):
            return
        if path.startswith("/api/ozon/") or path.startswith("/api/rus/"):
            return self._json(410, {"ok": False, "error": "Orbit Rus moved to http://127.0.0.1:8767/"})

        if path in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html")
        if path in ("/release", "/release.html"):
            return self._redirect("/new-product")
        if path in ("/internal/release", "/internal/release.html"):
            return self._file(WEB_DIR / "release.html")
        if path in ("/profit", "/profit.html"):
            return self._file(WEB_DIR / "profit_center.html")
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            return self._file(WEB_DIR / "static" / rel)
        if path == "/api/proxy-image":
            q = parse_qs(urlparse(self.path).query)
            url = unquote((q.get("url") or [""])[0]).strip()
            if not url:
                return self._json(400, {"ok": False, "error": "missing url"})
            try:
                fp, ctype = _download_remote_image(url)
                return self._file(fp, cache_seconds=86400)
            except (ValueError, urllib.error.URLError, TimeoutError, OSError) as e:
                return self._image_placeholder("image unavailable")
        if path in ("/costs", "/costs.html"):
            return self._file(WEB_DIR / "costs.html")
        if path in ("/titles", "/titles.html"):
            return self._file(WEB_DIR / "titles.html")
        if path in ("/promotions", "/promotions.html"):
            return self._file(WEB_DIR / "promotions.html")
        if path in ("/analytics", "/analytics.html"):
            return self._file(WEB_DIR / "analytics.html")
        if path in ("/deactivate", "/deactivate.html"):
            return self._file(WEB_DIR / "deactivate.html")
        if path in ("/images", "/images.html"):
            return self._file(WEB_DIR / "images.html")
        if path in ("/catalog", "/catalog.html"):
            return self._file(WEB_DIR / "catalog.html")
        if path in ("/settlement", "/settlement.html"):
            return self._file(WEB_DIR / "settlement.html")
        if path in ("/sourcing", "/sourcing.html"):
            return self._file(WEB_DIR / "sourcing.html")
        if path in ("/th-dim-fix", "/th-dim-fix.html"):
            return self._file(WEB_DIR / "th-dim-fix.html")
        if path in ("/sourcing/photoroom", "/sourcing/photoroom.html"):
            return self._file(WEB_DIR / "photoroom_showcase.html")
        if path in ("/ozon", "/ozon.html"):
            return self._file(WEB_DIR / "ozon.html")
        if path in ("/mx", "/mx.html"):
            return self._file(WEB_DIR / "mx.html")
        if path in ("/uk", "/uk.html"):
            return self._file(WEB_DIR / "uk.html")
        if path in ("/billing", "/billing.html"):
            return self._file(WEB_DIR / "billing.html")
        if path in ("/shopee-profit", "/shopee-profit.html"):
            return self._file(WEB_DIR / "shopee_profit.html")
        if path in ("/sku-profit", "/sku_profit", "/sku_profit.html"):
            return self._file(WEB_DIR / "sku_profit.html")
        if path == "/billing/shopee_report":
            q = parse_qs(urlparse(self.path).query)
            name = (q.get("f") or [""])[0]
            # 仅允许周报快照文件，防目录穿越
            if not name or not name.startswith("weekly_shopee_profit_") or "/" in name or ".." in name:
                return self.send_error(404)
            fp = ROOT / "outputs" / name
            if not fp.is_file():
                return self.send_error(404)
            return self._file(fp)

        if path == "/api/billing/shopee_reports":
            out_dir = ROOT / "outputs"
            files = []
            if out_dir.is_dir():
                for p in sorted(out_dir.glob("weekly_shopee_profit_*.html"), reverse=True)[:12]:
                    stat = p.stat()
                    files.append({"name": p.name, "mtime": int(stat.st_mtime), "size": stat.st_size})
            return self._json(200, {"ok": True, "reports": files})

        if path == "/api/shopee/profit/reports":
            from modules.shopee import profit_settlement as sp_profit

            return self._json(200, {"ok": True, "items": sp_profit.list_reports()})

        if path == "/api/shopee/profit/status":
            from modules.finance import th_orders_pull as orders_pull

            st = orders_pull.pull_status()
            sp = ((st.get("result") or {}).get("platforms") or {}).get("shopee")
            return self._json(
                200,
                {
                    "ok": True,
                    "running": bool(st.get("running")),
                    "message": st.get("message") or "",
                    "percent": st.get("percent") or 0,
                    "error": st.get("error"),
                    "last_result": sp,
                    "scheduler_running": st.get("scheduler_running"),
                },
            )

        if path == "/api/orders-pull/status":
            from modules.finance import th_orders_pull as orders_pull

            return self._json(200, {"ok": True, **orders_pull.pull_status()})

        if path == "/api/billing/shopee_settlement":
            from modules.shopee import profit_settlement as sp_profit

            q = parse_qs(urlparse(self.path).query)
            try:
                start = sp_profit.parse_iso_date((q.get("start") or [""])[0])
                end = sp_profit.parse_iso_date((q.get("end") or [""])[0])
                return self._json(200, sp_profit.settlement_summary(start, end))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/mx/approvals":
            from modules.miaoshou import mx_web_approval as mx_web

            q = parse_qs(urlparse(self.path).query)
            status = (q.get("status") or ["pending"])[0]
            items = mx_web.list_cards(status=status or None)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path.startswith("/api/mx/approvals/"):
            from modules.miaoshou import mx_web_approval as mx_web

            sub = path[len("/api/mx/approvals/") :].split("/")[0]
            if sub == "publish" or not sub:
                return self.send_error(404)
            detail = mx_web.get_card_detail(sub)
            if not detail:
                return self._json(404, {"ok": False, "error": "not found"})
            return self._json(200, {"ok": True, "card": detail})
        if path == "/api/mx/publish/status":
            return self._json(200, {"ok": True, **_mx_publish_status()})

        if path == "/api/uk/approvals":
            from modules.miaoshou import uk_web_approval as uk_web

            q = parse_qs(urlparse(self.path).query)
            status = (q.get("status") or ["pending"])[0]
            items = uk_web.list_cards(status=status or None)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path.startswith("/api/uk/approvals/"):
            from modules.miaoshou import uk_web_approval as uk_web

            sub = path[len("/api/uk/approvals/") :].split("/")[0]
            if sub == "publish" or not sub:
                return self.send_error(404)
            detail = uk_web.get_card_detail(sub)
            if not detail:
                return self._json(404, {"ok": False, "error": "not found"})
            return self._json(200, {"ok": True, "card": detail})
        if path == "/api/uk/publish/status":
            return self._json(200, {"ok": True, **_uk_publish_status()})

        if path == "/api/sourcing/list":
            from modules.sourcing import pipeline as sourcing_mod
            return self._json(200, {"ok": True, "items": sourcing_mod.list_offers()})
        if path == "/api/sourcing/item":
            from modules.sourcing import pipeline as sourcing_mod
            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or q.get("id") or [""])[0]
            draft = sourcing_mod.load_draft(offer_id)
            if draft:
                return self._json(200, {"ok": True, "draft": draft})
            try:
                scrape = sourcing_mod.load_scrape(offer_id)
            except FileNotFoundError as e:
                return self._json(404, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True, "scrape": scrape, "draft": None})
        if path == "/api/sourcing/build/status":
            return self._json(200, {"ok": True, **_sourcing_build_status()})
        if path == "/api/sourcing/photoroom-showcase":
            from modules.sourcing import photoroom_showcase as showcase_mod
            from modules.products import image_ai

            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or q.get("id") or [""])[0]
            manifest = showcase_mod.load_showcase(offer_id) if offer_id else None
            return self._json(
                200,
                {
                    "ok": True,
                    "offer_id": offer_id,
                    "manifest": manifest,
                    "recipes": image_ai.list_recipes(),
                    "enabled": image_ai.image_enabled(),
                },
            )
        if path == "/api/sourcing/photoroom-showcase/status":
            return self._json(200, {"ok": True, **_photoroom_showcase_status()})
        if path == "/api/sourcing/detail-text":
            from modules.sourcing import detail_text_cards as dtc_mod

            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or q.get("id") or [""])[0]
            manifest = dtc_mod.load_detail_text_cards(offer_id) if offer_id else None
            return self._json(200, {"ok": True, "offer_id": offer_id, "manifest": manifest})
        if path == "/api/sourcing/workbench":
            from modules.sourcing import image_workbench as wb_mod

            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or q.get("id") or [""])[0]
            if not offer_id:
                return self._json(400, {"ok": False, "error": "缺少 offer_id"})
            try:
                return self._json(200, {"ok": True, **wb_mod.get_workbench(offer_id)})
            except FileNotFoundError as e:
                return self._json(404, {"ok": False, "error": str(e)})
        if path == "/api/sourcing/workbench/shops":
            from modules.sourcing import tk_publish as tk_pub

            try:
                return self._json(200, {"ok": True, "shops": tk_pub.list_shop_options()})
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/sourcing/workbench/dewatermark/status":
            return self._json(200, {"ok": True, **_dewatermark_status()})
        if path == "/api/sourcing/asset":
            from modules.sourcing import pipeline as sourcing_mod
            q = parse_qs(urlparse(self.path).query)
            offer_id = (q.get("offer_id") or [""])[0]
            file_path = (q.get("file") or [""])[0]
            fp = sourcing_mod.resolve_asset(offer_id, file_path)
            if not fp:
                return self.send_error(404)
            return self._file(fp)
        if path == "/api/new-product/preview":
            from modules.sourcing import new_product_workbench as np_mod
            q = parse_qs(urlparse(self.path).query)
            raw = (q.get("offer_id") or q.get("url") or [""])[0]
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id or url"})
            try:
                return self._json(200, np_mod.build_preview(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/orbit/navigation":
            from shared_platform.orbit_registry import navigation_payload

            return self._json(200, {"ok": True, **navigation_payload()})
        if path in {
            "/api/product-workspace/collectbox-action/preview",
            "/api/product-workspace/collectbox-action/status",
        }:
            q = parse_qs(
                urlparse(self.path).query,
                keep_blank_values=True,
            )
            if (
                set(q) != {"offer_id", "plan_id"}
                or len(q["offer_id"]) != 1
                or len(q["plan_id"]) != 1
            ):
                return self._json(
                    400,
                    {
                        "schema_version": "collectbox-action-status/v1",
                        "ok": False,
                        "persisted": False,
                        "error": _collectbox_public_error(
                            category="VALIDATION",
                            code="collectbox_query_identity_invalid",
                            detail=(
                                "exactly one offer_id and plan_id "
                                "are required"
                            ),
                        ),
                        "external_writes_performed": [],
                        "external_write_count": 0,
                    },
                )
            request = {
                "offer_id": q["offer_id"][0],
                "plan_id": q["plan_id"][0],
            }
            status, payload = (
                _preview_collectbox_action(request)
                if path.endswith("/preview")
                else _collectbox_action_status(request)
            )
            return self._json(status, payload)
        if path == "/api/product-workspace/publish-preview":
            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            status, payload = _preview_oneclick_release(
                {
                    "offer_id": (q.get("offer_id") or [""])[0],
                    "plan_id": (q.get("plan_id") or [""])[0],
                }
            )
            return self._json(status, payload)
        if path == "/api/product-workspace/publish-status":
            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            status, payload = _oneclick_release_status(
                {
                    "job_id": (q.get("job_id") or [""])[0],
                    "plan_id": (q.get("plan_id") or [""])[0],
                }
            )
            return self._json(status, payload)
        if path == "/api/product-workspace/shopee-global-plan-preview":
            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(q) != {"offer_id"} or len(q["offer_id"]) != 1:
                return self._json(
                    400,
                    {
                        "ok": False,
                        "error": "exactly one offer_id is required",
                        "external_writes_performed": [],
                    },
                )
            status, payload = _preview_shopee_global_plan(q["offer_id"][0])
            return self._json(status, payload)
        if path == _CHANNEL_CATEGORY_PREVIEW_PATH:
            q = parse_qs(
                urlparse(self.path).query,
                keep_blank_values=True,
            )
            if (
                set(q) != {"offer_id", "target_label"}
                or len(q["offer_id"]) != 1
                or len(q["target_label"]) != 1
            ):
                return self._json(
                    400,
                    {
                        "ok": False,
                        "error": (
                            "exactly one offer_id and target_label "
                            "are required"
                        ),
                        "external_writes_performed": [],
                    },
                )
            status, payload = _preview_channel_category_decision(
                offer_id=q["offer_id"][0],
                target_label=q["target_label"][0],
            )
            return self._json(status, payload)
        if path == "/api/product-workspace/reconcile-target":
            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            status, payload = _reconcile_existing_shopee_target_readonly(
                offer_id=(q.get("offer_id") or [""])[0],
                target_label=(q.get("target_label") or [""])[0],
            )
            return self._json(status, payload)
        if (
            path
            == "/api/product-workspace/release-target/"
            "shopee-price-repair-preview"
        ):
            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            status, payload = _preview_existing_shopee_target_price(
                offer_id=(q.get("offer_id") or [""])[0],
                target_label=(q.get("target_label") or [""])[0],
            )
            return self._json(status, payload)
        if (
            path
            == "/api/product-workspace/release-target/"
            "shopee-price-reconciliation-preview"
        ):
            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            status, payload = _preview_shopee_price_reconciliation(
                offer_id=(q.get("offer_id") or [""])[0],
                target_label=(q.get("target_label") or [""])[0],
            )
            return self._json(status, payload)
        if (
            path
            == "/api/product-workspace/release-target/"
            "target-scoped-action-preview"
        ):
            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            status, payload = _preview_target_scoped_release_action(
                offer_id=(q.get("offer_id") or [""])[0],
                target_label=(q.get("target_label") or [""])[0],
            )
            return self._json(status, payload)
        if (
            path
            == "/api/product-workspace/release-target/"
            "target-scoped-reconciliation-preview"
        ):
            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            status, payload = _preview_target_scoped_reconciliation(
                offer_id=(q.get("offer_id") or [""])[0],
                target_label=(q.get("target_label") or [""])[0],
            )
            return self._json(status, payload)
        if path == "/api/workbench/dashboard":
            from shared_platform.workbench_store import default_workbench_store

            return self._json(200, {"ok": True, **default_workbench_store().dashboard()})
        if path == "/api/workbench/deep-ops":
            from shared_platform.workbench_store import default_workbench_store

            q = parse_qs(urlparse(self.path).query)
            try:
                return self._json(200, {"ok": True, **default_workbench_store().deep_ops((q.get("date") or [None])[0])})
            except ValueError as error:
                return self._json(400, {"ok": False, "error": str(error)})
        if path == "/api/workbench/tasks":
            from shared_platform.workbench_store import default_workbench_store

            q = parse_qs(urlparse(self.path).query)
            filters = {name: (q.get(name) or [None])[0] for name in ("project", "business_line", "owner", "priority", "status")}
            tasks = default_workbench_store().list_tasks(**filters)
            return self._json(200, {"ok": True, "items": tasks, "count": len(tasks)})
        if path.startswith("/api/workbench/tasks/"):
            from shared_platform.workbench_store import default_workbench_store

            task_id = unquote(path.rsplit("/", 1)[-1])
            task = default_workbench_store().get_task(task_id)
            if not task:
                return self._json(404, {"ok": False, "error": "task not found"})
            return self._json(200, {"ok": True, "task": task, "events": default_workbench_store().events(task_id)})
        if path in ("/api/release/dashboard", "/api/product-workspace/dashboard"):
            from shared_platform.release_control import build_release_dashboard

            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            offer_id = (q.get("offer_id") or ["3828811808"])[0]
            publication_targets = q.get("target")
            try:
                kwargs = {
                    "offer_id": offer_id,
                    "publication_targets": publication_targets,
                }
                if path == "/api/release/dashboard":
                    kwargs["seller_sku"] = (
                        q.get("seller_sku") or ["0946"]
                    )[0]
                payload = build_release_dashboard(**kwargs)
                if path == "/api/product-workspace/dashboard":
                    payload = _product_workspace_view(payload)
                else:
                    payload = dict(payload)
                    payload.pop("_source_product_identity", None)
                    payload.pop("_source_identity_inputs", None)
                    payload.pop("_sku_lineage", None)
                return self._json(200, payload)
            except FileNotFoundError as error:
                return self._json(404, {"ok": False, "error": str(error)})
            except (TypeError, ValueError) as error:
                return self._json(400, {"ok": False, "error": str(error)})
            except Exception as error:
                return self._json(500, {"ok": False, "error": str(error)})
        if path in ("/api/release/weekly-preview", "/api/profit-center/weekly"):
            from datetime import date

            from shared_platform.release_control import build_weekly_profit_rehearsal

            q = parse_qs(urlparse(self.path).query)
            start_raw = (q.get("start") or [""])[0].strip()
            end_raw = (q.get("end") or [""])[0].strip()
            if not start_raw or not end_raw:
                return self._json(
                    400,
                    {"ok": False, "error": "start and end are required (YYYY-MM-DD)"},
                )
            try:
                payload = build_weekly_profit_rehearsal(
                    period_start=date.fromisoformat(start_raw),
                    period_end=date.fromisoformat(end_raw),
                )
                if path == "/api/profit-center/weekly":
                    payload = {
                        **payload,
                        "surface": "profit-center",
                        "mode": "pre_release",
                    }
                return self._json(200, payload)
            except (TypeError, ValueError) as error:
                return self._json(400, {"ok": False, "error": str(error)})
            except FileNotFoundError as error:
                return self._json(404, {"ok": False, "error": str(error)})
            except Exception as error:
                return self._json(500, {"ok": False, "error": str(error)})
        if path in ("/api/orbit/report-runs", "/api/orbit/inbox"):
            from shared_platform.report_store import default_report_store

            q = parse_qs(urlparse(self.path).query)
            try:
                limit = int((q.get("limit") or ["20"])[0])
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "error": "limit must be an integer"})
            store = default_report_store()
            if path == "/api/orbit/report-runs":
                items = store.list_report_runs(limit=limit)
            else:
                status = (q.get("status") or [None])[0]
                items = store.list_inbox(status=status, limit=limit)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/status":
            return self._json(200, _api_status())
        if path == "/api/health":
            return self._json(
                200,
                {
                    "ok": True,
                    "service": "orbit-hive-local-console",
                    "root": str(ROOT),
                    "new_product": (WEB_DIR / "new_product.html").is_file(),
                    "catalog": (WEB_DIR / "catalog.html").is_file(),
                    "threaded": True,
                },
            )
        if path == "/api/digest/preview":
            from modules.hub import digest as digest_mod
            snap = digest_mod.collect_snapshot()
            return self._json(
                200,
                {"ok": True, "text": digest_mod.preview_text(), "snapshot": snap},
            )
        if path == "/api/titles":
            from modules.products import titles as title_mod
            items = title_mod.load_queue("pending")
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/titles/scan/status":
            return self._json(200, {"ok": True, **_scan_status()})
        if path == "/api/titles/push/status":
            return self._json(200, {"ok": True, **_push_status()})
        if path == "/api/analytics/summary":
            from modules.products import analytics as analytics_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            return self._json(200, {"ok": True, **analytics_mod.summary(region=region)})
        if path == "/api/analytics/products":
            from modules.products import analytics as analytics_mod
            q = parse_qs(urlparse(self.path).query)
            segment = (q.get("segment") or [None])[0]
            region = (q.get("region") or [None])[0]
            items = analytics_mod.load_analytics(segment=segment, region=region)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/analytics/sync/status":
            return self._json(200, {"ok": True, **_analytics_sync_status()})
        if path == "/api/deactivate":
            from modules.products import deactivate as deact_mod
            items = deact_mod.load_queue("pending")
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/deactivate/scan/status":
            return self._json(200, {"ok": True, **_deact_scan_status()})
        if path == "/api/deactivate/push/status":
            return self._json(200, {"ok": True, **_deact_push_status()})
        if path == "/api/images/products":
            from modules.products import images as image_mod
            q = parse_qs(urlparse(self.path).query)
            query = (q.get("q") or [None])[0]
            region = (q.get("region") or [None])[0]
            try:
                lim = int((q.get("limit") or ["40"])[0])
            except ValueError:
                lim = 40
            items = image_mod.search_products(query=query, region=region, limit=lim)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/images/recipes":
            from modules.products import image_ai
            return self._json(
                200,
                {
                    "ok": True,
                    "recipes": image_ai.list_recipes(),
                    "slots": image_ai.TIKTOK_SLOT_GUIDE,
                },
            )
        if path == "/api/images":
            from modules.products import images as image_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            items = image_mod.load_active_queue(region=region)
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/images/scan/status":
            return self._json(200, {"ok": True, **_image_scan_status()})
        if path == "/api/images/download":
            from modules.products import images as image_mod
            q = parse_qs(urlparse(self.path).query)
            try:
                row_id = int((q.get("id") or ["0"])[0])
                index = int((q.get("index") or ["0"])[0])
            except ValueError:
                return self._json(400, {"ok": False, "error": "invalid id"})
            rows = image_mod.load_queue(status=None)
            row = next((r for r in rows if r["id"] == row_id), None)
            if not row:
                return self.send_error(404)
            paths = row.get("generated_paths") or []
            if index < 0 or index >= len(paths):
                return self.send_error(404)
            fp = image_mod.resolve_image_path(paths[index])
            if not fp:
                return self.send_error(404)
            return self._file(fp)
        if path == "/api/images/download-zip":
            from modules.products import images as image_mod
            q = parse_qs(urlparse(self.path).query)
            try:
                row_id = int((q.get("id") or ["0"])[0])
            except ValueError:
                return self._json(400, {"ok": False, "error": "invalid id"})
            zp = image_mod.export_slot_zip(row_id)
            if not zp or not zp.is_file():
                return self.send_error(404)
            return self._file(zp)
        if path == "/api/promotions":
            from modules.products import promotions as promo_mod
            q = parse_qs(urlparse(self.path).query)
            act_filter = (q.get("action") or [None])[0]
            region_filter = (q.get("region") or [None])[0]
            items = promo_mod.load_queue(
                "pending", action=act_filter, region=region_filter
            )
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/promotions/activities":
            from modules.products import promotions as promo_mod
            q = parse_qs(urlparse(self.path).query)
            region_filter = (q.get("region") or [None])[0]
            try:
                acts = promo_mod.list_ongoing_by_shop(region=region_filter)
                return self._json(200, {"ok": True, "activities": acts})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/promotions/scan/status":
            return self._json(200, {"ok": True, **_promo_scan_status()})
        if path == "/api/promotions/push/status":
            return self._json(200, {"ok": True, **_promo_push_status()})
        if path == "/api/promotions/coupons":
            from modules.products import promotions as promo_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            try:
                coupons = promo_mod.list_coupons(region=region)
                return self._json(200, {"ok": True, "coupons": coupons})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/promotions/coupon-drafts":
            from modules.products import promotions as promo_mod
            drafts = promo_mod.load_coupon_drafts()
            return self._json(200, {"ok": True, "drafts": drafts})
        if path == "/api/catalog/stores":
            from modules.catalog import listings as cat_mod
            try:
                return self._json(200, {"ok": True, "stores": cat_mod.store_summary(), "summary": cat_mod.global_summary()})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/catalog/products":
            from modules.catalog import listings as cat_mod
            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            sku = (q.get("sku") or [None])[0]
            match_only = (q.get("match_only") or ["0"])[0] in ("1", "true", "yes")
            platform = (q.get("platform") or [None])[0]
            try:
                limit = min(int((q.get("limit") or ["300"])[0] or 300), 500)
                offset = int((q.get("offset") or ["0"])[0] or 0)
                data = cat_mod.list_products(
                    region, sku=sku, match_only=match_only, platform=platform,
                    limit=limit, offset=offset,
                )
                return self._json(200, {"ok": True, **data})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})

        if path == "/api/catalog/export-pdf":
            from modules.catalog import pdf_export as pdf_mod

            q = parse_qs(urlparse(self.path).query)
            region = (q.get("region") or [None])[0]
            sku = (q.get("sku") or [None])[0]
            match_only = (q.get("match_only") or ["0"])[0] in ("1", "true", "yes")
            platform = (q.get("platform") or [None])[0]
            limit = min(int((q.get("limit") or ["300"])[0] or 300), 500)
            translate = (q.get("translate") or ["1"])[0] not in ("0", "false", "no")
            try:
                pdf_bytes, fname = pdf_mod.export_catalog_pdf(
                    region,
                    sku=sku,
                    match_only=match_only,
                    platform=platform,
                    limit=limit,
                    translate=translate,
                )
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
            return self._bytes(200, pdf_bytes, "application/pdf", filename=fname)

        if path == "/api/catalog/lookup":
            from modules.catalog import listings as cat_mod
            q = parse_qs(urlparse(self.path).query)
            sku = (q.get("sku") or q.get("q") or [""])[0]
            region = (q.get("region") or [None])[0]
            if not (sku or "").strip():
                return self._json(400, {"ok": False, "error": "请提供 sku 参数"})
            data = cat_mod.lookup_sku(sku, region)
            return self._json(200, data)

        if path == "/api/catalog/sku-edit":
            from modules.catalog import sku_edit as sku_edit_mod
            q = parse_qs(urlparse(self.path).query)
            mk = (q.get("match_key") or q.get("sku") or [""])[0]
            sku_id = (q.get("sku_id") or [""])[0]
            if not (mk or "").strip() and not (sku_id or "").strip():
                return self._json(400, {"ok": False, "error": "请提供 match_key 或 sku_id"})
            return self._json(200, sku_edit_mod.get_edit_rows(mk, sku_id=sku_id))

        if path == "/api/catalog/shopee-find":
            from modules.catalog import sku_edit as sku_edit_mod
            q = parse_qs(urlparse(self.path).query)
            query = (q.get("q") or q.get("sku") or [""])[0]
            if not (query or "").strip():
                return self._json(400, {"ok": False, "error": "请提供 q 参数"})
            live = (q.get("live") or ["1"])[0] not in ("0", "false", "no")
            return self._json(200, sku_edit_mod.find_shopee_rows(query, live=live))

        if path == "/api/catalog/sync/preview":
            q = parse_qs(urlparse(self.path).query)
            mode = str((q.get("mode") or ["fast"])[0]).strip().lower()
            try:
                preview = _catalog_database_baseline(mode)
            except ValueError as error:
                return self._json(400, {"ok": False, "error": str(error)})
            except Exception as error:
                return self._json(
                    500, {"ok": False, "error": f"目录预检失败: {error}"}
                )
            return self._json(200, {"ok": True, **preview})

        if path == "/api/catalog/sync/status":
            return self._json(200, {"ok": True, **_catalog_sync_status()})

        if path == "/api/catalog/shopee-sync/status":
            return self._json(200, {"ok": True, **_shopee_sync_status()})

        if path == "/api/catalog/shopee-sync-tk":
            return self._json(405, {"ok": False, "error": "use POST"})

        if path == "/api/catalog/shopee-sync-tk-group":
            return self._json(405, {"ok": False, "error": "use POST"})

        if path == "/api/catalog/shopee-sync-tk":
            match_key = str(data.get("match_key") or "").strip()
            region = str(data.get("region") or "PH").upper()
            if not match_key:
                return self._json(400, {"ok": False, "error": "需要 match_key"})
            ok, msg = _start_shopee_sync("single", {"match_key": match_key, "region": region})
            if not ok:
                return self._json(400, {"ok": False, "error": msg})
            return self._json(200, {"ok": True, "started": True, "message": msg})

        if path == "/api/catalog/shopee-sync-tk-group":
            raw_keys = data.get("match_keys") or data.get("keys") or ""
            region = str(data.get("region") or "PH").upper()
            if not raw_keys:
                return self._json(400, {"ok": False, "error": "需要 match_keys"})
            ok, msg = _start_shopee_sync("group", {"match_keys": raw_keys, "region": region})
            if not ok:
                return self._json(400, {"ok": False, "error": msg})
            return self._json(200, {"ok": True, "started": True, "message": msg})

        if path == "/api/settlement/config":
            from modules.finance import settlement_pull as spull
            from modules.finance.settlement_report import (
                data_range,
                default_ad_rates,
                default_rates,
                fee_column_defs,
                live_fx,
                live_rates,
            )

            ds, de = spull.default_period()
            return self._json(
                200,
                {
                    "ok": True,
                    "default_start": ds.isoformat(),
                    "default_end": de.isoformat(),
                    "rates": live_rates(),
                    "live_rates": live_rates(),
                    "fx": live_fx(),
                    "ad_rates": default_ad_rates(),
                    "fee_columns": fee_column_defs(),
                    "data_range": data_range(),
                },
            )

        if path == "/api/settlement/summary":
            from modules.finance.settlement_report import (
                default_ad_rates,
                default_rates,
                live_rates,
                parse_iso_date,
                summarize_period,
            )

            q = parse_qs(urlparse(self.path).query)
            start_s = (q.get("start") or [""])[0]
            end_s = (q.get("end") or [""])[0]
            rates_raw = (q.get("rates") or [""])[0]
            ad_rates_raw = (q.get("ad_rates") or [""])[0]
            if not start_s or not end_s:
                return self._json(400, {"ok": False, "error": "需要 start 与 end"})
            try:
                rates = live_rates()
                ad_rates = default_ad_rates()
                if rates_raw:
                    rates.update(json.loads(rates_raw))
                if ad_rates_raw:
                    ad_rates.update(json.loads(ad_rates_raw))
                data = summarize_period(
                    parse_iso_date(start_s), parse_iso_date(end_s), rates, ad_rates
                )
                self._json(200, {"ok": True, **data})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/settlement/orders-list":
            from modules.finance.settlement_report import (
                default_ad_rates,
                default_rates,
                live_rates,
                orders_for_period,
                parse_iso_date,
            )

            q = parse_qs(urlparse(self.path).query)
            start_s = (q.get("start") or [""])[0]
            end_s = (q.get("end") or [""])[0]
            rates_raw = (q.get("rates") or [""])[0]
            ad_rates_raw = (q.get("ad_rates") or [""])[0]
            if not start_s or not end_s:
                return self._json(400, {"ok": False, "error": "需要 start 与 end"})
            try:
                rates = live_rates()
                ad_rates = default_ad_rates()
                if rates_raw:
                    rates.update(json.loads(rates_raw))
                if ad_rates_raw:
                    ad_rates.update(json.loads(ad_rates_raw))
                data = orders_for_period(
                    parse_iso_date(start_s), parse_iso_date(end_s), rates, ad_rates
                )
                self._json(200, {"ok": True, **data})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/settlement/orders":
            from modules.finance.settlement_report import order_rows_for_file

            q = parse_qs(urlparse(self.path).query)
            fname = (q.get("file") or [""])[0]
            if not fname:
                return self._json(400, {"ok": False, "error": "需要 file 参数"})
            try:
                rate = float((q.get("rate") or ["0"])[0] or 0) or None
                ad_rate_legacy = (q.get("ad_rate") or [""])[0]
                ad_rate_percent = (q.get("ad_rate_percent") or [""])[0]
                if ad_rate_legacy and ad_rate_percent:
                    return self._json(
                        400,
                        {
                            "ok": False,
                            "error": "provide ad_rate_percent or legacy ad_rate, not both",
                        },
                    )
                ad_rate_value = ad_rate_percent or ad_rate_legacy
                ad_rate_pct = (
                    float(ad_rate_value)
                    if ad_rate_value not in ("", None)
                    else None
                )
                statement_id = (q.get("statement_id") or [""])[0] or None
                order_id = (q.get("order_id") or [""])[0] or None
                data = order_rows_for_file(
                    fname,
                    rate=rate,
                    ad_rate_pct=ad_rate_pct,
                    statement_id=statement_id,
                    order_id=order_id,
                )
                self._json(200, {"ok": True, **data})
            except FileNotFoundError as e:
                self._json(404, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/settlement/pull/status":
            from modules.finance import settlement_pull as spull

            return self._json(200, {"ok": True, **spull.pull_status()})

        if path == "/api/sku-profit":
            from modules.finance.sku_profit_service import estimate as sku_profit_estimate

            q = parse_qs(urlparse(self.path).query)
            sku = (q.get("sku") or [""])[0].strip()
            platform = (q.get("platform") or ["both"])[0].strip() or "both"
            ad_raw = (q.get("ad_rate") or [""])[0].strip()
            ad_percent_raw = (q.get("ad_rate_percent") or [""])[0].strip()
            lookback_raw = (q.get("lookback_days") or [""])[0].strip()
            sale_raw = (q.get("sale") or q.get("sale_override") or [""])[0].strip()
            cost_raw = (q.get("cost") or q.get("cost_override") or [""])[0].strip()
            force_fx = (q.get("force_fx") or [""])[0].strip() in ("1", "true", "yes")
            try:
                ad_rate = float(ad_raw) if ad_raw else None
                ad_rate_percent = float(ad_percent_raw) if ad_percent_raw else None
                lookback_days = int(lookback_raw) if lookback_raw else None
                sale_override = float(sale_raw) if sale_raw else None
                cost_override = float(cost_raw) if cost_raw else None
            except ValueError:
                return self._json(400, {"ok": False, "error": "参数无效（ad_rate/lookback_days/sale/cost）"})
            try:
                data = sku_profit_estimate(
                    sku,
                    platform=platform,
                    ad_rate=ad_rate,
                    ad_rate_percent=ad_rate_percent,
                    lookback_days=lookback_days,
                    sale_override=sale_override,
                    cost_override=cost_override,
                    force_fx_refresh=force_fx,
                )
                code = 200 if data.get("ok") else 404
                self._json(code, data)
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/sku-profit/hot":
            from modules.finance.sku_profit_service import list_hot_skus

            q = parse_qs(urlparse(self.path).query)
            platform = (q.get("platform") or ["both"])[0].strip() or "both"
            try:
                limit = int((q.get("limit") or ["20"])[0] or 20)
            except ValueError:
                limit = 20
            try:
                self._json(200, list_hot_skus(platform=platform, limit=min(max(limit, 1), 50)))
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/costs/export.csv":
            out = ROOT / "exports" / "sku_costs.csv"
            cost_mod.export_csv(out)
            return self._file(out)

        self.send_error(404)

    def _handle_feishu_event(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})
        from modules.hub import feishu_events as feishu_evt
        code, resp = feishu_evt.handle_http_body(body)
        self._json(code, resp)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/feishu/event":
            return self._handle_feishu_event()
        if path in {
            "/api/product-workspace/collect",
            "/api/product-workspace/facts",
            "/api/product-workspace/title-draft",
            "/api/product-workspace/title-adopt",
            "/api/product-workspace/approve",
            "/api/product-workspace/release-plan/approve",
            "/api/product-workspace/shopee-global-plan-approval",
            _CHANNEL_CATEGORY_APPROVAL_PATH,
            "/api/product-workspace/miaoshou-draft/commit",
            "/api/product-workspace/collectbox-action/start",
            "/api/product-workspace/publish",
            "/api/product-workspace/publish-tiktok",
            "/api/product-workspace/publish-shopee-global",
            "/api/product-workspace/publish-ozon",
            "/api/product-workspace/release-target/manual-verify",
            "/api/product-workspace/release-target/shopee-price-repair",
            (
                "/api/product-workspace/release-target/"
                "shopee-price-reconciliation"
            ),
            (
                "/api/product-workspace/release-target/"
                "target-scoped-action"
            ),
            (
                "/api/product-workspace/release-target/"
                "target-scoped-reconciliation"
            ),
        }:
            origin = (self.headers.get("Origin") or "").strip()
            if origin:
                expected_port = int(self.server.server_address[1])
                if origin not in {
                    f"http://127.0.0.1:{expected_port}",
                    f"http://localhost:{expected_port}",
                }:
                    return self._json(
                        403,
                        {
                            "ok": False,
                            "error": "cross-origin product workflow write rejected",
                        },
                    )
            content_type = (self.headers.get("Content-Type") or "").lower()
            if "application/json" not in content_type:
                return self._json(
                    415,
                    {
                        "ok": False,
                        "error": "product workflow writes require application/json",
                    },
                )
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "error": "invalid Content-Length"})
            if length < 0:
                return self._json(400, {"ok": False, "error": "invalid Content-Length"})
            if length > PRODUCT_APPROVAL_BODY_LIMIT:
                return self._json(413, {"ok": False, "error": "request body is too large"})
            try:
                data = self._read_json()
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(400, {"ok": False, "error": "invalid json"})
            if not isinstance(data, dict):
                return self._json(400, {"ok": False, "error": "json body must be an object"})
            if path == "/api/product-workspace/collect":
                status, payload = _collect_product_workspace_locally(data)
            elif path == "/api/product-workspace/facts":
                status, payload = _save_product_workspace_facts_locally(data)
            elif path == "/api/product-workspace/title-draft":
                status, payload = _generate_product_workspace_title_draft(data)
            elif path == "/api/product-workspace/title-adopt":
                status, payload = _adopt_product_workspace_title_candidate(data)
            elif path == "/api/product-workspace/approve":
                status, payload = _approve_product_workspace_locally(data)
            elif path == "/api/product-workspace/release-plan/approve":
                status, payload = _approve_release_plan_locally(data)
            elif path == "/api/product-workspace/shopee-global-plan-approval":
                status, payload = _approve_shopee_global_plan_locally(data)
            elif path == _CHANNEL_CATEGORY_APPROVAL_PATH:
                status, payload = (
                    _approve_channel_category_decision_locally(data)
                )
            elif path == "/api/product-workspace/miaoshou-draft/commit":
                status, payload = _prepare_miaoshou_release(data)
            elif path == "/api/product-workspace/collectbox-action/start":
                status, payload = _start_collectbox_action(data)
            elif path in {
                "/api/product-workspace/publish",
                "/api/product-workspace/publish-tiktok",
            }:
                status, payload = _start_tiktok_release(data)
            elif path == "/api/product-workspace/publish-shopee-global":
                status, payload = _start_shopee_global_release(data)
            elif path == "/api/product-workspace/publish-ozon":
                status, payload = _start_ozon_release(data)
            elif path == "/api/product-workspace/release-target/manual-verify":
                status, payload = _manually_verify_release_target(data)
            elif (
                path
                == "/api/product-workspace/release-target/shopee-price-repair"
            ):
                status, payload = _repair_existing_shopee_target_price(data)
            elif (
                path
                == "/api/product-workspace/release-target/"
                "shopee-price-reconciliation"
            ):
                status, payload = _reconcile_existing_shopee_price_repair(
                    data
                )
            elif (
                path
                == "/api/product-workspace/release-target/"
                "target-scoped-action"
            ):
                status, payload = _execute_target_scoped_release_action(data)
            elif (
                path
                == "/api/product-workspace/release-target/"
                "target-scoped-reconciliation"
            ):
                status, payload = _execute_target_scoped_reconciliation(data)
            else:
                return self._json(
                    404,
                    {"ok": False, "error": "unknown product workflow write"},
                )
            return self._json(status, payload)
        if self._handle_product_flow_proxy("POST"):
            return
        if path.startswith("/api/new-product/"):
            return self._json(410, {"ok": False, "error": "Orbit Treasury moved to http://127.0.0.1:8766/"})
        if self._handle_ozon_proxy("POST"):
            return
        if path.startswith("/api/ozon/") or path.startswith("/api/rus/"):
            return self._json(410, {"ok": False, "error": "Orbit Rus moved to http://127.0.0.1:8767/"})
        try:
            data = self._read_json()
        except json.JSONDecodeError:
            return self._json(400, {"ok": False, "error": "invalid json"})

        if path.startswith("/api/workbench/"):
            from shared_platform.workbench_store import default_workbench_store

            store = default_workbench_store()
            try:
                if path == "/api/workbench/tasks":
                    return self._json(201, {"ok": True, "task": store.create_task(data)})
                if path == "/api/workbench/deep-ops":
                    session = store.update_deep_ops_session(str(data.get("date") or date.today().isoformat()), data)
                    return self._json(200, {"ok": True, "session": session})
                if path == "/api/workbench/inbox/import":
                    message_id = str(data.get("message_id") or data.get("source_key") or "").strip()
                    text = str(data.get("text") or data.get("title") or "").strip()
                    if not message_id or not text:
                        return self._json(400, {"ok": False, "error": "message_id and text are required"})
                    task = store.create_task({
                        "title": text[:500], "status": "inbox", "priority": data.get("priority") or "P2",
                        "project": data.get("project") or "", "business_line": data.get("business_line") or "",
                        "related_url": data.get("source_url") or "", "execution_notes": data.get("text") or text,
                        "source_key": f"feishu:{message_id}",
                    })
                    return self._json(201, {"ok": True, "task": task, "deduplicated": task.get("source_key") == f"feishu:{message_id}"})
                if path.startswith("/api/workbench/tasks/"):
                    parts = path.split("/")
                    if len(parts) < 5:
                        return self._json(404, {"ok": False, "error": "unknown workbench endpoint"})
                    task_id = unquote(parts[4])
                    if len(parts) == 6 and parts[5] == "transition":
                        task = store.transition(task_id, str(data.get("status") or ""), str(data.get("note") or ""))
                        if task["status"] in {"waiting_approval", "blocked", "done"}:
                            from shared_platform.workbench_notify import notify_task_change

                            notify_task_change(store, task, task["status"])
                    elif len(parts) == 5:
                        task = store.update_task(task_id, data)
                        if "owner" in data and task.get("owner"):
                            from shared_platform.workbench_notify import notify_task_change

                            notify_task_change(store, task, "assigned")
                    else:
                        return self._json(404, {"ok": False, "error": "unknown workbench endpoint"})
                    return self._json(200, {"ok": True, "task": task})
                if path == "/api/workbench/weekly-review":
                    return self._json(200, {"ok": True, **store.weekly_review(str(data.get("week_start") or ""), data.get("content"))})
            except KeyError:
                return self._json(404, {"ok": False, "error": "task not found"})
            except (TypeError, ValueError) as error:
                return self._json(400, {"ok": False, "error": str(error)})

        if path == "/api/shopee/profit/run":
            from modules.finance import th_orders_pull as orders_pull
            from modules.shopee import profit_settlement as sp_profit

            # 若请求 pull=1 / refresh=1，先走 escrow API 拉取
            if str(data.get("pull") or data.get("refresh") or "").lower() in (
                "1",
                "true",
                "yes",
            ) or data.get("pull") is True:
                lookback = data.get("lookback_days") or data.get("days") or 14
                try:
                    lookback = int(lookback)
                except (TypeError, ValueError):
                    lookback = 14
                ok, msg = orders_pull.start_pull(
                    platforms=["shopee"],
                    regions=[str(data.get("region") or "TH").upper()],
                    lookback_days=lookback,
                )
                code = 200 if ok else 409
                return self._json(code, {"ok": ok, "message": msg, "pull_started": ok})

            try:
                if data.get("date"):
                    start = end = sp_profit.parse_iso_date(str(data.get("date")))
                else:
                    start = sp_profit.parse_iso_date(str(data.get("from") or data.get("start") or ""))
                    end = sp_profit.parse_iso_date(str(data.get("to") or data.get("end") or ""))
                result = sp_profit.settlement_summary(start, end)
                return self._json(200, {"ok": True, "message": "Shopee range loaded", **result})
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e), "message": str(e)})

        if path == "/api/orders-pull":
            from modules.finance import th_orders_pull as orders_pull
            from modules.finance.settlement_report import parse_iso_date

            platforms = data.get("platforms") or data.get("platform") or ["tiktok", "shopee"]
            if isinstance(platforms, str):
                platforms = (
                    ["tiktok", "shopee"]
                    if platforms.lower() in ("both", "all")
                    else [platforms]
                )
            regions = data.get("regions") or data.get("region") or ["TH"]
            if isinstance(regions, str):
                regions = [r.strip().upper() for r in regions.replace(",", " ").split() if r.strip()]
            lookback = data.get("lookback_days") or data.get("days")
            start = end = None
            try:
                if data.get("start") and data.get("end"):
                    start = parse_iso_date(str(data.get("start")))
                    end = parse_iso_date(str(data.get("end")))
                if lookback is not None:
                    lookback = int(lookback)
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"参数无效: {e}"})
            ok, msg = orders_pull.start_pull(
                platforms=list(platforms),
                regions=list(regions),
                lookback_days=lookback,
                start=start,
                end=end,
            )
            code = 200 if ok else 409
            return self._json(code, {"ok": ok, "message": msg})

        if path == "/api/mx/approvals/clear":
            from modules.miaoshou import mx_web_approval as mx_web

            result = mx_web.clear_pending_inbox(reason=str(data.get("reason") or "manual_clear"))
            return self._json(200, {"ok": True, **result})

        if path.startswith("/api/mx/approvals/"):
            from modules.miaoshou import mx_web_approval as mx_web

            parts = path[len("/api/mx/approvals/") :].strip("/").split("/")
            token = parts[0] if parts else ""
            action = parts[1] if len(parts) > 1 else ""
            if not token:
                return self._json(400, {"ok": False, "error": "missing token"})
            try:
                if action == "approve":
                    result = mx_web.approve_token(token)
                    return self._json(200, result)
                if action == "reject":
                    result = mx_web.reject_token(token)
                    return self._json(200, result)
                if action == "publish":
                    ok, msg = _start_mx_publish(token)
                    if not ok:
                        return self._json(409, {"ok": False, "error": msg})
                    return self._json(200, {"ok": True, "message": msg})
                if action == "override":
                    l = int(data.get("length_cm") or data.get("l") or 0)
                    w = int(data.get("width_cm") or data.get("w") or 0)
                    h = int(data.get("height_cm") or data.get("h") or 0)
                    if min(l, w, h) <= 0:
                        return self._json(400, {"ok": False, "error": "尺寸须为正整数 cm"})
                    result = mx_web.apply_override(
                        token, length_cm=l, width_cm=w, height_cm=h, note=str(data.get("note") or "")
                    )
                    card = mx_web.get_card_detail(token)
                    return self._json(200, {**result, "card": card})
            except KeyError as e:
                return self._json(404, {"ok": False, "error": str(e)})
            except RuntimeError as e:
                return self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
            return self._json(404, {"ok": False, "error": "unknown action"})

        if path == "/api/uk/approvals/clear":
            from modules.miaoshou import uk_web_approval as uk_web

            result = uk_web.clear_pending_inbox(reason=str(data.get("reason") or "manual_clear"))
            return self._json(200, {"ok": True, **result})

        if path.startswith("/api/uk/approvals/"):
            from modules.miaoshou import uk_web_approval as uk_web

            parts = path[len("/api/uk/approvals/") :].strip("/").split("/")
            token = parts[0] if parts else ""
            action = parts[1] if len(parts) > 1 else ""
            if not token:
                return self._json(400, {"ok": False, "error": "missing token"})
            try:
                if action == "approve":
                    result = uk_web.approve_token(token)
                    return self._json(200, result)
                if action == "reject":
                    result = uk_web.reject_token(token)
                    return self._json(200, result)
                if action == "publish":
                    ok, msg = _start_uk_publish(token)
                    if not ok:
                        return self._json(409, {"ok": False, "error": msg})
                    return self._json(200, {"ok": True, "message": msg})
                if action == "override":
                    l = int(data.get("length_cm") or data.get("l") or 0)
                    w = int(data.get("width_cm") or data.get("w") or 0)
                    h = int(data.get("height_cm") or data.get("h") or 0)
                    if min(l, w, h) <= 0:
                        return self._json(400, {"ok": False, "error": "尺寸须为正整数 cm"})
                    result = uk_web.apply_override(
                        token, length_cm=l, width_cm=w, height_cm=h, note=str(data.get("note") or "")
                    )
                    card = uk_web.get_card_detail(token)
                    return self._json(200, {**result, "card": card})
            except KeyError as e:
                return self._json(404, {"ok": False, "error": str(e)})
            except RuntimeError as e:
                return self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
            return self._json(404, {"ok": False, "error": "unknown action"})

        if path == "/api/catalog/cost":
            from modules.catalog import listings as cat_mod
            try:
                mk = str(data.get("match_key") or "").strip()
                cost = float(data.get("cost_cny", 0))
                if not mk or cost <= 0:
                    return self._json(400, {"ok": False, "error": "match_key 与 cost_cny 必填且 > 0"})
                saved = cat_mod.save_cost_by_match_key(mk, cost, data.get("note") or "")
                self._json(200, {"ok": True, "saved": saved, "match_key": mk, "cost_cny": cost})
            except (TypeError, ValueError) as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/catalog/seller-sku":
            from modules.catalog import sku_edit as sku_edit_mod
            try:
                plat = str(data.get("platform") or "").strip()
                sku = str(data.get("seller_sku") or "").strip()
                push = bool(data.get("push"))
                result = sku_edit_mod.save_seller_sku(
                    plat,
                    sku,
                    push=push,
                    sku_id=data.get("sku_id"),
                    shop_cipher=data.get("shop_cipher"),
                    global_product_id=data.get("global_product_id"),
                    global_sku_id=data.get("global_sku_id"),
                    model_id=data.get("model_id"),
                    shop_id=data.get("shop_id"),
                    product_id=data.get("product_id"),
                    item_id=data.get("item_id"),
                    match_key=data.get("match_key"),
                )
                self._json(200, {"ok": True, **result})
            except (TypeError, ValueError) as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/catalog/sync":
            mode = str(data.get("mode") or "fast").strip().lower()
            ok, msg, status_code, details = _start_catalog_sync(
                mode=mode,
                expected_snapshot_id=str(data.get("expected_snapshot_id") or ""),
                confirm_catalog_update=data.get("confirm_catalog_update") is True,
            )
            if not ok:
                return self._json(
                    status_code, {"ok": False, "message": msg, **details}
                )
            return self._json(
                status_code, {"ok": True, "message": msg, **details}
            )

        if path == "/api/shopee/th_dim_fix/save":
            from modules.shopee.dim_fix import save_dimension

            try:
                result = save_dimension(
                    int(data["item_id"]),
                    float(data["length_cm"]),
                    float(data["width_cm"]),
                    float(data["height_cm"]),
                )
                return self._json(200, result)
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})

        if path == "/api/new-product/preview":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("url") or data.get("offer_id") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing url or offer_id"})
            try:
                if data.get("precollect"):
                    urls = data.get("overseas_urls") or []
                    if isinstance(urls, str):
                        urls = [x.strip() for x in urls.replace("\r", "\n").split("\n") if x.strip()]
                    result = np_mod.precollect_preview(
                        raw,
                        overseas_urls=list(urls),
                        source_code=str(data.get("source_code") or ""),
                        force=bool(data.get("force")),
                    )
                else:
                    result = np_mod.build_preview(raw, source_code=str(data.get("source_code") or ""))
                _warm_preview_image_cache(result)
                return self._json(200, result)
            except Exception as e:
                try:
                    fallback = np_mod.build_preview(raw, source_code=str(data.get("source_code") or ""))
                    fallback["precollect_error"] = str(e)
                    _warm_preview_image_cache(fallback)
                    return self._json(200, fallback)
                except Exception:
                    return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/review":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.save_review(raw, data.get("review") or {}))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/image-request":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            prompt = str(data.get("prompt") or "").strip()
            if not raw or not prompt:
                return self._json(400, {"ok": False, "error": "missing offer_id or prompt"})
            try:
                return self._json(200, np_mod.add_image_request(raw, prompt, kind=str(data.get("kind") or "supplement")))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/miaoshou-draft":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.prepare_miaoshou_draft(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/miaoshou-draft/commit":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.write_miaoshou_draft(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/miaoshou-second-review/continue":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.start_claim_miaoshou_to_tiktok(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/site-drafts/prepare":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.prepare_miaoshou_site_drafts(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/sku-numbering/fix":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.ensure_common_sequential_skus(raw))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/overseas-source":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            overseas_url = str(data.get("overseas_url") or "").strip()
            if not raw or not overseas_url:
                return self._json(400, {"ok": False, "error": "missing offer_id or overseas_url"})
            try:
                return self._json(200, np_mod.add_overseas_source(raw, overseas_url, fetch=bool(data.get("fetch"))))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/new-product/overseas-sources":
            from modules.sourcing import new_product_workbench as np_mod
            raw = str(data.get("offer_id") or data.get("url") or "").strip()
            urls = data.get("overseas_urls") or []
            if isinstance(urls, str):
                urls = [x.strip() for x in urls.replace("\r", "\n").split("\n") if x.strip()]
            if not raw:
                return self._json(400, {"ok": False, "error": "missing offer_id"})
            try:
                return self._json(200, np_mod.save_overseas_sources(raw, list(urls), fetch=bool(data.get("fetch"))))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        if path == "/api/catalog/shopee-sync-tk":
            match_key = str(data.get("match_key") or "").strip()
            region = str(data.get("region") or "PH").upper()
            if not match_key:
                return self._json(400, {"ok": False, "error": "需要 match_key"})
            ok, msg = _start_shopee_sync("single", {"match_key": match_key, "region": region})
            if not ok:
                return self._json(400, {"ok": False, "error": msg})
            return self._json(200, {"ok": True, "started": True, "message": msg})

        if path == "/api/catalog/shopee-sync-tk-group":
            raw_keys = data.get("match_keys") or data.get("keys") or ""
            region = str(data.get("region") or "PH").upper()
            if not raw_keys:
                return self._json(400, {"ok": False, "error": "需要 match_keys"})
            ok, msg = _start_shopee_sync("group", {"match_keys": raw_keys, "region": region})
            if not ok:
                return self._json(400, {"ok": False, "error": msg})
            return self._json(200, {"ok": True, "started": True, "message": msg})

        if path == "/api/catalog/shopee-sync-tk":
            match_key = str(data.get("match_key") or "").strip()
            region = str(data.get("region") or "PH").upper()
            if not match_key:
                return self._json(400, {"ok": False, "error": "需要 match_key"})
            try:
                from modules.catalog import shopee_push as sp_push

                result = sp_push.sync_tk_to_shopee_global(match_key, region=region)
                self._json(200, {"ok": True, **result})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/catalog/shopee-sync-tk-group":
            raw_keys = data.get("match_keys") or data.get("keys") or ""
            region = str(data.get("region") or "PH").upper()
            if not raw_keys:
                return self._json(400, {"ok": False, "error": "需要 match_keys"})
            try:
                from modules.catalog import shopee_push as sp_push

                result = sp_push.sync_tk_group_to_shopee(raw_keys, region=region)
                self._json(200, {"ok": True, **result})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/settlement/pull":
            from modules.finance import settlement_pull as spull
            from modules.finance.settlement_report import parse_iso_date

            start_s = str(data.get("start") or "").strip()
            end_s = str(data.get("end") or "").strip()
            if not start_s or not end_s:
                return self._json(400, {"ok": False, "error": "需要 start 与 end (YYYY-MM-DD)"})
            try:
                ok, msg = spull.start_pull(parse_iso_date(start_s), parse_iso_date(end_s))
                code = 200 if ok else 409
                self._json(code, {"ok": ok, "message": msg})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sku-profit/batch":
            from modules.finance.sku_profit_service import estimate_batch

            skus = data.get("skus") or []
            if isinstance(skus, str):
                skus = [x.strip() for x in skus.replace(",", "\n").splitlines() if x.strip()]
            elif not isinstance(skus, list):
                return self._json(400, {"ok": False, "error": "skus must be an array or text list"})
            if not skus:
                return self._json(400, {"ok": False, "error": "需要 skus 数组"})
            if len(skus) > 30:
                return self._json(400, {"ok": False, "error": "batch supports at most 30 SKUs"})
            try:
                result = estimate_batch(
                    skus,
                    platform=str(data.get("platform") or "both"),
                    ad_rate=data.get("ad_rate"),
                    ad_rate_percent=data.get("ad_rate_percent"),
                    lookback_days=data.get("lookback_days"),
                    cost_override=(
                        data.get("cost_override")
                        if "cost_override" in data
                        else data.get("cost")
                    ),
                )
                self._json(200 if result.get("ok") else 404, result)
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/costs":
            try:
                saved = cost_mod.save_costs_bulk(data.get("costs") or [])
                self._json(200, {"ok": True, "saved": saved})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/digest/send":
            from modules.hub.service import send_digest
            try:
                send_digest(dry_run=bool(data.get("dry_run")))
                self._json(200, {"ok": True, "message": "已发送飞书日报"})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/titles/scan":
            ok, msg = _start_scan(
                days=int(data.get("days") or 30),
                max_units=int(data.get("max_units", 1)),
                limit=int(data.get("limit") or 30),
                region=data.get("region") or None,
                mode=data.get("mode") or "velocity",
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/analytics/sync":
            ok, msg = _start_analytics_sync(data.get("region") or None)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/deactivate/scan":
            ok, msg = _start_deact_scan(
                limit=int(data.get("limit") or 50),
                region=data.get("region") or None,
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/deactivate/push":
            items = data.get("items") or []
            ok, msg = _start_deact_push(items)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/images/scan":
            mode = data.get("mode") or "b_class"
            products = data.get("products") or []
            main_recipes = data.get("main_recipes") if "main_recipes" in data else None
            explore_recipes = data.get("explore_recipes") or None
            custom_scenes = data.get("custom_scenes") or None
            include_default = bool(data.get("include_default_scenes"))
            ok, msg = _start_image_scan(
                limit=int(data.get("limit") or 10),
                region=data.get("region") or None,
                variants=int(data.get("variants") or 3),
                mode=mode,
                product_items=products if mode in ("manual", "explore") else None,
                main_recipe_ids=main_recipes,
                custom_scenes=custom_scenes,
                include_default_scenes=include_default,
                explore_recipe_ids=explore_recipes,
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/images/generate":
            from modules.products import images as image_mod
            pid = data.get("product_id") or ""
            cipher = data.get("shop_cipher") or ""
            if not pid or not cipher:
                return self._json(400, {"ok": False, "error": "missing product_id or shop_cipher"})
            try:
                ok = image_mod.generate_for_product(
                    pid,
                    cipher,
                    main_recipe_ids=data.get("main_recipes"),
                    custom_scenes=data.get("custom_scenes"),
                    include_default_scenes=bool(data.get("include_default_scenes")),
                    scan_source="manual",
                )
                self._json(200, {"ok": ok, "message": "已生成" if ok else "生成失败，见队列"})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/images/mark":
            from modules.products import images as image_mod
            row_id = int(data.get("id") or 0)
            action = data.get("action") or "done"
            if not row_id:
                return self._json(400, {"ok": False, "error": "missing id"})
            if action == "skip":
                ok = image_mod.mark_skipped(row_id)
            else:
                ok = image_mod.mark_done(row_id, data.get("selected_path"))
            self._json(200, {"ok": ok})
            return

        if path == "/api/titles/push":
            items = data.get("items") or []
            ok, msg = _start_push(items)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/promotions/scan":
            ok, msg = _start_promo_scan(
                days=int(data.get("days") or 30),
                max_units=int(data.get("max_units", 1)),
                limit=int(data.get("limit") or 30),
                region=data.get("region") or None,
                scope=data.get("scope") or "adjust",
                mode=data.get("mode") or "velocity",
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/promotions/coupons/scan":
            from modules.products import promotions as promo_mod
            try:
                n = promo_mod.scan_coupon_suggestions(
                    region=data.get("region") or None,
                    limit=int(data.get("limit") or 4),
                )
                self._json(200, {"ok": True, "count": n})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/promotions/coupon-drafts/mark":
            from modules.products import promotions as promo_mod
            draft_id = int(data.get("id") or 0)
            if draft_id:
                promo_mod.mark_coupon_draft_used(draft_id)
            self._json(200, {"ok": True})
            return

        if path == "/api/promotions/push":
            items = data.get("items") or []
            ok, msg = _start_promo_push(items)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/sourcing/build":
            offer_id = data.get("offer_id") or data.get("id") or ""
            plan_version = (data.get("plan_version") or data.get("plan") or "v2").strip()
            skip_slots = bool(data.get("skip_slots"))
            skip_images = bool(data.get("skip_images"))
            ok, msg = _start_sourcing_build(
                str(offer_id),
                plan_version=plan_version,
                skip_slots=skip_slots,
                skip_images=skip_images,
            )
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/sourcing/selection":
            from modules.sourcing import pipeline as sourcing_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                draft = sourcing_mod.save_selections(offer_id, data.get("selections") or data)
                self._json(200, {"ok": True, "draft": draft})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/photoroom-showcase/build":
            offer_id = data.get("offer_id") or data.get("id") or ""
            ok, msg = _start_photoroom_showcase(str(offer_id))
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/sourcing/detail-text/build":
            from modules.sourcing import detail_text_cards as dtc_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                manifest = dtc_mod.build_detail_text_cards(offer_id)
                self._json(200, {"ok": True, "manifest": manifest})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/download":
            from modules.sourcing import image_workbench as wb_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                raw = wb_mod.ensure_downloaded(offer_id)
                wb = wb_mod.get_workbench(offer_id)
                self._json(200, {"ok": True, "raw": raw, "workbench": wb})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/generate":
            from modules.sourcing import image_workbench as wb_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            source_file = str(data.get("source_file") or data.get("file") or "").strip()
            recipe_id = str(data.get("recipe_id") or data.get("recipe") or "").strip()
            if not offer_id or not source_file or not recipe_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id / source_file / recipe_id"})
                return
            try:
                entry = wb_mod.generate_image(offer_id, source_file=source_file, recipe_id=recipe_id)
                wb = wb_mod.get_workbench(offer_id)
                self._json(200, {"ok": True, "generated": entry, "workbench": wb})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/final":
            from modules.sourcing import image_workbench as wb_mod

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            action = str(data.get("action") or "save").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                if action == "add":
                    wb = wb_mod.add_to_final(
                        offer_id,
                        str(data.get("path") or ""),
                        str(data.get("target") or "tiktok_main"),
                    )
                elif action == "remove":
                    wb = wb_mod.remove_from_final(
                        offer_id,
                        str(data.get("path") or ""),
                        str(data.get("target") or "tiktok_main"),
                    )
                elif action == "reorder":
                    wb = wb_mod.reorder_final(
                        offer_id,
                        str(data.get("target") or "tiktok_main"),
                        list(data.get("paths") or []),
                    )
                elif action == "reset_defaults":
                    wb_mod.apply_raw_defaults(offer_id)
                    wb = wb_mod.get_workbench(offer_id)
                else:
                    wb = wb_mod.save_final(
                        offer_id,
                        tiktok_main=data.get("tiktok_main"),
                        tiktok_description=data.get("tiktok_description"),
                    )
                self._json(200, {"ok": True, "workbench": wb})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/dewatermark":
            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            ok, msg = _start_dewatermark_batch(offer_id)
            code = 200 if ok else 409
            self._json(code, {"ok": ok, "message": msg})
            return

        if path == "/api/sourcing/workbench/publish-tk":
            from modules.sourcing import tk_publish as tk_pub

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            product_id = str(data.get("product_id") or "").strip()
            shop_cipher = str(data.get("shop_cipher") or "").strip()
            region = str(data.get("region") or "MY").strip()
            if not offer_id or not product_id or not shop_cipher:
                self._json(400, {"ok": False, "error": "缺少 offer_id / product_id / shop_cipher"})
                return
            try:
                result = tk_pub.publish_to_product(
                    offer_id,
                    product_id=product_id,
                    shop_cipher=shop_cipher,
                    region=region,
                )
                self._json(200, {"ok": True, "result": result})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/api/sourcing/workbench/export-tk":
            from modules.sourcing import tk_publish as tk_pub

            offer_id = str(data.get("offer_id") or data.get("id") or "").strip()
            region = str(data.get("region") or "MY").strip()
            if not offer_id:
                self._json(400, {"ok": False, "error": "缺少 offer_id"})
                return
            try:
                zp = tk_pub.export_publish_bundle(offer_id, region=region)
                rel = str(zp.relative_to(ROOT))
                parts = rel.replace("\\", "/").split("/")
                sub = "/".join(parts[3:]) if len(parts) > 3 else zp.name
                url = f"/api/sourcing/asset?offer_id={offer_id}&file={quote(sub)}"
                self._json(200, {"ok": True, "path": rel, "url": url})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return

        self.send_error(404)


def serve(
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    page: str = "index",
    startup_refresh: bool | None = None,
):
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_DIR / "static").mkdir(parents=True, exist_ok=True)

    def _startup_refresh_tokens() -> None:
        try:
            from modules.hub.tokens import refresh_all

            r = refresh_all()
            if r.get("errors"):
                print("  [WARN] Token 刷新:", "; ".join(r["errors"][:2]))
            else:
                print("  [OK] Token 已自动刷新（TikTok + Shopee）")
        except Exception as e:
            print(f"  [WARN] Token 刷新跳过: {e}")

    if not (WEB_DIR / "costs.html").is_file():
        from modules.products.build_page import build_html
        build_html()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        server.daemon_threads = True
    except OSError as e:
        if getattr(e, "errno", None) == 48:
            print(f"  [WARN] 端口 {port} 已被占用。请先停止旧进程（旧版可能没有 /images 路由会 404）：")
            print(f"     lsof -i :{port}   # 查看 PID 后 kill <PID>")
            print(f"     然后重新运行: python3 main.py serve --page images")
        raise
    routes = {
        "index": "/",
        "release": "/internal/release",
        "product": "/new-product",
        "profit": "/profit",
        "catalog": "/catalog",
        "settlement": "/settlement",
        "costs": "/costs",
        "titles": "/titles",
        "promotions": "/promotions",
        "analytics": "/analytics",
        "deactivate": "/deactivate",
        "images": "/images",
        "sourcing": "/sourcing",
        "ozon": "/ozon",
        "mx": "/mx",
        "uk": "/uk",
        "sku-profit": "/sku-profit",
    }
    url = f"http://127.0.0.1:{port}{routes.get(page, '/')}"
    print(f"  [OK] 控制台: http://127.0.0.1:{port}/")
    print(f"  商品发布中心: http://127.0.0.1:{port}/new-product")
    print(f"  商品目录: http://127.0.0.1:{port}/catalog")
    print(f"  结算利润: http://127.0.0.1:{port}/settlement")
    print(f"  SKU利润探针: http://127.0.0.1:{port}/sku-profit")
    print(f"  Ozon 运营: http://127.0.0.1:{port}/ozon")
    print(f"  MX 上架审批: http://127.0.0.1:{port}/mx")
    print(f"  UK 上架审批: http://127.0.0.1:{port}/uk")
    print("  Orbit Rus: http://127.0.0.1:8767/")
    print(f"  1688 选品: http://127.0.0.1:{port}/sourcing")
    print("  Orbit Treasury: http://127.0.0.1:8766/")
    print(f"  Listing 优化: http://127.0.0.1:{port}/titles")
    print(f"  主图优化: http://127.0.0.1:{port}/images")
    print(f"  Analytics: http://127.0.0.1:{port}/analytics")
    print(f"  零销下架: http://127.0.0.1:{port}/deactivate")
    print(f"  促销调价: http://127.0.0.1:{port}/promotions")
    print(f"  成本维护: http://127.0.0.1:{port}/costs")
    print("  Ctrl+C 停止")
    if startup_refresh is None:
        startup_refresh = os.environ.get("ORBIT_STARTUP_REFRESH", "").lower() in ("1", "true", "yes")
    if startup_refresh:
        threading.Timer(0.5, _startup_refresh_tokens).start()
    else:
        print("  [OK] Startup token refresh skipped; run `python main.py tokens refresh` when needed.")

    try:
        from modules.finance.th_orders_pull import start_scheduler

        start_scheduler()
    except Exception as e:
        print(f"  [WARN] orders-pull 调度未启动: {e}")

    _recover_collectbox_actions()

    if open_browser:
        import webbrowser
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
        server.server_close()
