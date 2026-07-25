"""Pure, approval-gated publication planning for channel operations.

This module is deliberately independent from legacy channel adapters.  It
turns already-approved domain contracts into reviewable listing drafts, but
never reads a database, imports a channel client, or performs a network call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from domains.content_operations import ContentPackage
from domains.product_operations import ApprovedProductPackage
from shared_platform.contracts import ChannelListing


SUPPORTED_CHANNELS = ("tiktok", "shopee", "ozon", "miaoshou")


@dataclass(frozen=True)
class ChannelPublicationDraft:
    """A review-only draft and the conditions required before an adapter acts."""

    listing: ChannelListing
    action: str
    missing_conditions: tuple[str, ...]


@dataclass(frozen=True)
class ChannelPublicationPlan:
    """Deterministic output of :func:`build_publication_plan`.

    ``dry_run`` and ``approval_required`` are constants rather than caller
    options: this planning boundary cannot be used to publish by accident.
    """

    product_package_id: str
    content_package_id: str | None
    drafts: tuple[ChannelPublicationDraft, ...]
    dry_run: bool = True
    approval_required: bool = True


def build_publication_plan(
    product_package: ApprovedProductPackage,
    content_package: ContentPackage | None = None,
    *,
    channels: Iterable[str] = SUPPORTED_CHANNELS,
) -> ChannelPublicationPlan:
    """Build channel listing drafts without accessing adapters or persistence.

    Unsupported channels are rejected so that an unrecognised target cannot
    silently become a publish destination.  A supplied content package is
    checked for product identity and approval; omitting it is valid, but each
    draft calls out the content that a later channel adapter must provide.
    """
    requested_channels = _normalise_channels(channels)
    base_conditions = _product_conditions(product_package)
    content_conditions = _content_conditions(product_package, content_package)

    drafts = tuple(
        ChannelPublicationDraft(
            listing=ChannelListing(
                channel=channel,
                listing_id=_listing_id(channel, product_package.package_id),
                product_package_id=product_package.package_id,
                content_package_id=(content_package.package_id if content_package else None),
                status="draft",
            ),
            action="create_draft",
            missing_conditions=base_conditions + content_conditions,
        )
        for channel in requested_channels
    )
    return ChannelPublicationPlan(
        product_package_id=product_package.package_id,
        content_package_id=(content_package.package_id if content_package else None),
        drafts=drafts,
    )


def _normalise_channels(channels: Iterable[str]) -> tuple[str, ...]:
    normalised = tuple(str(channel).strip().lower() for channel in channels)
    if not normalised:
        raise ValueError("at least one publication channel is required")
    invalid = tuple(channel for channel in normalised if channel not in SUPPORTED_CHANNELS)
    if invalid:
        raise ValueError(f"unsupported publication channel(s): {', '.join(invalid)}")
    if len(set(normalised)) != len(normalised):
        raise ValueError("publication channels must not contain duplicates")
    return normalised


def _product_conditions(package: ApprovedProductPackage) -> tuple[str, ...]:
    missing: list[str] = []
    if package.approval.status.lower() != "approved":
        missing.append("product package approval is not approved")
    if not package.product.product_id:
        missing.append("product id is required")
    if not package.product.seller_sku:
        missing.append("seller SKU is required")
    if not package.product.title:
        missing.append("product title is required")
    return tuple(missing)


def _content_conditions(
    product_package: ApprovedProductPackage,
    content_package: ContentPackage | None,
) -> tuple[str, ...]:
    if content_package is None:
        return ("content package is required before channel submission",)

    missing: list[str] = []
    if content_package.product_id != product_package.product.product_id:
        missing.append("content package product id does not match product package")
    if content_package.approval and content_package.approval.status.lower() != "approved":
        missing.append("content package approval is not approved")
    if not content_package.copy:
        missing.append("channel-ready copy is required")
    if not content_package.image_urls:
        missing.append("at least one product image is required")
    return tuple(missing)


def _listing_id(channel: str, product_package_id: str) -> str:
    return f"draft:{channel}:{product_package_id}"
