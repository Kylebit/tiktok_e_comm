#!/usr/bin/env python3
"""Freeze and optionally execute the approved second-round image plan."""

from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
IMAGES_STATUS_BINDING_CONTRACT = 'orbit-images-status-paths/v2'
if __name__ == "__main__" and (not (REPO_ROOT / '.git').exists() or not all((REPO_ROOT / name).is_file() for name in (
    "core/config.py", "modules/sourcing/new_product_workbench.py",
    "shared_platform/publication_rounds.py",
))):
    raise SystemExit("COMPLETE_AGENT_SOURCE_REQUIRED: use the selected repository's scripts/repo_bound_agent_entry.py --profile <absolute-profile> --entry images")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PROVIDER_UPLOAD_BYTES = 10 * 1024 * 1024
SAFE_PROVIDER_UPLOAD_BYTES = 9_500_000
MAX_PAID_TASKS = 30
HANDOFF_SCHEMA_VERSION = "product-publication-workflow-handoff/v1"
BRAND_TRANSLATION_PLAN_SCHEMA = "brand-image-translation-plan/v1"
BRAND_TRANSLATION_REPORT_SCHEMA = "brand-image-translation/v1"
BRAND_TRANSLATION_LOCALES = {
    "livelyhive-sea": frozenset({"ms-MY", "th-TH", "vi-VN", "ru-RU", "es-MX"}),
    "homebloom-sea": frozenset({"ms-MY", "th-TH", "vi-VN"}),
}




def _runtime_root(runtime):
    if runtime is None:
        return REPO_ROOT
    from shared_platform.native_parent_images import NativeImageRuntime
    if type(runtime) is not NativeImageRuntime:
        raise ValueError("R2_SERVICE_RUNTIME_REQUIRED")
    return runtime.checked_root()

def _brand_family(brand_id: str) -> str:
    """Resolve approval-bound single-market brand aliases to their SEA family."""

    normalized = str(brand_id or "").strip().casefold()
    if normalized == "livelyhive-sea" or normalized.startswith("livelyhive-"):
        return "livelyhive-sea"
    if normalized == "homebloom-sea" or normalized.startswith("homebloom-"):
        return "homebloom-sea"
    return normalized


def _brand_translation_locales(brand_id: str) -> frozenset[str]:
    return BRAND_TRANSLATION_LOCALES.get(_brand_family(brand_id), frozenset())
DUAL_BRAND_PUBLICATION_BRIDGE_SCHEMA = "dual-brand-publication-bridge/v1"
DUAL_BRAND_APPROVAL_AUTHORITIES = {
    "Kyle": "CONVERSATION_APPROVAL",
    "product-publication-autopilot": "ACTIVE_AUTOPILOT_POLICY",
}
DUAL_BRAND_RELEASE_PROJECTION_VERSION = "dual-brand-release-projection/v2"
TARGET_LOCALE = {
    "tiktok:LH_PH": "en-PH",
    "tiktok:LH_MY": "ms-MY",
    "tiktok:LH_TH": "th-TH",
    "tiktok:LH_VN": "vi-VN",
    "tiktok:HB_PH": "en-PH",
    "tiktok:HB_MY": "ms-MY",
    "tiktok:HB_TH": "th-TH",
    "tiktok:HB_VN": "vi-VN",
    "tiktok:MX": "es-MX",
    "tiktok:GB": "en-GB",
    "shopee:PH": "en-PH",
    "shopee:MY": "ms-MY",
    "shopee:TH": "th-TH",
    "shopee:VN": "vi-VN",
    "ozon:RU": "ru-RU",
}


def _brand_generation_report_path(offer_id: str, *, runtime=None, reports_root=None) -> Path:
    _runtime_root(runtime)
    if reports_root is not None:
        if runtime is not None:
            raise ValueError('IMAGES_CAPTURED_STATUS_MODE_ONLY')
        return Path(reports_root) / str(offer_id) / 'brand-image-generation.json'
    return (
        _runtime_root(runtime)
        / "reports"
        / "product-preparation"
        / str(offer_id)
        / "brand-image-generation.json"
    )


def _brand_rework_report_path(offer_id: str, *, runtime=None) -> Path:
    _runtime_root(runtime)
    return (
        _runtime_root(runtime)
        / "reports"
        / "product-preparation"
        / str(offer_id)
        / "brand-image-rework.json"
    )


def prepare_brand_image_rework(
    offer_id: str,
    *,
    request_path: Path,
    authorized_by: str,
 runtime=None) -> dict[str, Any]:
    """Persist one audit-bound final-review rejection and invalidate descendants."""

    _runtime_root(runtime)
    authority = str(authorized_by or "").strip()
    if authority not in {"Kyle", "product-publication-autopilot"}:
        raise ValueError("brand image rework requires a governed review authority")
    directory = _runtime_root(runtime) / "reports" / "product-preparation" / str(offer_id)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if (
        not isinstance(request, dict)
        or request.get("schema_version") != "brand-image-rework-request/v1"
        or str(request.get("offer_id") or "") != str(offer_id)
    ):
        raise ValueError("brand image rework request is invalid")
    approval_authority = "CONVERSATION_APPROVAL"
    if authority == "product-publication-autopilot":
        from shared_platform.publication_rounds import autopilot_authorizes

        if not (
            autopilot_authorizes("image_quality_assurance")
            and autopilot_authorizes(
                "brand_image_generation", paid_purpose="brand_image_generation"
            )
        ):
            raise ValueError("automated brand image rework is not authorized by policy")
        qa_path = directory / "automated-image-qa.json"
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        if (
            not isinstance(qa, dict)
            or qa.get("status") != "FAILED"
            or str(request.get("qa_digest") or "") != str(qa.get("qa_digest") or "")
        ):
            raise ValueError("automated brand image rework must bind the failed QA digest")
        approval_authority = "FAILED_AUTOMATED_QA_AND_ACTIVE_AUTOPILOT_POLICY"
    generation = _brand_generation_summary(str(offer_id), runtime=runtime) or {}
    assets = [dict(row) for row in generation.get("assets") or [] if isinstance(row, Mapping)]
    # Legacy signed generation reports predate the explicit planned count.
    # Their complete contiguous numbered asset set remains the only safe fallback.
    planned_count = int(generation.get("planned_image_count") or len(assets))
    selected = sorted({int(value) for value in request.get("review_numbers") or []})
    if not selected or planned_count < 1 or any(
        value < 1 or value > planned_count for value in selected
    ):
        raise ValueError("brand image rework review numbers are invalid")
    briefs = request.get("briefs") or {}
    if not isinstance(briefs, dict) or set(briefs) != {str(value) for value in selected}:
        raise ValueError("brand image rework requires one exact brief per selected image")
    if any(not str(briefs[str(value)] or "").strip() for value in selected):
        raise ValueError("brand image rework contains an empty brief")

    by_number = {int(row.get("review_number") or 0): row for row in assets}
    if generation.get("status") != "BRAND_IMAGE_REVIEW_REQUIRED" or set(by_number) != set(
        range(1, planned_count + 1)
    ):
        raise ValueError("brand image rework requires one complete reviewed image set")

    existing = _brand_rework_report_path(str(offer_id), runtime=runtime)
    attempt_number = 1
    if existing.is_file():
        previous = json.loads(existing.read_text(encoding="utf-8"))
        if previous.get("status") in {"ACTIVE", "RUNNING"}:
            raise ValueError("a brand image rework is already active")
        attempt_number = int(previous.get("attempt_number") or 0) + 1
    if attempt_number > 3:
        raise ValueError('brand image rework limit of three has been exhausted')
    reference_dir = directory / "review-composition-references" / f"rework-{attempt_number:02d}"
    reference_dir.mkdir(parents=True, exist_ok=True)
    durable_paths: list[str] = []
    reference_evidence: list[dict[str, Any]] = []
    for index, raw in enumerate(request.get("local_composition_references") or [], start=1):
        source = Path(str(raw or "")).resolve()
        if not source.is_file():
            raise ValueError(f"composition reference {index} is unavailable")
        suffix = source.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("composition reference type is unsupported")
        destination = reference_dir / f"reference-{index:02d}{suffix}"
        if source.stat().st_size > SAFE_PROVIDER_UPLOAD_BYTES:
            destination = destination.with_suffix(".jpg")
            _compress_manual_source_image(source, destination)
        else:
            shutil.copy2(source, destination)
        durable_paths.append(str(destination))
        reference_evidence.append(
            {
                "kind": "USER_COMPOSITION_REFERENCE",
                "path": str(destination.relative_to(_runtime_root(runtime))),
                "digest": "sha256:" + _sha256_file(destination),
            }
        )
    durable_urls: list[str] = []
    for raw in request.get("comparison_product_urls") or []:
        url = str(raw or "").strip()
        if not url.startswith("https://") or url in durable_urls:
            raise ValueError("comparison product reference URL is invalid")
        durable_urls.append(url)
        reference_evidence.append({"kind": "HISTORICAL_PRODUCT_COMPOSITION_REFERENCE", "url": url})
    if len(reference_evidence) > 11 or (
        authority == "Kyle" and not reference_evidence
    ):
        raise ValueError("manual brand image rework requires 1-11 composition references")

    archive = directory / "image-rework-revisions" / f"rework-{attempt_number:02d}"
    archive.mkdir(parents=True, exist_ok=True)
    archived_files: list[str] = []
    governed = [
        "brand-image-generation.json",
        "brand-image-translation-plan.json",
        "brand-image-translation.json",
        "automated-image-qa.json",
        "lingshi-image-qa-assessment.json",
        "round2-image-snapshot.json",
    ]
    for name in governed:
        source = directory / name
        if not source.is_file():
            continue
        shutil.copy2(source, archive / name)
        archived_files.append(name)
        if name != "brand-image-generation.json":
            source.unlink()

    superseded_plan_id = None  # R3 consumes invalidation evidence; R2 never opens ReleaseStore.
    receipt = {
        "schema_version": "brand-image-rework/v1",
        "offer_id": str(offer_id),
        "status": "ACTIVE",
        "attempt_number": attempt_number,
        "authorized_by": authority,
        "approval_authority": approval_authority,
        "qa_digest": request.get("qa_digest"),
        "reason": str(request.get("reason") or "").strip(),
        "selected_review_numbers": selected,
        "briefs": {str(value): str(briefs[str(value)]).strip() for value in selected},
        "composition_reference_paths": durable_paths,
        "composition_reference_urls": durable_urls,
        "reference_evidence": reference_evidence,
        "superseded_assets": [by_number[value] for value in selected],
        "retained_review_numbers": [
            value for value in range(1, planned_count + 1) if value not in selected
        ],
        "archived_files": archived_files,
        "archive_path": str(archive.relative_to(_runtime_root(runtime))),
        "superseded_plan_id": superseded_plan_id,
        "round3_invalidation_required": True,
        "marketplace_writes": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "rework_request_digest":_canonical_digest(request),
    }
    _write_json_atomic(existing, receipt)
    return {
        "schema_version": "prepare-product-images-result/v1",
        "offer_id": str(offer_id),
        "status": "BRAND_IMAGE_REWORK_READY",
        "selected_review_numbers": selected,
        "retained_review_numbers": receipt["retained_review_numbers"],
        "composition_reference_count": len(reference_evidence),
        "superseded_plan_id": superseded_plan_id,
        "round3_invalidation_required": True,
        "platform_writes": 0,
        "miaoshou_writes": 0,
    }


def _brand_preflight_report_path(offer_id: str, *, runtime=None) -> Path:
    _runtime_root(runtime)
    return (
        _runtime_root(runtime)
        / "reports"
        / "product-preparation"
        / str(offer_id)
        / "brand-image-preflight.json"
    )


def _manual_public_assets_report_path(offer_id: str, *, runtime=None) -> Path:
    _runtime_root(runtime)
    return (
        _runtime_root(runtime)
        / "reports"
        / "product-preparation"
        / str(offer_id)
        / "manual-source-public-assets.json"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manual_public_reference_rows(offer_id: str, *, runtime=None) -> dict[int, dict[str, Any]]:
    _runtime_root(runtime)
    path = _manual_public_assets_report_path(offer_id, runtime=runtime)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return {}
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "manual-source-public-assets/v1"
        or str(value.get("offer_id") or "") != str(offer_id)
    ):
        raise ValueError("manual source public asset mapping is invalid")
    rows: dict[int, dict[str, Any]] = {}
    for raw in value.get("assets") or []:
        if not isinstance(raw, dict):
            raise ValueError("manual source public asset row is invalid")
        position = int(raw.get("position") or 0)
        source_path = (_runtime_root(runtime) / str(raw.get("source_path") or "")).resolve()
        artifact_path = (_runtime_root(runtime) / str(raw.get("artifact_path") or "")).resolve()
        url = str(raw.get("public_url") or "")
        if (
            position <= 0
            or position in rows
            or not source_path.is_file()
            or not artifact_path.is_file()
            or _sha256_file(source_path) != str(raw.get("source_sha256") or "")
            or _sha256_file(artifact_path) != str(raw.get("artifact_sha256") or "")
            or artifact_path.stat().st_size > MAX_PROVIDER_UPLOAD_BYTES
            or not url.startswith("https://")
        ):
            raise ValueError("manual source public asset identity drifted")
        rows[position] = dict(raw)
    return rows


def _compress_manual_source_image(source: Path, destination: Path) -> dict[str, Any]:
    """Create a visually equivalent provider-safe JPEG below the 10 MB limit."""

    from PIL import Image, ImageOps

    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        original_dimensions = [int(image.width), int(image.height)]
        working = image
        quality = 92
        while True:
            working.save(
                destination,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
            )
            if destination.stat().st_size <= SAFE_PROVIDER_UPLOAD_BYTES:
                break
            if quality > 78:
                quality -= 4
                continue
            next_size = (
                max(1, int(working.width * 0.9)),
                max(1, int(working.height * 0.9)),
            )
            if next_size == working.size:
                raise ValueError("manual source image cannot be compressed below 10MB")
            working = working.resize(next_size, Image.Resampling.LANCZOS)
            quality = 88
    if destination.stat().st_size > MAX_PROVIDER_UPLOAD_BYTES:
        raise ValueError("manual source image compression exceeded provider limit")
    return {
        "source_byte_count": source.stat().st_size,
        "artifact_byte_count": destination.stat().st_size,
        "source_dimensions": original_dimensions,
        "artifact_dimensions": [int(working.width), int(working.height)],
        "jpeg_quality": quality,
    }


def _prepare_manual_lingshi_references(
    offer_id: str, required_positions: set[int]
, *, runtime=None) -> dict[int, dict[str, Any]]:
    """Create digest-bound local references for Lingshi without a public upload service."""

    _runtime_root(runtime)
    intake_dir = _runtime_root(runtime) / "data" / "product_intake" / str(offer_id)
    record = json.loads((intake_dir / "record.json").read_text(encoding="utf-8"))
    if record.get("source_mode") != "manual_intake":
        raise ValueError("local Lingshi references are only valid for manual-intake products")
    images = {
        int(row.get("position") or 0): row
        for row in (record.get("images") or [])
        if isinstance(row, dict)
    }
    if not required_positions or not required_positions.issubset(images):
        raise ValueError("manual source positions do not cover the approved image plan")
    output_dir = (
        _runtime_root(runtime)
        / "reports"
        / "product-preparation"
        / str(offer_id)
        / "source-reference-assets"
    )
    rows: dict[int, dict[str, Any]] = {}
    for position in sorted(required_positions):
        source_row = images[position]
        source = (intake_dir / "images" / str(source_row.get("filename") or "")).resolve()
        if source.parent != (intake_dir / "images").resolve() or not source.is_file():
            raise ValueError("manual source image path is invalid")
        source_sha256 = _sha256_file(source)
        if source_sha256 != str(source_row.get("sha256") or ""):
            raise ValueError("manual source image digest drifted")
        artifact = output_dir / f"source-{position:02d}-lingshi-reference.jpg"
        compression = _compress_manual_source_image(source, artifact)
        rows[position] = {
            "kind": "manual_lingshi_reference",
            "action": "keep",
            "local_path": str(artifact),
            "source_digest": "sha256:" + source_sha256,
            "artifact_digest": "sha256:" + _sha256_file(artifact),
            "compression": compression,
        }
    return rows


def prepare_manual_source_public_assets(offer_id: str) -> dict[str, Any]:
    """Reject obsolete public uploads; Lingshi consumes digest-bound local images."""

    del offer_id
    raise ValueError(
        "public reference upload is retired; Lingshi receives the digest-bound local image directly"
    )


def _brand_generation_summary(offer_id: str, *, runtime=None, reports_root=None) -> dict[str, Any] | None:
    _runtime_root(runtime)
    path = _brand_generation_report_path(offer_id, runtime=runtime, reports_root=reports_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _brand_reuse_plan_path(offer_id: str, *, runtime=None) -> Path:
    _runtime_root(runtime)
    return (
        _runtime_root(runtime)
        / "reports"
        / "product-preparation"
        / str(offer_id)
        / "brand-image-reuse-plan.json"
    )


def _autopilot_reuse_plan(offer_id: str, plan: Mapping[str, Any], *, runtime=None) -> dict[str, Any]:
    """Resolve the safe default: every unproved role is generated, never reused."""

    from shared_platform.publication_rounds import autopilot_authorizes

    permitted = (autopilot_authorizes('brand_image_generation') if runtime is None
                 else 'brand_image_generation' in runtime.paid_context.policy['automatic_steps'])
    if not permitted:
        raise ValueError("brand image reuse review is not authorized by policy")
    items: list[dict[str, str]] = []
    for brand in plan.get("brand_plans") or []:
        if not isinstance(brand, Mapping):
            continue
        brand_id = str(brand.get("id") or "").strip()
        for asset in brand.get("generated_assets") or []:
            if not isinstance(asset, Mapping):
                continue
            role = str(asset.get("role") or "").strip()
            if brand_id and role:
                items.append(
                    {"brand_id": brand_id, "role": role, "decision": "GENERATE"}
                )
    if not items:
        raise ValueError("first-round image roles are unavailable")
    return {
        "schema_version": "brand-image-reuse-plan/v1",
        "offer_id": str(offer_id),
        "status": "APPROVED",
        "approved_by": "orbit-product-publication-default-v1",
        "approval_authority": "ACTIVE_AUTOPILOT_POLICY",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "decision_basis": (
            "自动驾驶只允许复用带品牌、角色、来源位置和摘要的已证明资产；"
            "没有该证据的角色全部安全地按缺口生成。"
        ),
        "items": items,
        "external_generation_count": 0,
        "platform_writes": 0,
    }


def _brand_translation_plan_path(offer_id: str, *, runtime=None) -> Path:
    _runtime_root(runtime)
    return (
        _runtime_root(runtime)
        / "reports"
        / "product-preparation"
        / str(offer_id)
        / "brand-image-translation-plan.json"
    )


def _brand_translation_report_path(offer_id: str, *, runtime=None, reports_root=None) -> Path:
    _runtime_root(runtime)
    if reports_root is not None:
        if runtime is not None:
            raise ValueError('IMAGES_CAPTURED_STATUS_MODE_ONLY')
        return Path(reports_root) / str(offer_id) / 'brand-image-translation.json'
    return (
        _runtime_root(runtime)
        / "reports"
        / "product-preparation"
        / str(offer_id)
        / "brand-image-translation.json"
    )


def _translation_preliminary(role: str) -> dict[str, str]:
    normalized = str(role or "").strip().lower()
    if normalized in {
        "size_comparison",
        "size_and_variants",
        "assembled_size",
        "piece_layout",
    }:
        return {
            "status": "CHECK_TEXT_CONTENT",
            "label": "先核对是否仅有尺寸",
            "reason": "仅含数字、尺寸线和通用单位的图片无需翻译；只有出现可翻译文案时才建议本地化。",
        }
    if normalized == "installation":
        return {
            "status": "RECOMMEND_TRANSLATE",
            "label": "初步建议翻译",
            "reason": "安装步骤图通常包含英文步骤文字，建议为非英语站点本地化。",
        }
    if normalized == "cover":
        return {
            "status": "NOT_NEEDED",
            "label": "初步无需翻译",
            "reason": "主图按无文字成品审核；若实际画面出现英文标题或卖点，确认时改为翻译。",
        }
    return {
        "status": "NOT_NEEDED",
        "label": "初步无需翻译",
        "reason": "该图片角色通常以商品或场景画面为主；若实际画面含文字，确认时改为翻译。",
    }


def _number_translation_review_assets(report: Mapping[str, Any]) -> dict[str, Any]:
    """Create a stable 1-based review order and non-binding translation advice."""
    output = dict(report)
    assets = [dict(row) for row in (report.get("assets") or []) if isinstance(row, dict)]
    assets.sort(key=lambda row: max(0, int(row.get("sequence") or 0)))
    for review_number, row in enumerate(assets, start=1):
        row["review_number"] = review_number
        row["translation_preliminary"] = _translation_preliminary(str(row.get("role") or ""))
    output["assets"] = assets
    output["translation_review_status"] = "PRELIMINARY_OPINIONS_READY"
    output["translation_review_note"] = (
        "所有最终图片已从 1 开始连续编号；翻译意见仅为初步建议，等待 Kyle 逐图确认。"
    )
    return output


def _brand_generation_identity_digest(generation: Mapping[str, Any]) -> str:
    assets = [
        {
            "review_number": int(row.get("review_number") or 0),
            "brand_id": str(row.get("brand_id") or ""),
            "role": str(row.get("role") or ""),
            "artifact_digest": str(row.get("artifact_digest") or ""),
            "public_url": str(row.get("public_url") or ""),
        }
        for row in (generation.get("assets") or [])
        if isinstance(row, dict)
    ]
    assets.sort(key=lambda row: row["review_number"])
    planned_count = int(generation.get("planned_image_count") or len(assets))
    if planned_count < 1 or len(assets) != planned_count or [
        row["review_number"] for row in assets
    ] != list(range(1, planned_count + 1)):
        raise ValueError("brand image generation does not match its approved numbered plan")
    for row in assets:
        if (
            not _brand_translation_locales(str(row["brand_id"]))
            or not row["role"]
            or re.fullmatch(r"sha256:[0-9a-f]{64}", row["artifact_digest"]) is None
            or not row["public_url"].startswith("https://")
        ):
            raise ValueError("brand image generation asset identity is incomplete")
    return _canonical_digest({
        "schema_version": "brand-image-generation-identity/v1",
        "offer_id": str(generation.get("offer_id") or ""),
        "assets": assets,
    })


def _build_brand_translation_plan(
    offer_id: str,
    *,
    generation: Mapping[str, Any],
    selections: Mapping[int, list[str]],
    approved_by: str,
    dimension_only_numbers: list[int] | tuple[int, ...] = (),
) -> dict[str, Any]:
    """Construct a new technical plan; historical Kyle files are read-only lineage."""
    if generation.get("status") != "BRAND_IMAGE_REVIEW_REQUIRED":
        raise ValueError("brand images are not ready for translation approval")
    if str(generation.get("offer_id") or "") != str(offer_id):
        raise ValueError("brand image generation offer_id does not match")
    autopilot_approver = "orbit-product-publication-default-v1"
    if approved_by == "Kyle":
        raise ValueError(
            "unverified historical Kyle brand translation plan cannot authorize paid execution; "
            "an independently verified receipt or technical successor is required"
        )
    if approved_by != autopilot_approver:
        raise ValueError("brand translation approval authority is invalid")
    assets = {
        int(row.get("review_number") or 0): row
        for row in (generation.get("assets") or [])
        if isinstance(row, dict)
    }
    tasks: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    selected_numbers = {int(number) for number in selections}
    dimension_only = sorted({int(number) for number in dimension_only_numbers})
    if selected_numbers.intersection(dimension_only):
        raise ValueError("dimension-only image cannot also be selected for translation")
    for number in dimension_only:
        asset = assets.get(number)
        if not asset or str(asset.get("role") or "") not in {
            "size_comparison",
            "assembled_size",
            "piece_layout",
            "size_and_variants",
        }:
            raise ValueError("dimension-only exclusion must reference a size comparison image")
    for raw_number, raw_locales in selections.items():
        number = int(raw_number)
        asset = assets.get(number)
        if not asset:
            raise ValueError("translation selection references an unknown image number")
        brand_id = str(asset.get("brand_id") or "")
        allowed = _brand_translation_locales(brand_id)
        for raw_locale in raw_locales:
            locale = str(raw_locale or "").strip()
            key = (number, locale)
            if locale not in allowed:
                raise ValueError("translation locale is not allowed for brand")
            if key in seen:
                raise ValueError("brand translation plan contains duplicate tasks")
            seen.add(key)
            tasks.append({
                "review_number": number,
                "brand_id": brand_id,
                "role": str(asset.get("role") or ""),
                "locale": locale,
                "source_url": str(asset.get("public_url") or ""),
                "source_artifact_digest": str(asset.get("artifact_digest") or ""),
            })
    tasks.sort(key=lambda row: (row["review_number"], row["locale"]))
    if len(tasks) > MAX_PAID_TASKS:
        raise ValueError("brand translation paid task count is invalid")
    return {
        "schema_version": BRAND_TRANSLATION_PLAN_SCHEMA,
        "offer_id": str(offer_id),
        "status": "APPROVED_BY_AUTOPILOT",
        "approved_by": approved_by,
        "approval_authority": "ACTIVE_AUTOPILOT_POLICY",
        "generation_identity_digest": _brand_generation_identity_digest(generation),
        "approved_task_count": len(tasks),
        "tasks": tasks,
        "scope_decisions": {
            "selected_review_numbers": sorted(selected_numbers),
            "dimension_only_no_translation_review_numbers": dimension_only,
            "dimension_only_rule": (
                "仅含数字、尺寸线和通用单位且没有可翻译文案的图片保留英文母版，不创建本地化任务。"
            ),
        },
        "platform_writes": 0,
        "miaoshou_writes": 0,
        "product_center_mutated": False,
    }


def build_approved_brand_translation_plan(
    offer_id: str,
    *,
    generation: Mapping[str, Any],
    selections: Mapping[int, list[str]],
    approved_by: str,
    dimension_only_numbers: list[int] | tuple[int, ...] = (),
) -> dict[str, Any]:
    """Freeze a new technical scope; Kyle attribution needs a verifiable receipt path."""
    if approved_by == "Kyle":
        raise ValueError(
            "Kyle brand translation approval requires an independent verifiable "
            "conversation receipt; this CLI has no receipt binding"
        )
    return _build_brand_translation_plan(
        offer_id,
        generation=generation,
        selections=selections,
        approved_by=approved_by,
        dimension_only_numbers=dimension_only_numbers,
    )


def _validate_brand_translation_plan(
    offer_id: str,
    *,
    plan: Mapping[str, Any],
    generation: Mapping[str, Any],
) -> dict[str, Any]:
    if plan.get("schema_version") != BRAND_TRANSLATION_PLAN_SCHEMA:
        raise ValueError("brand translation plan schema is invalid")
    if str(plan.get("offer_id") or "") != str(offer_id):
        raise ValueError("brand translation plan offer_id does not match")
    approved_by = str(plan.get("approved_by") or "")
    if approved_by == "Kyle":
        raise ValueError(
            "unverified historical Kyle brand translation plan cannot authorize paid execution; "
            "an independently verified receipt or technical successor is required"
        )
    if (
        approved_by != "orbit-product-publication-default-v1"
        or plan.get("status") != "APPROVED_BY_AUTOPILOT"
    ):
        raise ValueError("brand translation approval authority is missing")
    if plan.get("generation_identity_digest") != _brand_generation_identity_digest(generation):
        raise ValueError("brand image generation identity drifted")
    selections: dict[int, list[str]] = {}
    for row in plan.get("tasks") or []:
        if not isinstance(row, dict):
            raise ValueError("brand translation plan task is invalid")
        selections.setdefault(int(row.get("review_number") or 0), []).append(
            str(row.get("locale") or "")
        )
    scope_decisions = plan.get("scope_decisions") or {}
    if not isinstance(scope_decisions, Mapping):
        raise ValueError("brand translation scope decisions are invalid")
    dimension_only_numbers = scope_decisions.get(
        "dimension_only_no_translation_review_numbers"
    ) or []
    rebuilt = _build_brand_translation_plan(
        str(offer_id),
        generation=generation,
        selections=selections,
        approved_by=approved_by,
        dimension_only_numbers=dimension_only_numbers,
    )
    if dict(plan) != rebuilt:
        raise ValueError("brand translation plan identity or authority drifted")
    return dict(plan)


def freeze_conversation_approved_translation_scope(
    offer_id: str,
    *,
    generation: Mapping[str, Any],
    selected_review_numbers: list[int] | tuple[int, ...],
    dimension_only_numbers: list[int] | tuple[int, ...],
    approved_by: str,
 runtime=None) -> dict[str, Any]:
    """Route explicitly selected numbered images to locales in the approved target scope."""
    _runtime_root(runtime)
    report_dir = _runtime_root(runtime) / "reports" / "product-preparation" / str(offer_id)
    first_review = json.loads((report_dir / "first-review.json").read_text(encoding="utf-8"))
    if runtime is None:
        if first_review.get("status") != "FIRST_REVIEW_READY":
            raise ValueError("first review is not ready for translation scope approval")
        targets = list((first_review.get("target_selection") or {}).get("requested") or [])
    else:
        _runtime_root(runtime)
        targets = runtime.translation_targets(offer_id, first_review)
    if not targets or any(target not in TARGET_LOCALE for target in targets):
        raise ValueError("approved target locale routing is incomplete")

    assets = {
        int(row.get("review_number") or 0): row
        for row in (generation.get("assets") or [])
        if isinstance(row, dict)
    }
    selections: dict[int, list[str]] = {}
    for number in sorted({int(value) for value in selected_review_numbers}):
        asset = assets.get(number)
        if not asset:
            raise ValueError("translation selection references an unknown image number")
        brand_id = str(asset.get("brand_id") or "")
        routed_locales = {
            TARGET_LOCALE[target]
            for target in targets
            if ("homebloom-sea" if target.startswith("tiktok:HB_") else "livelyhive-sea")
            == _brand_family(brand_id)
            and TARGET_LOCALE[target] not in {"en-PH", "en-GB"}
        }
        selections[number] = sorted(
            routed_locales.intersection(_brand_translation_locales(brand_id))
        )
        if not selections[number]:
            raise ValueError("selected image has no non-English locale route")

    plan = build_approved_brand_translation_plan(
        str(offer_id),
        generation=generation,
        selections=selections,
        approved_by=approved_by,
        dimension_only_numbers=dimension_only_numbers,
    )
    plan_path = _brand_translation_plan_path(str(offer_id), runtime=runtime)
    existing_path = plan_path if runtime is None else runtime.retained_plan_path(plan_path)
    if existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("existing brand translation plan requires reconciliation") from error
        if existing != plan:
            raise ValueError(
                "existing brand translation plan differs from the requested scope or authority; "
                "preserve its lineage and create an explicit successor"
            )
        if existing_path != plan_path:
            _write_json_atomic(plan_path, existing)
        return existing
    _write_json_atomic(plan_path, plan)
    return plan


def _parse_review_numbers(raw: str | None) -> list[int]:
    if raw is None:
        return []
    if str(raw).strip().casefold() == "none":
        return []
    values: list[int] = []
    for token in str(raw).split(","):
        clean = token.strip()
        if not clean or not clean.isdigit() or int(clean) < 1:
            raise ValueError("image review numbers must be positive comma-separated integers")
        values.append(int(clean))
    if len(values) != len(set(values)):
        raise ValueError("image review numbers must not contain duplicates")
    return values


def _enrich_lingshi_asset_receipts(
    report_path: Path, report: dict[str, Any], *, runtime=None
) -> dict[str, Any]:
    checkpoint_dir = (runtime.checkpoint_directory(report['offer_id'], 'brand-image-translation-checkpoints')
                      if runtime is not None else report_path.parent / "brand-image-translation-checkpoints")
    receipts: dict[str, Mapping[str, Any]] = {}
    if checkpoint_dir.is_dir():
        for state_path in checkpoint_dir.glob("lingshi-*.json"):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                continue
            receipt = state.get("receipt") if isinstance(state, dict) else None
            if isinstance(receipt, dict) and receipt.get("client_business_id"):
                receipts[str(receipt["client_business_id"])] = receipt
    changed = False
    for row in report.get("assets") or []:
        if not isinstance(row, dict) or row.get("provider") != "lingshi-media/v1":
            continue
        receipt = receipts.get(str(row.get("artifact_id") or ""))
        if not receipt:
            continue
        additions = {
            "provider_task_id": receipt.get("task_id"),
            "cost": receipt.get("cost"),
            "channel_group": receipt.get("channel_group"),
            "outcome_unknown": bool(receipt.get("outcome_unknown")),
        }
        for key, value in additions.items():
            if row.get(key) != value:
                row[key] = value
                changed = True
    if changed:
        _write_json_atomic(report_path, report)
    return report


def generate_approved_brand_translations(
    offer_id: str,
    *,
    image_provider: str = "lingshi",
    translation_plan_path: Path | None = None,
    paid_context=None,
 runtime=None) -> dict[str, Any]:
    """Execute only the approved phase 2B tasks, then stop for human review."""
    _runtime_root(runtime)
    from shared_platform.publication_paid_requests import require_paid_context
    paid_context = require_paid_context(paid_context)
    if paid_context.offer_id != str(offer_id):
        raise ValueError('paid context belongs to another product')
    paid_context.ensure_ready()
    from modules.sourcing.localized_image_auto_translation import translate_image_regions
    from modules.sourcing.localized_image_ocr import detect_english_text_regions
    image_provider = str(image_provider or "lingshi").strip().lower()
    if image_provider != "lingshi":
        raise ValueError("new localized image generation is locked to Lingshi AI")

    generation = _brand_generation_summary(str(offer_id), runtime=runtime)
    if not isinstance(generation, dict) or generation.get("status") != "BRAND_IMAGE_REVIEW_REQUIRED":
        raise ValueError("all brand images must be ready before translation")
    selected_plan_path = translation_plan_path or _brand_translation_plan_path(str(offer_id), runtime=runtime)
    if runtime is not None and translation_plan_path is None:
        selected_plan_path = runtime.retained_plan_path(selected_plan_path)
    try:
        raw_plan = json.loads(selected_plan_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        raise ValueError("approved brand translation plan is required")
    if not isinstance(raw_plan, dict):
        raise ValueError("approved brand translation plan is invalid")
    plan = _validate_brand_translation_plan(
        str(offer_id), plan=raw_plan, generation=generation
    )
    for task in plan['tasks']:
        locales={TARGET_LOCALE[target] for target in paid_context.round1['canonical_targets'] if target in TARGET_LOCALE
                 and ('homebloom-sea' if target.startswith('tiktok:HB_') else 'livelyhive-sea')==_brand_family(task['brand_id'])}
        if task['locale'] not in locales:
            raise ValueError('translation locale is outside the frozen round-1 target scope')
    if raw_plan != plan:
        _write_json_atomic(_brand_translation_plan_path(str(offer_id), runtime=runtime), plan)
    plan_digest = _canonical_digest(plan)
    paid_context.bind_plan('translation', {'plan':plan, 'generation_identity':plan['generation_identity_digest']})
    paid_context.planned_requests=len(plan['tasks'])+len({row['review_number'] for row in plan['tasks']})
    report_path = _brand_translation_report_path(str(offer_id), runtime=runtime)
    try:
        previous = json.loads(report_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    if previous.get("plan_digest") not in {None, plan_digest}:
        raise ValueError("brand translation execution plan drifted")
    if previous.get("status") == "LOCALIZED_IMAGE_REVIEW_REQUIRED":
        previous = (_enrich_lingshi_asset_receipts(report_path, previous, runtime=runtime)
                    if runtime is not None else _enrich_lingshi_asset_receipts(report_path, previous))
        for row in previous.get('assets') or []:
            _validate_cached_asset(row,paid_context)
        return {
            "schema_version": "prepare-product-images-result/v1",
            "offer_id": str(offer_id),
            "status": "LOCALIZED_IMAGE_REVIEW_REQUIRED",
            "execution_status": "LOCALIZED_IMAGE_REVIEW_REQUIRED",
            "approved_task_count": len(plan["tasks"]),
            "external_generation_count": int(previous.get("external_generation_count") or 0),
            "translation_model_call_count": int(previous.get("translation_model_call_count") or 0),
            "platform_writes": 0,
            "miaoshou_writes": 0,
            "product_center_mutated": False,
            "review_report": str(report_path),
        }

    source_assets = {
        int(row.get("review_number") or 0): row
        for row in (generation.get("assets") or [])
        if isinstance(row, dict)
    }
    existing_text = {
        int(row.get("review_number") or 0): row
        for row in (previous.get("text_inventories") or [])
        if isinstance(row, dict)
    }
    existing_assets = {
        (int(row.get("source_review_number") or 0), str(row.get("locale") or "")): row
        for row in (previous.get("assets") or [])
        if isinstance(row, dict) and row.get("status") == "COMPLETED"
    }
    for row in existing_assets.values():
        _validate_cached_asset(row,paid_context)
    output = {
        "schema_version": BRAND_TRANSLATION_REPORT_SCHEMA,
        "offer_id": str(offer_id),
        "status": "RUNNING",
        "phase": "2B_APPROVED_IMAGE_TRANSLATION",
        "plan_digest": plan_digest,
        "generation_identity_digest": plan["generation_identity_digest"],
        "approved_task_count": len(plan["tasks"]),
        "approved_tasks": list(plan["tasks"]),
        "scope_decisions": dict(plan.get("scope_decisions") or {}),
        "pending_image_provider": image_provider,
        "text_inventories": list(existing_text.values()),
        "assets": list(existing_assets.values()),
        "translation_model_call_count": sum(
            int((row.get("translation_receipt") or {}).get("model_calls") or 0)
            for row in existing_text.values()
        ),
        "external_generation_count": sum(
            int(row.get("external_generation_count") or 0)
            for row in existing_assets.values()
        ),
        "provider_preflights": dict(previous.get("provider_preflights") or {}),
        "provider_preflight_completed": False,
        "platform_writes": 0,
        "miaoshou_writes": 0,
        "product_center_mutated": False,
        "started_at": previous.get("started_at") or datetime.now(timezone.utc).isoformat(),
    }
    pending_image_tasks = [
        row for row in plan["tasks"]
        if (row["review_number"], row["locale"]) not in existing_assets
    ]
    from modules.sourcing.lingshi_client import LingshiClient
    from modules.sourcing.localized_image_lingshi_generation import (
        generate_localized_reference_image,
    )

    client = runtime.client() if runtime is not None else LingshiClient.from_config(_runtime_root(runtime) / "config" / "lingshi.local.json")
    from modules.sourcing.brand_image_lingshi_generation import resolve_brand_image_model

    selected_image_model, selected_image_model_detail = resolve_brand_image_model(client)
    output["provider_preflight_completed"] = bool(
        output["provider_preflights"].get(image_provider)
    )
    if (
        (pending_image_tasks or len(existing_text) < len({row["review_number"] for row in plan["tasks"]}))
        and not output["provider_preflight_completed"]
    ):
        skills_catalog = client.list_skills()
        guide = client.guide()
        balance = client.balance()
        pricing = client.model_pricing(selected_image_model, active_only=True)
        active_prices = [
            float(row.get("min_price") or row.get("base_price") or 0)
            for row in (pricing.get("channel_groups") or [])
            if isinstance(row, dict)
            and row.get("is_active") is True
            and row.get("in_key_whitelist") is True
            and float(row.get("min_price") or row.get("base_price") or 0) > 0
        ]
        if not all(isinstance(value, dict) for value in (skills_catalog, guide, balance)):
            raise ValueError(f"{image_provider} provider preflight returned an invalid response")
        output["provider_preflights"][image_provider] = {
            "provider": "灵识 AI",
            "text_model": "gpt-5.4-nano",
            "image_model": selected_image_model,
            "image_model_display_name": selected_image_model_detail.get("display_name"),
            "image_model_resolution_policy": "AVAILABLE_COMPATIBLE_ALIAS",
            "text_endpoint": "/v1/chat/completions",
            "image_endpoint": "/api/v1/media/generate",
            "uploads_selected_source_images": True,
            "selected_source_image_count": len({row["review_number"] for row in plan["tasks"]}),
            "planned_text_call_count": len({row["review_number"] for row in plan["tasks"]}),
            "planned_image_task_count": len(pending_image_tasks),
            "balance_before": balance.get("balance"),
            "unit": balance.get("unit"),
            "active_image_unit_price_min": min(active_prices) if active_prices else None,
            "active_image_unit_price_max": max(active_prices) if active_prices else None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        output["provider_preflight_completed"] = True
        _write_json_atomic(report_path, output)

    for number in sorted({row["review_number"] for row in plan["tasks"]}):
        if number in existing_text:
            continue
        source = source_assets[number]
        source_bytes = _download_source(str(source["public_url"]))
        regions = detect_english_text_regions(source_bytes)
        if not regions:
            raise ValueError(f"image {number} contains no auditable English OCR regions")
        _verify_asset_bytes(source_bytes, source['artifact_digest'])
        translated = translate_image_regions(regions, paid_context=paid_context,
            business_identity={'offer_id':str(offer_id), 'brand_id':source['brand_id'], 'role':source['role'],
                               'phase':'translation-text', 'source_digest':source['artifact_digest']},
            **({'client':runtime.client()} if runtime is not None else {}))
        inventory = {
            "review_number": number,
            "brand_id": str(source.get("brand_id") or ""),
            "role": str(source.get("role") or ""),
            "source_url": str(source.get("public_url") or ""),
            "source_artifact_digest": str(source.get("artifact_digest") or ""),
            "ocr_regions": regions,
            "translations": translated.get("translations") or {},
            "translation_receipt": translated.get("receipt") or {},
        }
        existing_text[number] = inventory
        output["text_inventories"] = [existing_text[key] for key in sorted(existing_text)]
        output["translation_model_call_count"] = sum(
            int((row.get("translation_receipt") or {}).get("model_calls") or 0)
            for row in existing_text.values()
        )
        _write_json_atomic(report_path, output)

    for task in plan["tasks"]:
        key = (task["review_number"], task["locale"])
        if key in existing_assets:
            continue
        inventory = existing_text[task["review_number"]]
        translations = (inventory.get("translations") or {}).get(task["locale"])
        if not isinstance(translations, list) or not translations:
            raise ValueError("approved locale translation text is incomplete")
        source_bytes = _download_source(task["source_url"])
        _verify_asset_bytes(source_bytes, task['source_artifact_digest'])
        try:
            generated = generate_localized_reference_image(
                source_url=task["source_url"],
                source_bytes=source_bytes,
                locale=task["locale"],
                translations=translations,
                checkpoint_dir=(report_path.parent / "brand-image-translation-checkpoints" if runtime is None
                                else runtime.checkpoint_directory(offer_id, 'brand-image-translation-checkpoints')),
                model=selected_image_model,
                client=client, paid_context=paid_context,
                business_identity=_localized_business(offer_id, task),
            )
        except Exception as error:
            detail = str(error)
            quota_blocked = any(
                marker in detail.lower()
                for marker in ("quota_not_enough", "quota is not enough", "insufficient", "http 402")
            )
            output["status"] = (
                "PROVIDER_QUOTA_REQUIRED"
                if quota_blocked
                else "PROVIDER_RECONCILIATION_REQUIRED"
            )
            output["blocker"] = detail[:400]
            output["next_action"] = (
                f"补充 {image_provider} 额度后从现有检查点继续；不得跳过缺失图片进入发布。"
                if quota_blocked
                else "只读查询未决任务的稳定业务 ID；只有供应商明确返回原任务或任务不存在后才能续跑。"
            )
            _write_json_atomic(report_path, output)
            raise
        receipt = generated.get("receipt") or {}
        row = {
            "source_review_number": task["review_number"],
            "brand_id": task["brand_id"],
            "role": task["role"],
            "locale": task["locale"],
            "status": "COMPLETED",
            "artifact_id": str(receipt.get("client_business_id") or ""),
            "artifact_digest": str(receipt.get("output_digest") or ""),
            "public_url": str(receipt.get("public_url") or ""),
            "provider": str(receipt.get("provider") or ""),
            "model": str(receipt.get("model") or ""),
            "provider_task_id": receipt.get("task_id"),
            "checkpoint_path": generated.get("checkpoint_path"),
            "cost": receipt.get("cost"),
            "channel_group": receipt.get("channel_group"),
            "outcome_unknown": bool(receipt.get("outcome_unknown")),
            "external_generation_count": int(receipt.get("external_generation_count") or 0),
        }
        if (
            row["status"] != "COMPLETED"
            or re.fullmatch(r"sha256:[0-9a-f]{64}", row["artifact_digest"]) is None
            or not row["public_url"].startswith("https://")
            or row["external_generation_count"] != 1
        ):
            raise ValueError("localized image generation receipt is incomplete")
        existing_assets[key] = row
        output["assets"] = [existing_assets[k] for k in sorted(existing_assets)]
        output["external_generation_count"] = sum(
            int(item.get("external_generation_count") or 0)
            for item in existing_assets.values()
        )
        _write_json_atomic(report_path, output)

    output["status"] = "LOCALIZED_IMAGE_REVIEW_REQUIRED"
    output["completed_at"] = datetime.now(timezone.utc).isoformat()
    output["next_action"] = (
        f"请按原图编号和语言审核 {len(plan['tasks'])} 张翻译图片；审核前不写入妙手。"
    )
    _write_json_atomic(report_path, output)
    return {
        "schema_version": "prepare-product-images-result/v1",
        "offer_id": str(offer_id),
        "status": "LOCALIZED_IMAGE_REVIEW_REQUIRED",
        "execution_status": "LOCALIZED_IMAGE_REVIEW_REQUIRED",
        "approved_task_count": len(plan["tasks"]),
        "external_generation_count": output["external_generation_count"],
        "translation_model_call_count": output["translation_model_call_count"],
        "platform_writes": 0,
        "miaoshou_writes": 0,
        "product_center_mutated": False,
        "review_report": str(report_path),
    }


def retry_localized_brand_asset(
    offer_id: str,
    *,
    review_number: int,
    locale: str,
    failure_code: str,
    authorized_by: str = "",
    paid_context=None,
    rework_authorization_path: Path | None = None,
 runtime=None) -> dict[str, Any]:
    """Retry one exact localized asset with a durable, bounded attempt identity."""
    _runtime_root(runtime)
    from shared_platform.publication_paid_requests import require_paid_context
    paid_context = require_paid_context(paid_context)
    if paid_context.offer_id != str(offer_id):
        raise ValueError('paid context belongs to another product')
    paid_context.ensure_ready()

    from modules.sourcing.lingshi_client import LingshiClient
    from modules.sourcing.localized_image_lingshi_generation import (
        generate_localized_reference_image,
    )
    from shared_platform.publication_autopilot import load_autopilot_policy

    paid_context.planned_requests=1
    policy = paid_context.policy
    paid = policy["paid_models"]
    maximum = int(paid["maximum_automatic_paid_retries_per_task"])
    if paid.get("automatic_paid_retry") is not True or maximum != 3:
        raise ValueError("automatic paid retry policy is not active")
    if review_number < 1 or locale not in set().union(*BRAND_TRANSLATION_LOCALES.values()):
        raise ValueError("localized retry identity is invalid")
    if failure_code not in {"OCR_LANGUAGE", "FACTUAL_ALIGNMENT", "DUPLICATION"}:
        raise ValueError("localized retry failure code is not eligible")

    report_path = _brand_translation_report_path(str(offer_id), runtime=runtime)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (
        not isinstance(report, dict)
        or report.get("schema_version") != BRAND_TRANSLATION_REPORT_SCHEMA
        or str(report.get("offer_id") or "") != str(offer_id)
        or report.get("status") not in {"LOCALIZED_IMAGE_REVIEW_REQUIRED", "RUNNING"}
    ):
        raise ValueError("localized translation report is not retryable")

    task = next(
        (
            dict(row)
            for row in report.get("approved_tasks") or ()
            if isinstance(row, Mapping)
            and int(row.get("review_number") or 0) == review_number
            and str(row.get("locale") or "") == locale
        ),
        None,
    )
    if task is None:
        raise ValueError("localized retry target is outside the approved plan")
    assets = [dict(row) for row in report.get("assets") or () if isinstance(row, Mapping)]
    matches = [
        (index, row)
        for index, row in enumerate(assets)
        if int(row.get("source_review_number") or 0) == review_number
        and str(row.get("locale") or "") == locale
    ]
    if len(matches) != 1 or matches[0][1].get("status") != "COMPLETED":
        raise ValueError("localized retry requires one exact completed asset")
    asset_index, current_asset = matches[0]
    if current_asset.get("outcome_unknown") is True:
        raise ValueError("unknown paid outcome must be reconciled before retry")

    qa_path = report_path.parent / "automated-image-qa.json"
    try:
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        qa = {}
    failed_codes = {
        str(row.get("code") or "")
        for row in qa.get("checks") or ()
        if isinstance(row, Mapping) and row.get("status") == "FAILED"
    }
    user_authorized = str(authorized_by or "").strip() == "Kyle"
    if failure_code not in failed_codes and not user_authorized:
        raise ValueError("localized retry lacks exact failed QA evidence")
    if not user_authorized:
        from shared_platform.publication_rounds import canonical_digest
        unsigned_qa=dict(qa);qa_seal=unsigned_qa.pop('qa_digest',None)
        if (qa_seal!=canonical_digest(unsigned_qa) or qa.get('offer_id')!=str(offer_id)
                or qa.get('round1_snapshot_digest')!=paid_context.round1['snapshot_digest']
                or current_asset.get('artifact_digest') not in qa.get('artifact_digests',[])):
            raise ValueError('QA retry receipt does not bind this product, current approval and artifact')
        findings = qa.get('asset_findings') or []
        if not any(row.get('artifact_digest') == current_asset.get('artifact_digest')
                   and row.get('code') == failure_code for row in findings if isinstance(row,dict)):
            raise ValueError('QA retry requires an exact rejected artifact, not a product-level failed code')

    attempts = [
        dict(row)
        for row in report.get("retry_attempts") or ()
        if isinstance(row, Mapping)
    ]
    matching_attempts = [
        row
        for row in attempts
        if int(row.get("review_number") or 0) == review_number
        and str(row.get("locale") or "") == locale
    ]
    running = next(
        (row for row in reversed(matching_attempts) if row.get("status") == "RUNNING"),
        None,
    )
    attempt_number = (
        int(running.get("attempt_number") or 0)
        if running
        else len(matching_attempts) + 1
    )
    if attempt_number < 1 or attempt_number > maximum:
        raise ValueError("localized paid retry limit of three has been exhausted")

    inventories = {
        int(row.get("review_number") or 0): dict(row)
        for row in report.get("text_inventories") or ()
        if isinstance(row, Mapping)
    }
    inventory = inventories.get(review_number) or {}
    translations = (inventory.get("translations") or {}).get(locale)
    if not isinstance(translations, list) or not translations:
        raise ValueError("localized retry translation text is incomplete")
    from modules.sourcing.localized_image_auto_translation import (
        normalize_translations_for_locale,
    )

    translations = normalize_translations_for_locale(translations, locale)
    inventory_translations = dict(inventory.get("translations") or {})
    inventory_translations[locale] = translations
    inventory["translations"] = inventory_translations
    inventories[review_number] = inventory
    report["text_inventories"] = [inventories[key] for key in sorted(inventories)]

    if running is None:
        if paid_context.summary()['occupied'] >= paid_context.cap:
            from shared_platform.publication_paid_requests import PaidRequestBlocked
            raise PaidRequestBlocked('PAID_BUDGET_EXHAUSTED: old paid output remains retained; no replacement submitted')
        authorization = {'offer_id':str(offer_id), 'round1_snapshot_digest':paid_context.round1['snapshot_digest'],
            'old_artifact_digest':current_asset['artifact_digest'], 'old_task_id':current_asset['provider_task_id'],
            'review_number':review_number,'locale':locale,'failure_code':failure_code,
            'authorized_by':authorized_by if user_authorized else 'ACTIVE_AUTOPILOT_POLICY',
            'qa_digest':qa.get('qa_digest') if not user_authorized else None,
            'attempt':attempt_number}
        if rework_authorization_path is not None:
            supplied = json.loads(Path(rework_authorization_path).read_text(encoding='utf-8'))
            if any(supplied.get(key)!=value for key,value in authorization.items()):
                raise ValueError('existing rework intent is bound to another artifact or input')
            authorization['existing_intent_digest'] = _canonical_digest(supplied)
        basis = {'checkpoint_path':current_asset.get('checkpoint_path'),
            'old_artifact_digest':current_asset['artifact_digest'],'old_task_id':current_asset['provider_task_id'],
            'next_attempt':attempt_number,'reason':'user_requested_rework' if user_authorized else 'qa_rejected',
            'authorization_ref':f'audit://round2/{offer_id}/rework/{_canonical_digest(authorization)}',
            'authorization_sha256':_canonical_digest(authorization),
            'qa_digest':qa.get('qa_digest') if not user_authorized else None}
        running = {
            "review_number": review_number,
            "locale": locale,
            "attempt_number": attempt_number,
            "failure_code": failure_code,
            "qa_digest": str(qa.get("qa_digest") or ""),
            "authorized_by": "Kyle" if user_authorized else "ACTIVE_AUTOPILOT_POLICY",
            "status": "RUNNING",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "superseded_asset": current_asset,
            "rework_basis": basis,
        }
        attempts.append(running)
        report["retry_attempts"] = attempts
        report["status"] = "RUNNING"
        _write_json_atomic(report_path, report)

    source_bytes = _download_source(str(task.get("source_url") or ""))
    _verify_asset_bytes(source_bytes,task['source_artifact_digest'])
    client = runtime.client() if runtime is not None else LingshiClient.from_config(_runtime_root(runtime) / "config" / "lingshi.local.json")
    from modules.sourcing.brand_image_lingshi_generation import resolve_brand_image_model

    selected_image_model, _selected_image_model_detail = resolve_brand_image_model(client)
    generated = generate_localized_reference_image(
        source_url=str(task.get("source_url") or ""),
        source_bytes=source_bytes,
        locale=locale,
        translations=translations,
        checkpoint_dir=(report_path.parent / "brand-image-translation-checkpoints" if runtime is None
                        else runtime.checkpoint_directory(offer_id, 'brand-image-translation-checkpoints')),
        retry_attempt=attempt_number,
        model=selected_image_model,
        client=client,
        paid_context=paid_context, business_identity=_localized_business(offer_id,task),
        rework_basis=running['rework_basis'],
    )
    receipt = dict(generated.get("receipt") or {})
    replacement = {
        **current_asset,
        "status": "COMPLETED",
        "artifact_id": str(receipt.get("client_business_id") or ""),
        "artifact_digest": str(receipt.get("output_digest") or ""),
        "public_url": str(receipt.get("public_url") or ""),
        "provider": str(receipt.get("provider") or ""),
        "model": str(receipt.get("model") or ""),
        "provider_task_id": receipt.get("task_id"),
            "checkpoint_path": generated.get("checkpoint_path"),
        "cost": receipt.get("cost"),
        "channel_group": receipt.get("channel_group"),
        "outcome_unknown": bool(receipt.get("outcome_unknown")),
        "external_generation_count": int(receipt.get("external_generation_count") or 0),
        "retry_attempt": attempt_number,
        "supersedes_artifact_digest": str(current_asset.get("artifact_digest") or ""),
    }
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", replacement["artifact_digest"]) is None
        or not replacement["public_url"].startswith("https://")
        or replacement["external_generation_count"] != 1
        or replacement["outcome_unknown"]
    ):
        raise ValueError("localized retry receipt is incomplete")
    assets[asset_index] = replacement
    running.update(
        {
            "status": "COMPLETED",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "replacement_artifact_digest": replacement["artifact_digest"],
            "provider_task_id": replacement["provider_task_id"],
            "external_generation_count": 1,
        }
    )
    report["assets"] = assets
    report["retry_attempts"] = attempts
    report["paid_requests"] = paid_context.summary()
    report["external_generation_count"] = report['paid_requests']['new_this_invocation']
    report["status"] = "LOCALIZED_IMAGE_REVIEW_REQUIRED"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_json_atomic(report_path, report)
    return {
        "schema_version": "localized-image-retry-result/v1",
        "offer_id": str(offer_id),
        "status": "COMPLETED",
        "review_number": review_number,
        "locale": locale,
        "attempt_number": attempt_number,
        "maximum_attempts": maximum,
        "artifact_digest": replacement["artifact_digest"],
        "public_url": replacement["public_url"],
        "confirmed_paid_request_count": 1,
        "platform_writes": 0,
        "miaoshou_writes": 0,
    }


def _build_brand_image_execution_plan(
    offer_id: str,
    *,
    plan: Mapping[str, Any],
    reuse_plan: Mapping[str, Any],
    source_rows_by_position: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve approved Miaoshou reuse first, leaving only real generation gaps."""
    if reuse_plan.get("schema_version") != "brand-image-reuse-plan/v1":
        raise ValueError("brand image reuse plan schema is invalid")
    if str(reuse_plan.get("offer_id") or "") != str(offer_id):
        raise ValueError("brand image reuse plan offer_id does not match")
    if reuse_plan.get("status") != "APPROVED":
        raise ValueError("brand image reuse plan is not approved")
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in reuse_plan.get("items") or []:
        if not isinstance(raw, dict):
            continue
        key = (str(raw.get("brand_id") or "").strip(), str(raw.get("role") or "").strip())
        if not all(key) or key in decisions:
            raise ValueError("brand image reuse plan contains duplicate or invalid items")
        decisions[key] = dict(raw)

    task_specs: list[dict[str, Any]] = []
    reused_assets: list[dict[str, Any]] = []
    expected_keys: set[tuple[str, str]] = set()
    sequence = 0
    brands = [row for row in (plan.get("brand_plans") or []) if isinstance(row, dict)]
    for brand in brands:
        brand_id = str(brand.get("id") or "").strip()
        reference_rows = [
            dict(source_rows_by_position[position])
            for position in (brand.get("reference_positions") or [])
            if type(position) is int and position in source_rows_by_position
        ]
        for asset in brand.get("generated_assets") or []:
            if not isinstance(asset, dict) or int(asset.get("quantity") or 0) != 1:
                raise ValueError("brand image plan requires one final image per reviewed role")
            sequence += 1
            role = str(asset.get("role") or "").strip()
            key = (brand_id, role)
            expected_keys.add(key)
            decision = decisions.get(key)
            if decision is None:
                raise ValueError("brand image reuse plan does not cover every final image role")
            action = str(decision.get("decision") or "").strip().upper()
            if role == "cover" and action != "GENERATE":
                raise ValueError("cover image must always be generated")
            if action == "GENERATE":
                task_specs.append({
                    "sequence": sequence,
                    "brand": brand,
                    "asset": asset,
                    "reference_rows": reference_rows,
                })
                continue
            if action != "REUSE":
                raise ValueError("brand image reuse decision must be REUSE or GENERATE")
            try:
                source_position = int(decision.get("source_position") or 0)
            except (TypeError, ValueError):
                source_position = 0
            source = source_rows_by_position.get(source_position) or {}
            public_url = str(source.get("url") or "").strip()
            source_digest = str(decision.get("source_digest") or "").strip()
            if not public_url.startswith("https://") or re.fullmatch(r"sha256:[0-9a-f]{64}", source_digest) is None:
                raise ValueError("reused Miaoshou image lacks exact HTTPS and digest evidence")
            reused_assets.append({
                "sequence": sequence,
                "brand_id": brand_id,
                "brand_label": str(brand.get("label") or ""),
                "role": role,
                "status": "REUSED_FROM_MIAOSHOU",
                "source_position": source_position,
                "artifact_digest": source_digest,
                "public_url": public_url,
                "provider": "MIAOSHOU_EXISTING",
                "external_generation_count": 0,
            })
    if set(decisions) != expected_keys:
        raise ValueError("brand image reuse plan contains unbound image roles")
    return {
        "brands": brands,
        "task_specs": task_specs,
        "reused_assets": reused_assets,
        "final_image_count": sequence,
        "generation_count": len(task_specs),
        "reused_count": len(reused_assets),
    }


def generate_approved_brand_images(
    offer_id: str,
    *,
    image_provider: str = "lingshi",
    reuse_plan_path: Path | None = None,
    paid_context=None,
 runtime=None) -> dict[str, Any]:
    """Execute only phase 2A and stop for human review before translation."""
    _runtime_root(runtime)
    from shared_platform.publication_paid_requests import require_paid_context
    paid_context = require_paid_context(paid_context)
    if paid_context.offer_id != str(offer_id):
        raise ValueError('paid context belongs to another product')
    paid_context.ensure_ready()
    from modules.sourcing.brand_image_lingshi_generation import (
        QUALITY as LINGSHI_BRAND_QUALITY,
        SIZE as LINGSHI_BRAND_SIZE,
        generate_brand_image,
        resolve_brand_image_model,
    )
    from modules.sourcing.lingshi_client import LingshiClient
    from modules.sourcing.new_product_workbench import load_state

    image_provider = str(image_provider or "lingshi").strip().lower()
    if image_provider != "lingshi":
        raise ValueError("new Product Center image generation is locked to Lingshi AI")

    report_dir = _runtime_root(runtime) / "reports" / "product-preparation" / str(offer_id)
    checkpoint_directory = (report_dir / 'brand-image-checkpoints-lingshi' if runtime is None
                            else runtime.checkpoint_directory(offer_id, 'brand-image-checkpoints-lingshi'))
    from shared_platform.publication_rounds import validate_round2_input

    state = (load_state(str(offer_id)) if runtime is None else load_state(str(offer_id), state_dir=runtime.state_dir))
    round1_snapshot = (validate_round2_input(str(offer_id), state) if runtime is None else validate_round2_input(str(offer_id), state, reports_root=runtime.reports_root))
    plan = round1_snapshot.get("image_plan") or {}
    if plan.get("status") != "APPROVED":
        raise ValueError("brand image plan is not approved")
    if (plan.get("translation_plan") or {}).get("status") != "DEFERRED_UNTIL_ALL_IMAGES_GENERATED":
        raise ValueError("translation must remain deferred during brand generation")
    source_rows_by_position = {
        index: dict(row)
        for index, row in enumerate(((state.get("review") or {}).get("image_actions") or []), start=1)
        if isinstance(row, dict)
        and str(row.get("action") or "").strip().lower() != "remove"
        and str(row.get("url") or "").startswith("https://")
    }
    for position, row in _manual_public_reference_rows(str(offer_id), runtime=runtime).items():
        source_rows_by_position[position] = {
            "url": row["public_url"],
            "kind": "manual_public_reference",
            "action": "keep",
            "source_digest": "sha256:" + str(row["source_sha256"]),
            "artifact_digest": "sha256:" + str(row["artifact_sha256"]),
        }
    source = state.get("source") if isinstance(state.get("source"), dict) else {}
    source_mode = str(
        source.get("source_mode") or source.get("source_authority") or ""
    ).strip().casefold()
    required_reference_positions = {
        int(position)
        for brand in (plan.get("brand_plans") or [])
        if isinstance(brand, dict)
        for position in (brand.get("reference_positions") or [])
        if type(position) is int and position > 0
    }
    if source_mode in {"manual-intake", "manual_intake"}:
        source_rows_by_position.update(
            _prepare_manual_lingshi_references(str(offer_id), required_reference_positions, runtime=runtime)
        )
    missing_references = sorted(required_reference_positions - set(source_rows_by_position))
    if missing_references:
        raise ValueError(
            "approved source positions are missing: "
            + ", ".join(str(value) for value in missing_references)
        )
    selected_reuse_path = reuse_plan_path or _brand_reuse_plan_path(str(offer_id), runtime=runtime)
    if runtime is not None and reuse_plan_path is None:
        selected_reuse_path = runtime.retained_plan_path(selected_reuse_path)
    try:
        reuse_plan = json.loads(selected_reuse_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        reuse_plan = (_autopilot_reuse_plan(str(offer_id), plan) if runtime is None
                      else _autopilot_reuse_plan(str(offer_id), plan, runtime=runtime))
        _write_json_atomic(_brand_reuse_plan_path(str(offer_id), runtime=runtime), reuse_plan)
    execution = _build_brand_image_execution_plan(
        str(offer_id),
        plan=plan,
        reuse_plan=reuse_plan,
        source_rows_by_position=source_rows_by_position,
    )
    brands = execution["brands"]
    task_specs = execution["task_specs"]
    paid_context.planned_requests=len(task_specs)
    planned_roles = {
        str(brand.get("id") or ""): [
            str(asset.get("role") or "")
            for asset in (brand.get("generated_assets") or [])
            if isinstance(asset, Mapping)
        ]
        for brand in brands
    }
    brand_families = [_brand_family(brand_id) for brand_id in planned_roles]
    if (
        not 1 <= len(brands) <= 2
        or execution["final_image_count"] < len(brands)
        or any(family not in BRAND_TRANSLATION_LOCALES for family in brand_families)
        or len(brand_families) != len(set(brand_families))
        or any(not roles or len(roles) != len(set(roles)) for roles in planned_roles.values())
    ):
        raise ValueError("approved brand final set is incomplete or duplicated")
    source_bindings = {}
    for position in sorted(required_reference_positions):
        ref = source_rows_by_position[position]
        raw = Path(ref['local_path']).read_bytes() if ref.get('local_path') else _download_source(ref['url'])
        actual = 'sha256:' + hashlib.sha256(raw).hexdigest()
        if ref.get('source_digest') and ref['source_digest'] != actual:
            raise ValueError('approved reference source bytes drifted')
        ref['source_digest'] = actual
        source_bindings[str(position)] = {'url':ref.get('url'), 'source_digest':actual}
    paid_context.bind_plan('brand', {'image_plan':plan, 'reuse_plan':reuse_plan, 'sources':source_bindings})
    canonical_reuse_path = _brand_reuse_plan_path(str(offer_id), runtime=runtime)
    try:
        persisted_reuse = json.loads(canonical_reuse_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        persisted_reuse = None
    if persisted_reuse != reuse_plan:
        _write_json_atomic(canonical_reuse_path, reuse_plan)
    previous = _brand_generation_summary(str(offer_id), runtime=runtime) or {}
    completed_by_key = {
        f"{row.get('brand_id')}:{row.get('role')}": row
        for row in (previous.get("assets") or []) if isinstance(row, dict)
    }
    rework: dict[str, Any] = {}
    rework_path = _brand_rework_report_path(str(offer_id), runtime=runtime)
    if rework_path.is_file():
        loaded_rework = json.loads(rework_path.read_text(encoding="utf-8"))
        if isinstance(loaded_rework, dict) and loaded_rework.get("status") in {"ACTIVE", "RUNNING"}:
            rework = loaded_rework
    selected_rework_numbers = {
        int(value) for value in (rework.get("selected_review_numbers") or [])
    }
    if selected_rework_numbers:
        sequence_keys = {
            int(spec["sequence"]): f"{spec['brand']['id']}:{spec['asset']['role']}"
            for spec in task_specs
        }
        if not selected_rework_numbers.issubset(sequence_keys):
            raise ValueError("active brand image rework no longer matches the frozen image plan")
        for number in selected_rework_numbers:
            completed_by_key.pop(sequence_keys[number], None)
        rework["status"] = "RUNNING"
        rework["generation_started_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(rework_path, rework)
    current_generation_count = (
        len(selected_rework_numbers) if selected_rework_numbers else execution["generation_count"]
    )
    output = {
        "schema_version": "brand-image-generation/v1",
        "offer_id": str(offer_id),
        "status": "RUNNING",
        "phase": "2A_BRAND_IMAGE_GENERATION",
        "round1_snapshot_digest": round1_snapshot["snapshot_digest"],
        "translation_plan_status": "DEFERRED_UNTIL_ALL_IMAGES_GENERATED_AND_REVIEWED",
        "planned_image_count": execution["final_image_count"],
        "planned_brand_roles": planned_roles,
        "planned_generation_count": current_generation_count,
        "reused_image_count": execution["reused_count"],
        "assets": list(execution["reused_assets"]) + list(completed_by_key.values()),
        "external_generation_count": sum(
            int(row.get("external_generation_count") or 0)
            for row in completed_by_key.values()
        ),
        "platform_writes": 0,
        "miaoshou_writes": 0,
        "product_center_mutated": False,
        "started_at": previous.get("started_at") or datetime.now(timezone.utc).isoformat(),
        "provider": "lingshi-media/v1",
        "rework_attempt_number": rework.get("attempt_number"),
        "superseded_external_generation_count": len(rework.get("superseded_assets") or []),
    }
    _write_json_atomic(_brand_generation_report_path(str(offer_id), runtime=runtime), output)

    def stop_for_provider(error: Exception, *, failed_key: str = "") -> dict[str, Any]:
        message = str(error)
        safe_message = re.sub(r"https?://\S+", "[provider-url-redacted]", message)[:240]
        quota_blocked = any(
            marker in message.casefold()
            for marker in (
                "quota_not_enough",
                "quota is not enough",
                "recharge_required",
                "insufficient",
                "http 402",
            )
        )
        output["status"] = (
            "PROVIDER_QUOTA_REQUIRED" if quota_blocked else "PROVIDER_RECONCILIATION_REQUIRED"
        )
        output["blocker"] = (
            "灵识 AI 额度不足，缺失的付费生图任务尚未创建。"
            if quota_blocked
            else "灵识 AI 请求未完成，必须先核对供应商任务状态再续跑。"
        )
        output["next_action"] = (
            "充值灵识 AI 后使用相同批准命令续跑；已完成图片会按断点复用。"
            if quota_blocked
            else "先按持久化任务编号核对灵识 AI 状态；不得盲目重复创建。"
        )
        output["provider_error_code"] = "quota_not_enough" if quota_blocked else "provider_request_incomplete"
        output["provider_error_type"] = type(error).__name__
        output["provider_safe_error"] = safe_message
        output["failed_asset_key"] = failed_key
        output["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(_brand_generation_report_path(str(offer_id), runtime=runtime), output)
        return {
            "schema_version": "prepare-product-images-result/v1",
            "offer_id": str(offer_id),
            "status": output["status"],
            "execution_status": output["status"],
            "planned_brand_image_count": execution["final_image_count"],
            "completed_brand_image_count": len(output["assets"]),
            "external_generation_count": output["external_generation_count"],
            "blocker": output["blocker"],
            "next_action": output["next_action"],
            "platform_writes": 0,
            "miaoshou_writes": 0,
            "product_center_mutated": False,
            "review_report": str(_brand_generation_report_path(str(offer_id), runtime=runtime)),
        }

    client = runtime.client() if runtime is not None else LingshiClient.from_config(_runtime_root(runtime) / "config" / "lingshi.local.json")
    # Read-only connectivity, credential, model and pricing gates before the first paid POST.
    try:
        balance = client.balance()
        if not isinstance(balance, dict):
            raise ValueError("Lingshi provider preflight returned an invalid response")
        selected_model, model_detail = resolve_brand_image_model(client)
        pricing = client.model_pricing(selected_model, active_only=True)
        active_prices = [
            float(row.get("min_price") or row.get("base_price") or 0)
            for row in (pricing.get("channel_groups") or [])
            if isinstance(row, dict)
            and row.get("is_active") is True
            and row.get("in_key_whitelist") is True
            and float(row.get("min_price") or row.get("base_price") or 0) > 0
        ]
        output["provider_preview"] = {
            "provider": "灵识 AI",
            "endpoint": "/api/v1/media/generate",
            "model": selected_model,
            "model_display_name": model_detail.get("display_name"),
            "model_resolution_policy": "AVAILABLE_COMPATIBLE_ALIAS",
            "size": LINGSHI_BRAND_SIZE,
            "quality": LINGSHI_BRAND_QUALITY,
            "uploads_source_images": True,
            "planned_paid_task_count": current_generation_count,
            "balance_before": balance.get("balance"),
            "unit": balance.get("unit"),
            "active_unit_price_min": min(active_prices) if active_prices else None,
            "active_unit_price_max": max(active_prices) if active_prices else None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_atomic(_brand_generation_report_path(str(offer_id), runtime=runtime), output)
    except Exception as error:
        return stop_for_provider(error)
    listing_copy = state.get("listing_copy") if isinstance(state.get("listing_copy"), dict) else {}
    frozen_facts = (
        (round1_snapshot.get("fact_snapshot") or {}).get("product_facts") or {}
    )
    product_identity = str(
        json.dumps(frozen_facts, ensure_ascii=False, sort_keys=True)
    ).strip()
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def build_generated_row(spec: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        brand = spec["brand"]
        asset = spec["asset"]
        key = f"{brand['id']}:{asset['role']}"
        existing = completed_by_key.get(key)
        if isinstance(existing, dict) and existing.get("status") == "COMPLETED":
            _validate_cached_asset(existing,paid_context)
            return key, dict(existing)
        reference_rows = list(spec.get("reference_rows") or [])
        product_paths = [
            str(row.get("local_path") or "")
            for row in reference_rows
            if str(row.get("local_path") or "").strip()
        ]
        product_urls = [
            str(row.get("url") or "")
            for row in reference_rows
            if not str(row.get("local_path") or "").strip()
            and str(row.get("url") or "").startswith("https://")
        ]
        review_number = int(spec["sequence"])
        rework_selected = review_number in selected_rework_numbers
        composition_paths = (
            [str(value) for value in rework.get("composition_reference_paths") or []]
            if rework_selected
            else []
        )
        composition_urls = (
            [str(value) for value in rework.get("composition_reference_urls") or []]
            if rework_selected
            else []
        )
        selected_brief = str(asset["brief"])
        rework_basis = None
        if rework_selected:
            selected_brief = str((rework.get("briefs") or {}).get(str(review_number)) or "").strip()
            if not selected_brief:
                raise ValueError("active brand image rework brief is missing")
            prior = next(row for row in rework['superseded_assets'] if int(row['review_number'])==review_number)
            if not prior.get('checkpoint_path'):
                raise ValueError('legacy brand rework requires the exact old checkpoint binding')
            from modules.sourcing.image_generation_checkpoint import inspect_image_checkpoint
            old = inspect_image_checkpoint(prior['checkpoint_path'])
            rework_basis = {'checkpoint_path':prior['checkpoint_path'],'old_artifact_digest':prior['artifact_digest'],
                'old_task_id':prior['task_id'],'next_attempt':int(prior.get('retry_attempt') or 0)+1,
                'reason':'user_requested_rework' if rework['authorized_by']=='Kyle' else 'qa_rejected',
                'authorization_ref':f'audit://round2/{offer_id}/brand-rework/{rework["attempt_number"]}',
                'authorization_sha256':rework['rework_request_digest'],'qa_digest':rework.get('qa_digest')}
        url_digests={url:'sha256:'+hashlib.sha256(_download_source(url)).hexdigest()
                     for url in product_urls+composition_urls}
        frozen_urls={row['url']:row['source_digest'] for row in source_bindings.values() if row.get('url')}
        if any(url_digests[url]!=frozen_urls[url] for url in product_urls):
            raise ValueError('reference bytes changed after the technical plan was frozen')
        generated = generate_brand_image(
            offer_id=str(offer_id),
            brand_id=str(brand["id"]),
            brand_label=str(brand["label"]),
            positioning=str(brand["positioning"]),
            role=str(asset["role"]),
            brief=selected_brief,
            product_identity=product_identity,
            source_paths=product_paths + composition_paths,
            source_urls=product_urls + composition_urls,
            source_url_digests=url_digests,
            paid_context=paid_context,
            rework_basis=rework_basis,
            product_reference_count=len(product_paths) + len(product_urls),
            checkpoint_dir=(report_dir / "brand-image-checkpoints-lingshi" if runtime is None
                            else checkpoint_directory),
            client=client,
            model=selected_model,
        )
        receipt = generated["receipt"]
        return key, {
            "sequence": spec["sequence"],
            "brand_id": brand["id"],
            "brand_label": brand["label"],
            "role": asset["role"],
            "status": "COMPLETED",
            "artifact_id": generated["artifact_id"],
            "artifact_path": generated["local_path"],
            "artifact_digest": receipt["output_digest"],
            "public_url": receipt["public_url"],
            "provider": receipt["provider"],
            "model": receipt["model"],
            "task_id": receipt["task_id"],
            "checkpoint_path": generated['checkpoint_path'],
            "retry_attempt": receipt['retry_attempt'],
            "cost": receipt.get("cost"),
            "channel_group": receipt.get("channel_group"),
            "external_generation_count": 1,
        }

    pending_specs = [
        spec
        for spec in task_specs
        if not (
            isinstance(completed_by_key.get(
                f"{spec['brand']['id']}:{spec['asset']['role']}"
            ), dict)
            and completed_by_key[
                f"{spec['brand']['id']}:{spec['asset']['role']}"
            ].get("status") == "COMPLETED"
        )
    ]
    output["max_parallel_tasks"] = (1 if runtime is not None else min(3, max(1, len(pending_specs))))
    failures: list[tuple[str, Exception]] = []
    with (runtime.generation_executor() if runtime is not None
          else ThreadPoolExecutor(max_workers=output["max_parallel_tasks"])) as executor:
        future_specs = {executor.submit(build_generated_row, spec): spec for spec in pending_specs}
        for future in as_completed(future_specs):
            spec = future_specs[future]
            fallback_key = f"{spec['brand']['id']}:{spec['asset']['role']}"
            try:
                key, row = future.result()
            except Exception as error:
                failures.append((fallback_key, error))
                continue
            output["assets"] = [
                item for item in output["assets"]
                if f"{item.get('brand_id')}:{item.get('role')}" != key
            ]
            output["assets"].append(row)
            output["assets"].sort(key=lambda item: int(item.get("sequence") or 0))
            output["external_generation_count"] = sum(
                int(item.get("external_generation_count") or 0) for item in output["assets"]
            )
            _write_json_atomic(_brand_generation_report_path(str(offer_id), runtime=runtime), output)

    if failures:
        failed_key, error = failures[0]
        output["provider_failure_count"] = len(failures)
        return stop_for_provider(error, failed_key=failed_key)

    for key, row in completed_by_key.items():
        output["assets"] = [
            item for item in output["assets"]
            if f"{item.get('brand_id')}:{item.get('role')}" != key
        ]
        output["assets"].append(row)
        output["assets"].sort(key=lambda item: int(item.get("sequence") or 0))
        output["external_generation_count"] = sum(
            int(item.get("external_generation_count") or 0) for item in output["assets"]
        )
        _write_json_atomic(_brand_generation_report_path(str(offer_id), runtime=runtime), output)
    output = _number_translation_review_assets(output)
    output["status"] = "BRAND_IMAGE_REVIEW_REQUIRED"
    output["completed_at"] = datetime.now(timezone.utc).isoformat()
    output["next_action"] = "请按图片编号审核最终图片和翻译初步意见；确认后再冻结翻译位置和目标语言。"
    _write_json_atomic(_brand_generation_report_path(str(offer_id), runtime=runtime), output)
    if selected_rework_numbers:
        replacement_by_number = {
            int(row.get("sequence") or 0): dict(row)
            for row in output.get("assets") or []
            if isinstance(row, Mapping)
        }
        rework["status"] = "BRAND_IMAGE_REVIEW_REQUIRED"
        rework["replacement_assets"] = [
            replacement_by_number[number] for number in sorted(selected_rework_numbers)
        ]
        rework["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(rework_path, rework)
    return {
        "schema_version": "prepare-product-images-result/v1",
        "offer_id": str(offer_id),
        "status": "BRAND_IMAGE_REVIEW_REQUIRED",
        "execution_status": "BRAND_IMAGE_REVIEW_REQUIRED",
        "planned_brand_image_count": execution["final_image_count"],
        "generated_brand_image_count": execution["generation_count"],
        "reused_brand_image_count": execution["reused_count"],
        "completed_brand_image_count": len(output["assets"]),
        "external_generation_count": output["external_generation_count"],
        "translation_plan_status": output["translation_plan_status"],
        "platform_writes": 0,
        "miaoshou_writes": 0,
        "product_center_mutated": False,
        "review_report": str(_brand_generation_report_path(str(offer_id), runtime=runtime)),
    }


def _deferred_dual_brand_translation(offer_id: str, *, runtime=None) -> dict[str, Any] | None:
    """Return the generation-first gate recorded by the first-round packet."""
    _runtime_root(runtime)
    path = _runtime_root(runtime) / "reports" / "product-preparation" / str(offer_id) / "first-review.json"
    try:
        packet = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None
    plan = packet.get("image_execution_plan") if isinstance(packet, dict) else None
    if not isinstance(plan, dict):
        return None
    translation = plan.get("translation_plan")
    brands = plan.get("brand_plans")
    if (
        not isinstance(translation, dict)
        or translation.get("status") != "DEFERRED_UNTIL_ALL_IMAGES_GENERATED"
        or not isinstance(brands, list)
        or not 1 <= len(brands) <= 2
    ):
        return None
    planned_count = sum(
        max(0, int(asset.get("quantity") or 0))
        for brand in brands if isinstance(brand, dict)
        for asset in (brand.get("generated_assets") or []) if isinstance(asset, dict)
    )
    reuse_ready = _brand_reuse_plan_path(str(offer_id), runtime=runtime).is_file()
    manual_source_public_asset_required = False
    if reuse_ready:
        try:
            from modules.sourcing.new_product_workbench import load_state

            state = (load_state(str(offer_id)) if runtime is None else load_state(str(offer_id), state_dir=runtime.state_dir))
            source = state.get("source") if isinstance(state.get("source"), dict) else {}
            source_mode = str(
                source.get("source_mode") or source.get("source_authority") or ""
            ).strip().casefold()
            review = state.get("review") if isinstance(state.get("review"), dict) else {}
            public_positions = {
                index
                for index, row in enumerate(review.get("image_actions") or [], start=1)
                if isinstance(row, dict)
                and str(row.get("action") or "").strip().lower() != "remove"
                and str(row.get("url") or "").startswith("https://")
            }
            public_positions.update(_manual_public_reference_rows(str(offer_id), runtime=runtime))
            required_positions = {
                int(position)
                for brand in brands
                if isinstance(brand, dict)
                for position in (brand.get("reference_positions") or [])
                if type(position) is int and position > 0
            }
            manual_source_public_asset_required = bool(
                source_mode in {"manual-intake", "manual_intake"}
                and required_positions - public_positions
            )
        except (FileNotFoundError, OSError, TypeError, ValueError):
            manual_source_public_asset_required = True
    # Lingshi accepts the digest-bound local image as an inline reference, so
    # manual intake no longer depends on a separate public upload provider.
    manual_source_public_asset_required = False
    status = (
        "MANUAL_SOURCE_PUBLIC_ASSET_REQUIRED"
        if manual_source_public_asset_required
        else "BRAND_IMAGE_GENERATION_REQUIRED"
        if reuse_ready
        else "MIAOSHOU_IMAGE_REUSE_REVIEW_REQUIRED"
    )
    result = {
        "schema_version": "prepare-product-images-result/v1",
        "offer_id": str(offer_id),
        "status": status,
        "execution_status": status,
        "brand_plan_count": len(brands),
        "planned_brand_image_count": planned_count,
        "translation_plan_status": "DEFERRED_UNTIL_ALL_IMAGES_GENERATED",
        "next_action": (
            "先为本地来源图建立摘要匹配的公共 HTTPS 参考资产；确认上传前不调用付费生图。"
            if manual_source_public_asset_required
            else "先生成并审核全部双品牌新图，再提交翻译图片与目标语言计划。"
        ),
        "paid_task_count": 0,
        "external_generation_count": 0,
        "platform_writes": 0,
        "product_center_mutated": False,
    }
    _write_json_atomic(
        _brand_preflight_report_path(str(offer_id), runtime=runtime),
        {
            **result,
            "schema_version": "brand-image-preflight/v1",
            "offer_id": str(offer_id),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return result


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        if not str(new_url or "").startswith("https://"):
            raise ValueError("localized image source redirected outside HTTPS")
        return super().redirect_request(request, fp, code, msg, headers, new_url)


def _download_source(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("localized image source must use HTTPS")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "OrbitProductImages/1.0", "Accept": "image/*"},
    )
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    with opener.open(request, timeout=45) as response:
        final_url = str(response.geturl() or "")
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if not final_url.startswith("https://") or not content_type.startswith("image/"):
            raise ValueError("localized image source response is invalid")
        data = response.read(MAX_SOURCE_BYTES + 1)
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError("localized image source size is invalid")
    return data


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)






























def _result(
    summary: dict[str, Any], *, executed: bool, handoff: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    review = summary.get("review") or {}
    tasks = review.get("tasks") or []
    statuses = [str(row.get("status") or "") for row in tasks if isinstance(row, dict)]
    ready = all(status == "READY_FOR_REVIEW" for status in statuses)
    approved = review.get("status") == "APPROVED"
    approval_recorded = bool(review.get("approval_intent") or review.get("approval"))
    miaoshou_sync = review.get("miaoshou_pre_review_sync") or {}
    execution_status = (
        "ROUND2_IMAGES_READY"
        if ready or approved or approval_recorded
        else "PAID_CONFIRMATION_REQUIRED"
    )
    return {
        "schema_version": "prepare-product-images-result/v1",
        "offer_id": str(summary.get("offer_id") or ""),
        "status": execution_status,
        "approval_status": "APPROVED" if approval_recorded else "PENDING",
        "execution_status": execution_status,
        "input_schema_version": review.get("input_schema_version"),
        "input_digest": review.get("approved_snapshot_digest"),
        "review_revision": review.get("revision"),
        "selected_positions": review.get("selected_positions") or [],
        "route_locales": review.get("route_locales") or {},
        "paid_task_count": len(tasks),
        "external_generation_count": int(review.get("external_generation_count") or 0),
        "miaoshou_external_write_count": 0,
        "legacy_miaoshou_pre_review_sync_present": bool(miaoshou_sync),
        "paid_generation_executed": executed,
        "product_center_mutated": bool(review.get("product_center_mutated")),
        "platform_writes": int(review.get("platform_writes") or 0),
        "handoff": dict(handoff) if handoff else None,
        "review_url": (
            "http://127.0.0.1:8765/new-product?offer_id="
            f"{summary.get('offer_id')}"
            if tasks else None
        ),
        "result_url": (
            "http://127.0.0.1:8765/localized-image-review?offer_id="
            f"{summary.get('offer_id')}"
            if tasks else None
        ),
    }


def _localized_business(offer_id: str, task: Mapping[str, Any]) -> dict[str, Any]:
    return {'offer_id':str(offer_id), 'brand_id':task['brand_id'], 'role':task['role'], 'locale':task['locale']}


def _validate_cached_asset(row: Mapping[str,Any], context) -> None:
    """A report cache is reusable only with its current complete local checkpoint."""
    from modules.sourcing.image_generation_checkpoint import ImageCheckpoint,business_lock,digest
    if not str(row.get('provider') or '').startswith('lingshi'):
        if row.get('external_generation_count')==0:return
        raise ValueError('legacy paid result needs its original provider recovery bridge')
    path=Path(str(row.get('checkpoint_path') or '')).resolve()
    if not path.is_file() or not path.is_relative_to(context.directory):
        raise ValueError('cached image lacks its exact local checkpoint binding')
    cp=ImageCheckpoint.from_path(path)
    business={'offer_id':context.offer_id,'brand_id':row['brand_id'],'role':row['role']}
    if row.get('locale'):business['locale']=row['locale']
    if cp.business_digest!=digest({'kind':cp.kind,**business}):raise ValueError('cached image business identity drifted')
    with business_lock(cp.root,cp.business_digest):
        state=cp.open_for_execution()
        if (state['status']!='COMPLETED' or state.get('task_id')!=(row.get('provider_task_id') or row.get('task_id'))
                or state.get('output_digest')!=str(row.get('artifact_digest') or '').removeprefix('sha256:')
                or hashlib.sha256(cp.output_path.read_bytes()).hexdigest()!=state.get('output_digest')):
            raise ValueError('cached image receipt or artifact differs from its checkpoint')


def _verify_asset_bytes(raw: bytes, expected_digest: str) -> None:
    from modules.sourcing.localized_image_lingshi_generation import _png_bytes
    if 'sha256:'+hashlib.sha256(_png_bytes(raw)).hexdigest() != expected_digest:
        raise ValueError('approved source artifact bytes drifted')


def _image_status_result(offer_id, round1, generation, translated_path):
    """The original report-only status projection; no referenced history is read."""
    if translated_path.is_file():
        report=json.loads(translated_path.read_text(encoding='utf-8'))
        return {'offer_id':str(offer_id),'status':report['status'],'platform_writes':0,'external_generation_count':0}
    return {'offer_id':str(offer_id),'status':generation['status'] if generation else 'BRAND_IMAGE_GENERATION_REQUIRED',
            'planned_brand_image_count':sum(len(b.get('generated_assets') or []) for b in round1['image_plan'].get('brand_plans') or []),
            'completed_brand_image_count':len((generation or {}).get('assets') or []),'platform_writes':0,'external_generation_count':0}


def _checked_status_binding(args, *, runtime=None, arguments=None):
    profile=getattr(args,'binding_profile',None)
    supplied=getattr(args,'binding_profile_sha256',None)
    if profile is None and supplied is None:
        return None
    if profile is None or supplied is None or runtime is not None:
        raise ValueError('IMAGES_FROZEN_STATUS_BINDING_REQUIRED')
    if hashlib.sha256(profile.read_bytes()).hexdigest() != supplied:
        raise ValueError('IMAGES_PROFILE_DIGEST_CHANGED')
    from scripts.repo_bound_agent_entry import check_binding, check_arguments
    bound=check_binding(profile,'images')
    if bound.get('entry_mode') != 'images-captured-status' or Path(bound['source_root']) != REPO_ROOT:
        raise ValueError('IMAGES_SOURCE_BINDING_MISMATCH')
    if bound['profile_sha256'] != supplied:
        raise ValueError('IMAGES_PROFILE_DIGEST_CHANGED')
    defaults={'image_provider':'lingshi','approved_by':'orbit-product-publication-default-v1',
        'retry_failure_code':'OCR_LANGUAGE','rework_authorized_by':'','retry_authorized_by':''}
    for name,value in vars(args).items():
        if name not in ('offer_id','binding_profile','binding_profile_sha256') and value != defaults.get(name):
            if value is not False:
                raise ValueError('IMAGES_CAPTURED_STATUS_MODE_ONLY')
    if arguments is not None:
        # The internal frozen-profile route also rejects explicit defaults and abbreviations.
        seen=set(); index=0
        while index<len(arguments):
            option,separator,_value=arguments[index].partition('=')
            if option not in ('--offer-id','--binding-profile','--binding-profile-sha256') or option in seen:
                raise ValueError('IMAGES_CAPTURED_STATUS_MODE_ONLY')
            seen.add(option); index += 1 if separator else 2
        if seen != {'--offer-id','--binding-profile','--binding-profile-sha256'}:
            raise ValueError('IMAGES_FROZEN_STATUS_BINDING_REQUIRED')
    check_arguments(bound,'images',['--offer-id',str(args.offer_id)])
    return bound


def _captured_status_inputs(offer_id, binding):
    from scripts.repo_bound_agent_entry import checked_path
    from modules.sourcing.new_product_workbench import load_state
    from shared_platform.publication_rounds import validate_round2_input
    for name,path in (
        ('STATE',Path(binding['state_dir'])/(offer_id+'.json')),
        ('R1',Path(binding['round1_reports_root'])/offer_id/'round1-approved-snapshot.json'),
        ('R2_GENERATION',_brand_generation_report_path(offer_id,reports_root=binding['r2_reports_root']))):
        checked_path(str(path),'IMAGES_CAPTURED_'+name)
    state=load_state(offer_id,state_dir=Path(binding['state_dir']))
    round1=validate_round2_input(offer_id,state,reports_root=Path(binding['round1_reports_root']))
    generation=_brand_generation_summary(offer_id,reports_root=binding['r2_reports_root'])
    if not generation or not isinstance(generation.get('status'),str) or not generation['status']:
        raise ValueError('IMAGES_CAPTURED_R2_GENERATION_INVALID')
    translated=_brand_translation_report_path(offer_id,reports_root=binding['r2_reports_root'])
    if translated.exists() or translated.is_symlink():
        checked_path(str(translated),'IMAGES_CAPTURED_R2_TRANSLATION')
        report=json.loads(translated.read_text(encoding='utf-8'))
        if not isinstance(report,dict) or not isinstance(report.get('status'),str) or not report['status']:
            raise ValueError('IMAGES_CAPTURED_R2_TRANSLATION_INVALID')
    return round1,generation,translated


def _run_captured_status(args, binding, *, runtime=None):
    if runtime is not None or _checked_status_binding(args) != binding:
        raise ValueError('IMAGES_FROZEN_STATUS_BINDING_REQUIRED')
    from scripts.repo_bound_agent_entry import checked_path
    from modules.sourcing.image_generation_checkpoint import business_lock, digest
    offer_id=str(args.offer_id)
    # Missing/invalid captured inputs stop before even creating a phase lock.
    _captured_status_inputs(offer_id,binding)
    phase_root=Path(binding['phase_lock_root'])/offer_id
    phase_digest=digest({'scope':'round2-phase','offer_id':offer_id})
    lock_path=phase_root/f'.lingshi-{phase_digest[:24]}.lock'
    if lock_path.exists() or lock_path.is_symlink():
        checked_path(str(lock_path),'IMAGES_PHASE_LOCK')
    with business_lock(phase_root,phase_digest):
        if _checked_status_binding(args) != binding:
            raise ValueError('IMAGES_FROZEN_BINDING_CHANGED')
        round1,generation,translated=_captured_status_inputs(offer_id,binding)
        if (round1.get('image_plan',{}).get('translation_plan') or {}).get('status') != 'DEFERRED_UNTIL_ALL_IMAGES_GENERATED':
            return {'offer_id':offer_id,'status':'LEGACY_R2_BRIDGE_REQUIRED',
                'next_action':'Bind the existing approved legacy review to R2 paid context; original provider receipts are retained.',
                'platform_writes':0,'external_generation_count':0}
        return _image_status_result(offer_id,round1,generation,translated)


def run(args: argparse.Namespace, *, runtime=None, binding=None) -> dict[str, Any]:
    if binding is not None:
        return _run_captured_status(args,binding,runtime=runtime)
    _runtime_root(runtime)
    if runtime is not None:
        runtime.require_offer(args.offer_id)
    from modules.sourcing.image_generation_checkpoint import atomic_json, business_lock, digest
    from shared_platform.publication_paid_requests import load_paid_context
    if any(getattr(args, flag, False) for flag in ('execute_miaoshou', 'finalize_release_handoff', 'approve_all')):
        raise ValueError('Miaoshou, publication bridge and ReleasePlan belong to round 3; use publish-approved-product')
    if getattr(args, 'prepare_manual_public_reference', False):
        raise ValueError('separate public upload is disabled; approved generation uses digest-bound inline references')
    if not str(args.offer_id).isdigit():
        raise ValueError('canonical numeric Product Center offer_id is required')
    paid_flags = [bool(getattr(args,'execute_brand_generation',False)), bool(getattr(args,'execute_paid',False)),
                  getattr(args,'retry_localized_review_number',None) is not None]
    if sum(paid_flags)>1:
        raise ValueError('run one paid phase at a time')
    from modules.sourcing.new_product_workbench import load_state
    from shared_platform.publication_rounds import validate_round2_input
    state = (load_state(str(args.offer_id)) if runtime is None else load_state(str(args.offer_id), state_dir=runtime.state_dir))
    round1 = (validate_round2_input(str(args.offer_id), state) if runtime is None else validate_round2_input(str(args.offer_id), state, reports_root=runtime.reports_root))
    directory = _runtime_root(runtime)/'reports/product-preparation'/str(args.offer_id)
    context = None
    if any(paid_flags):
        context = runtime.paid_context if runtime is not None else load_paid_context(offer_id=str(args.offer_id), round1=round1, repo_root=_runtime_root(runtime),
            policy_path=getattr(args,'paid_policy',None), usage_baseline_path=getattr(args,'usage_baseline',None))
    with business_lock(directory, digest({'scope':'round2-phase','offer_id':str(args.offer_id)})):
        if getattr(args,'prepare_brand_rework',False):
            if any(paid_flags) or getattr(args,'approve_translation_images',None) is not None:
                raise ValueError('prepare rework as one local action')
            return prepare_brand_image_rework(args.offer_id, request_path=Path(args.rework_request),
                                              authorized_by=str(args.rework_authorized_by), runtime=runtime)
        if (round1.get('image_plan',{}).get('translation_plan') or {}).get('status') != 'DEFERRED_UNTIL_ALL_IMAGES_GENERATED':
            return {'offer_id':str(args.offer_id), 'status':'LEGACY_R2_BRIDGE_REQUIRED',
                    'next_action':'Bind the existing approved legacy review to R2 paid context; original provider receipts are retained.',
                    'platform_writes':0, 'external_generation_count':0}
        if context is not None:
            context.ensure_ready()
            project_reports=True
            try:
                if paid_flags[0]:
                    result = generate_approved_brand_images(args.offer_id, image_provider=getattr(args,'image_provider','lingshi'),
                                reuse_plan_path=getattr(args,'reuse_plan',None), paid_context=context, runtime=runtime)
                elif paid_flags[1]:
                    result = generate_approved_brand_translations(args.offer_id, image_provider=getattr(args,'image_provider','lingshi'),
                                translation_plan_path=getattr(args,'translation_plan',None), paid_context=context, runtime=runtime)
                else:
                    result = retry_localized_brand_asset(args.offer_id, review_number=int(args.retry_localized_review_number),
                        locale=str(args.retry_locale), failure_code=str(args.retry_failure_code),
                        authorized_by=str(getattr(args,'retry_authorized_by','')), paid_context=context,
                        rework_authorization_path=getattr(args,'rework_authorization',None), runtime=runtime)
            except Exception as error:
                if 'TECHNICAL_PLAN_DRIFT' in str(error):project_reports=False
                raise
            finally:
                accounting = context.summary()
                atomic_json(directory/'paid-request-summary.json', accounting)
                for name in ('brand-image-generation.json','brand-image-translation.json') if project_reports else ():
                    path=directory/name
                    if path.is_file():
                        report=json.loads(path.read_text(encoding='utf-8'))
                        report['paid_requests']=accounting
                        report['completed_asset_generation_count']=sum(int(row.get('external_generation_count') or 0) for row in report.get('assets') or [])
                        report['external_generation_count']=accounting['new_this_invocation']
                        atomic_json(path,report)
            return {**result, 'paid_requests':accounting, 'external_generation_count':accounting['new_this_invocation'],
                    'confirmed_paid_request_count':accounting['confirmed'], 'new_paid_request_count':accounting['new_this_invocation']}
        generation = _brand_generation_summary(args.offer_id, runtime=runtime)
        if generation and getattr(args,'approve_translation_images',None) is not None:
            plan=freeze_conversation_approved_translation_scope(args.offer_id,generation=generation,
                selected_review_numbers=_parse_review_numbers(args.approve_translation_images),
                dimension_only_numbers=_parse_review_numbers(getattr(args,'dimension_only_images',None)), approved_by=args.approved_by, runtime=runtime)
            return {'offer_id':str(args.offer_id),'status':'TRANSLATION_SCOPE_APPROVED','approved_task_count':plan['approved_task_count'],
                    'platform_writes':0,'external_generation_count':0,'translation_plan':str(_brand_translation_plan_path(args.offer_id, runtime=runtime))}
        translated_path=_brand_translation_report_path(args.offer_id, runtime=runtime)
        return _image_status_result(args.offer_id,round1,generation,translated_path)


def main( *, runtime=None) -> int:
    _runtime_root(runtime)
    parser = argparse.ArgumentParser(
        description="Prepare the approved second-round localized image review."
    )
    parser.add_argument("--offer-id", required=True)
    parser.add_argument('--paid-policy', type=Path, help='Existing applicable policy; no default active policy is shipped.')
    parser.add_argument('--usage-baseline', type=Path, help='Upper-layer verified all-product prior usage binding.')
    parser.add_argument('--rework-authorization', type=Path, help='Existing user intent or exact QA rework evidence.')
    parser.add_argument("--execute-paid", action="store_true")
    parser.add_argument("--execute-brand-generation", action="store_true")
    parser.add_argument("--prepare-brand-rework", action="store_true")
    parser.add_argument(
        "--rework-request",
        type=Path,
        help="Audit-bound brand-image-rework-request/v1 for final-review replacements.",
    )
    parser.add_argument("--rework-authorized-by", default="")
    parser.add_argument("--retry-localized-review-number", type=int)
    parser.add_argument("--retry-locale", choices=tuple(sorted(set().union(*BRAND_TRANSLATION_LOCALES.values()))))
    parser.add_argument(
        "--retry-failure-code",
        choices=("OCR_LANGUAGE", "FACTUAL_ALIGNMENT", "DUPLICATION"),
        default="OCR_LANGUAGE",
    )
    parser.add_argument("--retry-authorized-by", default="")
    parser.add_argument(
        "--approve-translation-images",
        help="Approved numbered master images, for example 7,14; use 'none' for an audited zero-task plan.",
    )
    parser.add_argument(
        "--dimension-only-images",
        help="Reviewed size-comparison images containing only dimensions and no translatable prose.",
    )
    parser.add_argument("--prepare-manual-public-reference", action="store_true")
    parser.add_argument("--confirm-public-upload", action="store_true")
    parser.add_argument(
        "--reuse-plan",
        type=Path,
        help="Approved brand-image-reuse-plan/v1 built from the read-only Miaoshou image audit.",
    )
    parser.add_argument(
        "--translation-plan",
        type=Path,
        help="Approved brand-image-translation-plan/v1 bound to the reviewed brand-image set.",
    )
    parser.add_argument(
        "--image-provider",
        choices=("lingshi",),
        default="lingshi",
        help="Approved paid image provider for brand and localized image tasks.",
    )
    parser.add_argument("--confirm-paid-generation", action="store_true")
    parser.add_argument("--approve-all", action="store_true")
    parser.add_argument("--approved-by", default="orbit-product-publication-default-v1")
    parser.add_argument("--execute-miaoshou", action="store_true")
    parser.add_argument("--confirm-miaoshou-write", action="store_true")
    parser.add_argument("--finalize-release-handoff", action="store_true")
    parser.add_argument(
        "--uploaded-assets",
        type=Path,
        help="Optional durable JSON mapping for legacy generated artifacts without a provider result URL.",
    )
    parser.add_argument('--binding-profile',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--binding-profile-sha256',help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        binding = _checked_status_binding(args,runtime=runtime,arguments=sys.argv[1:])
        result = run(args, runtime=runtime, binding=binding)
    except Exception as error:
        print(
            json.dumps(
                {
                    "schema_version": "prepare-product-images-result/v1",
                    "offer_id": str(args.offer_id),
                    "status": "FAILED",
                    "error": str(error),
                    "platform_writes": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
