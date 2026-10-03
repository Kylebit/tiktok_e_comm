"""Fail-closed round-2 image QA bound to exact generated artifacts.

The provider assessment is evidence, not authority.  This module independently
checks role coverage, artifact identity and localization routing, then binds the
three visual judgements to the same complete digest set.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from shared_platform.publication_rounds import canonical_digest


QA_SCHEMA = "automated-image-qa/v1"
ASSESSMENT_SCHEMA = "lingshi-image-qa-assessment/v1"
REQUIRED_VISUAL_CHECKS = ("FACTUAL_ALIGNMENT", "OCR_LANGUAGE", "DUPLICATION")


class PublicationImageQAError(ValueError):
    pass


def _rows(value: object, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    return [dict(row) for row in value.get(key) or () if isinstance(row, Mapping)]


def _artifact_digests(*documents: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for document in documents:
        for row in _rows(document, "assets"):
            digest = str(row.get("artifact_digest") or "")
            if digest.startswith("sha256:"):
                values.append(digest)
    return sorted(values)


def build_automated_image_qa(
    *,
    round1_snapshot: Mapping[str, Any],
    generation: Mapping[str, Any],
    translation: Mapping[str, Any],
    visual_assessment: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a PASS receipt only when local and provider evidence agree exactly."""

    offer_id = str(round1_snapshot.get("offer_id") or "")
    round1_digest = str(round1_snapshot.get("snapshot_digest") or "")
    if not offer_id.isdigit() or not round1_digest.startswith("sha256:"):
        raise PublicationImageQAError("round-1 snapshot identity is invalid")
    generated = _rows(generation, "assets")
    localized = _rows(translation, "assets")
    expected_count = sum(
        int(asset.get("quantity") or 0)
        for plan in (round1_snapshot.get("image_plan") or {}).get("brand_plans") or ()
        if isinstance(plan, Mapping)
        for asset in plan.get("generated_assets") or ()
        if isinstance(asset, Mapping)
    )
    generated_keys = [
        (str(row.get("brand_id") or ""), str(row.get("role") or ""))
        for row in generated
    ]
    role_coverage = (
        expected_count > 0
        and len(generated) == expected_count
        and len(generated_keys) == len(set(generated_keys))
        and all(row.get("status") == "COMPLETED" for row in generated)
    )

    tasks = _rows(translation, "approved_tasks")
    localized_keys = {
        (
            row.get("source_review_number"),
            str(row.get("brand_id") or ""),
            str(row.get("role") or ""),
            str(row.get("locale") or ""),
        )
        for row in localized
        if row.get("status") == "COMPLETED"
    }
    task_keys = {
        (
            row.get("review_number"),
            str(row.get("brand_id") or ""),
            str(row.get("role") or ""),
            str(row.get("locale") or ""),
        )
        for row in tasks
    }
    target_routing = task_keys == localized_keys and len(task_keys) == len(tasks)

    artifact_digests = _artifact_digests(generation, translation)
    assessment = deepcopy(dict(visual_assessment))
    supplied_assessment_digest = str(assessment.pop("assessment_digest", ""))
    assessment_ok = supplied_assessment_digest == canonical_digest(assessment)
    assessment["assessment_digest"] = supplied_assessment_digest
    raw_visual_checks = assessment.get("checks") or ()
    if isinstance(raw_visual_checks, Mapping):
        visual_rows = {
            str(code): dict(row)
            for code, row in raw_visual_checks.items()
            if isinstance(row, Mapping)
        }
    else:
        visual_rows = {
            str(row.get("code") or row.get("name") or row.get("type") or ""): dict(row)
            for row in raw_visual_checks
            if isinstance(row, Mapping)
        }
    assessment_binding_ok = (
        assessment_ok
        and assessment.get("schema_version") == ASSESSMENT_SCHEMA
        and str(assessment.get("offer_id") or "") == offer_id
        and list(assessment.get("artifact_digests") or ()) == artifact_digests
    )

    def visual_status(code: str) -> str:
        if not assessment_binding_ok:
            return "FAILED"
        return (
            "PASSED"
            if visual_rows.get(code, {}).get("status") == "PASSED"
            else "FAILED"
        )

    checks = [
        {"code": "ROLE_COVERAGE", "status": "PASSED" if role_coverage else "FAILED"},
        {
            "code": "FACTUAL_ALIGNMENT",
            "status": visual_status("FACTUAL_ALIGNMENT"),
            "evidence": supplied_assessment_digest,
        },
        {
            "code": "OCR_LANGUAGE",
            "status": visual_status("OCR_LANGUAGE"),
            "evidence": supplied_assessment_digest,
        },
        {
            "code": "DUPLICATION",
            "status": visual_status("DUPLICATION"),
            "evidence": supplied_assessment_digest,
        },
        {"code": "TARGET_ROUTING", "status": "PASSED" if target_routing else "FAILED"},
    ]
    status = "PASSED" if all(row["status"] == "PASSED" for row in checks) else "FAILED"
    receipt: dict[str, Any] = {
        "schema_version": QA_SCHEMA,
        "status": status,
        "offer_id": offer_id,
        "round1_snapshot_digest": round1_digest,
        "generation_identity_digest": str(
            translation.get("generation_identity_digest") or ""
        ),
        "generated_asset_count": len(generated),
        "localized_asset_count": len(localized),
        "artifact_digests": artifact_digests,
        "visual_assessment_digest": supplied_assessment_digest,
        "checks": checks,
        "external_write_count": 0,
    }
    receipt["qa_digest"] = canonical_digest(receipt)
    return receipt


def persist_automated_image_qa(
    receipt: Mapping[str, Any], *, path: Path
) -> Path:
    document = deepcopy(dict(receipt))
    supplied = str(document.pop("qa_digest", ""))
    if supplied != canonical_digest(document):
        raise PublicationImageQAError("automated image QA digest drifted")
    document["qa_digest"] = supplied
    encoded = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") != encoded:
        previous = json.loads(path.read_text(encoding="utf-8"))
        previous_digest = str(previous.get("qa_digest") or "")
        if not previous_digest.startswith("sha256:"):
            raise PublicationImageQAError("existing automated image QA evidence is unsigned")
        unsigned_previous = dict(previous)
        unsigned_previous.pop("qa_digest", None)
        if previous_digest != canonical_digest(unsigned_previous):
            raise PublicationImageQAError("existing automated image QA evidence drifted")
        attempt_dir = path.parent / "image-qa-attempts"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        archive = attempt_dir / f"{previous_digest.removeprefix('sha256:')}.json"
        if archive.is_file():
            if archive.read_text(encoding="utf-8") != path.read_text(encoding="utf-8"):
                raise PublicationImageQAError("archived automated image QA evidence conflicts")
            path.unlink()
        else:
            path.replace(archive)
    if not path.is_file():
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
    return path


__all__ = [
    "ASSESSMENT_SCHEMA",
    "PublicationImageQAError",
    "QA_SCHEMA",
    "build_automated_image_qa",
    "persist_automated_image_qa",
]
