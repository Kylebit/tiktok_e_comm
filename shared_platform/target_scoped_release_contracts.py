"""Stable shared-platform contracts for one governed release target action.

The platform owns authority, durable state and proof consumption. Channel
operations owns the official proof providers and marketplace adapters. This
module deliberately contains no marketplace imports.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


SHOPEE_SAFE_PRE_SUBMIT_RETRY = "shopee_safe_pre_submit_retry_v1"
OZON_EXISTING_PRODUCT_STOCK_RECONCILIATION = (
    "ozon_existing_product_stock_reconciliation_v1"
)
TARGET_SCOPED_OPERATION_KINDS: dict[str, str] = {
    "shopee:MY": SHOPEE_SAFE_PRE_SUBMIT_RETRY,
    "shopee:VN": SHOPEE_SAFE_PRE_SUBMIT_RETRY,
    "ozon:RU": OZON_EXISTING_PRODUCT_STOCK_RECONCILIATION,
}

SHOPEE_REGIONAL_COPY_POLICY_VERSION = (
    "shopee-platform-derived-translation/v1"
)
SHOPEE_REGIONAL_COPY_LINT_POLICY_VERSION = (
    "shopee-regional-copy-lint/v1"
)
SHOPEE_REGIONAL_IMAGE_POLICY_VERSION = (
    "shopee-linked-image-observation/v1"
)
SHOPEE_GLOBAL_IMAGE_OBSERVATION_POLICY_VERSION = (
    "shopee-global-rehost-observation/v1"
)
SHOPEE_REGIONAL_EXPECTED_LANGUAGES = {
    "MY": "ms-Latn",
    "VN": "vi-Latn",
}

_SENSITIVE_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "confirmation_token",
    "authorization",
    "cookie",
    "secret",
    "raw_response",
)

_CJK_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_VIETNAMESE_SIGNAL_PATTERN = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệ"
    r"ìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụ"
    r"ưừứửữựỳýỷỹỵđĐ]"
)
_MALAY_SIGNAL_PATTERN = re.compile(
    r"\b(yang|untuk|dengan|adalah|kertas|dinding|pelekat|"
    r"reka bentuk|hiasan|rumah|mudah|sesuai|warna|saiz|"
    r"bahan|produk|kualiti|penghantaran|dan|atau)\b",
    re.IGNORECASE,
)
_HIGH_RISK_CLAIM_TERMS: dict[str, tuple[str, ...]] = {
    "claim:waterproof": (
        "waterproof",
        "kalis air",
        "chống nước",
    ),
    "claim:removable": (
        "removable",
        "boleh ditanggalkan",
        "có thể tháo rời",
    ),
    "claim:residue_free": (
        "residue-free",
        "residue free",
        "tanpa sisa",
        "không để lại keo",
        "không để lại dư lượng",
    ),
    "claim:reusable": (
        "reusable",
        "boleh digunakan semula",
        "có thể tái sử dụng",
    ),
    "claim:certified": (
        "certified",
        "certification",
        "diperakui",
        "chứng nhận",
    ),
    "claim:warranty": (
        "warranty",
        "waranti",
        "bảo hành",
    ),
    "claim:medical": (
        "medical claim",
        "medical grade",
        "tuntutan perubatan",
        "cấp y tế",
    ),
    "claim:safety_performance": (
        "guaranteed safe",
        "safety certified",
        "dijamin selamat",
        "an toàn tuyệt đối",
    ),
}


class TargetScopedContractError(ValueError):
    """A target-scoped request, proof or result violated the stable contract."""


class TargetScopedCommandUnavailable(TargetScopedContractError):
    """The immutable plan cannot authorize a complete target command."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = _required_text(code, "code")


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise TargetScopedContractError(
            "target-scoped evidence must be JSON-serializable"
        ) from error


def canonical_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def operation_kind_for_target(target_label: str) -> str:
    label = str(target_label or "").strip()
    try:
        return TARGET_SCOPED_OPERATION_KINDS[label]
    except KeyError as error:
        raise TargetScopedContractError(
            f"target-scoped action is not supported for {label or 'empty target'}"
        ) from error


def _strict_non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TargetScopedContractError(
            f"{field} must be a non-negative integer"
        )
    return value


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TargetScopedContractError(f"{field} is required")
    return text


def _strict_positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            f"immutable plan requires numeric {field}",
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            f"immutable plan requires positive {field}",
        )
    return number


def _normalised_seller_sku(value: object) -> tuple[str, str]:
    seller_sku = _required_text(value, "seller_sku")
    if not seller_sku.isdigit() or len(seller_sku) > 32:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable seller_sku must contain 1-32 digits",
        )
    return seller_sku, seller_sku[-4:].zfill(4)


def shopee_regional_observation_policy(
    *,
    site: object,
    source_global_master_digest: object,
) -> dict[str, str]:
    """Build verifier-only regional policy from immutable plan facts."""

    region = str(site or "").strip().upper()
    expected_language = SHOPEE_REGIONAL_EXPECTED_LANGUAGES.get(region)
    if expected_language is None:
        raise TargetScopedContractError(
            "Shopee regional observation only supports MY and VN"
        )
    return {
        "regional_copy_policy_version": (
            SHOPEE_REGIONAL_COPY_POLICY_VERSION
        ),
        "source_global_master_digest": _required_text(
            source_global_master_digest,
            "source_global_master_digest",
        ),
        "expected_language": expected_language,
        "regional_copy_lint_policy_version": (
            SHOPEE_REGIONAL_COPY_LINT_POLICY_VERSION
        ),
        "regional_image_verification_policy_version": (
            SHOPEE_REGIONAL_IMAGE_POLICY_VERSION
        ),
    }


def _copy_text(value: object) -> tuple[str, bool]:
    if not isinstance(value, str):
        return "", False
    return unicodedata.normalize("NFC", value).strip(), True


def _normalised_copy_for_comparison(value: str) -> str:
    return " ".join(value.casefold().split())


def _copy_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[^\W_]+", value.casefold())
        if len(token) > 1
    }


def _copy_leakage_classification(
    *,
    source_title: str,
    source_description: str,
    regional_title: str,
    regional_description: str,
) -> str:
    source_title_normalised = _normalised_copy_for_comparison(source_title)
    source_description_normalised = _normalised_copy_for_comparison(
        source_description
    )
    regional_title_normalised = _normalised_copy_for_comparison(
        regional_title
    )
    regional_description_normalised = _normalised_copy_for_comparison(
        regional_description
    )
    if (
        source_title_normalised
        and source_description_normalised
        and regional_title_normalised == source_title_normalised
        and regional_description_normalised
        == source_description_normalised
    ):
        return "full_source_copy"
    if (
        source_title_normalised
        and regional_title_normalised == source_title_normalised
    ) or (
        source_description_normalised
        and regional_description_normalised
        == source_description_normalised
    ):
        return "partial_source_copy"
    source_tokens = _copy_tokens(
        f"{source_title} {source_description}"
    )
    regional_tokens = _copy_tokens(
        f"{regional_title} {regional_description}"
    )
    if (
        source_tokens
        and len(source_tokens & regional_tokens) / len(source_tokens) >= 0.5
    ):
        return "partial_source_overlap"
    return "none"


def _matched_high_risk_claims(value: str) -> set[str]:
    normalised = _normalised_copy_for_comparison(value)
    return {
        rule_id
        for rule_id, terms in _HIGH_RISK_CLAIM_TERMS.items()
        if any(
            _normalised_copy_for_comparison(term) in normalised
            for term in terms
        )
    }


def evaluate_shopee_regional_copy_observation(
    *,
    source_title: object,
    source_description: object,
    source_global_master_digest: object,
    regional_title: object,
    regional_description: object,
    site: object,
) -> dict[str, Any]:
    """Return redacted evidence for Shopee-owned regional translation."""

    policy = shopee_regional_observation_policy(
        site=site,
        source_global_master_digest=source_global_master_digest,
    )
    region = str(site or "").strip().upper()
    source_title_text, source_title_shape = _copy_text(source_title)
    source_description_text, source_description_shape = _copy_text(
        source_description
    )
    regional_title_text, regional_title_shape = _copy_text(regional_title)
    regional_description_text, regional_description_shape = _copy_text(
        regional_description
    )
    source_shape_exact = source_title_shape and source_description_shape
    regional_shape_exact = (
        regional_title_shape and regional_description_shape
    )
    combined = f"{regional_title_text}\n{regional_description_text}"
    cjk_count = len(_CJK_PATTERN.findall(combined))
    vietnamese_signal_count = len(
        _VIETNAMESE_SIGNAL_PATTERN.findall(combined)
    )
    malay_signal_count = len(_MALAY_SIGNAL_PATTERN.findall(combined))
    latin_letter_count = sum(
        1
        for character in combined
        if character.isalpha() and ord(character) < 0x0250
    )
    language_signal_strong = (
        vietnamese_signal_count > 0
        if region == "VN"
        else malay_signal_count > 0
    )
    leakage = _copy_leakage_classification(
        source_title=source_title_text,
        source_description=source_description_text,
        regional_title=regional_title_text,
        regional_description=regional_description_text,
    )
    source_claims = _matched_high_risk_claims(
        f"{source_title_text}\n{source_description_text}"
    )
    regional_claims = _matched_high_risk_claims(combined)
    added_high_risk_claims = sorted(regional_claims - source_claims)
    title_length = len(regional_title_text)
    description_length = len(regional_description_text)
    obvious_truncation = (
        0 < title_length < 3
        or (
            len(source_description_text) >= 80
            and 0 < description_length
            < max(20, len(source_description_text) // 10)
        )
    )

    needs_review_rules: set[str] = set()
    warning_rules: set[str] = set()
    if not source_shape_exact or not regional_shape_exact:
        needs_review_rules.add("copy:shape_ambiguous")
    if not regional_title_text:
        needs_review_rules.add("copy:title_missing")
    if not regional_description_text:
        needs_review_rules.add("copy:description_missing")
    if obvious_truncation:
        needs_review_rules.add("copy:obviously_truncated")
    if cjk_count:
        needs_review_rules.add("copy:cjk_present")
    if leakage == "full_source_copy":
        if region == "MY" or (
            region == "VN" and not language_signal_strong
        ):
            needs_review_rules.add("copy:full_source_leakage")
    elif leakage != "none":
        warning_rules.add("copy:partial_source_overlap")
    if added_high_risk_claims:
        needs_review_rules.update(
            f"copy:new_high_risk:{rule_id.split(':', 1)[1]}"
            for rule_id in added_high_risk_claims
        )
    if not language_signal_strong:
        warning_rules.add("copy:language_signal_weak")

    if needs_review_rules:
        status = "needs_review"
    elif warning_rules:
        status = "warning"
    else:
        status = "observed"
    rule_ids = sorted(needs_review_rules | warning_rules)
    observation = {
        "schema_version": (
            "platform-derived-translation-observation/v1"
        ),
        "authority": "shopee_official_regional_get",
        "provider": "shopee_auto_translation",
        "site": region,
        "expected_language": policy["expected_language"],
        "source_global_master_digest": policy[
            "source_global_master_digest"
        ],
        "regional_copy_policy_version": policy[
            "regional_copy_policy_version"
        ],
        "regional_copy_lint_policy_version": policy[
            "regional_copy_lint_policy_version"
        ],
        "title": {
            "present": bool(regional_title_text),
            "length": title_length,
        },
        "description": {
            "present": bool(regional_description_text),
            "length": description_length,
        },
        "source_lengths": {
            "title": len(source_title_text),
            "description": len(source_description_text),
        },
        "unicode_signals": {
            "cjk_count": cjk_count,
            "vietnamese_signal_count": vietnamese_signal_count,
            "malay_signal_count": malay_signal_count,
            "latin_letter_count": latin_letter_count,
        },
        "language_signal": (
            "strong" if language_signal_strong else "weak"
        ),
        "source_copy_leakage": leakage,
        "source_claim_rule_ids": sorted(source_claims),
        "matched_rule_ids": rule_ids,
        "semantic_equivalence": "unverified",
        "status": status,
        "summary_code": f"regional_copy_{status}",
        "regional_copy_digest": canonical_digest(
            {
                "title": regional_title_text,
                "description": regional_description_text,
                "shape_exact": regional_shape_exact,
            }
        ),
    }
    _assert_redacted(observation, path="regional_copy_observation")
    observation["evidence_digest"] = canonical_digest(observation)
    return observation


def evaluate_shopee_regional_image_observation(
    *,
    approved_count: object,
    regional_image_urls: object,
    global_linkage_verified: object,
    stable_ordered_image_ids: object = None,
    stable_ordered_ids_exact: object = None,
) -> dict[str, Any]:
    """Return URL-redacted evidence for platform-rehosted regional images."""

    approved_shape_exact = (
        not isinstance(approved_count, bool)
        and isinstance(approved_count, int)
        and approved_count > 0
    )
    urls_shape_exact = (
        isinstance(regional_image_urls, (list, tuple))
        and not isinstance(regional_image_urls, (str, bytes))
    )
    urls = (
        [
            value.strip()
            for value in regional_image_urls
            if isinstance(value, str)
        ]
        if urls_shape_exact
        else []
    )
    urls_shape_exact = bool(
        urls_shape_exact
        and len(urls) == len(regional_image_urls)
        and all(urls)
    )
    stable_ids_available = isinstance(
        stable_ordered_image_ids, (list, tuple)
    ) and not isinstance(stable_ordered_image_ids, (str, bytes))
    stable_ids = (
        [
            str(value or "").strip()
            for value in stable_ordered_image_ids
        ]
        if stable_ids_available
        else []
    )
    stable_ids_shape_exact = bool(
        not stable_ids_available
        or (
            stable_ids
            and all(stable_ids)
            and len(stable_ids) == len(urls)
        )
    )
    stable_exact_shape = (
        stable_ordered_ids_exact is None
        or stable_ordered_ids_exact is True
        or stable_ordered_ids_exact is False
    )
    regional_count = len(urls)
    main_image_present = bool(urls and urls[0])

    needs_review_rules: set[str] = set()
    warning_rules: set[str] = set()
    if (
        not approved_shape_exact
        or not urls_shape_exact
        or not stable_ids_shape_exact
        or not stable_exact_shape
    ):
        needs_review_rules.add("image:shape_ambiguous")
    if approved_shape_exact and regional_count != approved_count:
        needs_review_rules.add("image:count_mismatch")
    if not main_image_present:
        needs_review_rules.add("image:main_missing")
    if global_linkage_verified is not True:
        needs_review_rules.add("image:global_linkage_unverified")
    if stable_ordered_ids_exact is False:
        needs_review_rules.add("image:stable_order_mismatch")
    elif stable_ordered_ids_exact is True and not stable_ids_available:
        needs_review_rules.add("image:stable_order_evidence_missing")
    elif stable_ordered_ids_exact is None:
        warning_rules.add(
            "image:linked_count_verified_order_unverifiable"
        )

    if needs_review_rules:
        status = "needs_review"
        verification_scope = "identity_unverified"
    elif stable_ordered_ids_exact is True:
        status = "observed"
        verification_scope = "stable_ordered_ids_exact"
    else:
        status = "warning"
        verification_scope = (
            "linked_count_verified_order_unverifiable"
        )
    observation = {
        "schema_version": (
            "platform-derived-image-observation/v1"
        ),
        "authority": "shopee_official_regional_get",
        "regional_image_verification_policy_version": (
            SHOPEE_REGIONAL_IMAGE_POLICY_VERSION
        ),
        "approved_count": (
            approved_count if approved_shape_exact else None
        ),
        "regional_count": regional_count,
        "main_image_present": main_image_present,
        "global_linkage_verified": global_linkage_verified is True,
        "stable_ordered_ids_available": stable_ids_available,
        "stable_ordered_ids_exact": (
            stable_ordered_ids_exact
            if stable_exact_shape
            else None
        ),
        "url_identity_exact": False,
        "verification_scope": verification_scope,
        "matched_rule_ids": sorted(
            needs_review_rules | warning_rules
        ),
        "status": status,
        "summary_code": f"regional_images_{status}",
        "regional_image_observation_digest": canonical_digest(urls),
        "stable_ordered_ids_digest": (
            canonical_digest(stable_ids)
            if stable_ids_available
            else None
        ),
    }
    _assert_redacted(observation, path="regional_image_observation")
    observation["evidence_digest"] = canonical_digest(observation)
    return observation


def _validated_regional_observation(
    value: object,
    *,
    schema_version: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TargetScopedContractError(
            "regional observation must be a mapping"
        )
    observation = dict(value)
    supplied_digest = str(
        observation.pop("evidence_digest", "") or ""
    ).strip()
    if observation.get("schema_version") != schema_version:
        raise TargetScopedContractError(
            "regional observation schema is invalid"
        )
    if not supplied_digest or supplied_digest != canonical_digest(observation):
        raise TargetScopedContractError(
            "regional observation evidence digest is invalid"
        )
    _assert_redacted(observation, path="regional_observation")
    observation["evidence_digest"] = supplied_digest
    return observation


def shopee_regional_observation_outcome(
    *,
    listing_hard_exact: object,
    copy_observation: object,
    image_observation: object,
) -> dict[str, Any]:
    """Map official hard facts and derived observations to one outcome."""

    copy_observation = _validated_regional_observation(
        copy_observation,
        schema_version=(
            "platform-derived-translation-observation/v1"
        ),
    )
    image_observation = _validated_regional_observation(
        image_observation,
        schema_version="platform-derived-image-observation/v1",
    )
    copy_status = str(copy_observation.get("status") or "")
    image_status = str(image_observation.get("status") or "")
    valid_statuses = {"observed", "warning", "needs_review"}
    if copy_status not in valid_statuses or image_status not in valid_statuses:
        raise TargetScopedContractError(
            "regional observation status is invalid"
        )
    listing_verified = listing_hard_exact is True
    succeeded = (
        listing_verified
        and copy_status != "needs_review"
        and image_status != "needs_review"
    )
    rule_ids = {
        str(value)
        for value in (
            list(copy_observation.get("matched_rule_ids") or ())
            + list(image_observation.get("matched_rule_ids") or ())
        )
        if str(value)
    }
    if not listing_verified:
        rule_ids.add("listing:hard_exact_failed")
    outcome = {
        "schema_version": "shopee-regional-observation-outcome/v1",
        "outcome": (
            "SUCCEEDED" if succeeded else "RECONCILIATION_REQUIRED"
        ),
        "listing_identity_verified": listing_verified,
        "derived_translation_status": copy_status,
        "derived_image_status": image_status,
        "semantic_equivalence": "unverified",
        "profit_status": "unverified",
        "manual_review_required": (
            copy_status in {"warning", "needs_review"}
            or image_status in {"warning", "needs_review"}
        ),
        "reconciliation_required": not succeeded,
        "matched_rule_ids": sorted(rule_ids),
    }
    _assert_redacted(outcome, path="regional_observation_outcome")
    outcome["evidence_digest"] = canonical_digest(outcome)
    return outcome


def approved_shopee_channel_master_digest(
    title: object,
    description: object,
    ordered_image_urls: object,
) -> str:
    """Digest the immutable approved copy and source-image lineage.

    Shopee can rehost global images, so this digest is deliberately retained
    as plan lineage and must not be recomputed from official rehosted URLs.
    """

    clean_title = unicodedata.normalize(
        "NFC",
        str(title or "").strip(),
    )
    exact_description = str(
        description if description is not None else ""
    )
    if not clean_title or not exact_description.strip():
        raise TargetScopedContractError(
            "approved Shopee title and description are required"
        )
    if (
        isinstance(ordered_image_urls, (str, bytes))
        or not isinstance(ordered_image_urls, (list, tuple))
        or not ordered_image_urls
    ):
        raise TargetScopedContractError(
            "approved Shopee ordered image URLs are required"
        )
    ordered = []
    for position, value in enumerate(ordered_image_urls, start=1):
        image_url = str(value or "").strip()
        if not image_url:
            raise TargetScopedContractError(
                "approved Shopee image URL is required"
            )
        ordered.append(
            {"position": position, "image_url": image_url}
        )
    return canonical_digest(
        {
            "schema_version": "approved-shopee-channel-master/v1",
            "title": clean_title,
            "description": exact_description,
            "ordered_images": ordered,
        }
    )


def approved_shopee_copy_digest(
    title: object,
    description: object,
) -> str:
    """Return the copy-only digest that an official global GET can reproduce."""

    clean_title = unicodedata.normalize(
        "NFC",
        str(title or "").strip(),
    )
    exact_description = str(
        description if description is not None else ""
    )
    if not clean_title or not exact_description.strip():
        raise TargetScopedContractError(
            "approved Shopee title and description are required"
        )
    return canonical_digest(
        {
            "schema_version": "approved-shopee-copy/v1",
            "title": clean_title,
            "description": exact_description,
        }
    )


def approved_source_image_manifest_digest(
    ordered_image_urls: object,
) -> str:
    """Return a plan-only digest for the approved ordered source images."""

    if (
        isinstance(ordered_image_urls, (str, bytes))
        or not isinstance(ordered_image_urls, (list, tuple))
        or not ordered_image_urls
    ):
        raise TargetScopedContractError(
            "approved Shopee ordered source image URLs are required"
        )
    ordered = []
    for position, value in enumerate(ordered_image_urls, start=1):
        image_url = str(value or "").strip()
        if not image_url:
            raise TargetScopedContractError(
                "approved Shopee source image URL is required"
            )
        ordered.append(
            {"position": position, "image_url": image_url}
        )
    return canonical_digest(
        {
            "schema_version": (
                "approved-shopee-source-image-manifest/v1"
            ),
            "ordered_images": ordered,
        }
    )


def shopee_global_image_id_mapping_digest(
    ordered_image_ids: object,
) -> str:
    """Digest ordered official IDs without persisting the raw identifiers."""

    if (
        isinstance(ordered_image_ids, (str, bytes))
        or not isinstance(ordered_image_ids, (list, tuple))
        or not ordered_image_ids
    ):
        raise TargetScopedContractError(
            "ordered Shopee global image IDs are required"
        )
    ordered = [str(value or "").strip() for value in ordered_image_ids]
    if not all(ordered) or len(set(ordered)) != len(ordered):
        raise TargetScopedContractError(
            "ordered Shopee global image IDs must be nonempty and unique"
        )
    return canonical_digest(
        {
            "schema_version": (
                "shopee-official-global-image-id-snapshot/v1"
            ),
            "ordered_image_ids": ordered,
        }
    )


def evaluate_shopee_global_image_observation(
    *,
    approved_count: object,
    official_image_urls: object,
    official_image_ids: object,
    prior_mapping_digest: object = None,
) -> dict[str, Any]:
    """Summarize rehosted global images without claiming URL/order equality."""

    approved_shape_exact = (
        not isinstance(approved_count, bool)
        and isinstance(approved_count, int)
        and approved_count > 0
    )
    urls_shape_exact = (
        isinstance(official_image_urls, (list, tuple))
        and not isinstance(official_image_urls, (str, bytes))
    )
    ids_shape_exact = (
        isinstance(official_image_ids, (list, tuple))
        and not isinstance(official_image_ids, (str, bytes))
    )
    urls = (
        [value.strip() for value in official_image_urls]
        if urls_shape_exact
        and all(isinstance(value, str) for value in official_image_urls)
        else []
    )
    image_ids = (
        [value.strip() for value in official_image_ids]
        if ids_shape_exact
        and all(isinstance(value, str) for value in official_image_ids)
        else []
    )
    urls_nonempty_unique = bool(
        urls_shape_exact
        and len(urls) == len(official_image_urls)
        and urls
        and all(urls)
        and len(set(urls)) == len(urls)
    )
    ids_nonempty_unique = bool(
        ids_shape_exact
        and len(image_ids) == len(official_image_ids)
        and image_ids
        and all(image_ids)
        and len(set(image_ids)) == len(image_ids)
    )
    url_count = len(urls)
    image_id_count = len(image_ids)
    counts_aligned = bool(
        approved_shape_exact
        and url_count == approved_count
        and image_id_count == approved_count
    )
    snapshot_digest = (
        shopee_global_image_id_mapping_digest(image_ids)
        if ids_nonempty_unique
        else None
    )
    supplied_mapping = (
        str(prior_mapping_digest or "").strip()
        if prior_mapping_digest is not None
        else ""
    )
    mapping_shape_exact = (
        not supplied_mapping
        or bool(_SHA256_PATTERN.fullmatch(supplied_mapping))
    )
    mapping_available = bool(supplied_mapping and mapping_shape_exact)
    mapping_exact = (
        snapshot_digest == supplied_mapping
        if mapping_available and snapshot_digest
        else None
    )

    needs_review_rules: set[str] = set()
    warning_rules: set[str] = set()
    if (
        not approved_shape_exact
        or not urls_shape_exact
        or not ids_shape_exact
        or not mapping_shape_exact
    ):
        needs_review_rules.add("global_image:shape_ambiguous")
    if not urls_nonempty_unique:
        needs_review_rules.add(
            "global_image:official_urls_not_nonempty_unique"
        )
    if not ids_nonempty_unique:
        needs_review_rules.add(
            "global_image:official_ids_not_nonempty_unique"
        )
    if not counts_aligned:
        needs_review_rules.add("global_image:count_mismatch")
    if mapping_available and mapping_exact is not True:
        needs_review_rules.add("global_image:prior_mapping_mismatch")
    if not mapping_available:
        warning_rules.add("global_image:rehosted_order_unverifiable")

    if needs_review_rules:
        status = "needs_review"
        verification_scope = "identity_unverified"
    elif mapping_exact is True:
        status = "observed"
        verification_scope = "stable_ordered_ids_exact"
    else:
        status = "warning"
        verification_scope = (
            "linked_count_verified_order_unverifiable"
        )
    observation = {
        "schema_version": (
            "platform-derived-global-image-observation/v1"
        ),
        "authority": "shopee_official_global_get",
        "provider": "shopee",
        "global_image_observation_policy_version": (
            SHOPEE_GLOBAL_IMAGE_OBSERVATION_POLICY_VERSION
        ),
        "approved_count": (
            approved_count if approved_shape_exact else None
        ),
        "official_image_url_count": url_count,
        "official_image_id_count": image_id_count,
        "official_urls_nonempty_unique": urls_nonempty_unique,
        "official_ids_nonempty_unique": ids_nonempty_unique,
        "counts_aligned": counts_aligned,
        "official_image_id_snapshot_digest": snapshot_digest,
        "prior_mapping_available": mapping_available,
        "prior_mapping_exact": mapping_exact,
        "url_identity_exact": False,
        "approved_order_exact": mapping_exact is True,
        "verification_scope": verification_scope,
        "matched_rule_ids": sorted(
            needs_review_rules | warning_rules
        ),
        "semantic_equivalence": "unverified",
        "status": status,
        "manual_review_required": status != "observed",
        "summary_code": f"global_images_{status}",
    }
    _assert_redacted(observation, path="global_image_observation")
    observation["evidence_digest"] = canonical_digest(observation)
    return observation


def _validated_global_image_observation(
    value: object,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TargetScopedContractError(
            "global image observation must be a mapping"
        )
    observation = dict(value)
    supplied_digest = str(
        observation.pop("evidence_digest", "") or ""
    ).strip()
    if (
        observation.get("schema_version")
        != "platform-derived-global-image-observation/v1"
        or observation.get("authority")
        != "shopee_official_global_get"
        or observation.get(
            "global_image_observation_policy_version"
        )
        != SHOPEE_GLOBAL_IMAGE_OBSERVATION_POLICY_VERSION
    ):
        raise TargetScopedContractError(
            "global image observation schema or authority is invalid"
        )
    if not supplied_digest or supplied_digest != canonical_digest(observation):
        raise TargetScopedContractError(
            "global image observation evidence digest is invalid"
        )
    status = str(observation.get("status") or "")
    scope = str(observation.get("verification_scope") or "")
    order_exact = observation.get("approved_order_exact")
    url_exact = observation.get("url_identity_exact")
    approved_count = observation.get("approved_count")
    url_count = observation.get("official_image_url_count")
    image_id_count = observation.get("official_image_id_count")
    snapshot_digest = str(
        observation.get("official_image_id_snapshot_digest") or ""
    )
    if url_exact is not False or status not in {
        "observed",
        "warning",
        "needs_review",
    }:
        raise TargetScopedContractError(
            "global image observation status is invalid"
        )
    if status in {"observed", "warning"} and (
        isinstance(approved_count, bool)
        or not isinstance(approved_count, int)
        or approved_count <= 0
        or url_count != approved_count
        or image_id_count != approved_count
        or observation.get("official_urls_nonempty_unique") is not True
        or observation.get("official_ids_nonempty_unique") is not True
        or observation.get("counts_aligned") is not True
        or not _SHA256_PATTERN.fullmatch(snapshot_digest)
    ):
        raise TargetScopedContractError(
            "eligible global images require exact count and shape evidence"
        )
    if (
        status == "observed"
        and (
            scope != "stable_ordered_ids_exact"
            or order_exact is not True
            or observation.get("prior_mapping_exact") is not True
            or observation.get("prior_mapping_available") is not True
            or observation.get("manual_review_required") is not False
        )
    ):
        raise TargetScopedContractError(
            "observed global images require exact prior mapping"
        )
    if (
        status == "warning"
        and (
            scope
            != "linked_count_verified_order_unverifiable"
            or order_exact is not False
            or observation.get("prior_mapping_available") is not False
            or observation.get("prior_mapping_exact") is not None
            or observation.get("manual_review_required") is not True
        )
    ):
        raise TargetScopedContractError(
            "warning global images must keep order unverifiable"
        )
    _assert_redacted(observation, path="global_image_observation")
    observation["evidence_digest"] = supplied_digest
    return observation


def shopee_global_image_observation_outcome(
    *,
    global_hard_facts_exact: object,
    image_observation: object,
) -> dict[str, Any]:
    """Gate execution while retaining honest rehost/manual-review semantics."""

    observation = _validated_global_image_observation(
        image_observation
    )
    status = str(observation["status"])
    hard_exact = global_hard_facts_exact is True
    execution_allowed = (
        hard_exact and status in {"observed", "warning"}
    )
    rules = {
        str(value)
        for value in observation.get("matched_rule_ids") or ()
        if str(value)
    }
    if not hard_exact:
        rules.add("global_listing:hard_facts_failed")
    outcome = {
        "schema_version": (
            "shopee-global-image-observation-outcome/v1"
        ),
        "execution_allowed": execution_allowed,
        "global_hard_facts_exact": hard_exact,
        "global_image_status": status,
        "global_image_verification_scope": observation[
            "verification_scope"
        ],
        "global_image_url_identity_exact": False,
        "global_image_approved_order_exact": observation[
            "approved_order_exact"
        ],
        "manual_review_required": (
            status != "observed" or not hard_exact
        ),
        "reconciliation_required": not execution_allowed,
        "semantic_equivalence": "unverified",
        "matched_rule_ids": sorted(rules),
    }
    _assert_redacted(outcome, path="global_image_observation_outcome")
    outcome["evidence_digest"] = canonical_digest(outcome)
    return outcome


def _prior_shopee_global_image_mapping_digest(
    payload: Mapping[str, Any],
    *,
    approved_count: int,
) -> str | None:
    mapping = payload.get("shopee_global_image_mapping")
    if mapping is None:
        return None
    if not isinstance(mapping, Mapping):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable Shopee global image mapping must be a mapping",
        )
    digest = str(mapping.get("ordered_image_ids_digest") or "").strip()
    count = mapping.get("image_count")
    if (
        mapping.get("schema_version")
        != "approved-shopee-global-image-mapping/v1"
        or not _SHA256_PATTERN.fullmatch(digest)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != approved_count
    ):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable Shopee global image mapping is invalid",
        )
    return digest


def _approved_shopee_master_digest(
    payload: Mapping[str, Any],
) -> tuple[str, str, str, int, str | None]:
    listing = payload.get("listing_copy")
    images = payload.get("images")
    if not isinstance(listing, Mapping) or not isinstance(images, list):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan lacks approved Shopee copy or ordered images",
        )
    candidates = [
        row
        for row in (listing.get("candidates") or ())
        if isinstance(row, Mapping)
        and str(row.get("channel") or "").lower() == "shopee"
        and str(row.get("site") or "").upper() == "CNSC"
        and str(row.get("policy_check") or "").lower() == "passed"
        and str(row.get("title") or "").strip()
    ]
    if len(candidates) != 1:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan requires one approved Shopee CNSC title",
        )
    description = str(listing.get("shopee_description_en") or "")
    if not description.strip() or not images:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan requires approved Shopee description and images",
        )
    ordered_image_urls: list[str] = []
    for index, row in enumerate(images, start=1):
        if not isinstance(row, Mapping):
            raise TargetScopedCommandUnavailable(
                "planned_command_incomplete",
                "immutable ordered image entry is invalid",
            )
        url = str(row.get("image_url") or "").strip()
        position = row.get("position")
        if not url or isinstance(position, bool) or not isinstance(position, int):
            raise TargetScopedCommandUnavailable(
                "planned_command_incomplete",
                "immutable ordered image requires position and image_url",
            )
        if position != index:
            raise TargetScopedCommandUnavailable(
                "planned_command_incomplete",
                "immutable images must use exact consecutive order",
            )
        ordered_image_urls.append(url)
    image_count = len(ordered_image_urls)
    return (
        approved_shopee_channel_master_digest(
            candidates[0]["title"],
            description,
            ordered_image_urls,
        ),
        approved_shopee_copy_digest(
            candidates[0]["title"],
            description,
        ),
        approved_source_image_manifest_digest(ordered_image_urls),
        image_count,
        _prior_shopee_global_image_mapping_digest(
            payload,
            approved_count=image_count,
        ),
    )


def _approved_parcel(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    facts = payload.get("product_facts")
    if not isinstance(facts, Mapping):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan lacks approved parcel facts",
        )
    weight = _strict_positive_number(facts.get("weight_kg"), "weight_kg")
    package = facts.get("package_cm")
    if not isinstance(package, list) or len(package) != 3:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan requires three package dimensions",
        )
    dimensions = [
        _strict_positive_number(value, f"package_cm[{index}]")
        for index, value in enumerate(package)
    ]
    parcel = {
        "weight_kg": weight,
        "package_cm": dimensions,
    }
    return parcel, canonical_digest(
        {"schema_version": "approved-parcel/v1", **parcel}
    )


def _planned_shopee_command(
    payload: Mapping[str, Any],
    *,
    target_label: str,
) -> dict[str, Any]:
    region = target_label.rsplit(":", 1)[1]
    seller_sku, model_sku = _normalised_seller_sku(
        payload.get("seller_sku")
    )
    pricing = payload.get("pricing")
    selected = (
        pricing.get("selected_targets")
        if isinstance(pricing, Mapping)
        else None
    )
    target_pricing = (
        selected.get(target_label)
        if isinstance(selected, Mapping)
        else None
    )
    derived = (
        target_pricing.get("derived_preview")
        if isinstance(target_pricing, Mapping)
        else None
    )
    if not isinstance(derived, Mapping):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            f"immutable plan lacks {target_label} approved pricing",
        )
    local_price = _strict_positive_number(
        derived.get("local_original_price"),
        "local_original_price",
    )
    currency = str(derived.get("source_currency") or "").strip().upper()
    expected_currency = {"MY": "MYR", "VN": "VND"}[region]
    if currency != expected_currency:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            f"immutable {target_label} price must use {expected_currency}",
        )
    (
        master_digest,
        copy_digest,
        source_image_manifest_digest,
        image_count,
        prior_image_mapping_digest,
    ) = _approved_shopee_master_digest(payload)
    parcel, parcel_digest = _approved_parcel(payload)
    excluded = [50052] if region == "VN" else []
    regional_policy = shopee_regional_observation_policy(
        site=region,
        source_global_master_digest=master_digest,
    )
    return {
        "schema_version": "shopee-existing-global-command/v2",
        "builder_policy_version": "target-scoped-shopee/v2",
        "target_label": target_label,
        "operation_kind": SHOPEE_SAFE_PRE_SUBMIT_RETRY,
        "region": region,
        "seller_sku": seller_sku,
        "model_sku": model_sku,
        "existing_global_only": True,
        "forbid_global_create": True,
        "forbid_global_update": True,
        "forbid_model_init": True,
        "allow_token_refresh": False,
        "item_status": "NORMAL",
        "local_original_price": local_price,
        "local_currency": currency,
        "approved_master_digest": master_digest,
        "approved_copy_digest": copy_digest,
        "approved_source_image_manifest_digest": (
            source_image_manifest_digest
        ),
        "global_image_observation_policy_version": (
            SHOPEE_GLOBAL_IMAGE_OBSERVATION_POLICY_VERSION
        ),
        "global_image_order_authority": (
            "prior_plan_mapping"
            if prior_image_mapping_digest
            else "unverifiable"
        ),
        "approved_global_image_mapping_digest": (
            prior_image_mapping_digest
        ),
        **regional_policy,
        "regional_observation_policy_digest": canonical_digest(
            regional_policy
        ),
        "approved_image_count": image_count,
        "parcel": parcel,
        "parcel_digest": parcel_digest,
        "logistics_policy_version": (
            "approved-parcel-enabled-channels-exclude-50052/v1"
            if region == "VN"
            else "approved-parcel-enabled-channels/v1"
        ),
        "excluded_logistics_ids": excluded,
    }


def _planned_ozon_command(payload: Mapping[str, Any]) -> dict[str, Any]:
    actions = payload.get("target_actions")
    action = (
        actions.get("ozon:RU") if isinstance(actions, Mapping) else None
    )
    if not isinstance(action, Mapping):
        raise TargetScopedCommandUnavailable(
            "successor_plan_stock_decision_required",
            "Ozon stock action requires a Kyle-approved successor plan",
        )
    seller_sku, offer_id = _normalised_seller_sku(
        payload.get("seller_sku")
    )
    required_text = {
        field: str(action.get(field) or "").strip()
        for field in (
            "expected_listing_digest",
            "inventory_snapshot_id",
            "inventory_snapshot_revision_or_digest",
        )
    }
    if any(not value for value in required_text.values()):
        raise TargetScopedCommandUnavailable(
            "successor_plan_stock_decision_required",
            "Ozon successor plan lacks governed listing or inventory identity",
        )
    stock = action.get("desired_stock_quantity")
    if isinstance(stock, bool) or not isinstance(stock, int) or stock <= 0:
        raise TargetScopedCommandUnavailable(
            "successor_plan_stock_decision_required",
            "Ozon successor plan requires a positive desired stock quantity",
        )
    if (
        str(action.get("schema_version") or "")
        != "ozon-existing-product-stock-command/v1"
        or str(action.get("warehouse_policy") or "")
        != "single_active_non_kgt"
    ):
        raise TargetScopedCommandUnavailable(
            "successor_plan_stock_decision_required",
            "Ozon successor plan stock schema or warehouse policy is invalid",
        )
    return {
        "schema_version": "ozon-existing-product-stock-command/v1",
        "builder_policy_version": "target-scoped-ozon-stock/v1",
        "target_label": "ozon:RU",
        "operation_kind": OZON_EXISTING_PRODUCT_STOCK_RECONCILIATION,
        "seller_sku": seller_sku,
        "offer_id": offer_id,
        "existing_product_only": True,
        "forbid_import": True,
        "forbid_create": True,
        "expected_listing_digest": required_text[
            "expected_listing_digest"
        ],
        "desired_stock_quantity": stock,
        "inventory_snapshot_id": required_text["inventory_snapshot_id"],
        "inventory_snapshot_revision_or_digest": required_text[
            "inventory_snapshot_revision_or_digest"
        ],
        "warehouse_policy": "single_active_non_kgt",
    }


def planned_target_command(
    plan_payload: Mapping[str, Any],
    *,
    target_label: str,
) -> tuple[dict[str, Any], str]:
    """Purely derive one write command from an immutable ReleasePlan payload."""

    if not isinstance(plan_payload, Mapping):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable release payload is required",
        )
    label = str(target_label or "").strip()
    operation_kind_for_target(label)
    if label not in list(plan_payload.get("targets") or ()):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "target is absent from the immutable release plan",
        )
    if label in {"shopee:MY", "shopee:VN"}:
        command = _planned_shopee_command(
            plan_payload,
            target_label=label,
        )
    else:
        command = _planned_ozon_command(plan_payload)
    return command, canonical_digest(command)


def _assert_redacted(value: object, *, path: str = "evidence") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                raise TargetScopedContractError(
                    f"{path}.{key} contains a forbidden sensitive field"
                )
            if (
                normalized
                in {
                    "title",
                    "description",
                    "source_title",
                    "source_description",
                    "regional_title",
                    "regional_description",
                }
                and isinstance(item, str)
            ):
                raise TargetScopedContractError(
                    f"{path}.{key} contains raw copy"
                )
            if (
                (
                    "image_url" in normalized
                    or "image_id" in normalized
                    or normalized == "token"
                )
                and not normalized.endswith("digest")
                and isinstance(item, (str, list, tuple))
            ):
                raise TargetScopedContractError(
                    f"{path}.{key} contains raw platform identity"
                )
            _assert_redacted(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_redacted(item, path=f"{path}[{index}]")


def _parse_utc(value: object, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise TargetScopedContractError(
            f"{field} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise TargetScopedContractError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def target_failure_digest(
    *,
    target_label: str,
    attempts: int,
    error: object,
    failure_event_digests: list[str] | tuple[str, ...],
) -> str:
    return canonical_digest(
        {
            "target_label": _required_text(target_label, "target_label"),
            "attempts": _strict_non_negative_int(attempts, "attempts"),
            "error": str(error or ""),
            "failure_event_digests": [
                str(value or "").strip() for value in failure_event_digests
            ],
        }
    )


def target_preflight_digest(
    *,
    plan_id: str,
    run_id: str,
    target_label: str,
    operation_kind: str,
    product_revision: int,
    payload_digest: str,
    planned_command_digest: str,
    failure_attempt: int,
    failure_digest: str,
    target_idempotency_key: str,
) -> str:
    expected_kind = operation_kind_for_target(target_label)
    if operation_kind != expected_kind:
        raise TargetScopedContractError(
            "operation_kind does not match the server target allowlist"
        )
    return canonical_digest(
        {
            "schema_version": "target-scoped-preflight/v1",
            "plan_id": _required_text(plan_id, "plan_id"),
            "run_id": _required_text(run_id, "run_id"),
            "target_label": _required_text(target_label, "target_label"),
            "operation_kind": operation_kind,
            "product_revision": _strict_non_negative_int(
                product_revision, "product_revision"
            ),
            "payload_digest": _required_text(
                payload_digest, "payload_digest"
            ),
            "planned_command_digest": _required_text(
                planned_command_digest, "planned_command_digest"
            ),
            "failure_attempt": _strict_non_negative_int(
                failure_attempt, "failure_attempt"
            ),
            "failure_digest": _required_text(
                failure_digest, "failure_digest"
            ),
            "target_idempotency_key": _required_text(
                target_idempotency_key, "target_idempotency_key"
            ),
        }
    )


@dataclass(frozen=True)
class TargetScopedOperationRequest:
    """Exact server-authorized request passed to a channel proof/adapter seam."""

    plan_id: str
    confirmation_token: str
    approval_scope_digest: str
    product_id: str
    seller_sku: str
    product_package_id: str
    content_package_id: str
    run_id: str
    target_label: str
    operation_kind: str
    product_revision: int
    payload_digest: str
    planned_command: Mapping[str, Any]
    planned_command_digest: str
    preflight_digest: str
    failure_attempt: int
    failure_digest: str
    target_idempotency_key: str
    approved_by: str = "Kyle"

    def __post_init__(self) -> None:
        for field in (
            "plan_id",
            "confirmation_token",
            "approval_scope_digest",
            "product_id",
            "seller_sku",
            "product_package_id",
            "content_package_id",
            "run_id",
            "target_label",
            "operation_kind",
            "payload_digest",
            "planned_command_digest",
            "preflight_digest",
            "failure_digest",
            "target_idempotency_key",
        ):
            _required_text(getattr(self, field), field)
        _strict_non_negative_int(self.product_revision, "product_revision")
        _strict_non_negative_int(self.failure_attempt, "failure_attempt")
        if self.approved_by != "Kyle":
            raise TargetScopedContractError(
                "target-scoped action requires approved_by=Kyle"
            )
        expected_kind = operation_kind_for_target(self.target_label)
        if self.operation_kind != expected_kind:
            raise TargetScopedContractError(
                "operation_kind does not match target_label"
            )
        if not isinstance(self.planned_command, Mapping):
            raise TargetScopedContractError(
                "planned_command must be a mapping"
            )
        _assert_redacted(self.planned_command, path="planned_command")
        if canonical_digest(dict(self.planned_command)) != (
            self.planned_command_digest
        ):
            raise TargetScopedContractError(
                "planned_command_digest does not match planned_command"
            )
        if (
            self.planned_command.get("target_label") != self.target_label
            or self.planned_command.get("operation_kind")
            != self.operation_kind
        ):
            raise TargetScopedContractError(
                "planned_command identity does not match request"
            )
        expected_preflight = target_preflight_digest(
            plan_id=self.plan_id,
            run_id=self.run_id,
            target_label=self.target_label,
            operation_kind=self.operation_kind,
            product_revision=self.product_revision,
            payload_digest=self.payload_digest,
            planned_command_digest=self.planned_command_digest,
            failure_attempt=self.failure_attempt,
            failure_digest=self.failure_digest,
            target_idempotency_key=self.target_idempotency_key,
        )
        if self.preflight_digest != expected_preflight:
            raise TargetScopedContractError(
                "preflight_digest does not match target failure identity"
            )

    @property
    def confirmation_token_digest(self) -> str:
        return hashlib.sha256(
            self.confirmation_token.encode("utf-8")
        ).hexdigest()

    def durable_identity(self) -> dict[str, Any]:
        """Return the immutable operation identity without persisting secrets."""

        return {
            "schema_version": "target-scoped-operation-request/v1",
            "plan_id": self.plan_id,
            "confirmation_token_digest": self.confirmation_token_digest,
            "approval_scope_digest": self.approval_scope_digest,
            "product_id": self.product_id,
            "seller_sku": self.seller_sku,
            "product_package_id": self.product_package_id,
            "content_package_id": self.content_package_id,
            "run_id": self.run_id,
            "target_label": self.target_label,
            "operation_kind": self.operation_kind,
            "product_revision": self.product_revision,
            "payload_digest": self.payload_digest,
            "planned_command": dict(self.planned_command),
            "planned_command_digest": self.planned_command_digest,
            "preflight_digest": self.preflight_digest,
            "failure_attempt": self.failure_attempt,
            "failure_digest": self.failure_digest,
            "target_idempotency_key": self.target_idempotency_key,
            "approved_by": self.approved_by,
        }

    def operation_digest(self, proof_digest: str) -> str:
        return canonical_digest(
            {
                **self.durable_identity(),
                "proof_digest": _required_text(
                    proof_digest, "proof_digest"
                ),
            }
        )


@dataclass(frozen=True)
class OfficialTargetProof:
    """Redacted official proof bound to one exact target failure attempt."""

    schema_version: str
    operation_kind: str
    plan_id: str
    run_id: str
    target_label: str
    product_revision: int
    payload_digest: str
    planned_command_digest: str
    preflight_digest: str
    failure_attempt: int
    failure_digest: str
    provided_by: str
    allow_refresh: bool
    observed_at: str
    expires_at: str
    checks: Mapping[str, bool]
    semantic_evidence: Mapping[str, Any]
    redacted_summary: Mapping[str, Any]
    external_writes_performed: tuple[str, ...]
    proof_digest: str

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        request: TargetScopedOperationRequest,
        now: datetime | None = None,
    ) -> "OfficialTargetProof":
        if isinstance(value, cls):
            value = value.durable_payload()
        if not isinstance(value, Mapping):
            raise TargetScopedContractError(
                "official target proof must be a mapping"
            )
        else:
            raw = dict(value)
            checks = raw.get("checks")
            semantic = raw.get("semantic_evidence")
            summary = raw.get("redacted_summary") or {}
            writes = raw.get("external_writes_performed")
            if not isinstance(checks, Mapping) or not checks:
                raise TargetScopedContractError(
                    "official target proof requires named checks"
                )
            if not isinstance(semantic, Mapping) or not semantic:
                raise TargetScopedContractError(
                    "official target proof requires semantic_evidence"
                )
            if not isinstance(summary, Mapping):
                raise TargetScopedContractError(
                    "redacted_summary must be a mapping"
                )
            if not isinstance(writes, (list, tuple)):
                raise TargetScopedContractError(
                    "external_writes_performed must be a list"
                )
            semantic_payload = {
                "schema_version": str(
                    raw.get("schema_version")
                    or "official-target-proof/v1"
                ),
                "operation_kind": str(raw.get("operation_kind") or ""),
                "plan_id": str(raw.get("plan_id") or ""),
                "run_id": str(raw.get("run_id") or ""),
                "target_label": str(raw.get("target_label") or ""),
                "product_revision": raw.get("product_revision"),
                "payload_digest": str(raw.get("payload_digest") or ""),
                "planned_command_digest": str(
                    raw.get("planned_command_digest") or ""
                ),
                "preflight_digest": str(raw.get("preflight_digest") or ""),
                "failure_attempt": raw.get("failure_attempt"),
                "failure_digest": str(raw.get("failure_digest") or ""),
                "provided_by": str(raw.get("provided_by") or ""),
                "allow_refresh": raw.get("allow_refresh"),
                "checks": dict(checks),
                "semantic_evidence": dict(semantic),
                "external_writes_performed": list(writes),
            }
            computed_digest = canonical_digest(semantic_payload)
            supplied_digest = str(raw.get("proof_digest") or "").strip()
            if supplied_digest and supplied_digest != computed_digest:
                raise TargetScopedContractError(
                    "official proof_digest does not match semantic evidence"
                )
            proof = cls(
                schema_version=semantic_payload["schema_version"],
                operation_kind=semantic_payload["operation_kind"],
                plan_id=semantic_payload["plan_id"],
                run_id=semantic_payload["run_id"],
                target_label=semantic_payload["target_label"],
                product_revision=_strict_non_negative_int(
                    semantic_payload["product_revision"],
                    "product_revision",
                ),
                payload_digest=semantic_payload["payload_digest"],
                planned_command_digest=semantic_payload[
                    "planned_command_digest"
                ],
                preflight_digest=semantic_payload["preflight_digest"],
                failure_attempt=_strict_non_negative_int(
                    semantic_payload["failure_attempt"],
                    "failure_attempt",
                ),
                failure_digest=semantic_payload["failure_digest"],
                provided_by=semantic_payload["provided_by"],
                allow_refresh=semantic_payload["allow_refresh"] is True,
                observed_at=str(raw.get("observed_at") or ""),
                expires_at=str(raw.get("expires_at") or ""),
                checks=dict(checks),
                semantic_evidence=dict(semantic),
                redacted_summary=dict(summary),
                external_writes_performed=tuple(str(item) for item in writes),
                proof_digest=computed_digest,
            )

        expected = {
            "operation_kind": request.operation_kind,
            "plan_id": request.plan_id,
            "run_id": request.run_id,
            "target_label": request.target_label,
            "product_revision": request.product_revision,
            "payload_digest": request.payload_digest,
            "planned_command_digest": request.planned_command_digest,
            "preflight_digest": request.preflight_digest,
            "failure_attempt": request.failure_attempt,
            "failure_digest": request.failure_digest,
        }
        actual = {field: getattr(proof, field) for field in expected}
        if actual != expected:
            raise TargetScopedContractError(
                "official target proof identity does not match the request"
            )
        if proof.provided_by != "03":
            raise TargetScopedContractError(
                "official target proof must be provided by channel operations"
            )
        if proof.allow_refresh:
            raise TargetScopedContractError(
                "official target proof must use allow_refresh=false"
            )
        if proof.external_writes_performed:
            raise TargetScopedContractError(
                "official target proof must perform zero external writes"
            )
        if any(value is not True for value in proof.checks.values()):
            raise TargetScopedContractError(
                "official target proof did not pass every required check"
            )
        _assert_redacted(proof.semantic_evidence)
        _assert_redacted(proof.redacted_summary, path="redacted_summary")
        observed = _parse_utc(proof.observed_at, "observed_at")
        expires = _parse_utc(proof.expires_at, "expires_at")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if observed > current:
            raise TargetScopedContractError(
                "official target proof observed_at is in the future"
            )
        if expires <= current or expires <= observed:
            raise TargetScopedContractError(
                "official target proof is expired"
            )
        return proof

    def durable_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_kind": self.operation_kind,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "target_label": self.target_label,
            "product_revision": self.product_revision,
            "payload_digest": self.payload_digest,
            "planned_command_digest": self.planned_command_digest,
            "preflight_digest": self.preflight_digest,
            "failure_attempt": self.failure_attempt,
            "failure_digest": self.failure_digest,
            "provided_by": self.provided_by,
            "allow_refresh": self.allow_refresh,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "checks": dict(self.checks),
            "semantic_evidence": dict(self.semantic_evidence),
            "redacted_summary": dict(self.redacted_summary),
            "external_writes_performed": list(
                self.external_writes_performed
            ),
            "proof_digest": self.proof_digest,
        }


@dataclass(frozen=True)
class TargetScopedOperationResult:
    """Normalized, redacted outcome from one channel operation call."""

    succeeded: bool
    readback_verified: bool
    detail: str
    external_reference: str | None
    submission_accepted: bool
    evidence: Mapping[str, Any]

    @classmethod
    def from_value(cls, value: object) -> "TargetScopedOperationResult":
        if isinstance(value, cls):
            result = value
        elif isinstance(value, Mapping):
            raw = dict(value)
            evidence = raw.get("readback_evidence")
            if evidence is None:
                evidence = raw.get("evidence")
            result = cls(
                succeeded=raw.get("succeeded") is True,
                readback_verified=raw.get("readback_verified") is True,
                detail=str(raw.get("detail") or "").strip(),
                external_reference=(
                    str(raw.get("external_reference") or "").strip() or None
                ),
                submission_accepted=raw.get("submission_accepted") is True,
                evidence=dict(evidence or {}),
            )
        else:
            evidence = getattr(value, "readback_evidence", None)
            result = cls(
                succeeded=getattr(value, "succeeded", None) is True,
                readback_verified=(
                    getattr(value, "readback_verified", None) is True
                ),
                detail=str(getattr(value, "detail", "") or "").strip(),
                external_reference=(
                    str(
                        getattr(value, "external_reference", "") or ""
                    ).strip()
                    or None
                ),
                submission_accepted=(
                    getattr(value, "submission_accepted", None) is True
                ),
                evidence=dict(evidence or {}),
            )
        if not result.detail:
            raise TargetScopedContractError(
                "target-scoped adapter result requires detail"
            )
        writes = result.evidence.get("external_writes_performed")
        if not isinstance(writes, (list, tuple)):
            raise TargetScopedContractError(
                "adapter result must explicitly report external_writes_performed"
            )
        _assert_redacted(result.evidence, path="result_evidence")
        return result

    @property
    def external_writes_performed(self) -> list[str]:
        return [
            str(value)
            for value in (
                self.evidence.get("external_writes_performed") or ()
            )
            if str(value)
        ]

    @property
    def outcome(self) -> str:
        if (
            self.succeeded
            and self.readback_verified
            and self.evidence.get("verified") is True
        ):
            return "SUCCEEDED"
        if (
            not self.succeeded
            and not self.readback_verified
            and not self.external_reference
            and not self.submission_accepted
            and self.evidence.get("pre_submit_failure") is True
            and not self.external_writes_performed
        ):
            return "FAILED_PRE_SUBMIT"
        return "RECONCILIATION_REQUIRED"

    def durable_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "target-scoped-operation-result/v1",
            "succeeded": self.succeeded,
            "readback_verified": self.readback_verified,
            "detail": self.detail,
            "external_reference": self.external_reference,
            "submission_accepted": self.submission_accepted,
            "evidence": dict(self.evidence),
            "external_writes_performed": self.external_writes_performed,
            "outcome": self.outcome,
        }
