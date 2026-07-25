"""Pure read adapter from the legacy image-review dictionaries to a hand-off.

The legacy workbench keeps suite planning, per-asset review decisions, and
generation audits separately.  This module intentionally only reads those
snapshots: it does not read a database, write a file, invoke generation, or
call a marketplace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from shared_platform.contracts import ApprovalRecord, ContentPackage


@dataclass(frozen=True)
class ContentAssetLineage:
    """Review evidence retained alongside one URL in a content hand-off."""

    shot_id: str
    artifact_id: str
    image_url: str
    audit_id: str


@dataclass(frozen=True)
class ContentPackageHandoff:
    """A stable package plus the source evidence needed to audit its images."""

    content_package: ContentPackage
    asset_lineage: tuple[ContentAssetLineage, ...]


def build_content_package_handoff(
    *,
    product_id: str,
    suite_plan: Mapping[str, Any],
    asset_decisions: Mapping[str, Any],
    generation_audits: Mapping[str, Any],
    copy: Mapping[str, str] | None = None,
    package_id: str | None = None,
) -> ContentPackageHandoff:
    """Build a ``ContentPackage`` using only explicitly approved assets.

    Asset order follows the selected suite order, never audit-file ordering.
    An asset needs an ``approved`` decision, a locally verified download, and
    an HTTPS URL.  The returned lineage links each accepted URL back to its
    artifact and generation audit without extending the shared contract.
    """
    clean_product_id = str(product_id or "").strip()
    if not clean_product_id:
        raise ValueError("product_id is required for a ContentPackage hand-off")

    suite = suite_plan.get("suite") if isinstance(suite_plan.get("suite"), Mapping) else {}
    items = suite.get("items") if isinstance(suite.get("items"), list) else []
    lineage: list[ContentAssetLineage] = []
    seen_urls: set[str] = set()

    for item in items:
        if not isinstance(item, Mapping) or not bool(item.get("selected", True)):
            continue
        shot_id = str(item.get("id") or "").strip()
        if not shot_id:
            continue
        approved = _approved_audit_for_shot(shot_id, asset_decisions, generation_audits)
        if approved is None or approved.image_url in seen_urls:
            continue
        seen_urls.add(approved.image_url)
        lineage.append(approved)

    resolved_package_id = str(package_id or "").strip() or f"content:{clean_product_id}"
    approval = ApprovalRecord(
        approval_id=f"content-review:{resolved_package_id}",
        subject_type="content_package",
        subject_id=resolved_package_id,
        status="approved" if lineage else "pending",
    )
    content_package = ContentPackage(
        package_id=resolved_package_id,
        product_id=clean_product_id,
        copy=dict(copy or {}),
        image_urls=tuple(row.image_url for row in lineage),
        approval=approval,
    )
    return ContentPackageHandoff(content_package=content_package, asset_lineage=tuple(lineage))


def _approved_audit_for_shot(
    shot_id: str,
    asset_decisions: Mapping[str, Any],
    generation_audits: Mapping[str, Any],
) -> ContentAssetLineage | None:
    """Select a single approved artifact for a shot deterministically."""
    candidates: list[ContentAssetLineage] = []
    for artifact_id, audit in generation_audits.items():
        if not isinstance(audit, Mapping):
            continue
        clean_artifact_id = str(artifact_id or "").strip()
        audit_shot_id = str(audit.get("shot_id") or clean_artifact_id.split("_", 1)[0]).strip()
        decision = asset_decisions.get(clean_artifact_id)
        if not isinstance(decision, Mapping) or decision.get("decision") != "approved":
            continue
        image_url = _audit_image_url(audit)
        if audit_shot_id != shot_id or not bool(audit.get("download_verified")) or not image_url:
            continue
        candidates.append(ContentAssetLineage(
            shot_id=shot_id,
            artifact_id=clean_artifact_id,
            image_url=image_url,
            audit_id=str(audit.get("audit_id") or f"generation_audit:{clean_artifact_id}"),
        ))
    return min(candidates, key=lambda row: row.artifact_id) if candidates else None


def _audit_image_url(audit: Mapping[str, Any]) -> str:
    data = ((audit.get("final_response") or {}).get("result") or {}).get("data") or []
    first = data[0] if isinstance(data, list) and data else {}
    url = str(first.get("url") or "") if isinstance(first, Mapping) else ""
    return url.strip() if url.strip().startswith("https://") else ""
