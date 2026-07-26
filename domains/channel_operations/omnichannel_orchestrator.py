"""Pure, single-approval orchestration for multi-channel publication.

This module deliberately stops at an *authorised execution plan*.  It never
imports a marketplace client, opens a database, writes a file, or calls a
network.  A later integration boundary may consume an authorised plan and
invoke adapters while persisting per-step results and idempotency records.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from shared_platform.contracts import ApprovedProductPackage, ContentPackage


CHANNEL_ORDER = ("miaoshou", "tiktok", "shopee", "ozon")

# These are the sites for which this repository currently has a recognisable
# real-write path.  "Recognisable" does not mean that the legacy adapter has
# already adopted this orchestrator's token contract; that distinction is
# called out by ``adapter_gate_status`` below.
AUDITED_ADAPTER_SITES: Mapping[str, tuple[str, ...]] = {
    "miaoshou": ("COMMON",),
    "tiktok": ("GB", "MX"),
    "shopee": ("MY", "PH", "TH", "VN"),
    "ozon": ("RU",),
}

ADAPTER_NAMES: Mapping[str, str] = {
    "miaoshou": "new_product_workbench_miaoshou_commit",
    "tiktok": "miaoshou_tiktok_publish",
    "shopee": "shopee_cnsc_publish",
    "ozon": "ozon_product_import",
}

ADAPTER_GATE_STATUS: Mapping[str, str] = {
    "miaoshou": "literal_confirmation_boolean",
    "tiktok": "persisted_confirmation_card",
    "shopee": "orchestrator_gate_required",
    "ozon": "orchestrator_gate_required",
}


class PublicationAuthorizationError(ValueError):
    """Raised when an execution request does not satisfy the approval gate."""


@dataclass(frozen=True)
class PublicationPreflight:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PublicationStep:
    sequence: int
    code: str
    label: str
    mutates_external_state: bool


@dataclass(frozen=True)
class ChannelExecutionPlan:
    channel: str
    site: str
    adapter: str
    adapter_gate_status: str
    depends_on: tuple[str, ...]
    preflight: tuple[PublicationPreflight, ...]
    steps: tuple[PublicationStep, ...]
    idempotency_key: str
    executable: bool


@dataclass(frozen=True)
class SingleApprovalSummary:
    collect_box_id: str | None
    product_id: str
    seller_sku: str
    product_package_id: str
    content_package_id: str
    target_labels: tuple[str, ...]
    image_count: int
    approval_scope_digest: str
    confirmation_token: str
    irreversible_action_count: int
    statement: str


@dataclass(frozen=True)
class OmnichannelPublicationPlan:
    """A deterministic preview or an authorised-but-unexecuted plan."""

    targets: tuple[ChannelExecutionPlan, ...]
    approval: SingleApprovalSummary
    plan_id: str
    dry_run: bool
    all_preflights_passed: bool
    execution_authorized: bool
    adapter_calls_performed: bool = False


def build_omnichannel_publication_plan(
    product_package: ApprovedProductPackage,
    content_package: ContentPackage,
    *,
    site_selection: Mapping[str, Iterable[str]],
    execute: bool = False,
    user_approved: bool = False,
    confirmation_token: str | None = None,
    commercial_scope: Mapping[str, object] | None = None,
) -> OmnichannelPublicationPlan:
    """Build one reviewable plan for every selected marketplace site.

    The default is a dry run.  Setting ``execute=True`` still performs no
    external action: it only returns a plan that an integration layer is
    permitted to consume.  Authorisation requires both a literal
    ``user_approved=True`` and the exact confirmation token emitted by the
    dry-run for the same immutable packages, image order, and target set.
    """

    selected = _normalise_site_selection(site_selection)
    collect_box_id = _collect_box_id(product_package.source_reference)
    common = _common_preflight(
        product_package,
        content_package,
        collect_box_id=collect_box_id,
    )
    scope_payload = _scope_payload(
        product_package,
        content_package,
        selected,
        commercial_scope=commercial_scope,
    )
    approval_digest = _sha256(scope_payload)
    expected_token = f"PUBLISH-{approval_digest[:16].upper()}"

    targets = tuple(
        _target_plan(
            channel=channel,
            site=site,
            common_preflight=common,
            approval_digest=approval_digest,
            selected_targets=frozenset(
                (selected_channel, selected_site)
                for selected_channel, selected_sites in selected
                for selected_site in selected_sites
            ),
        )
        for channel, sites in selected
        for site in sites
    )
    all_passed = all(target.executable for target in targets)
    target_labels = tuple(f"{target.channel}:{target.site}" for target in targets)
    approval = SingleApprovalSummary(
        collect_box_id=collect_box_id,
        product_id=product_package.product.product_id,
        seller_sku=product_package.product.seller_sku,
        product_package_id=product_package.package_id,
        content_package_id=content_package.package_id,
        target_labels=target_labels,
        image_count=len(content_package.image_urls),
        approval_scope_digest=approval_digest,
        confirmation_token=expected_token,
        irreversible_action_count=sum(
            step.mutates_external_state
            for target in targets
            for step in target.steps
        ),
        statement=(
            "Approve one exact product/content revision and the listed targets; "
            "partial retry must reuse each target idempotency key."
        ),
    )

    if execute:
        if user_approved is not True:
            raise PublicationAuthorizationError(
                "execute requires literal user_approved=True"
            )
        if not confirmation_token:
            raise PublicationAuthorizationError(
                "execute requires the dry-run confirmation token"
            )
        if confirmation_token != expected_token:
            raise PublicationAuthorizationError(
                "confirmation token does not match this publication scope"
            )
        if not all_passed:
            blockers = sorted(
                {
                    check.detail
                    for target in targets
                    for check in target.preflight
                    if not check.passed
                }
            )
            raise PublicationAuthorizationError(
                "publication preflight failed: " + "; ".join(blockers)
            )

    return OmnichannelPublicationPlan(
        targets=targets,
        approval=approval,
        plan_id=f"omnichannel:{approval_digest}",
        dry_run=not execute,
        all_preflights_passed=all_passed,
        execution_authorized=bool(execute),
    )


def _normalise_site_selection(
    site_selection: Mapping[str, Iterable[str]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(site_selection, Mapping):
        raise TypeError("site_selection must be a mapping of channel to sites")

    unknown = sorted(
        str(channel).strip().lower()
        for channel in site_selection
        if str(channel).strip().lower() not in CHANNEL_ORDER
    )
    if unknown:
        raise ValueError(f"unsupported publication channel(s): {', '.join(unknown)}")

    selected: list[tuple[str, tuple[str, ...]]] = []
    for channel in CHANNEL_ORDER:
        raw_sites = next(
            (
                sites
                for key, sites in site_selection.items()
                if str(key).strip().lower() == channel
            ),
            None,
        )
        if raw_sites is None:
            continue
        if isinstance(raw_sites, str):
            raw_sites = (raw_sites,)
        sites = tuple(str(site).strip().upper() for site in raw_sites)
        if not sites or any(not site for site in sites):
            raise ValueError(f"{channel} requires at least one non-empty site")
        if len(set(sites)) != len(sites):
            raise ValueError(f"{channel} sites must not contain duplicates")
        selected.append((channel, tuple(sorted(sites))))

    if not selected:
        raise ValueError("at least one publication target is required")
    return tuple(selected)


def _common_preflight(
    product_package: ApprovedProductPackage,
    content_package: ContentPackage,
    *,
    collect_box_id: str | None,
) -> tuple[PublicationPreflight, ...]:
    product = product_package.product
    product_approval = product_package.approval
    content_approval = content_package.approval
    image_urls = tuple(content_package.image_urls)
    return (
        PublicationPreflight(
            "product_approval",
            (
                product_approval.status.casefold() == "approved"
                and product_approval.subject_type.casefold() == "product"
                and product_approval.subject_id == product.product_id
            ),
            "product package has a matching approved product decision",
        ),
        PublicationPreflight(
            "content_approval",
            bool(
                content_approval
                and content_approval.status.casefold() == "approved"
                and content_approval.subject_type.casefold() == "content_package"
                and content_approval.subject_id == content_package.package_id
            ),
            "content package has a matching approved content decision",
        ),
        PublicationPreflight(
            "product_content_identity",
            bool(
                product.product_id
                and content_package.product_id == product.product_id
            ),
            "product and content package identities match",
        ),
        PublicationPreflight(
            "commercial_identity",
            bool(product.seller_sku and product.title),
            "seller SKU and title are present",
        ),
        PublicationPreflight(
            "channel_copy",
            bool(content_package.copy),
            "approved channel copy is present",
        ),
        PublicationPreflight(
            "ordered_https_images",
            bool(
                image_urls
                and len(set(image_urls)) == len(image_urls)
                and all(url.startswith("https://") for url in image_urls)
            ),
            "approved images are unique, ordered, and use HTTPS",
        ),
        PublicationPreflight(
            "collect_box_lineage",
            collect_box_id is not None,
            "Miaoshou collect-box identity is present in source_reference",
        ),
    )


def _target_plan(
    *,
    channel: str,
    site: str,
    common_preflight: tuple[PublicationPreflight, ...],
    approval_digest: str,
    selected_targets: frozenset[tuple[str, str]],
) -> ChannelExecutionPlan:
    adapter_site = _tiktok_country(site) if channel == "tiktok" else site
    site_supported = adapter_site in AUDITED_ADAPTER_SITES[channel]
    selected_channels = frozenset(
        selected_channel for selected_channel, _site in selected_targets
    )
    dependency_ready = (
        True
        if channel == "miaoshou"
        else "miaoshou" in selected_channels
        if channel == "tiktok"
        else any(
            selected_channel == "tiktok"
            and (
                channel == "ozon"
                or _tiktok_country(selected_site) == site
            )
            for selected_channel, selected_site in selected_targets
        )
    )
    preflight = common_preflight + (
        PublicationPreflight(
            "audited_adapter_site",
            site_supported,
            (
                f"{channel}:{site} has an audited repository adapter path"
                if site_supported
                else f"{channel}:{site} has no audited repository adapter path"
            ),
        ),
        PublicationPreflight(
            "upstream_target_selected",
            dependency_ready,
            (
                f"{channel}:{site} has its required upstream target selected"
                if dependency_ready
                else (
                    f"{channel}:{site} requires a TikTok master target"
                    if channel in {"shopee", "ozon"}
                    else f"{channel}:{site} requires the Miaoshou common draft target"
                )
            ),
        ),
    )
    target_digest = _sha256(
        {
            "approval_scope_digest": approval_digest,
            "channel": channel,
            "site": site,
            "adapter": ADAPTER_NAMES[channel],
        }
    )
    return ChannelExecutionPlan(
        channel=channel,
        site=site,
        adapter=ADAPTER_NAMES[channel],
        adapter_gate_status=ADAPTER_GATE_STATUS[channel],
        depends_on=_dependencies_for_target(
            channel,
            site=site,
            selected_targets=selected_targets,
        ),
        preflight=preflight,
        steps=_steps_for(channel, site),
        idempotency_key=f"publish:{channel}:{site}:{target_digest}",
        executable=all(check.passed for check in preflight),
    )


def _tiktok_country(site: str) -> str:
    """Return the country component for both legacy and store-level sites."""

    normalised = str(site or "").strip().upper()
    prefix, separator, country = normalised.partition("_")
    if separator and prefix in {"LH", "HB"}:
        return country
    return normalised


def _dependencies_for_target(
    channel: str,
    *,
    site: str,
    selected_targets: frozenset[tuple[str, str]],
) -> tuple[str, ...]:
    """Describe the intended product-data lineage without executing it."""

    if channel == "miaoshou":
        return ()
    if channel == "tiktok":
        return ("miaoshou:COMMON:verified_draft",)
    candidates = [
        selected_site
        for selected_channel, selected_site in selected_targets
        if selected_channel == "tiktok"
        and (channel == "ozon" or _tiktok_country(selected_site) == site)
    ]
    if not candidates:
        return ("tiktok:MASTER:verified_readback",)
    source = min(candidates, key=_tiktok_source_priority)
    return (f"tiktok:{source}:verified_readback",)


def _tiktok_source_priority(site: str) -> tuple[int, int, str]:
    country_order = {"PH": 0, "MY": 1, "TH": 2, "VN": 3, "MX": 4, "GB": 5}
    normalised = str(site or "").strip().upper()
    country = _tiktok_country(normalised)
    shop_rank = (
        0
        if normalised.startswith("LH_")
        else 1
        if normalised.startswith("HB_")
        else 0
    )
    return (country_order.get(country, 99), shop_rank, normalised)


def _steps_for(channel: str, site: str) -> tuple[PublicationStep, ...]:
    labels: Mapping[str, tuple[tuple[str, str, bool], ...]] = {
        "miaoshou": (
            ("write_draft", "Write the approved product draft to the collect box", True),
            ("write_images", "Write the exact approved image order", True),
            ("verify_draft", "Read back and verify draft fields and images", False),
        ),
        "tiktok": (
            ("claim_site", f"Claim collect-box item for TikTok {site}", True),
            ("save_site_draft", "Save site price, SKU, package and content", True),
            ("verify_site_draft", "Read back the site draft before submission", False),
            ("submit_publish", f"Submit the TikTok {site} publish task", True),
            ("verify_listing", "Poll and record the platform listing result", False),
        ),
        "shopee": (
            ("prepare_cnsc", "Prepare the approved CNSC global payload", False),
            ("upsert_global", "Create or reuse the Shopee global product", True),
            ("publish_site", f"Publish the global product to Shopee {site}", True),
            ("verify_listing", "Poll and record item/model identifiers", False),
        ),
        "ozon": (
            ("prepare_ru_draft", "Prepare Russian copy and the Ozon attribute payload", False),
            ("prepare_images", "Prepare channel-compliant image assets", True),
            ("import_product", "Submit the Ozon product import", True),
            ("verify_listing", "Poll import and verify product/rich-content status", False),
        ),
    }
    return tuple(
        PublicationStep(index, code, label, mutates)
        for index, (code, label, mutates) in enumerate(labels[channel], start=1)
    )


def _scope_payload(
    product_package: ApprovedProductPackage,
    content_package: ContentPackage,
    selected: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    commercial_scope: Mapping[str, object] | None = None,
) -> dict[str, object]:
    product = product_package.product
    return {
        "schema": "omnichannel-publication/v1",
        "product_package_id": product_package.package_id,
        "product_approval_id": product_package.approval.approval_id,
        "product_id": product.product_id,
        "seller_sku": product.seller_sku,
        "title": product.title,
        "sku_ids": list(product.sku_ids),
        "attributes": sorted(
            (str(key), str(value)) for key, value in product.attributes.items()
        ),
        "source_reference": product_package.source_reference,
        "content_package_id": content_package.package_id,
        "content_approval_id": (
            content_package.approval.approval_id
            if content_package.approval
            else None
        ),
        "copy": sorted(
            (str(key), str(value)) for key, value in content_package.copy.items()
        ),
        "image_urls": list(content_package.image_urls),
        "video_urls": list(content_package.video_urls),
        "targets": [
            {"channel": channel, "sites": list(sites)}
            for channel, sites in selected
        ],
        "commercial_scope": dict(commercial_scope or {}),
    }


def _collect_box_id(source_reference: str | None) -> str | None:
    if not source_reference:
        return None
    matched = re.search(r"(?<!\d)(\d{6,})(?!\d)", str(source_reference))
    return matched.group(1) if matched else None


def _sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
