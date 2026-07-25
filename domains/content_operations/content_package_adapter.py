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
    missing_shot_ids: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    superseded_artifact_ids: tuple[str, ...] = ()
    stale_external_write: bool = False


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
    selected_shot_ids: list[str] = []
    missing_shot_ids: list[str] = []
    seen_urls: set[str] = set()

    for item in items:
        if not isinstance(item, Mapping) or not bool(item.get("selected", True)):
            continue
        shot_id = str(item.get("id") or "").strip()
        if not shot_id or shot_id in selected_shot_ids:
            continue
        selected_shot_ids.append(shot_id)
        approved = _approved_audit_for_shot(shot_id, asset_decisions, generation_audits)
        if approved is None or approved.image_url in seen_urls:
            missing_shot_ids.append(shot_id)
            continue
        seen_urls.add(approved.image_url)
        lineage.append(approved)

    resolved_package_id = str(package_id or "").strip() or f"content:{clean_product_id}"
    approval = ApprovalRecord(
        approval_id=f"content-review:{resolved_package_id}",
        subject_type="content_package",
        subject_id=resolved_package_id,
        status="approved" if selected_shot_ids and not missing_shot_ids else "pending",
    )
    content_package = ContentPackage(
        package_id=resolved_package_id,
        product_id=clean_product_id,
        copy=dict(copy or {}),
        image_urls=tuple(row.image_url for row in lineage),
        approval=approval,
    )
    return ContentPackageHandoff(
        content_package=content_package,
        asset_lineage=tuple(lineage),
        missing_shot_ids=tuple(missing_shot_ids),
    )


def build_workbench_content_package_handoff(
    *,
    product_id: str,
    state: Mapping[str, Any],
    suite_plan: Mapping[str, Any],
    generation_audits: Mapping[str, Any],
    copy: Mapping[str, str] | None = None,
    package_id: str | None = None,
) -> ContentPackageHandoff:
    """Adapt an already-loaded Treasury state without reading or writing it.

    Unlike the generic legacy adapter, this selects *one current version* per
    selected shot before considering its review.  A historical keep therefore
    cannot approve a newer size-card (or any other replacement) by accident.
    """
    clean_product_id = str(product_id or "").strip()
    if not clean_product_id:
        raise ValueError("product_id is required for a ContentPackage hand-off")
    content = state.get("content_package") if isinstance(state.get("content_package"), Mapping) else {}
    review = state.get("review") if isinstance(state.get("review"), Mapping) else {}
    suite = suite_plan.get("suite") if isinstance(suite_plan.get("suite"), Mapping) else {}
    selected_shots = [
        str(item.get("id") or "").strip()
        for item in (suite.get("items") or [])
        if isinstance(item, Mapping) and bool(item.get("selected", True)) and str(item.get("id") or "").strip()
    ]
    selected_shots = list(dict.fromkeys(selected_shots))
    current_by_shot = _current_audits_by_shot(content, selected_shots, generation_audits)
    decisions = content.get("asset_decisions") if isinstance(content.get("asset_decisions"), Mapping) else {}
    final_decisions = content.get("generated_image_miaoshou_decisions") if isinstance(content.get("generated_image_miaoshou_decisions"), Mapping) else {}
    lineage: list[ContentAssetLineage] = []
    missing: list[str] = []
    blockers: list[str] = []
    superseded: list[str] = []
    for shot_id in selected_shots:
        current = current_by_shot.get(shot_id)
        if current is None:
            missing.append(shot_id)
            blockers.append(f"{shot_id}: no current technically verified artifact")
            continue
        artifact_id, audit = current
        superseded.extend(
            candidate_id for candidate_id, candidate in generation_audits.items()
            if candidate_id != artifact_id and isinstance(candidate, Mapping)
            and str(candidate.get("shot_id") or str(candidate_id).split("_", 1)[0]) == shot_id
        )
        if not _has_final_content_approval(artifact_id, decisions, final_decisions):
            missing.append(shot_id)
            blockers.append(f"{shot_id}: current artifact {artifact_id} lacks final content approval")
            continue
        url = _audit_image_url(audit)
        lineage.append(ContentAssetLineage(
            shot_id=shot_id, artifact_id=artifact_id, image_url=url,
            audit_id=str(audit.get("audit_id") or f"generation_audit:{artifact_id}"),
        ))

    if not copy or not any(str(value).strip() for value in copy.values()):
        blockers.append("usable copy is required")
    subject = str(content.get("approval_subject_id") or content.get("product_id") or "").strip()
    if subject and subject != clean_product_id:
        blockers.append("content approval subject does not match product_id")
    if not all(bool(content.get(key)) for key in ("fact_card_approved", "planning_scope_approved", "suite_approved")):
        blockers.append("content fact-card, planning scope, and suite approvals are required")
    storyboard = content.get("storyboard_reviews") if isinstance(content.get("storyboard_reviews"), Mapping) else {}
    if any(str((storyboard.get(shot_id) or {}).get("decision") or "") != "approved" for shot_id in selected_shots):
        blockers.append("every selected storyboard shot requires approval")

    urls = tuple(row.image_url for row in lineage)
    current_urls = tuple(
        _audit_image_url(current[1]) for current in current_by_shot.values() if current is not None
    )
    written_urls = _written_image_urls(content)
    stale_external_write = bool(written_urls and any(url not in written_urls for url in current_urls))
    if stale_external_write:
        blockers.append("external Miaoshou image write is stale for the current artifact set")
    resolved_package_id = str(package_id or "").strip() or f"content:{clean_product_id}"
    approval = ApprovalRecord(
        approval_id=f"content-review:{resolved_package_id}", subject_type="content_package",
        subject_id=resolved_package_id,
        status="approved" if selected_shots and not missing and not [b for b in blockers if not b.startswith("external ")] else "pending",
    )
    return ContentPackageHandoff(
        content_package=ContentPackage(resolved_package_id, clean_product_id, dict(copy or {}), urls, approval=approval),
        asset_lineage=tuple(lineage), missing_shot_ids=tuple(missing), blockers=tuple(blockers),
        superseded_artifact_ids=tuple(sorted(set(superseded))), stale_external_write=stale_external_write,
    )


def _current_audits_by_shot(content, selected_shots, generation_audits):
    explicit = content.get("current_artifact_ids") if isinstance(content.get("current_artifact_ids"), Mapping) else {}
    overlay = content.get("dimension_overlay_upgrade") if isinstance(content.get("dimension_overlay_upgrade"), Mapping) else {}
    if overlay.get("artifact_id"):
        explicit = {**explicit, "sz1": overlay.get("artifact_id")}
    result = {}
    for shot_id in selected_shots:
        candidates = [(str(key), value) for key, value in generation_audits.items() if isinstance(value, Mapping)
                      and str(value.get("shot_id") or str(key).split("_", 1)[0]) == shot_id
                      and bool(value.get("download_verified")) and _audit_image_url(value)]
        wanted = str(explicit.get(shot_id) or "").strip()
        if wanted:
            result[shot_id] = next(((key, audit) for key, audit in candidates if key == wanted), None)
        elif candidates:
            result[shot_id] = max(candidates, key=lambda row: (_created_at_key(row[1]), _version_key(row[0]), row[0]))
    return result


def _created_at_key(audit):
    return str(audit.get("created_at") or "")


def _version_key(artifact_id):
    import re
    match = re.search(r"[_-](?:r|v)(\\d+)(?:[_-]|$)", artifact_id, re.I)
    return int(match.group(1)) if match else -1


def _has_final_content_approval(artifact_id, decisions, final_decisions):
    decision = decisions.get(artifact_id)
    if isinstance(decision, Mapping) and decision.get("decision") == "approved":
        return True
    final = final_decisions.get(artifact_id)
    return isinstance(final, Mapping) and final.get("action") == "keep" and final.get("status") == "reviewed_locally"


def _written_image_urls(content):
    write = content.get("miaoshou_ordered_images_write")
    if not isinstance(write, Mapping):
        write = content.get("miaoshou_generated_images_write")
    if not isinstance(write, Mapping) or not write.get("verified"):
        return set()
    return {str(url).strip() for url in (write.get("ordered_image_urls") or write.get("image_urls") or write.get("generated_image_urls") or []) if str(url).strip()}


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
