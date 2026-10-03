"""Bounded TikTok keyword evidence for pre-publication title review."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

DIAGNOSE_OPTIMIZE_PATH = "/product/202411/products/diagnose_optimize"
LIVE_SUGGESTIONS_PATH = "/product/202405/products/suggestions"
REQUIRED_SCOPE = "serller.product.optimize"
DEFAULT_PENDING_CLAIMS = (
    "Self-Adhesive",
    "Peel and Stick",
    "Waterproof",
    "Removable",
    "Easy Installation",
    "Washable",
)


def _bounded_text(value: object, field: str, *, limit: int = 300) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text or len(text) > limit:
        raise ValueError(f"{field} is required and must not exceed {limit} characters")
    return text


def build_prelisting_diagnosis_request(
    *,
    target_label: str,
    shop_cipher: str,
    category_id: str,
    title: str,
) -> dict[str, Any]:
    """Build one official, read-only pre-listing TITLE diagnosis request."""

    target = _bounded_text(target_label, "target_label", limit=80)
    if not target.startswith("tiktok:"):
        raise ValueError("target_label must identify one TikTok target")
    cipher = _bounded_text(shop_cipher, "shop_cipher", limit=200)
    category = _bounded_text(category_id, "category_id", limit=40)
    if not category.isdigit():
        raise ValueError("category_id must be digits")
    safe_title = _bounded_text(title, "title", limit=300)
    return {
        "schema_version": "tiktok-prelisting-keyword-request/v1",
        "target_label": target,
        "method": "POST",
        "path": DIAGNOSE_OPTIMIZE_PATH,
        "required_scope": REQUIRED_SCOPE,
        "query": {"shop_cipher": cipher},
        "body": {
            "category_id": category,
            "title": safe_title,
            "optimization_fields": ["TITLE"],
        },
        "external_write_count": 0,
    }


def _keyword_texts(response: Mapping[str, Any]) -> list[str]:
    data = response.get("data")
    diagnoses = data.get("diagnoses") if isinstance(data, Mapping) else None
    if not isinstance(diagnoses, list):
        raise ValueError("TikTok diagnosis response has no diagnoses")
    values: list[str] = []
    seen: set[str] = set()
    for diagnosis in diagnoses:
        if not isinstance(diagnosis, Mapping):
            continue
        if str(diagnosis.get("field") or "").upper() != "TITLE":
            continue
        suggestion = diagnosis.get("suggestion")
        words = suggestion.get("seo_words") if isinstance(suggestion, Mapping) else None
        for row in words or ():
            if not isinstance(row, Mapping):
                continue
            text = " ".join(str(row.get("text") or "").split()).strip()
            if not text or len(text) > 120 or re.search(r"[\x00-\x1f]", text):
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(text)
            if len(values) >= 100:
                return values
    return values


def parse_diagnosis_response(
    response: Mapping[str, Any],
    *,
    target_label: str,
    category_id: str,
    observed_at: str,
) -> dict[str, Any]:
    """Reduce an official response to safe keyword evidence without raw IDs."""

    if response.get("code") != 0:
        raise ValueError("TikTok official keyword diagnosis was rejected")
    target = _bounded_text(target_label, "target_label", limit=80)
    category = _bounded_text(category_id, "category_id", limit=40)
    timestamp = _bounded_text(observed_at, "observed_at", limit=80)
    words = _keyword_texts(response)
    return {
        "schema_version": "tiktok-official-keyword-evidence/v1",
        "status": "OFFICIAL_READ_COMPLETED",
        "target_label": target,
        "category_id": category,
        "source": "tiktok_shop_open_api",
        "endpoint": DIAGNOSE_OPTIMIZE_PATH,
        "observed_at": timestamp,
        "keywords": [
            {
                "text": text,
                "source_status": "OFFICIAL_RECOMMENDATION",
                "product_fact_status": "UNVERIFIED",
                "selected": False,
                "fact_verified": False,
            }
            for text in words
        ],
        "search_volume_available": False,
        "sales_volume_available": False,
        "external_write_count": 0,
    }


def verified_keywords_for_title(rows: Iterable[object]) -> list[str]:
    """Return only terms explicitly selected after independent fact review."""

    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if row.get("selected") is not True or row.get("fact_verified") is not True:
            continue
        text = " ".join(str(row.get("text") or "").split()).strip()
        key = text.casefold()
        if text and len(text) <= 120 and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def review_candidate_keywords_for_title(
    rows: Iterable[object], *, limit: int = 3
) -> list[str]:
    """Return a small deduplicated set for one-stage human title review."""

    result: list[str] = []
    seen: set[str] = set()
    seen_concepts: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or row.get("selected") is not True:
            continue
        text = " ".join(str(row.get("text") or "").split()).strip()
        key = text.casefold()
        concept = (
            "adhesive_application"
            if key in {"self-adhesive", "peel and stick"}
            else key
        )
        if not text or len(text) > 120 or key in seen or concept in seen_concepts:
            continue
        seen.add(key)
        seen_concepts.add(concept)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def inherit_livelyhive_sea_evidence(
    direct_rows: Iterable[object],
    follower_targets: Iterable[object],
) -> list[dict[str, Any]]:
    """Project LivelyHive SEA search evidence without claiming a target read."""

    livelyhive = [
        dict(row)
        for row in direct_rows
        if isinstance(row, Mapping)
        and str(row.get("target_label") or "").upper().startswith("TIKTOK:LH_")
        and row.get("status") == "OFFICIAL_READ_COMPLETED"
    ]
    inherited: list[dict[str, Any]] = []
    for value in follower_targets:
        target = str(value or "").strip()
        upper = target.upper()
        if not target.startswith("tiktok:") or upper.startswith("TIKTOK:LH_"):
            continue
        region = upper.rsplit("_", 1)[-1] if upper.startswith("TIKTOK:HB_") else ""
        sources = [
            row for row in livelyhive
            if not region or str(row.get("target_label") or "").upper().endswith("_" + region)
        ]
        words: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source in sources:
            for raw in source.get("keywords") or ():
                if not isinstance(raw, Mapping):
                    continue
                text = " ".join(str(raw.get("text") or "").split()).strip()
                key = text.casefold()
                if not text or key in seen:
                    continue
                seen.add(key)
                words.append({
                    "text": text,
                    "source_status": "INHERITED_LIVELYHIVE_SEA_OFFICIAL_RECOMMENDATION",
                    "product_fact_status": "UNVERIFIED",
                    "selected": False,
                    "fact_verified": False,
                })
        inherited.append({
            "schema_version": "tiktok-inherited-keyword-evidence/v1",
            "status": "INHERITED_LIVELYHIVE_SEA_EVIDENCE",
            "target_label": target,
            "source_target_labels": [str(row.get("target_label") or "") for row in sources],
            "keywords": words,
            "search_volume_available": False,
            "sales_volume_available": False,
            "external_write_count": 0,
        })
    return inherited


def build_review_workflow(target_labels: Iterable[object]) -> dict[str, Any]:
    """Create the durable zero-write review plan shown before Lingshi."""

    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in target_labels:
        target = str(value or "").strip()
        if not target.startswith("tiktok:") or target in seen:
            continue
        seen.add(target)
        direct_read = target.upper().startswith("TIKTOK:LH_")
        targets.append(
            {
                "target_label": target,
                "status": (
                    "READY_FOR_EXPLICIT_OFFICIAL_READ"
                    if direct_read
                    else "FOLLOW_LIVELYHIVE_SEA_EVIDENCE"
                ),
                "reason_code": (
                    "prelisting_diagnosis_supported"
                    if direct_read
                    else "user_directed_livelyhive_sea_inheritance"
                ),
            }
        )
    return {
        "schema_version": "tiktok-keyword-review-workflow/v1",
        "status": "NOT_REQUESTED",
        "required_scope": REQUIRED_SCOPE,
        "prelisting_endpoint": DIAGNOSE_OPTIMIZE_PATH,
        "postlisting_endpoint": LIVE_SUGGESTIONS_PATH,
        "targets": targets,
        "pending_claims": [
            {
                "text": text,
                "status": "PENDING_FACT_REVIEW",
                "selected": False,
                "fact_verified": False,
            }
            for text in DEFAULT_PENDING_CLAIMS
        ],
        "seller_center_metrics": {
            "source": "manual_seller_center_evidence_only",
            "api_search_volume_available": False,
            "api_sales_volume_available": False,
        },
        "external_write_count": 0,
    }


__all__ = [
    "DEFAULT_PENDING_CLAIMS",
    "DIAGNOSE_OPTIMIZE_PATH",
    "LIVE_SUGGESTIONS_PATH",
    "REQUIRED_SCOPE",
    "build_prelisting_diagnosis_request",
    "build_review_workflow",
    "inherit_livelyhive_sea_evidence",
    "parse_diagnosis_response",
    "review_candidate_keywords_for_title",
    "verified_keywords_for_title",
]
