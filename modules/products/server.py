"""本地 Web 控制台：页面 + REST API。"""

from __future__ import annotations

import json
import ipaddress
import mimetypes
import socket
import threading
import time
import hashlib
import math
import os
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

from core.config import ROOT
from modules.products import costs as cost_mod
from shared_platform.registry import http_registry

WEB_DIR = ROOT / "web"
DEFAULT_PORT = 8765
IMAGE_CACHE_DIR = ROOT / "data" / "web_image_cache"
PRODUCT_APPROVAL_BODY_LIMIT = 64 * 1024
_product_approval_lock = threading.Lock()
_release_execution_lock = threading.Lock()
_product_workbench_locks_guard = threading.Lock()
_product_workbench_locks: dict[str, threading.Lock] = {}

# Phase 1 ownership seam. Handler dispatch and every existing URL remain
# unchanged; later route modules can consume this registry during extraction.
HTTP_DOMAIN_REGISTRY = http_registry()


def _product_workspace_view(payload: dict) -> dict:
    """Present governed evidence and durable V1 state as the formal workspace."""
    view_payload = dict(payload)
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
    release_v1 = _release_v1_view(payload)
    return {
        **view_payload,
        "schema_version": "product-workspace-v1",
        "mode": "formal_v1",
        "workspace_mode": "formal_v1",
        "approval": payload.get("approval_rehearsal", {}),
        "publication_plan": payload.get("publication_rehearsal", {}),
        "release_v1": release_v1,
    }


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
                "superseded_release_plan_id": active_plan_id or None,
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
                "prior_release_plan_id": active_plan_id or None,
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
        "superseded_release_plan_id": active_plan_id or None,
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
        locked_stale_refresh = (
            locked
            and str(listing_copy.get("status") or "").startswith("superseded")
            and data.get("refresh_stale_locked_candidate") is True
            and data.get("user_approved") is True
            and str(data.get("approved_by") or "").strip() == "Kyle"
        )
        if locked and not locked_stale_refresh:
            return 409, {
                "ok": False,
                "error_code": "locked_title_refresh_requires_kyle_approval",
                "error": (
                    "approved product facts are locked; only an explicitly "
                    "approved refresh of a stale title candidate is allowed"
                ),
            }
        if locked_stale_refresh and not (
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
        superseded_plan_id = ""
        if locked_stale_refresh:
            store = default_release_store()
            active_plan = store.active_plan_for_product(offer_id)
            superseded_plan_id = (
                str(active_plan.get("plan_id") or "").strip()
                if isinstance(active_plan, dict)
                else ""
            )
            if superseded_plan_id:
                try:
                    store.supersede_plan(
                        superseded_plan_id,
                        reason=(
                            "Kyle refreshed a stale audited title candidate "
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
        "superseded_release_plan_id": superseded_plan_id or None,
        "marketplace_writes_performed": [],
        "dashboard": _product_workspace_view(dashboard),
    }


def _release_plan_payload_from_dashboard(dashboard: dict) -> tuple[dict, list[str]]:
    """Build the exact immutable V1 payload without persisting it."""
    from domains.content_operations import release_listing_copy_identity

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
        "targets": targets,
        "product_revision": int(product.get("revision") or 0),
        "product_approval_id": str(actual_approval.get("approval_id") or ""),
        "product_fingerprint": str(actual_approval.get("input_fingerprint") or ""),
        "content_approval_status": str(content.get("approval_status") or ""),
        "content_strategy": str(content.get("strategy") or ""),
        "product_facts": {
            "title": str(product.get("title") or ""),
            "source_title_zh": str(product.get("source_title_zh") or ""),
            "source_offer_id": str(product.get("source_offer_id") or ""),
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
    return payload, list(dict.fromkeys(value for value in blockers if value))


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
    return persisted_payload == current_payload


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
        return {
            "eligible_for_plan_approval": False,
            "blockers": blockers,
            "plan": active,
            "plan_persisted": True,
            "plan_approved": approved,
            "run": historical_run,
            "miaoshou_prepared": bool(
                common and common.get("status") == "SUCCEEDED"
            ),
            "adapter_blockers": [],
            "publish_ready": False,
            "historical": True,
        }

    if not payload.get("plan_id"):
        return historical_view() or {
            "eligible_for_plan_approval": False,
            "blockers": blockers,
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
    return {
        "eligible_for_plan_approval": not blockers,
        "blockers": blockers,
        "plan": plan,
        "plan_persisted": bool(persisted),
        "plan_approved": approved,
        "run": run,
        "miaoshou_prepared": miaoshou_prepared,
        "adapter_blockers": list(dict.fromkeys(adapter_blockers)),
        "publish_ready": bool(
            approved
            and miaoshou_prepared
            and not adapter_blockers
            and (dashboard.get("actual_release_gate") or {}).get("ready")
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
        return 409, {
            "ok": False,
            "error": "release plan is not ready for approval",
            "blockers": blockers,
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
            predecessor = (
                active["plan_id"]
                if active and active["plan_id"] != preview["plan_id"]
                else None
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


def _prepare_miaoshou_release(data: dict) -> tuple[int, dict]:
    """Execute only the approved common-draft write and verified readback."""
    from modules.sourcing import new_product_workbench as np_mod
    from shared_platform.release_store import (
        ReleaseAuthorizationError,
        ReleaseStoreError,
        default_release_store,
    )

    if data.get("confirm_miaoshou_write") is not True:
        return 400, {
            "ok": False,
            "error": "explicit confirm_miaoshou_write=true is required",
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
        if target["status"] == "FAILED":
            run = store.retry_failed_targets(
                run["run_id"],
                ["miaoshou:COMMON"],
            )
        store.begin_target(run["run_id"], "miaoshou:COMMON")
    except (ReleaseAuthorizationError, ReleaseStoreError, StopIteration) as error:
        return 409, {"ok": False, "error": str(error)}

    try:
        result = np_mod.write_miaoshou_draft(plan_payload["product_id"])
        if not result.get("written_to_miaoshou") or not result.get("verified"):
            failed_checks = [
                str(name)
                for name, passed in (result.get("checks") or {}).items()
                if not passed
            ]
            detail = ", ".join(failed_checks) or "unknown fields"
            raise RuntimeError(
                "Miaoshou draft readback did not verify every approved field: "
                + detail
            )
        store.record_target_success(
            run["run_id"],
            "miaoshou:COMMON",
            external_id=str(result.get("offer_id") or plan_payload["product_id"]),
            readback_evidence={
                "source": "miaoshou_open_api",
                "verified": True,
                "offer_id": str(
                    result.get("offer_id") or plan_payload["product_id"]
                ),
                "collect_box_detail_id": result.get("detail_id"),
                "checks": dict(result.get("checks") or {}),
                "image_count": len(
                    ((result.get("draft") or {}).get("imgUrls") or ())
                ),
            },
        )
    except Exception as error:
        store_record_error = ""
        try:
            store.record_target_failure(
                run["run_id"],
                "miaoshou:COMMON",
                error=str(error),
            )
        except Exception as record_error:
            store_record_error = str(record_error)
        payload = {
            "ok": False,
            "error": str(error),
            "run": store.get_run(run["run_id"]),
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


def _publish_selected_release(data: dict) -> tuple[int, dict]:
    """Execute the approved plan once through durable per-target adapters."""
    from domains.channel_operations.release_executor import AdapterExecutionRequest
    from modules.products.release_adapters import production_adapter_registry
    from shared_platform.release_store import default_release_store

    if data.get("confirm_publish") is not True:
        return 400, {
            "ok": False,
            "error": "explicit confirm_publish=true is required",
        }
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

    copy_blockers = _immutable_listing_copy_preflight(plan.get("payload") or {})
    if copy_blockers:
        return 409, {
            "ok": False,
            "error": "approved ReleasePlan has invalid immutable listing copy",
            "blockers": copy_blockers,
            "external_writes_performed": [],
        }

    run = store.start_run(plan_id)
    common = next(
        (
            row
            for row in (run.get("targets") or ())
            if row.get("target_label") == "miaoshou:COMMON"
        ),
        None,
    )
    if not common or common.get("status") != "SUCCEEDED":
        return 409, {
            "ok": False,
            "error": "Miaoshou COMMON must succeed with verified readback first",
            "run": run,
        }

    registry = production_adapter_registry()
    target_rows = (dashboard.get("omnichannel_preview") or {}).get("targets") or ()
    adapter_blockers: list[dict[str, str]] = []
    for target in target_rows:
        label = f"{target.get('channel')}:{target.get('site')}"
        if label == "miaoshou:COMMON":
            continue
        registration = registry.get(str(target.get("adapter") or ""))
        if not registration or not registration.executable:
            adapter_blockers.append(
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
    if adapter_blockers:
        return 409, {
            "ok": False,
            "error": "selected targets are not yet executable through unified V1 adapters",
            "adapter_blockers": adapter_blockers,
            "external_writes_performed": [],
            "run": run,
            "dashboard": _product_workspace_view(dashboard),
        }

    with _release_execution_lock:
        run = store.get_run(run["run_id"]) or run
        failed = [
            row["target_label"]
            for row in (run.get("targets") or ())
            if row.get("status") == "FAILED"
        ]
        if failed:
            run = store.retry_failed_targets(run["run_id"], failed)
        interrupted = [
            row["target_label"]
            for row in (run.get("targets") or ())
            if row.get("status") == "RUNNING"
        ]
        if interrupted:
            run = store.recover_interrupted_targets(
                run["run_id"],
                interrupted,
            )

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
        for durable_target in ordered:
            label = str(durable_target.get("target_label") or "")
            if (
                label == "miaoshou:COMMON"
                or durable_target.get("status")
                in {"SUCCEEDED", "SUBMITTED_UNVERIFIED", "MANUALLY_VERIFIED"}
            ):
                continue
            channel, site = label.split(":", 1)
            current_run = store.get_run(run["run_id"]) or run
            statuses = {
                row["target_label"]: row.get("status")
                for row in (current_run.get("targets") or ())
            }
            if channel == "tiktok":
                dependencies = ("miaoshou:COMMON",)
            elif channel == "shopee":
                dependencies = tuple(
                    candidate
                    for candidate in (
                        f"tiktok:LH_{site}",
                        f"tiktok:HB_{site}",
                        f"tiktok:{site}",
                    )
                    if candidate in statuses
                )
            elif channel == "ozon":
                dependencies = tuple(
                    candidate
                    for candidate in (
                        "tiktok:LH_PH",
                        "tiktok:HB_PH",
                        "tiktok:PH",
                    )
                    if candidate in statuses
                )
            else:
                dependencies = ()
            if dependencies and not any(
                statuses.get(dependency) == "SUCCEEDED"
                for dependency in dependencies
            ):
                continue

            plan_target = target_by_label.get(label) or {}
            registration = registry.get(str(plan_target.get("adapter") or ""))
            if not registration or not registration.executable:
                continue
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
                    store.record_target_failure(
                        run["run_id"],
                        label,
                        error=result.detail,
                        external_id=result.external_reference,
                    )
            except Exception as error:
                try:
                    store.record_target_failure(
                        run["run_id"],
                        label,
                        error=str(error),
                    )
                except Exception:
                    pass

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
            "run": final_run,
            "dashboard": _product_workspace_view(refreshed_dashboard),
        }


def _manually_verify_release_target(data: dict) -> tuple[int, dict]:
    """Record Kyle's exact platform inspection for a target without API access."""

    from shared_platform.release_store import (
        ImmutableReleaseError,
        ReleaseAuthorizationError,
        ReleaseStoreError,
        default_release_store,
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
    allowed = {
        "tiktok:HB_PH",
        "tiktok:HB_MY",
        "tiktok:HB_TH",
        "tiktok:HB_VN",
        "tiktok:MX",
        "tiktok:GB",
    }
    if target_label not in allowed or target_label not in (plan.get("targets") or ()):
        return 400, {
            "ok": False,
            "error": "target is not an approved API-less TikTok destination",
        }
    marketplace_product_id = str(
        data.get("marketplace_product_id") or ""
    ).strip()
    if (
        not marketplace_product_id
        or len(marketplace_product_id) > 128
        or any(character.isspace() for character in marketplace_product_id)
    ):
        return 400, {
            "ok": False,
            "error": "marketplace_product_id must be a non-empty ID without spaces",
        }
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    evidence = {
        "source": "kyle_marketplace_console_inspection",
        "marketplace_product_id": marketplace_product_id,
        "identity_matches": checks.get("identity_matches") is True,
        "seller_sku_matches": checks.get("seller_sku_matches") is True,
        "single_listing_for_sku": checks.get("single_listing_for_sku") is True,
        "title_matches": checks.get("title_matches") is True,
        "price_matches": checks.get("price_matches") is True,
        "images_match": checks.get("images_match") is True,
        "logistics_match": checks.get("logistics_match") is True,
    }
    run_id = f"release-run:{plan['payload_digest'][:24]}"
    try:
        target = store.record_manual_verification(
            run_id,
            target_label,
            verified_by="Kyle",
            user_verified=True,
            verification_evidence=evidence,
        )
    except (
        ValueError,
        ImmutableReleaseError,
        ReleaseAuthorizationError,
        ReleaseStoreError,
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
            "/api/product-workspace/miaoshou-draft/commit",
            "/api/product-workspace/publish",
            "/api/product-workspace/release-target/manual-verify",
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
            elif path == "/api/product-workspace/miaoshou-draft/commit":
                status, payload = _prepare_miaoshou_release(data)
            elif path == "/api/product-workspace/release-target/manual-verify":
                status, payload = _manually_verify_release_target(data)
            else:
                status, payload = _publish_selected_release(data)
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

    if open_browser:
        import webbrowser
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
        server.server_close()
