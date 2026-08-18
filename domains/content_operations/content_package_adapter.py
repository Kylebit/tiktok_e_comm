"""Pure read adapter from the legacy image-review dictionaries to a hand-off.

The legacy workbench keeps suite planning, per-asset review decisions, and
generation audits separately.  This module intentionally only reads those
snapshots: it does not read a database, write a file, invoke generation, or
call a marketplace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

from shared_platform.contracts import ApprovalRecord, ContentPackage

EXPERIENCE_RECIPE_REVIEW_MODE = "experience_recipe_auto_v1"
SOURCE_ONLY_FINAL_APPROVAL_SCHEMA = "source-only-final-content-approval/v1"


def source_only_review_signature(
    image_actions: list[Mapping[str, Any]], image_order: list[str]
) -> str:
    """Bind one exact source-image decision set and its approved order."""

    payload = {
        "image_actions": [
            {
                "url": str(row.get("url") or ""),
                "action": str(row.get("action") or ""),
                "note": str(row.get("note") or ""),
            }
            for row in image_actions
        ],
        "image_order": list(image_order),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _source_only_video_identity_digest(video_url: str) -> str:
    digest = hashlib.sha256(str(video_url or "").strip().encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def source_only_final_approval_digest(
    *,
    review_signature: str,
    video_action: str,
    video_url: str,
    approved_by: str = "Kyle",
) -> str:
    """Return the immutable identity of a source-only final approval."""

    payload = {
        "schema_version": SOURCE_ONLY_FINAL_APPROVAL_SCHEMA,
        "status": "approved",
        "approved_by": str(approved_by or "").strip(),
        "source_only_review_signature": str(review_signature or "").strip(),
        "video_action": str(video_action or "").strip(),
        "video_identity_digest": _source_only_video_identity_digest(video_url),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def source_only_final_approval_valid(
    content: Mapping[str, Any], review: Mapping[str, Any]
) -> bool:
    """Validate that final approval still binds the current source-only draft."""

    if str(content.get("content_strategy") or "") != "source_only":
        return False
    if content.get("fact_card_approved") is not True:
        return False
    if content.get("planning_scope_approved") is not True:
        return False
    actions = review.get("image_actions")
    order = review.get("image_order")
    if not isinstance(actions, list) or not actions:
        return False
    if not all(isinstance(row, Mapping) for row in actions):
        return False
    if not isinstance(order, list) or not all(isinstance(url, str) for url in order):
        return False
    decision_urls = [
        str(row.get("url") or row.get("output_url") or "").strip()
        for row in actions
    ]
    kept_urls = [
        str(row.get("url") or row.get("output_url") or "").strip()
        for row in actions
        if str(row.get("action") or "") == "keep"
    ]
    if (
        not kept_urls
        or any(not url for url in decision_urls)
        or len(decision_urls) != len(set(decision_urls))
        or any(str(row.get("action") or "") not in {"keep", "remove"} for row in actions)
        or any(not url.startswith("https://") for url in kept_urls)
        or len(kept_urls) != len(set(kept_urls))
        or len(order) != len(set(order))
        or set(order) != set(kept_urls)
    ):
        return False
    current_signature = source_only_review_signature(actions, order)
    if str(content.get("source_only_review_signature") or "") != current_signature:
        return False
    approval = content.get("source_only_final_approval")
    if not isinstance(approval, Mapping):
        return False
    video_url = str(review.get("video_url") or "").strip()
    video_action = str(review.get("video_action") or "").strip()
    if video_url:
        if video_action not in {"keep", "remove"}:
            return False
    elif video_action not in {"none", "remove"}:
        return False
    expected_digest = source_only_final_approval_digest(
        review_signature=current_signature,
        video_action=video_action,
        video_url=video_url,
        approved_by="Kyle",
    )
    return bool(
        approval.get("schema_version") == SOURCE_ONLY_FINAL_APPROVAL_SCHEMA
        and approval.get("status") == "approved"
        and approval.get("approved_by") == "Kyle"
        and approval.get("source_only_review_signature") == current_signature
        and approval.get("video_action") == video_action
        and approval.get("video_identity_digest")
        == _source_only_video_identity_digest(video_url)
        and approval.get("approval_digest") == expected_digest
    )


@dataclass(frozen=True)
class ContentAssetLineage:
    """Review evidence retained alongside one URL in a content hand-off."""

    shot_id: str
    artifact_id: str
    image_url: str
    audit_id: str
    asset_type: str = "generated"
    decision_source: str = "asset_decisions"


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
    video_action: str = "none",
    video_url: str = "",
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

    video_urls, video_blockers = _approved_video_urls(video_action, video_url)
    resolved_package_id = str(package_id or "").strip() or f"content:{clean_product_id}"
    approval = ApprovalRecord(
        approval_id=f"content-review:{resolved_package_id}",
        subject_type="content_package",
        subject_id=resolved_package_id,
        status=(
            "approved"
            if selected_shot_ids and not missing_shot_ids and not video_blockers
            else "pending"
        ),
    )
    content_package = ContentPackage(
        package_id=resolved_package_id,
        product_id=clean_product_id,
        copy=dict(copy or {}),
        image_urls=tuple(row.image_url for row in lineage),
        video_urls=video_urls,
        approval=approval,
    )
    return ContentPackageHandoff(
        content_package=content_package,
        asset_lineage=tuple(lineage),
        missing_shot_ids=tuple(missing_shot_ids),
        blockers=video_blockers,
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
    strategy = str(content.get("content_strategy") or "ai_assisted").strip()
    if strategy not in {"source_only", "ai_assisted"}:
        strategy = "ai_assisted"
    suite = suite_plan.get("suite") if isinstance(suite_plan.get("suite"), Mapping) else {}
    selected_shots = [] if strategy == "source_only" else [
        str(item.get("id") or "").strip()
        for item in (suite.get("items") or [])
        if isinstance(item, Mapping) and bool(item.get("selected", True)) and str(item.get("id") or "").strip()
    ]
    selected_shots = list(dict.fromkeys(selected_shots))
    final_ai_urls = _approved_ai_final_image_urls(review, content)
    current_by_shot = _current_audits_by_shot(content, selected_shots, generation_audits)
    decisions = content.get("asset_decisions") if isinstance(content.get("asset_decisions"), Mapping) else {}
    final_decisions = content.get("generated_image_miaoshou_decisions") if isinstance(content.get("generated_image_miaoshou_decisions"), Mapping) else {}
    generated_lineage: list[ContentAssetLineage] = []
    missing: list[str] = []
    blockers: list[str] = []
    superseded: list[str] = []
    if final_ai_urls is not None:
        generated_lineage = [
            ContentAssetLineage(
                shot_id=f"final-{index}",
                artifact_id=f"miaoshou-final:{index}",
                image_url=url,
                audit_id="miaoshou_ordered_images_write",
                decision_source="final_content_approval.verified_miaoshou_ordered_images",
            )
            for index, url in enumerate(final_ai_urls, start=1)
        ]
    else:
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
            decision_source = _final_content_approval_source(artifact_id, decisions, final_decisions)
            if decision_source is None:
                if _has_explicit_final_rejection(
                    artifact_id, decisions, final_decisions
                ):
                    continue
                missing.append(shot_id)
                blockers.append(f"{shot_id}: current artifact {artifact_id} lacks final content approval")
                continue
            url = _audit_image_url(audit)
            generated_lineage.append(ContentAssetLineage(
                shot_id=shot_id, artifact_id=artifact_id, image_url=url,
                audit_id=str(audit.get("audit_id") or f"generation_audit:{artifact_id}"),
                decision_source=decision_source,
            ))

    source_lineage, source_blockers = _approved_source_lineage(review)
    blockers.extend(source_blockers)

    if not copy or not any(str(value).strip() for value in copy.values()):
        blockers.append("usable copy is required")
    subject = str(content.get("approval_subject_id") or content.get("product_id") or "").strip()
    if subject and subject != clean_product_id:
        blockers.append("content approval subject does not match product_id")
    if strategy == "source_only":
        if not source_only_final_approval_valid(content, review):
            blockers.append(
                "source-only final content approval is missing or stale"
            )
        lineage, order_blockers = _source_only_order_lineage(
            source_lineage, review.get("image_order")
        )
    else:
        if not all(
            bool(content.get(key))
            for key in (
                "fact_card_approved",
                "planning_scope_approved",
                "suite_approved",
            )
        ):
            blockers.append(
                "content fact-card and a current adopted storyboard recipe are required"
            )
        if (
            final_ai_urls is None
            and
            str(content.get("planning_review_mode") or "")
            != EXPERIENCE_RECIPE_REVIEW_MODE
        ):
            storyboard = (
                content.get("storyboard_reviews")
                if isinstance(content.get("storyboard_reviews"), Mapping)
                else {}
            )
            if any(
                str((storyboard.get(shot_id) or {}).get("decision") or "")
                != "approved"
                for shot_id in selected_shots
            ):
                blockers.append("every selected storyboard shot requires approval")
        if final_ai_urls is not None:
            lineage, order_blockers = generated_lineage, []
        else:
            lineage, order_blockers = _order_lineage(
                source_lineage + generated_lineage, review.get("image_order")
            )
    blockers.extend(order_blockers)
    urls = tuple(row.image_url for row in lineage)
    video_action = str(
        review.get("video_action") or content.get("video_action") or "none"
    ).strip()
    video_url = str(
        review.get("video_url")
        or content.get("approved_video_url")
        or content.get("video_url")
        or content.get("source_video_url")
        or ""
    ).strip()
    video_urls, video_blockers = _approved_video_urls(video_action, video_url)
    blockers.extend(video_blockers)
    written_urls = _written_image_urls(content)
    stale_external_write = bool(written_urls and written_urls != set(urls))
    if stale_external_write:
        blockers.append("external Miaoshou image write is stale for the current artifact set")
    resolved_package_id = str(package_id or "").strip() or f"content:{clean_product_id}"
    approval = ApprovalRecord(
        approval_id=f"content-review:{resolved_package_id}", subject_type="content_package",
        subject_id=resolved_package_id,
        status=(
            "approved"
            if (
                (bool(lineage) if strategy == "source_only" else bool(selected_shots))
                and not missing
                and not [b for b in blockers if not b.startswith("external ")]
            )
            else "pending"
        ),
    )
    return ContentPackageHandoff(
        content_package=ContentPackage(
            package_id=resolved_package_id,
            product_id=clean_product_id,
            copy=dict(copy or {}),
            image_urls=urls,
            video_urls=video_urls,
            approval=approval,
        ),
        asset_lineage=tuple(lineage), missing_shot_ids=tuple(missing), blockers=tuple(blockers),
        superseded_artifact_ids=tuple(sorted(set(superseded))), stale_external_write=stale_external_write,
    )


def _approved_video_urls(
    video_action: Any,
    video_url: Any,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return only an explicitly kept HTTPS video, or an explicit no-video set."""

    action = str(video_action or "none").strip().lower()
    url = str(video_url or "").strip()
    if action == "keep":
        if not url.startswith("https://"):
            return (), ("video_action=keep requires an approved HTTPS video URL",)
        return (url,), ()
    if action in {"none", "remove"}:
        return (), ()
    return (), (
        "video requires an explicit keep, remove, or none decision",
    )


def _current_audits_by_shot(content, selected_shots, generation_audits):
    explicit = content.get("current_artifact_ids") if isinstance(content.get("current_artifact_ids"), Mapping) else {}
    overlay = content.get("dimension_overlay_upgrade") if isinstance(content.get("dimension_overlay_upgrade"), Mapping) else {}
    overlay_artifact_id = str(overlay.get("artifact_id") or "").strip()
    overlay_audit = generation_audits.get(overlay_artifact_id)
    overlay_shot_id = str(
        (overlay_audit.get("shot_id") if isinstance(overlay_audit, Mapping)
        else overlay.get("shot_id")) or ""
    ).strip()
    if overlay_artifact_id and overlay_shot_id in selected_shots:
        explicit = {**explicit, overlay_shot_id: overlay_artifact_id}
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
    raw = str(audit.get("created_at") or "").strip()
    if not raw:
        return (0, 0.0, "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (1, parsed.timestamp(), raw)
    except ValueError:
        return (0, 0.0, raw)


def _version_key(artifact_id):
    import re
    match = re.search(r"[_-](?:r|v)(\\d+)(?:[_-]|$)", artifact_id, re.I)
    return int(match.group(1)) if match else -1


def _has_final_content_approval(artifact_id, decisions, final_decisions):
    return _final_content_approval_source(artifact_id, decisions, final_decisions) is not None


def _final_content_approval_source(artifact_id, decisions, final_decisions):
    decision = decisions.get(artifact_id)
    if isinstance(decision, Mapping) and decision.get("decision") == "approved":
        return "asset_decisions.approved"
    final = final_decisions.get(artifact_id)
    if isinstance(final, Mapping) and final.get("action") == "keep" and final.get("status") == "reviewed_locally":
        return "generated_image_miaoshou_decisions.keep_reviewed_locally"
    return None


def _has_explicit_final_rejection(artifact_id, decisions, final_decisions):
    """Treat a reviewed reject+remove pair as an intentional suite exclusion."""
    decision = decisions.get(artifact_id)
    final = final_decisions.get(artifact_id)
    return bool(
        isinstance(decision, Mapping)
        and decision.get("decision") == "rejected"
        and isinstance(final, Mapping)
        and final.get("action") == "remove"
        and final.get("status") == "reviewed_locally"
    )


def _approved_source_lineage(review: Mapping[str, Any]) -> tuple[list[ContentAssetLineage], list[str]]:
    """Read final source-image decisions without treating them as generation audits."""
    rows = review.get("image_actions") if isinstance(review.get("image_actions"), list) else []
    lineage: list[ContentAssetLineage] = []
    blockers: list[str] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            continue
        action = str(row.get("action") or "review")
        url = str(row.get("output_url") or row.get("url") or "").strip()
        if action == "keep":
            if not url.startswith("https://"):
                blockers.append(f"source image {index} is keep but has no HTTPS URL")
                continue
            lineage.append(ContentAssetLineage(
                shot_id="", artifact_id=f"source:{index}", image_url=url,
                audit_id=f"review.image_actions[{index - 1}]", asset_type="source",
                decision_source="review.image_actions.keep",
            ))
        elif action != "remove":
            blockers.append(f"source image {index} still requires an explicit keep or remove decision")
    return lineage, blockers


def _order_lineage(
    lineage: list[ContentAssetLineage], image_order: Any
) -> tuple[list[ContentAssetLineage], list[str]]:
    """Apply saved final order, then append approved assets in stable source/shot order."""
    by_url: dict[str, ContentAssetLineage] = {}
    for row in lineage:
        by_url.setdefault(row.image_url, row)
    ordered: list[ContentAssetLineage] = []
    seen_urls: set[str] = set()
    blockers: list[str] = []
    if isinstance(image_order, list):
        for raw_url in image_order:
            url = str(raw_url or "").strip()
            if not url:
                continue
            row = by_url.get(url)
            if row is None:
                blockers.append(f"review.image_order contains an unapproved or unknown URL: {url}")
            elif url not in seen_urls:
                ordered.append(row)
                seen_urls.add(url)
    for row in lineage:
        if row.image_url not in seen_urls:
            ordered.append(row)
            seen_urls.add(row.image_url)
    return ordered, blockers


def _source_only_order_lineage(
    lineage: list[ContentAssetLineage], image_order: Any
) -> tuple[list[ContentAssetLineage], list[str]]:
    """Require one exact saved order containing only approved source images."""
    by_url: dict[str, ContentAssetLineage] = {}
    for row in lineage:
        by_url.setdefault(row.image_url, row)
    blockers: list[str] = []
    if not by_url:
        blockers.append("source_only requires at least one approved HTTPS source image")
    if not isinstance(image_order, list) or not [
        value for value in image_order if str(value or "").strip()
    ]:
        blockers.append("source_only requires an explicit final image_order")
        return [], blockers

    ordered: list[ContentAssetLineage] = []
    seen_urls: set[str] = set()
    for raw_url in image_order:
        url = str(raw_url or "").strip()
        if not url:
            continue
        if url in seen_urls:
            blockers.append(f"review.image_order contains a duplicate URL: {url}")
            continue
        seen_urls.add(url)
        row = by_url.get(url)
        if row is None:
            blockers.append(
                f"review.image_order contains a non-source or unapproved URL: {url}"
            )
            continue
        ordered.append(row)
    missing_urls = [url for url in by_url if url not in seen_urls]
    if missing_urls:
        blockers.append(
            "every kept source image must appear exactly once in review.image_order"
        )
    return ordered, blockers


def _written_image_urls(content):
    write = content.get("miaoshou_ordered_images_write")
    if not isinstance(write, Mapping):
        write = content.get("miaoshou_generated_images_write")
    if not isinstance(write, Mapping) or not (write.get("verified") or write.get("status") == "verified"):
        return set()
    return {str(url).strip() for url in (write.get("ordered_image_urls") or write.get("image_urls") or write.get("generated_image_urls") or []) if str(url).strip()}


def _approved_ai_final_image_urls(
    review: Mapping[str, Any], content: Mapping[str, Any]
) -> list[str] | None:
    """Return the final Miaoshou authority set only when its approval is exact.

    The completed final approval intentionally supersedes pending model candidates.
    It remains fail-closed when the recorded final image write, review order, suite
    identity, or signed approval no longer agree.
    """
    approval = content.get("final_content_approval")
    write = content.get("miaoshou_ordered_images_write")
    if not (
        str(content.get("content_strategy") or "ai_assisted") == "ai_assisted"
        and content.get("suite_approved") is True
        and isinstance(approval, Mapping)
        and approval.get("schema_version") == "ai-assisted-final-content-approval/v1"
        and approval.get("status") == "approved"
        and approval.get("approved_by") == "Kyle"
        and str(approval.get("approved_at") or "").strip()
        and isinstance(write, Mapping)
        and str(write.get("status") or "") == "verified"
    ):
        return None
    checks = write.get("checks") if isinstance(write.get("checks"), Mapping) else {}
    urls = [
        str(url).strip()
        for url in (write.get("ordered_image_urls") or [])
        if str(url).strip()
    ]
    review_urls = [
        str(url).strip()
        for url in (review.get("image_order") or [])
        if str(url).strip()
    ]
    if not (
        urls
        and len(urls) == len(set(urls))
        and urls == review_urls
        and urls == list(approval.get("image_order") or [])
        and urls == list(approval.get("miaoshou_ordered_image_urls") or [])
        and len(urls) == int(write.get("written_image_count") or 0)
        and checks.get("main_images_exact_order")
        and checks.get("detail_images_exact_order")
        and int(write.get("suite_revision") or 0)
        == max(1, int(content.get("suite_revision") or 1))
    ):
        return None
    recipe_signature = json.dumps(
        {
            "content_strategy": str(content.get("content_strategy") or "ai_assisted"),
            "fact_card_approved": bool(content.get("fact_card_approved")),
            "planning_scope_approved": bool(content.get("planning_scope_approved")),
            "suite_approved": bool(content.get("suite_approved")),
            "identity_reference_urls": list(content.get("identity_reference_urls") or []),
            "primary_identity_url": str(content.get("primary_identity_url") or ""),
            "suite_customization": content.get("suite_customization") or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if str(write.get("recipe_signature") or "") != recipe_signature:
        return None
    expected_payload = {
        "schema_version": "ai-assisted-final-content-approval/v1",
        "status": "approved",
        "approved_by": "Kyle",
        "image_order": review_urls,
        "miaoshou_ordered_image_urls": urls,
        "video_action": str(review.get("video_action") or "none"),
        "asset_decisions": content.get("asset_decisions") or {},
        "generated_image_decisions": content.get("generated_image_miaoshou_decisions") or {},
    }
    expected_digest = hashlib.sha256(
        json.dumps(
            expected_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    if approval.get("approval_digest") != expected_digest:
        return None
    return urls


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
