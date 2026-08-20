"""Read-only release-candidate orchestration for the local Orbit console.

This module composes the five-domain contracts into a review surface.  It
never persists an approval, writes a workbench file, or calls a marketplace.
The product approval it creates is explicitly labelled as a simulation and is
used only to prove that the downstream channel draft contracts can be built.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from core.config import ROOT
from core.db import connect_readonly
from domains.channel_operations import (
    OmnichannelPublicationPlan,
    build_channel_pricing_preview,
    build_omnichannel_publication_plan,
    build_publication_plan,
)
from domains.content_operations import (
    build_workbench_content_package_handoff,
    listing_title_fact_signature,
    release_listing_copy_identity,
)
from domains.product_operations import (
    ModelSkuAssignment,
    SkuAssignment,
    build_product_facts_snapshot,
    finalize_new_source_sku_reservation,
    preview_product_approval_lock,
    reservations_from_documents,
    resolve_sku_lineage_reservation,
    resolve_source_product_identity,
)
from modules.finance.profit_engine import exchange_rate_for
from modules.ozon.price_convert import exchange_rates as ozon_exchange_rates
from modules.sourcing.new_product_workbench import _source_summary, price_review
from shared_platform.report_store import ReportRunStore
from shared_platform.weekly_profit_runner import build_weekly_profit_preview


DEFAULT_OFFER_ID = "3828811808"
DEFAULT_CANDIDATE_SELLER_SKU = "0946"

TIKTOK_STORE_TARGETS: Mapping[str, tuple[str, str, str]] = {
    "LH_PH": ("lh_ph", "LivelyHive", "PH"),
    "LH_MY": ("lh_my", "LivelyHive", "MY"),
    "LH_TH": ("lh_th", "LivelyHive", "TH"),
    "LH_VN": ("lh_vn", "LivelyHive", "VN"),
    "HB_PH": ("hb_ph", "HomeBloom", "PH"),
    "HB_MY": ("hb_my", "HomeBloom", "MY"),
    "HB_TH": ("hb_th", "HomeBloom", "TH"),
    "HB_VN": ("hb_vn", "HomeBloom", "VN"),
    "MX": ("mx", "LivelyHive", "MX"),
    "GB": ("gb", "UK_IMPORT", "GB"),
}
PUBLICATION_TARGET_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("miaoshou", "COMMON"),
    *(("tiktok", site) for site in TIKTOK_STORE_TARGETS),
    ("shopee", "PH"),
    ("shopee", "MY"),
    ("shopee", "TH"),
    ("shopee", "VN"),
    ("ozon", "RU"),
)
PUBLICATION_TARGET_LABELS = frozenset(
    f"{channel}:{site}" for channel, site in PUBLICATION_TARGET_ALLOWLIST
)


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


def _known_tiktok_seller_skus(database_path: Path) -> tuple[str, ...]:
    values: set[str] = set()
    with connect_readonly(database_path) as connection:
        rows = connection.execute(
            "SELECT seller_sku FROM products "
            "WHERE seller_sku IS NOT NULL AND TRIM(seller_sku) != ''"
        ).fetchall()
        values.update(str(row["seller_sku"]).strip() for row in rows)
    return tuple(sorted(values))


def _catalog_sku_is_owned_by_release(
    database_path: Path,
    release_store,
    *,
    product_id: str,
    seller_sku: str,
) -> bool:
    """Recognize catalogue rows created by this product's approved release.

    The SKU uniqueness gate runs before publication.  After a successful
    official read-back is cached into the catalogue, the exact same row must
    become lifecycle evidence rather than invalidate its own approval.
    Unknown rows or last-four aliases remain conflicts.
    """

    active = release_store.active_plan_for_product(product_id)
    if (
        not active
        or str(active.get("status") or "") != "APPROVED"
        or str(active.get("seller_sku") or "").strip() != seller_sku
    ):
        return False
    digest = str(active.get("payload_digest") or "")
    run = release_store.get_run(f"release-run:{digest[:24]}") if digest else None
    if not run:
        return False

    owned_tiktok_ids: set[str] = set()
    owned_shopee_ids: set[str] = set()
    for target in run.get("targets") or ():
        if str(target.get("status") or "") != "SUCCEEDED":
            continue
        label = str(target.get("target_label") or "")
        evidence = (target.get("readback") or {}).get("evidence") or {}
        evidence_sku = str(evidence.get("seller_sku") or "").strip()
        if evidence_sku and evidence_sku != seller_sku:
            continue
        external_id = str(
            evidence.get("product_id")
            or evidence.get("item_id")
            or target.get("external_id")
            or ""
        ).strip()
        if not external_id:
            continue
        if label.startswith("tiktok:"):
            owned_tiktok_ids.add(external_id)
        elif label.startswith("shopee:"):
            owned_shopee_ids.add(external_id)

    exact_rows: list[tuple[str, str]] = []
    with connect_readonly(database_path) as connection:
        try:
            exact_rows.extend(
                ("tiktok", str(row["product_id"]))
                for row in connection.execute(
                    "SELECT product_id FROM products WHERE seller_sku = ?",
                    (seller_sku,),
                ).fetchall()
            )
        except sqlite3.Error:
            pass
        try:
            exact_rows.extend(
                ("shopee", str(row["item_id"]))
                for row in connection.execute(
                    "SELECT item_id FROM shopee_products WHERE seller_sku = ?",
                    (seller_sku,),
                ).fetchall()
            )
        except sqlite3.Error:
            pass
    if not exact_rows:
        return False
    return all(
        identity in (
            owned_tiktok_ids if platform == "tiktok" else owned_shopee_ids
        )
        for platform, identity in exact_rows
    )


def _locally_reserved_seller_skus(
    project_root: Path,
    *,
    exclude_offer_id: str,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                fact["seller_sku"]
                for fact in _local_seller_sku_reservations(
                    project_root,
                    exclude_offer_id=exclude_offer_id,
                )
            }
        )
    )


def _local_seller_sku_reservations(
    project_root: Path,
    *,
    exclude_offer_id: str,
) -> tuple[dict[str, str], ...]:
    """Read approved, legacy-locked, and verified-claim reservations.

    A catalog row is not the only proof that a Seller SKU is occupied. Older
    workbench flows locked the base SKU before the product reached the local
    catalog, while a verified TikTok claim can reserve a contiguous variant
    range. Both facts must remain active until their legacy lifecycle is
    explicitly reconciled.
    """
    state_dir = project_root / "data" / "new_product_workbench"
    if not state_dir.is_dir():
        return ()
    states: dict[str, Mapping[str, Any]] = {}
    claims: dict[str, Mapping[str, Any]] = {}
    for path in state_dir.glob("*.json"):
        if not path.stem.isdigit():
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(state, Mapping):
            continue
        states[path.stem] = state
    for path in state_dir.glob("*_tiktok_claim.json"):
        offer_id = path.name.removesuffix("_tiktok_claim.json")
        if not offer_id.isdigit():
            continue
        try:
            claim = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(claim, Mapping):
            claims[offer_id] = claim
    return tuple(
        fact.payload()
        for fact in reservations_from_documents(states, claims)
        if fact.offer_id != exclude_offer_id
    )


def _canonical_seller_sku(value: object) -> str:
    raw = str(value or "").strip()
    return raw[-4:].zfill(4) if raw.isdigit() else raw


def _seller_sku_matches(candidate: str, occupied: object) -> bool:
    clean_candidate = _canonical_seller_sku(candidate)
    return any(
        _canonical_seller_sku(value) == clean_candidate
        for value in occupied
    )


def _next_available_seller_skus(
    catalog_skus: object,
    reserved_skus: object,
    *,
    requested_count: int,
    additional_occupied_skus: object = (),
) -> tuple[str, ...]:
    catalog_numeric = {
        int(_canonical_seller_sku(value))
        for value in catalog_skus
        if _canonical_seller_sku(value).isdigit()
    }
    occupied_numeric = {
        *catalog_numeric,
        *(
            int(_canonical_seller_sku(value))
            for value in reserved_skus
            if _canonical_seller_sku(value).isdigit()
        ),
        *(
            int(_canonical_seller_sku(value))
            for value in additional_occupied_skus
            if _canonical_seller_sku(value).isdigit()
        ),
    }
    width = max(1, int(requested_count or 1))
    start = max(catalog_numeric, default=0) + 1
    while start + width - 1 <= 9999:
        block = tuple(range(start, start + width))
        if all(value not in occupied_numeric for value in block):
            return tuple(f"{value:04d}" for value in block)
        start += 1
    return ()


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


def _commercial_approval_facts(
    review: Mapping[str, Any],
    pricing_review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return only durable product facts owned by product approval.

    Target selection, video decisions, exchange rates and calculated prices
    belong to content/pricing/ReleasePlan authorities. Keeping them out of the
    product fingerprint lets those later-stage choices change without asking
    Kyle to re-approve the physical product facts.
    """

    del pricing_review  # retained in the call signature for compatibility
    category = review.get("category")
    facts = {
        "cost_cny": review.get("cost_cny"),
        "weight_kg": review.get("weight_kg"),
        "package_cm": list(review.get("package_cm") or ()),
        "selected_sku_keys": list(review.get("selected_sku_keys") or ()),
        "sku_label_overrides": {
            str(key): str(value)
            for key, value in sorted(
                (review.get("sku_label_overrides") or {}).items()
                if isinstance(review.get("sku_label_overrides"), Mapping)
                else ()
            )
        },
        "category": dict(category) if isinstance(category, Mapping) else category,
    }
    raw_sku_facts = review.get("sku_commercial_facts")
    if isinstance(raw_sku_facts, Mapping):
        # Preserve the exact historical approval fingerprint for legacy
        # single-SKU reviews.  SKU-only fields enter the fingerprint only when
        # the canonical SKU-keyed contract is actually present.
        facts["sku_commercial_facts"] = {
            str(key): {
                "cost_cny": row.get("cost_cny"),
                "weight_kg": row.get("weight_kg"),
                "package_cm": list(row.get("package_cm") or ()),
            }
            for key, row in sorted(raw_sku_facts.items())
            if isinstance(row, Mapping)
        }
    return facts


def _product_approval_facts_match(
    actual: Mapping[str, Any] | None,
    expected: Mapping[str, Any] | None,
) -> bool:
    """Accept legacy approvals after removing later-stage fingerprint fields."""

    if not isinstance(actual, Mapping) or not isinstance(expected, Mapping):
        return False
    return _commercial_approval_facts(actual) == dict(expected)


def _commercial_release_blockers(review: Mapping[str, Any]) -> list[str]:
    """Return only missing or structurally invalid commercial facts.

    Kyle may deliberately approve a Chinese working title or a reviewed cost
    that differs from source price evidence. Those differences remain visible
    warnings and are written into the approval audit rather than hard gates.
    """

    blockers: list[str] = []
    title = str(review.get("title") or "").strip()
    raw_sku_facts = review.get("sku_commercial_facts")
    if not title:
        blockers.append("请确认商品标题")
    try:
        weight = float(review.get("weight_kg") or 0)
    except (TypeError, ValueError):
        weight = 0
    if raw_sku_facts is None and weight <= 0:
        blockers.append("请确认商品重量")
    package = (
        list(review.get("package_cm") or ())
        if isinstance(review.get("package_cm"), (list, tuple))
        else []
    )
    try:
        package_ready = len(package) == 3 and all(float(value or 0) > 0 for value in package)
    except (TypeError, ValueError):
        package_ready = False
    if raw_sku_facts is None and not package_ready:
        blockers.append("请确认完整包装尺寸")
    if not review.get("selected_sites"):
        blockers.append("请至少选择一个目标站点")
    try:
        cost = float(review.get("cost_cny") or 0)
    except (TypeError, ValueError):
        cost = 0
    if raw_sku_facts is None and cost <= 0:
        blockers.append("请确认来源成本")
    selected_sku_keys = tuple(
        str(value or "").strip()
        for value in (review.get("selected_sku_keys") or ())
        if str(value or "").strip()
    )
    if raw_sku_facts is not None:
        if not isinstance(raw_sku_facts, Mapping) or set(raw_sku_facts) != set(
            selected_sku_keys
        ):
            blockers.append("请确认每个已选 SKU 的成本、重量和包装尺寸")
        else:
            for sku_key in selected_sku_keys:
                row = raw_sku_facts.get(sku_key)
                if not isinstance(row, Mapping):
                    blockers.append("请确认每个已选 SKU 的成本、重量和包装尺寸")
                    break
                try:
                    sku_package = list(row.get("package_cm") or ())
                    valid = (
                        float(row.get("cost_cny") or 0) > 0
                        and float(row.get("weight_kg") or 0) > 0
                        and len(sku_package) == 3
                        and all(float(value or 0) > 0 for value in sku_package)
                    )
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    blockers.append("请确认每个已选 SKU 的成本、重量和包装尺寸")
                    break
    return blockers


def _commercial_approval_warnings(review: Mapping[str, Any]) -> list[str]:
    """Return reviewable commercial differences that Kyle may override."""

    title = str(review.get("title") or "").strip()
    warnings: list[str] = []
    if title and (
        not re.search(r"[A-Za-z]", title)
        or re.search(r"[\u3400-\u9fff]", title)
    ):
        warnings.append(
            "当前商品标题仍含中文或缺少英文字母；可以先锁定事实，但发布前建议采用平台标题候选"
        )
    return warnings


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


def _normalise_release_sites(values: object) -> tuple[str, ...]:
    """Map workbench site keys (for example ``lh_ph``) to channel sites."""

    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple, set)):
        return ()
    sites: set[str] = set()
    for value in values:
        raw = str(value or "").strip().upper().replace("-", "_")
        if not raw:
            continue
        site = raw.rsplit("_", 1)[-1]
        if site and site.isalnum() and len(site) <= 8:
            sites.add(site)
    return tuple(sorted(sites))


def _default_omnichannel_targets(review: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Build the shared target matrix from the workbench's approved scope."""

    selected_store_keys = {
        str(value or "").strip().lower()
        for value in (review.get("selected_sites") or ())
        if str(value or "").strip()
    }
    selected_tiktok_sites = tuple(
        site
        for site, (target_key, _shop, country) in TIKTOK_STORE_TARGETS.items()
        if target_key in selected_store_keys
        and country in {"PH", "MY", "TH", "VN"}
    )
    tiktok_sites = tuple(dict.fromkeys((*selected_tiktok_sites, "MX", "GB")))
    targets: dict[str, tuple[str, ...]] = {
        "miaoshou": ("COMMON",),
        "tiktok": tiktok_sites,
    }
    if selected_tiktok_sites:
        targets["shopee"] = tuple(
            dict.fromkeys(
                country
                for site in selected_tiktok_sites
                for _target_key, _shop, country in (TIKTOK_STORE_TARGETS[site],)
            )
        )
    targets["ozon"] = ("RU",)
    return targets


def _publication_scope(
    review: Mapping[str, Any],
    requested_targets: object,
) -> tuple[dict[str, tuple[str, ...]], dict[str, Any]]:
    """Validate and resolve the read-only publication target selection.

    Browser-controlled values are never forwarded as arbitrary adapter names
    or sites.  The allowlist is deliberately narrower than the legacy
    repository: this release surface only offers the sixteen targets that its UI
    can name, price, and review explicitly.
    """

    default_selection = _default_omnichannel_targets(review)
    default_labels = [
        f"{channel}:{site}"
        for channel, site in PUBLICATION_TARGET_ALLOWLIST
        if site in default_selection.get(channel, ())
    ]
    if requested_targets is None:
        selected_labels = {
            f"{channel}:{site}"
            for channel, sites in default_selection.items()
            for site in sites
        }
        source = "workbench_default"
    else:
        if isinstance(requested_targets, (str, bytes)) or not isinstance(
            requested_targets,
            (list, tuple, set),
        ):
            raise TypeError("publication_targets must be a list of channel:site labels")
        raw_labels = [str(value or "").strip() for value in requested_targets]
        if not raw_labels or any(not value for value in raw_labels):
            raise ValueError("at least one publication target must be selected")
        normalised: list[str] = []
        for raw in raw_labels:
            channel, separator, site = raw.partition(":")
            label = f"{channel.strip().lower()}:{site.strip().upper()}"
            if not separator or label not in PUBLICATION_TARGET_LABELS:
                raise ValueError(f"unsupported publication target: {raw}")
            normalised.append(label)
        if len(set(normalised)) != len(normalised):
            raise ValueError("publication targets must not contain duplicates")
        selected_labels = set(normalised)
        source = "user_selection"

    selection: dict[str, tuple[str, ...]] = {}
    ordered_labels: list[str] = []
    for channel, site in PUBLICATION_TARGET_ALLOWLIST:
        label = f"{channel}:{site}"
        if label not in selected_labels:
            continue
        selection.setdefault(channel, ())
        selection[channel] = (*selection[channel], site)
        ordered_labels.append(label)
    if not ordered_labels:
        raise ValueError("at least one publication target must be selected")

    available_targets: list[dict[str, Any]] = []
    for channel, site in PUBLICATION_TARGET_ALLOWLIST:
        row: dict[str, Any] = {
            "label": f"{channel}:{site}",
            "channel": channel,
            "site": site,
            "selected": f"{channel}:{site}" in selected_labels,
        }
        if channel == "tiktok":
            target_key, shop, country = TIKTOK_STORE_TARGETS[site]
            row.update(
                target_key=target_key,
                shop=shop,
                country=country,
            )
        available_targets.append(row)
    return selection, {
        "source": source,
        "default_labels": default_labels,
        "selected_labels": ordered_labels,
        "selected_count": len(ordered_labels),
        "available_targets": available_targets,
        "selection_applied_to_plan": True,
        "read_only_preflight": True,
    }


def _blocked_omnichannel_preview(
    *,
    site_selection: Mapping[str, tuple[str, ...]],
    blockers: list[str],
) -> dict[str, Any]:
    return {
        "available": False,
        "ready": False,
        "dry_run": True,
        "plan_id": "",
        "all_preflights_passed": False,
        "confirmation_token_summary": None,
        "approval_summary": None,
        "workflow": {
            "common_draft": "miaoshou:COMMON",
            "master": "tiktok:approved_master_readback",
            "derived_channels": ["shopee", "ozon"],
        },
        "site_selection": {
            channel: list(sites) for channel, sites in site_selection.items()
        },
        "targets": [],
        "blockers": list(dict.fromkeys(blockers)),
        "adapter_calls_performed": False,
    }


def _serialize_omnichannel_preview(
    plan: OmnichannelPublicationPlan,
    *,
    pricing_by_target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    token = plan.approval.confirmation_token
    return {
        "available": True,
        "ready": plan.all_preflights_passed,
        "dry_run": plan.dry_run,
        "plan_id": plan.plan_id,
        "all_preflights_passed": plan.all_preflights_passed,
        "confirmation_token_summary": {
            "prefix": token.split("-", 1)[0],
            "scope_fingerprint": plan.approval.approval_scope_digest[:16],
            "masked": f"{token[:12]}...{token[-4:]}",
        },
        "approval_summary": {
            "collect_box_id": plan.approval.collect_box_id,
            "product_id": plan.approval.product_id,
            "seller_sku": plan.approval.seller_sku,
            "product_package_id": plan.approval.product_package_id,
            "content_package_id": plan.approval.content_package_id,
            "target_labels": list(plan.approval.target_labels),
            "image_count": plan.approval.image_count,
            "approval_scope_digest": plan.approval.approval_scope_digest,
            "irreversible_action_count": plan.approval.irreversible_action_count,
            "statement": plan.approval.statement,
        },
        "workflow": {
            "common_draft": "miaoshou:COMMON",
            "master": "tiktok:approved_master_readback",
            "derived_channels": ["shopee", "ozon"],
        },
        "site_selection": {
            target.channel: sorted(
                {
                    row.site
                    for row in plan.targets
                    if row.channel == target.channel
                }
            )
            for target in plan.targets
        },
        "targets": [
            {
                "channel": target.channel,
                "site": target.site,
                "adapter": target.adapter,
                "adapter_gate_status": target.adapter_gate_status,
                "repository_adapter_audited": next(
                    (
                        check.passed
                        for check in target.preflight
                        if check.code == "audited_adapter_site"
                    ),
                    False,
                ),
                "depends_on": list(target.depends_on),
                "preflights": [asdict(check) for check in target.preflight],
                "steps": [asdict(step) for step in target.steps],
                "idempotency_key": target.idempotency_key,
                "executable": target.executable,
                "pricing": dict(
                    (pricing_by_target or {}).get(
                        f"{target.channel}:{target.site}",
                        {},
                    )
                ),
            }
            for target in plan.targets
        ],
        "blockers": list(
            dict.fromkeys(
                check.detail
                for target in plan.targets
                for check in target.preflight
                if not check.passed
            )
        ),
        "adapter_calls_performed": plan.adapter_calls_performed,
    }


def _release_pricing_review(
    review: Mapping[str, Any],
    *,
    selected_site_keys: object | None = None,
    sku_commercial_facts: object = (),
    model_skus_by_variant: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Replay the persisted legacy pricing inputs without fetching live FX."""

    fx_rates = (
        dict(review.get("fx_rates") or {})
        if isinstance(review.get("fx_rates"), Mapping)
        else {}
    )
    currencies = ("PHP", "MYR", "THB", "VND", "GBP", "MXN")
    shopee_rates: dict[str, float] = {}
    for currency in currencies:
        rate = exchange_rate_for(currency)
        if rate > 0:
            shopee_rates[currency] = rate
    effective_site_keys = (
        review.get("selected_sites") or ()
        if selected_site_keys is None
        else selected_site_keys
    )

    def build_one(cost_value: object, weight_value: object, package_value: object):
        cost = float(_decimal(cost_value))
        weight = float(_decimal(weight_value))
        package = (
            list(package_value)
            if isinstance(package_value, (list, tuple))
            else []
        )
        package_cm = [float(_decimal(value)) for value in package[:3]]
        while len(package_cm) < 3:
            package_cm.append(0.0)
        legacy = price_review(
            cost,
            weight,
            package_cm,
            fx_rates=fx_rates,
        )
        return build_channel_pricing_preview(
            legacy,
            selected_site_keys=effective_site_keys,
            shopee_exchange_rates=shopee_rates,
            ozon_exchange_rates=ozon_exchange_rates(),
        )

    result = build_one(
        review.get("cost_cny"),
        review.get("weight_kg"),
        review.get("package_cm"),
    )
    assignments = dict(model_skus_by_variant or {})
    variant_rows = [
        row for row in (sku_commercial_facts or ()) if isinstance(row, Mapping)
    ]
    sku_pricing: list[dict[str, Any]] = []
    for row in variant_rows:
        variant_key = str(
            row.get("selected_key") or row.get("source_key") or ""
        ).strip()
        if not variant_key:
            continue
        variant_pricing = build_one(
            row.get("cost_cny"),
            row.get("weight_kg"),
            row.get("package_cm"),
        )
        sku_pricing.append(
            {
                "variant_key": variant_key,
                "source_key": str(row.get("source_key") or variant_key),
                "label": str(row.get("label") or variant_key),
                "model_sku": assignments.get(variant_key),
                "input": dict(variant_pricing.get("input") or {}),
                "selected_store_prices": list(
                    variant_pricing.get("selected_store_prices") or ()
                ),
                "target_pricing": dict(
                    variant_pricing.get("target_pricing") or {}
                ),
            }
        )
    result["sku_pricing"] = sku_pricing
    return result


def _pricing_site_keys_for_scope(
    review: Mapping[str, Any],
    site_selection: Mapping[str, tuple[str, ...]],
    base_pricing: Mapping[str, Any],
) -> tuple[str, ...]:
    """Resolve store-level pricing rows for the selected country targets.

    TikTok target labels carry an exact legacy ``lh_*`` or ``hb_*`` store key.
    Only those selected store rows feed the channel pricing preview; selecting
    a country on Shopee never silently selects a TikTok store.  The complete
    ten-store legacy audit remains present regardless of this selected subset.
    """

    del review, base_pricing
    return tuple(
        TIKTOK_STORE_TARGETS[site][0]
        for site in site_selection.get("tiktok", ())
        if site in TIKTOK_STORE_TARGETS
    )


def _apply_store_level_pricing(
    pricing: dict[str, Any],
    site_selection: Mapping[str, tuple[str, ...]],
) -> None:
    """Bind every channel price to the exact selected TikTok store targets."""

    target_pricing = pricing.get("target_pricing")
    if not isinstance(target_pricing, dict):
        return
    selected_rows = [
        row
        for row in (pricing.get("selected_store_prices") or ())
        if isinstance(row, Mapping)
    ]
    rows_by_key = {
        str(row.get("target_key") or "").strip().lower(): dict(row)
        for row in selected_rows
        if str(row.get("target_key") or "").strip()
    }

    def sku_prices_for(target_label: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for variant in pricing.get("sku_pricing") or ():
            if not isinstance(variant, Mapping):
                continue
            per_target = variant.get("target_pricing")
            price_row = (
                per_target.get(target_label)
                if isinstance(per_target, Mapping)
                else None
            )
            if not isinstance(price_row, Mapping):
                continue
            rows.append({
                "variant_key": variant.get("variant_key"),
                "source_key": variant.get("source_key"),
                "label": variant.get("label"),
                "model_sku": variant.get("model_sku"),
                **dict(price_row),
            })
        return rows

    for site in site_selection.get("tiktok", ()):
        target_key, shop, country = TIKTOK_STORE_TARGETS[site]
        row = rows_by_key.get(target_key)
        target_pricing[f"tiktok:{site}"] = {
            "role": "master_listing",
            "status": "ready" if row and row.get("list_price") is not None else "blocked",
            "store_prices": [row] if row else [],
            "source_field": "legacy price_review.*.list_price",
            "store_target": {
                "target_key": target_key,
                "shop": shop,
                "country": country,
            },
            "write_fields": ["skuMap.*.price", "skuMap.*.priceIncludeVat"],
            "sku_prices": [
                {
                    "variant_key": variant.get("variant_key"),
                    "source_key": variant.get("source_key"),
                    "label": variant.get("label"),
                    "model_sku": variant.get("model_sku"),
                    **dict(price_row),
                }
                for variant in (pricing.get("sku_pricing") or ())
                if isinstance(variant, Mapping)
                for price_row in (
                    next(
                        (
                            row
                            for row in (
                                variant.get("selected_store_prices") or ()
                            )
                            if isinstance(row, Mapping)
                            and str(row.get("target_key") or "") == target_key
                        ),
                        None,
                    ),
                )
                if isinstance(price_row, Mapping)
            ],
            **(
                {}
                if row
                else {"blocker": f"No legacy pricing row exists for {target_key}."}
            ),
        }

    rows_by_country = {
        country: [
            rows_by_key[target_key]
            for site, (target_key, _shop, row_country) in TIKTOK_STORE_TARGETS.items()
            if row_country == country and target_key in rows_by_key
        ]
        for country in ("PH", "MY", "TH", "VN")
    }
    for country in site_selection.get("shopee", ()):
        candidates = rows_by_country.get(country, [])
        existing = target_pricing.get(f"shopee:{country}")
        if not candidates or not isinstance(existing, Mapping):
            target_pricing[f"shopee:{country}"] = {
                "role": "derived_listing",
                "status": "blocked",
                "depends_on": f"tiktok:{country}:selected_store_readback",
                "source_policy": "same_country_selected_tiktok_store_required",
                "source_candidates": [],
                "blocker": (
                    f"Shopee {country} requires a selected TikTok store "
                    f"in {country}."
                ),
            }
            continue
        chosen_key = str((existing.get("source") or {}).get("target_key") or "")
        target_pricing[f"shopee:{country}"] = {
            **dict(existing),
            "sku_prices": sku_prices_for(f"shopee:{country}"),
            "source_policy": "prefer_livelyhive_then_homebloom_within_country",
            "source_candidates": [
                {
                    "target_key": row.get("target_key"),
                    "shop": row.get("shop"),
                    "region": row.get("region"),
                    "list_price": row.get("list_price"),
                    "currency": row.get("currency"),
                }
                for row in candidates
            ],
            "selected_source_target_key": chosen_key,
            "source_selection_note": (
                f"{chosen_key} is the deterministic master; all selected "
                f"{country} TikTok stores remain visible for review."
            ),
        }

    ozon = target_pricing.get("ozon:RU")
    if "RU" in site_selection.get("ozon", ()) and isinstance(ozon, Mapping):
        chosen_key = str((ozon.get("source") or {}).get("target_key") or "")
        target_pricing["ozon:RU"] = {
            **dict(ozon),
            "sku_prices": sku_prices_for("ozon:RU"),
            "source_policy": (
                "country_priority_PH_MY_TH_VN_MX_GB_then_"
                "prefer_livelyhive_then_homebloom"
            ),
            "source_candidates": [
                {
                    "target_key": row.get("target_key"),
                    "shop": row.get("shop"),
                    "region": row.get("region"),
                    "list_price": row.get("list_price"),
                    "currency": row.get("currency"),
                }
                for row in selected_rows
            ],
            "selected_source_target_key": chosen_key,
            "source_selection_note": (
                f"{chosen_key} is the deterministic Ozon master source."
                if chosen_key
                else "Ozon requires at least one selected TikTok store."
            ),
        }


def build_release_dashboard(
    *,
    offer_id: object = DEFAULT_OFFER_ID,
    seller_sku: object = None,
    root: str | Path = ROOT,
    database_path: str | Path | None = None,
    report_store_path: str | Path | None = None,
    publication_targets: object = None,
) -> dict[str, Any]:
    """Build the complete local release rehearsal without side effects."""
    clean_offer_id = _clean_offer_id(offer_id)
    project_root = Path(root)
    state_path = project_root / "data" / "new_product_workbench" / f"{clean_offer_id}.json"
    state = _read_json(state_path)
    if str(state.get("offer_id") or "").strip() != clean_offer_id:
        raise ValueError("workbench offer identity does not match the requested offer_id")
    review = state.get("review") if isinstance(state.get("review"), Mapping) else {}
    db_path = Path(database_path or project_root / "data" / "shop.db")
    known_skus = _known_seller_skus(db_path)
    tiktok_skus = _known_tiktok_seller_skus(db_path)
    reservation_facts = list(
        _local_seller_sku_reservations(
            project_root,
            exclude_offer_id=clean_offer_id,
        )
    )
    from shared_platform.release_store import ReleaseStore

    release_store_path = Path(
        report_store_path or project_root / "data" / "orbit_platform.db"
    )
    release_store = ReleaseStore(release_store_path)
    listing_copy_state = (
        state.get("listing_copy")
        if isinstance(state.get("listing_copy"), Mapping)
        else {}
    )
    lineage_plan = release_store.active_plan_for_product(clean_offer_id)
    if not lineage_plan:
        lineage_plan_id = str(
            listing_copy_state.get("superseded_release_plan_id") or ""
        ).strip()
        lineage_candidate = (
            release_store.get_plan(lineage_plan_id)
            if lineage_plan_id
            else None
        )
        if (
            lineage_candidate
            and str(lineage_candidate.get("product_id") or "").strip()
            == clean_offer_id
        ):
            lineage_plan = lineage_candidate
    lineage_seller_sku = str(
        (lineage_plan or {}).get("seller_sku") or ""
    ).strip()
    if lineage_seller_sku and (
        not lineage_seller_sku.isdigit() or len(lineage_seller_sku) > 32
    ):
        raise ValueError("release plan lineage has an invalid seller_sku")
    for row in release_store.active_sku_reservations():
        if str(row.get("product_id") or "").strip() == clean_offer_id:
            continue
        reservation_facts.append(
            {
                "seller_sku": str(row.get("seller_sku") or "").strip(),
                "offer_id": str(row.get("product_id") or "").strip(),
                "source": "release_plan_reservation",
                "status": str(row.get("status") or "").strip().lower(),
            }
        )
    durable_reserved_sku_keys = release_store.active_reserved_sku_keys()
    reserved_skus = tuple(
        sorted(
            {
                fact["seller_sku"]
                for fact in reservation_facts
                if str(fact.get("seller_sku") or "").strip()
            }
        )
    )
    requested_sku_count = max(
        1,
        len(review.get("selected_sku_keys") or ()),
    )
    next_seller_skus = _next_available_seller_skus(
        tiktok_skus,
        (*reserved_skus, *durable_reserved_sku_keys),
        requested_count=requested_sku_count,
        additional_occupied_skus=known_skus,
    )
    actual_approval = (
        state.get("product_approval")
        if isinstance(state.get("product_approval"), Mapping)
        else {}
    )
    locked_review_sku = str(review.get("seller_sku") or "").strip()
    has_locked_sku = bool(
        locked_review_sku.isdigit()
        and (
            bool(review.get("fields_locked"))
            or str(actual_approval.get("status") or "").strip().casefold()
            == "approved"
        )
    )
    requested_seller_sku = str(seller_sku or "").strip()
    locked_sku_drifted_from_lineage = bool(
        has_locked_sku
        and lineage_seller_sku
        and locked_review_sku != lineage_seller_sku
    )
    if locked_sku_drifted_from_lineage:
        # A successor may never change the Seller SKU. Older dashboard
        # versions could lock a newly allocated catalogue candidate after the
        # predecessor was superseded. Project the immutable predecessor SKU so
        # Kyle can re-approve the corrected local facts; do not let the stale
        # workbench lock create an impossible successor identity.
        clean_seller_sku = _clean_seller_sku(lineage_seller_sku)
        seller_sku_source = "release_plan_lineage_repair"
    elif has_locked_sku:
        clean_seller_sku = _clean_seller_sku(locked_review_sku)
        seller_sku_source = "approved_workbench_lock"
    elif requested_seller_sku:
        # Kept for internal/legacy callers. The formal product workspace omits
        # this argument and always consumes the automatic catalog candidate.
        clean_seller_sku = _clean_seller_sku(requested_seller_sku)
        seller_sku_source = "legacy_explicit_candidate"
    elif lineage_seller_sku:
        # Editing approved facts creates a successor ReleasePlan, not a new
        # product identity. Preserve the exact immutable Seller SKU from the
        # active or explicitly superseded predecessor even when the workbench
        # facts are temporarily unlocked for Kyle's re-approval.
        clean_seller_sku = _clean_seller_sku(lineage_seller_sku)
        seller_sku_source = "release_plan_lineage"
    else:
        if not next_seller_skus:
            raise ValueError(
                "no contiguous Seller SKU range is available in the 0001-9999 namespace"
            )
        clean_seller_sku = next_seller_skus[0]
        seller_sku_source = "automatic_catalog_and_reservation_scan"
    lineage_owns_seller_sku = bool(
        lineage_seller_sku
        and lineage_seller_sku == clean_seller_sku
    )
    approval_known_skus = known_skus
    if lineage_owns_seller_sku or _catalog_sku_is_owned_by_release(
        db_path,
        release_store,
        product_id=clean_offer_id,
        seller_sku=clean_seller_sku,
    ):
        approval_known_skus = tuple(
            value for value in known_skus if value != clean_seller_sku
        )
    displayed_sku_range = (
        (clean_seller_sku,)
        if seller_sku_source
        in {
            "approved_workbench_lock",
            "release_plan_lineage",
            "release_plan_lineage_repair",
        }
        else next_seller_skus
    )
    source = (
        _source_summary(clean_offer_id)
        if project_root.resolve() == Path(ROOT).resolve()
        else (
            state.get("source")
            if isinstance(state.get("source"), Mapping)
            else {}
        )
    )
    selected_title_sku_keys = {
        str(value) for value in (review.get("selected_sku_keys") or ())
    }
    title_label_overrides = (
        review.get("sku_label_overrides")
        if isinstance(review.get("sku_label_overrides"), Mapping)
        else {}
    )
    current_listing_copy_signature = listing_title_fact_signature(
        {
            "offer_id": clean_offer_id,
            "source_title_zh": str(source.get("title_source") or "").strip(),
            "category": dict(review.get("category") or {}),
            "cost_cny": review.get("cost_cny"),
            "weight_kg": review.get("weight_kg"),
            "package_cm": list(review.get("package_cm") or ()),
            "sku_commercial_facts": {
                str(key): {
                    "cost_cny": row.get("cost_cny"),
                    "weight_kg": row.get("weight_kg"),
                    "package_cm": list(row.get("package_cm") or ()),
                }
                for key, row in sorted(
                    (review.get("sku_commercial_facts") or {}).items()
                    if isinstance(review.get("sku_commercial_facts"), Mapping)
                    else ()
                )
                if isinstance(row, Mapping)
            },
            "selected_skus": [
                {
                    "key": str(row.get("key") or row.get("name") or ""),
                    "label": str(
                        title_label_overrides.get(
                            str(row.get("key") or row.get("name") or "")
                        )
                        or row.get("name")
                        or row.get("key")
                        or ""
                    ),
                }
                for row in (source.get("skus") or ())
                if isinstance(row, Mapping)
                and str(row.get("key") or row.get("name") or "")
                in selected_title_sku_keys
            ],
        }
    )
    content_state = (
        state.get("content_package")
        if isinstance(state.get("content_package"), Mapping)
        else {}
    )
    content_strategy = str(
        content_state.get("content_strategy") or "ai_assisted"
    ).strip()
    if content_strategy not in {"source_only", "ai_assisted"}:
        content_strategy = "ai_assisted"
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
    review_package_path = package_dir / "review_package.json"
    review_package_available = review_package_path.is_file()
    review_package = (
        _read_json(review_package_path)
        if review_package_available
        else {}
    )
    collect_box = (
        review_package.get("collect_box")
        if isinstance(review_package.get("collect_box"), Mapping)
        else {}
    )
    if (
        review_package_available
        and str(collect_box.get("detail_id") or "").strip() != collect_box_id
    ):
        raise ValueError("review package collect-box identity does not match its evidence directory")
    source_identity_inputs = {
        "collect_box": dict(collect_box),
        "precollect": (
            dict(source.get("precollect"))
            if isinstance(source.get("precollect"), Mapping)
            else {}
        ),
        "source_record": (
            dict(source.get("source_record"))
            if isinstance(source.get("source_record"), Mapping)
            else {}
        ),
        "source_authority": str(
            source.get("source_authority") or "1688"
        ),
    }
    source_identity_resolution = resolve_source_product_identity(
        **source_identity_inputs
    )
    source_identity_payload = source_identity_resolution.payload()
    source_identity_public = {
        "schema_version": source_identity_payload["schema_version"],
        "status": source_identity_payload["status"],
        "ready": source_identity_payload["ready"],
        "blockers": list(source_identity_payload["blockers"]),
        "identity_digest": (
            source_identity_payload["identity"].get("identity_digest")
            if isinstance(source_identity_payload.get("identity"), Mapping)
            else None
        ),
        "source_item_code": (
            source_identity_payload["identity"].get("source_item_code")
            if isinstance(source_identity_payload.get("identity"), Mapping)
            else None
        ),
    }
    lineage_context = (
        release_store.source_sku_lineage_context(
            source_offer_id=source_identity_resolution.identity.source_offer_id,
            source_authority=source_identity_resolution.identity.source_authority,
            source_identity_digest=(
                source_identity_resolution.identity.identity_digest
            ),
            exclude_product_id=clean_offer_id,
        )
        if source_identity_resolution.ready
        and source_identity_resolution.identity is not None
        else {"predecessor_records": [], "existing_reservations": []}
    )
    current_lineage_payload = (
        ((lineage_plan or {}).get("payload") or {}).get("sku_lineage")
        if isinstance((lineage_plan or {}).get("payload"), Mapping)
        else None
    )
    current_lineage_identity = (
        ((lineage_plan or {}).get("payload") or {}).get(
            "source_product_identity"
        )
        if isinstance((lineage_plan or {}).get("payload"), Mapping)
        else None
    )
    reused_lineage_payload: dict[str, Any] | None = None
    if (
        source_identity_resolution.ready
        and source_identity_resolution.identity is not None
        and isinstance(current_lineage_payload, Mapping)
        and isinstance(current_lineage_identity, Mapping)
        and current_lineage_identity.get("identity_digest")
        == source_identity_resolution.identity.identity_digest
    ):
        sku_lineage_resolution = None
        reused_lineage_payload = dict(current_lineage_payload)
    elif (
        source_identity_resolution.identity is not None
        and source_identity_resolution.ready
    ):
        sku_lineage_resolution = resolve_sku_lineage_reservation(
            source_identity=source_identity_resolution.identity,
            predecessor_records=lineage_context["predecessor_records"],
            existing_reservations=lineage_context[
                "existing_reservations"
            ],
        )
    else:
        sku_lineage_resolution = None
    sku_lineage_payload = (
        reused_lineage_payload
        or (
            sku_lineage_resolution.payload()
            if sku_lineage_resolution is not None
            else {
                "schema_version": "sku-lineage-reservation/v1",
                "status": "BLOCKED_SKU_LINEAGE",
                "ready": False,
                "lineage_mode": "BLOCKED",
                "blockers": list(source_identity_resolution.blockers),
            }
        )
    )
    if (
        sku_lineage_resolution is not None
        and sku_lineage_resolution.ready
        and sku_lineage_resolution.lineage_mode == "NEW_SOURCE"
    ):
        variant_keys = list(
            review.get("selected_sku_keys") or ("default",)
        )
        model_values = list(next_seller_skus[: len(variant_keys)])
        if len(model_values) < len(variant_keys):
            base = int(clean_seller_sku)
            model_values = [
                f"{base + index:04d}"
                for index in range(len(variant_keys))
            ]
        try:
            assignment = SkuAssignment(
                seller_sku=clean_seller_sku,
                model_skus=tuple(
                    ModelSkuAssignment(
                        variant_key=key,
                        model_sku=model_sku,
                    )
                    for key, model_sku in zip(
                        variant_keys,
                        model_values,
                    )
                ),
            )
        except (TypeError, ValueError) as error:
            sku_lineage_payload = {
                **sku_lineage_payload,
                "status": "BLOCKED_SKU_LINEAGE",
                "ready": False,
                "assignment": None,
                "reservation": None,
                "blockers": [
                    f"BLOCKED_SKU_LINEAGE: {error}",
                ],
            }
        else:
            finalized = finalize_new_source_sku_reservation(
                source_identity=source_identity_resolution.identity,
                assignment=assignment,
                existing_reservations=lineage_context[
                    "existing_reservations"
                ],
            )
            if finalized.ready and finalized.reservation is not None:
                sku_lineage_payload = {
                    **sku_lineage_payload,
                    "assignment": assignment.payload(),
                    "reservation": finalized.reservation.payload(),
                }
            else:
                sku_lineage_payload = {
                    **sku_lineage_payload,
                    "status": "BLOCKED_SKU_LINEAGE",
                    "ready": False,
                    "assignment": None,
                    "reservation": None,
                    "blockers": list(finalized.blockers),
                }
    sku_lineage_blockers = list(
        sku_lineage_payload.get("blockers") or ()
    )
    if (
        sku_lineage_resolution is not None
        and sku_lineage_resolution.ready
        and sku_lineage_resolution.lineage_mode == "INHERITED_PREDECESSOR"
        and sku_lineage_resolution.assignment is not None
    ):
        clean_seller_sku = sku_lineage_resolution.assignment.seller_sku
        seller_sku_source = "source_lineage_inheritance"
        lineage_owns_seller_sku = True
        approval_known_skus = tuple(
            value for value in known_skus if value != clean_seller_sku
        )
        displayed_sku_range = tuple(
            row.model_sku
            for row in sku_lineage_resolution.assignment.model_skus
        )
    suite_plan = (
        review_package.get("plan")
        if isinstance(review_package.get("plan"), Mapping)
        else {}
    )
    audits = _generation_audits(package_dir)
    handoff_state = dict(state)
    source_video = (
        source.get("video") if isinstance(source.get("video"), Mapping) else {}
    )
    if (
        str(review.get("video_action") or "").strip().casefold() == "keep"
        and not str(review.get("video_url") or "").strip()
        and str(source_video.get("url") or "").strip()
    ):
        handoff_review = dict(review)
        handoff_review["video_url"] = str(source_video["url"]).strip()
        handoff_state["review"] = handoff_review
    content_handoff = build_workbench_content_package_handoff(
        product_id=clean_offer_id,
        state=handoff_state,
        suite_plan=suite_plan,
        generation_audits=audits,
        copy=_content_copy(review, collect_box),
        package_id=f"content:{clean_offer_id}",
    )
    if not review_package_available and content_strategy != "source_only":
        missing_package_blocker = (
            "Image review package has not been prepared; review source images "
            "or open the AI image studio before content approval."
        )
        pending_approval = replace(
            content_handoff.content_package.approval,
            status="pending",
        )
        content_handoff = replace(
            content_handoff,
            content_package=replace(
                content_handoff.content_package,
                approval=pending_approval,
            ),
            blockers=tuple(
                dict.fromkeys(
                    [*content_handoff.blockers, missing_package_blocker]
                )
            ),
        )

    candidate_reservations = tuple(
        fact
        for fact in reservation_facts
        if _seller_sku_matches(clean_seller_sku, (fact["seller_sku"],))
    )
    product_row = {
        "product_id": clean_offer_id,
        "seller_sku": clean_seller_sku,
        "title": str(
            review.get("title")
            or collect_box.get("source_title")
            or source.get("title_source")
            or ""
        ).strip(),
        "sku_ids": list(review.get("selected_sku_keys") or ()),
        "platform": "orbit_release_rehearsal",
    }
    approved_source_reference = ""
    if (
        str(actual_approval.get("status") or "").strip().casefold() == "approved"
        and str(actual_approval.get("subject_id") or "").strip() == clean_offer_id
        and str(actual_approval.get("seller_sku") or "").strip() == clean_seller_sku
    ):
        approved_source_reference = str(
            actual_approval.get("source_reference") or ""
        ).strip()
    simulated_approval = {
        "approval_id": f"simulation:product:{clean_offer_id}:{clean_seller_sku}",
        "package_id": f"product:{clean_offer_id}:{clean_seller_sku}",
        "subject_type": "product",
        "subject_id": clean_offer_id,
        "status": "approved",
        "approved_by": "Kyle (release rehearsal only)",
        "approved_at": str(state.get("updated_at") or "2026-07-25T00:00:00+08:00"),
        # Operational state writes (for example Miaoshou readback evidence)
        # advance the workbench revision.  Once product facts are approved,
        # keep the publication identity pinned to that approval's immutable
        # source reference so execution state cannot invalidate ReleasePlan.
        "source_reference": (
            approved_source_reference
            or f"workbench:{clean_offer_id}:revision:{state.get('_revision', 0)}"
        ),
    }
    simulation_state = dict(state)
    simulation_state.pop("product_approval", None)
    effective_publication_targets = publication_targets
    if effective_publication_targets is None:
        active_release_plan = release_store.active_plan_for_product(
            clean_offer_id
        )
        if (
            active_release_plan
            and str(active_release_plan.get("status") or "") == "APPROVED"
            and str(active_release_plan.get("seller_sku") or "").strip()
            == clean_seller_sku
            and active_release_plan.get("targets")
        ):
            # A browser reload must reopen the exact approved scope. Falling
            # back to the workbench's older default sites would silently
            # replace a 12-target plan with a new 10-target preview and disable
            # safe retries.
            effective_publication_targets = list(
                active_release_plan["targets"]
            )
    omnichannel_selection, publication_scope = _publication_scope(
        review,
        effective_publication_targets,
    )
    product_facts = build_product_facts_snapshot(
        product_id=clean_offer_id,
        source=source,
        review=review,
    )
    commercial_rows = [
        fact.payload()
        for fact in product_facts.selected_sku_commercial_facts
    ]
    model_skus_by_variant = {
        str(row.get("variant_key") or ""): str(row.get("model_sku") or "")
        for row in (
            (sku_lineage_payload.get("assignment") or {}).get("model_skus")
            if isinstance(sku_lineage_payload.get("assignment"), Mapping)
            else ()
        )
        if isinstance(row, Mapping)
    }
    base_release_pricing = _release_pricing_review(
        review,
        sku_commercial_facts=commercial_rows,
        model_skus_by_variant=model_skus_by_variant,
    )
    scope_site_keys = _pricing_site_keys_for_scope(
        review,
        omnichannel_selection,
        base_release_pricing,
    )
    release_pricing = _release_pricing_review(
        review,
        selected_site_keys=scope_site_keys,
        sku_commercial_facts=commercial_rows,
        model_skus_by_variant=model_skus_by_variant,
    )
    _apply_store_level_pricing(release_pricing, omnichannel_selection)
    release_pricing["selection_source"] = publication_scope["source"]
    release_pricing["publication_target_labels"] = list(
        publication_scope["selected_labels"]
    )
    release_pricing["workbench_selected_store_prices"] = list(
        base_release_pricing["selected_store_prices"]
    )
    approval_preview = preview_product_approval_lock(
        state=simulation_state,
        product_row=product_row,
        content_package=content_handoff.content_package,
        seller_sku=clean_seller_sku,
        known_seller_skus=approval_known_skus,
        user_approved=True,
        approval_fact=simulated_approval,
        expected_revision=int(state.get("_revision") or 0),
        approval_input_facts=_commercial_approval_facts(review, base_release_pricing),
    )
    commercial_blockers = _commercial_release_blockers(review)
    approval_warnings = list(
        dict.fromkeys(
            [
                *_commercial_approval_warnings(review),
                *product_facts.warnings,
            ]
        )
    )
    seller_sku_blockers = (
        [
            "seller_sku is reserved by another workbench or verified TikTok claim"
        ]
        if candidate_reservations
        else []
    )
    approval_blockers = list(
        dict.fromkeys(
            [
                *approval_preview.blockers,
                *seller_sku_blockers,
                *sku_lineage_blockers,
                *commercial_blockers,
                *product_facts.blockers,
            ]
        )
    )
    approved_product_package = (
        approval_preview.approved_package
        if not commercial_blockers
        and not seller_sku_blockers
        and product_facts.ready
        else None
    )
    approval_state_patch = (
        dict(approval_preview.state_patch)
        if approved_product_package is not None
        else {}
    )
    publication_plan = (
        build_publication_plan(
            approved_product_package,
            content_handoff.content_package,
        )
        if approved_product_package is not None
        else None
    )
    listing_copy_state = (
        dict(state.get("listing_copy"))
        if isinstance(state.get("listing_copy"), Mapping)
        else {}
    )
    selected_target_labels = [
        f"{channel}:{site}"
        for channel, sites in omnichannel_selection.items()
        for site in sites
    ]
    listing_copy_identity, listing_copy_blockers = release_listing_copy_identity(
        listing_copy_state,
        approved_product_title=review.get("title"),
        current_input_signature=current_listing_copy_signature,
        target_labels=selected_target_labels,
    )
    omnichannel_preview = (
        _serialize_omnichannel_preview(
            build_omnichannel_publication_plan(
                approved_product_package,
                content_handoff.content_package,
                site_selection=omnichannel_selection,
                commercial_scope={
                    "pricing_schema": release_pricing["schema_version"],
                    "selected_store_prices": release_pricing["selected_store_prices"],
                    "workbench_exchange_rates": release_pricing[
                        "workbench_exchange_rates"
                    ],
                    "shopee_exchange_rates": release_pricing[
                        "shopee_exchange_rates"
                    ],
                    "ozon_exchange_rates": release_pricing["ozon_exchange_rates"],
                    "listing_copy": listing_copy_identity,
                    "derived_source_bindings": {
                        f"{channel}:{site}": {
                            "selected_source_target_key": (
                                release_pricing["target_pricing"]
                                .get(f"{channel}:{site}", {})
                                .get("selected_source_target_key")
                            ),
                            "source_policy": (
                                release_pricing["target_pricing"]
                                .get(f"{channel}:{site}", {})
                                .get("source_policy")
                            ),
                            "source_candidate_keys": [
                                row.get("target_key")
                                for row in (
                                    release_pricing["target_pricing"]
                                    .get(f"{channel}:{site}", {})
                                    .get("source_candidates")
                                    or ()
                                )
                            ],
                        }
                        for channel in ("shopee", "ozon")
                        for site in omnichannel_selection.get(channel, ())
                    },
                },
            ),
            pricing_by_target=release_pricing["target_pricing"],
        )
        if approved_product_package is not None
        else _blocked_omnichannel_preview(
            site_selection=omnichannel_selection,
            blockers=[
                *content_handoff.blockers,
                *approval_blockers,
            ]
            or ["Approved product and content packages are not ready."],
        )
    )

    content_approved = (
        content_handoff.content_package.approval is not None
        and content_handoff.content_package.approval.status == "approved"
    )
    simulated_ready = approved_product_package is not None
    channel_ready = bool(
        publication_plan
        and all(not draft.missing_conditions for draft in publication_plan.drafts)
    )
    expected_approval = (
        approval_state_patch.get("product_approval")
        if isinstance(approval_state_patch.get("product_approval"), Mapping)
        else {}
    )
    required_approval_matches = {
        "status": "approved",
        "subject_type": "product",
        "subject_id": clean_offer_id,
        "seller_sku": clean_seller_sku,
        "input_fingerprint": str(expected_approval.get("input_fingerprint") or ""),
    }
    fingerprint_matches = bool(actual_approval) and (
        str(actual_approval.get("input_fingerprint") or "")
        == required_approval_matches["input_fingerprint"]
        or _product_approval_facts_match(
            actual_approval.get("approval_input_facts"),
            expected_approval.get("approval_input_facts"),
        )
    )
    actual_product_approved = (
        simulated_ready
        and bool(required_approval_matches["input_fingerprint"])
        and bool(actual_approval)
        and all(
        str(actual_approval.get(key) or "") == expected
        for key, expected in required_approval_matches.items()
        if key != "input_fingerprint"
        )
        and fingerprint_matches
        and all(
        str(actual_approval.get(key) or "").strip()
        for key in ("approval_id", "package_id", "approved_by", "approved_at")
        )
    )
    current_image_urls = list(content_handoff.content_package.image_urls)
    source_thumbnail_url = next(
        (
            str(row.get("url") or "").strip()
            for row in (source.get("images") or ())
            if isinstance(row, Mapping)
            and str(row.get("url") or "").strip().startswith("https://")
        ),
        "",
    )
    thumbnail_url = (
        str(current_image_urls[0]).strip()
        if current_image_urls
        and str(current_image_urls[0]).strip().startswith("https://")
        else source_thumbnail_url
    )
    image_write_verified, written_image_urls = _verified_image_write(content_state)
    current_images_written = (
        image_write_verified
        and written_image_urls == current_image_urls
        and bool(current_image_urls)
    )
    actual_blockers: list[str] = []
    actual_blockers.extend(source_identity_resolution.blockers)
    actual_blockers.extend(sku_lineage_blockers)
    actual_blockers.extend(commercial_blockers)
    actual_blockers.extend(seller_sku_blockers)
    actual_blockers.extend(product_facts.blockers)
    if not actual_approval:
        actual_blockers.append("Product approval has not been persisted.")
    elif not actual_product_approved:
        actual_blockers.append(
            "Persisted product approval does not match the current product facts, Seller SKU, and input fingerprint."
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
    source_skus: list[dict[str, Any]] = []
    seen_source_sku_keys: set[str] = set()
    inherited_model_skus = {
        str(row.get("variant_key") or ""): str(row.get("model_sku") or "")
        for row in (
            (sku_lineage_payload.get("assignment") or {}).get("model_skus")
            if isinstance(sku_lineage_payload.get("assignment"), Mapping)
            else ()
        )
        if isinstance(row, Mapping)
    }
    commercial_by_key = {
        str(row.get("selected_key") or row.get("source_key") or ""): row
        for row in commercial_rows
    }
    sku_label_overrides = (
        review.get("sku_label_overrides")
        if isinstance(review.get("sku_label_overrides"), Mapping)
        else {}
    )
    from domains.product_operations.variant_display import (
        VariantDisplayError,
        normalize_publication_specification,
    )

    source_item_code = str(
        source.get("source_item_code") or source.get("itemNum") or ""
    ).strip()
    for row in source.get("skus") or ():
        if not isinstance(row, Mapping):
            continue
        key = str(row.get("key") or row.get("name") or "").strip()
        if not key or key in seen_source_sku_keys:
            continue
        seen_source_sku_keys.add(key)
        source_name = str(row.get("name") or key).strip() or key
        raw_name = str(sku_label_overrides.get(key) or source_name).strip()
        try:
            name = normalize_publication_specification(
                raw_name,
                internal_identifiers=(source_item_code,),
            )
            normalization_status = "READY"
        except VariantDisplayError:
            name = raw_name
            normalization_status = "USER_REVIEW_REQUIRED"
        source_sku = {
            "key": key,
            "label": name,
            "name": name,
            "source_label": source_name,
            "label_overridden": key in sku_label_overrides,
            "normalization_status": normalization_status,
            "price_cny": row.get("price"),
        }
        if inherited_model_skus.get(key):
            source_sku["model_sku"] = inherited_model_skus[key]
        if isinstance(commercial_by_key.get(key), Mapping):
            source_sku["commercial_facts"] = dict(commercial_by_key[key])
        source_skus.append(source_sku)
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
        "_source_product_identity": source_identity_payload,
        "_source_identity_inputs": source_identity_inputs,
        "_sku_lineage": sku_lineage_payload,
        "source_product_identity": source_identity_public,
        "sku_lineage": {
            "schema_version": sku_lineage_payload.get("schema_version"),
            "status": sku_lineage_payload.get("status"),
            "ready": sku_lineage_payload.get("ready"),
            "lineage_mode": sku_lineage_payload.get("lineage_mode"),
            "assignment": sku_lineage_payload.get("assignment"),
            "reservation_digest": (
                (sku_lineage_payload.get("reservation") or {}).get(
                    "reservation_digest"
                )
                if isinstance(
                    sku_lineage_payload.get("reservation"), Mapping
                )
                else None
            ),
            "blockers": list(sku_lineage_payload.get("blockers") or ()),
        },
        "product": {
            "offer_id": clean_offer_id,
            "source_item_code": str(
                source.get("source_item_code")
                or source.get("itemNum")
                or ""
            ).strip(),
            "seller_sku_candidate": clean_seller_sku,
            "title": product_row["title"],
            "source_title_zh": str(
                source.get("title_source")
                or collect_box.get("source_title")
                or ""
            ).strip(),
            "category": dict(review.get("category") or {}),
            "cost_cny": review.get("cost_cny"),
            "weight_kg": review.get("weight_kg"),
            "package_cm": list(review.get("package_cm") or ()),
            "sku_commercial_facts": {
                str(row.get("selected_key") or row.get("source_key") or ""): {
                    "cost_cny": row.get("cost_cny"),
                    "weight_kg": row.get("weight_kg"),
                    "package_cm": list(row.get("package_cm") or ()),
                }
                for row in commercial_rows
            },
            "selected_sites": list(review.get("selected_sites") or ()),
            "selected_sku_keys": list(review.get("selected_sku_keys") or ()),
            "sku_label_overrides": {
                str(key): str(value)
                for key, value in sku_label_overrides.items()
            },
            "source_skus": source_skus,
            "revision": int(state.get("_revision") or 0),
            "fields_locked": bool(review.get("fields_locked")),
            "facts_status": (
                "approved_locked"
                if actual_product_approved and bool(review.get("fields_locked"))
                else "awaiting_kyle_review"
            ),
            "facts_source": "miaoshou_precollect_plus_workbench_review",
            "fact_evidence": product_facts.payload(),
            "actual_product_approved": actual_product_approved,
            "actual_approval": dict(actual_approval),
            "seller_sku_governance": {
                "candidate": clean_seller_sku,
                "available": (
                    not candidate_reservations
                    and (
                        lineage_owns_seller_sku
                        or not _seller_sku_matches(
                            clean_seller_sku, known_skus
                        )
                    )
                ),
                "generated_by_system": seller_sku_source
                == "automatic_catalog_and_reservation_scan",
                "allocation_source": seller_sku_source,
                "reservation_conflicts": [
                    dict(fact) for fact in candidate_reservations
                ],
                "suggested_base_sku": (
                    displayed_sku_range[0] if displayed_sku_range else ""
                ),
                "suggested_sku_range": list(displayed_sku_range),
                "next_available_sku_range": list(next_seller_skus),
                "requested_sku_count": requested_sku_count,
                "catalog_sku_count": len(known_skus),
                "reservation_count": len(reserved_skus),
                "source": (
                    "tiktok_catalog_sequence_plus_all_catalog_occupancy_plus_"
                    "workbench_locks_plus_verified_claims_plus_release_reservations"
                ),
            },
            "thumbnail": {
                "url": thumbnail_url,
                "source": (
                    "approved_content_package"
                    if thumbnail_url
                    and thumbnail_url in current_image_urls
                    else ("source_preview" if thumbnail_url else "missing")
                ),
                "approved": bool(
                    thumbnail_url
                    and thumbnail_url in current_image_urls
                    and content_approved
                ),
            },
        },
        "listing_copy": {
            **listing_copy_state,
            "current_input_signature": current_listing_copy_signature,
            "release_blockers": listing_copy_blockers,
        },
        "pricing_review": release_pricing,
        "publication_scope": publication_scope,
        "content": {
            "package_id": content_handoff.content_package.package_id,
            "strategy": content_strategy,
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
            "video_urls": list(content_handoff.content_package.video_urls),
        },
        "approval_rehearsal": {
            "ready": simulated_ready,
            "blockers": approval_blockers,
            "warnings": approval_warnings,
            "state_patch_preview": approval_state_patch,
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
        "omnichannel_preview": omnichannel_preview,
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
