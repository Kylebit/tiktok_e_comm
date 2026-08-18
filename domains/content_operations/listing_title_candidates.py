"""Audited model-generated listing title candidates for a new product."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Any, Callable

from modules.sourcing.image_suite_plan import chat_completions, message_content

POLICY_VERSION = "listing-copy-candidates-v8"
TOAPI_TITLE_MODEL = "gpt-5.4-mini-official"
EXPECTED_TARGETS = (
    ("tiktok", "MY", "English / Malay", 255),
    ("tiktok", "PH", "English", 255),
    ("tiktok", "TH", "Thai", 255),
    ("tiktok", "VN", "Vietnamese", 255),
    ("tiktok", "MX", "Spanish (Mexico)", 255),
    ("tiktok", "GB", "English (UK)", 255),
    ("shopee", "CNSC", "English", 120),
    ("ozon", "RU", "Russian", 200),
)

_TIMESTAMP_FIELDS = frozenset(
    {
        "created_at",
        "updated_at",
        "generated_at",
        "adopted_at",
        "reviewed_at",
    }
)

_TITLE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "for",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
    }
)
_UNAPPROVED_VARIANT_NOISE = re.compile(
    r"\b(?:as\s+shown|picture\s+colou?r|default|random\s+colou?r)\b",
    re.I,
)

SYSTEM_PROMPT = """You are a senior cross-border ecommerce listing strategist.
This is not a literal translation task. Use the verified Chinese source facts
to create commercially useful, platform-native product titles. Preserve the
product identity, but optimize word order, search intent, local vocabulary,
readability, and platform conventions for each market.

Use only the verified product facts supplied by the user. Never invent
material, dimensions, quantity, certification, waterproof/removable claims,
brand, compatibility, or performance. Produce natural search-friendly titles,
not keyword lists or translated source-platform noise.
Never include any brand name or internal/source/platform identifier in the
Shopee description, including Seller SKU, SKU, item code, product code,
Product ID, Item ID, Offer ID, or source ID.
The Shopee description must contain only information about the product itself:
product identity, verified design and attributes, selected sellable options,
intended use, application guidance, package contents and factual product
cautions. Never include origin or manufacturing location, payment support or
payment methods, price or discounts, stock, shipping, delivery or logistics,
seller/storefront/supplier details, contact or customer service, warranty,
after-sales service, returns or refunds, wholesale/export/MOQ terms.

Return strict JSON only:
{
  "semantic_master_en": "fact-grounded English product title, <=180 chars",
  "shopee_description_en": "fact-grounded English Shopee global description, 700-1800 chars",
  "candidates": [
    {"channel":"tiktok","site":"MY","language":"English / Malay","title":"..."},
    {"channel":"tiktok","site":"PH","language":"English","title":"..."},
    {"channel":"tiktok","site":"TH","language":"Thai","title":"..."},
    {"channel":"tiktok","site":"VN","language":"Vietnamese","title":"..."},
    {"channel":"tiktok","site":"MX","language":"Spanish (Mexico)","title":"..."},
    {"channel":"tiktok","site":"GB","language":"English (UK)","title":"..."},
    {"channel":"shopee","site":"CNSC","language":"English","title":"..."},
    {"channel":"ozon","site":"RU","language":"Russian","title":"..."}
  ],
  "notes_zh": "brief Chinese explanation of choices and uncertainty"
}

Platform strategy:
- TikTok Shop: lead with the recognizable product type and strongest verified
  visual/use-context phrase; keep the title scannable on mobile.
- Shopee CNSC: use concise English search phrases without repetition.
- Shopee description: write a useful English global-master description with
  short readable sections for product overview, verified details, suitable
  spaces, application guidance, package contents, and factual cautions. It
  will be translated by Shopee when imported into local shops, so keep the
  English clear and literal. Do not output Chinese characters anywhere in
  semantic_master_en or shopee_description_en. If a source brand or attribute
  value contains Chinese, omit that value instead of copying, translating, or
  transliterating it. Do not mention a claim unless it is present in the
  verified facts. Do not turn commercial, transaction, logistics or seller
  metadata into description content even when it appears in the source facts.
- Ozon RU: write natural Russian retail copy, not transliterated English.
- Localize meaning and search phrasing for each site; do not merely translate
  the English master word for word.

Rules: no emoji, ALL CAPS, superlatives, medical claims, unsupported promises,
or source-platform words. Put the product type and strongest verified visual
attribute early. Respect the platform limits supplied in the facts."""


def toapi_title_completion(
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    """Use the configured ToAPI gateway for a text-only title request."""

    response = chat_completions(
        messages,
        model=TOAPI_TITLE_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return message_content(response)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_decimal(value: Any) -> str | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if not parsed.is_finite():
        return str(value)
    normalized = format(parsed.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def fact_snapshot(facts: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable product facts that govern candidate freshness.

    ``verified_attributes`` are deliberately excluded. Miaoshou readback can
    enrich that mapping after a COMMON draft write without changing the
    product facts Kyle approved. The full model request remains fingerprinted
    separately for audit.
    """

    selected_skus = []
    for row in facts.get("selected_skus") or ():
        if not isinstance(row, dict):
            continue
        selected_skus.append(
            {
                "key": str(row.get("key") or "").strip(),
                "label": str(row.get("label") or "").strip(),
            }
        )
    return {
        "offer_id": str(facts.get("offer_id") or "").strip(),
        "source_title_zh": facts.get("source_title_zh") or "",
        "category": facts.get("category") or {},
        "cost_cny": _canonical_decimal(facts.get("cost_cny")),
        "weight_kg": _canonical_decimal(facts.get("weight_kg")),
        "package_cm": [
            _canonical_decimal(value)
            for value in (facts.get("package_cm") or [])
        ],
        "selected_skus": selected_skus,
    }


def fact_signature(facts: dict[str, Any]) -> str:
    snapshot = fact_snapshot(facts)
    # Copy freshness is intentionally narrower than the complete audited fact
    # snapshot. Cost, parcel weight and package dimensions drive pricing and
    # the immutable ReleasePlan, but they do not change the product identity
    # expressed by the approved listing copy.
    copy_freshness = {
        key: snapshot[key]
        for key in (
            "offer_id",
            "source_title_zh",
            "category",
            "selected_skus",
        )
    }
    return "sha256:" + hashlib.sha256(
        _canonical(copy_freshness).encode("utf-8")
    ).hexdigest()


def model_input_signature(facts: dict[str, Any]) -> str:
    relevant = {
        **fact_snapshot(facts),
        "verified_attributes": facts.get("verified_attributes") or {},
    }
    return "sha256:" + hashlib.sha256(_canonical(relevant).encode("utf-8")).hexdigest()


def _stable_release_value(value: Any) -> Any:
    """Remove operational timestamps while preserving commercial evidence."""

    if isinstance(value, Mapping):
        return {
            str(key): _stable_release_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _TIMESTAMP_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_stable_release_value(item) for item in value]
    return value


def _required_candidate_keys(
    target_labels: Iterable[object],
) -> tuple[tuple[str, str], ...]:
    required: set[tuple[str, str]] = set()
    for raw_label in target_labels:
        channel, separator, raw_site = str(raw_label or "").partition(":")
        if not separator:
            continue
        channel = channel.casefold()
        site = raw_site.upper()
        if channel == "tiktok":
            required.add(("tiktok", site.rsplit("_", 1)[-1]))
        elif channel == "shopee":
            required.add(("shopee", "CNSC"))
        elif channel == "ozon":
            required.add(("ozon", site))
    return tuple(sorted(required))


def release_listing_copy_identity(
    listing_copy: Mapping[str, Any] | None,
    *,
    approved_product_title: object,
    current_input_signature: object,
    target_labels: Iterable[object],
) -> tuple[dict[str, Any], list[str]]:
    """Return stable ReleasePlan copy identity plus approval blockers.

    Model timestamps are deliberately excluded. Every commercial candidate
    field remains in the identity, so a title/policy/model/description change
    creates a different plan and confirmation token.
    """

    source = dict(listing_copy or {})
    candidates = [
        _stable_release_value(row)
        for row in (source.get("candidates") or ())
        if isinstance(row, Mapping)
    ]
    candidates.sort(
        key=lambda row: (
            str(row.get("channel") or "").casefold(),
            str(row.get("site") or "").upper(),
            _canonical(row),
        )
    )
    description = str(source.get("shopee_description_en") or "").strip()
    identity = {
        "schema_version": str(source.get("schema_version") or "").strip(),
        "status": str(source.get("status") or "").strip(),
        "provider": str(source.get("provider") or "").strip(),
        "policy_version": str(source.get("policy_version") or "").strip(),
        "model": str(source.get("model") or "").strip(),
        "input_signature": str(source.get("input_signature") or "").strip(),
        "semantic_master_en": str(
            source.get("semantic_master_en") or ""
        ).strip(),
        "shopee_description_digest": (
            "sha256:"
            + hashlib.sha256(description.encode("utf-8")).hexdigest()
            if description
            else ""
        ),
        "candidates": candidates,
    }

    # Workflow metadata remains part of the immutable identity for audit and
    # drift detection, but it must not ask the operator to approve the same
    # commercial facts twice.  Release eligibility is based only on fields
    # that are actually sent to a storefront below.
    blockers: list[str] = []

    by_key = {
        (
            str(row.get("channel") or "").casefold(),
            str(row.get("site") or "").upper(),
        ): row
        for row in candidates
    }
    for channel, site in _required_candidate_keys(target_labels):
        candidate = by_key.get((channel, site))
        if (
            not candidate
            or str(candidate.get("policy_check") or "") != "passed"
            or not str(candidate.get("title") or "").strip()
        ):
            blockers.append(
                f"approved listing title candidate is missing for {channel}:{site}"
            )
    if any(str(label).startswith("shopee:") for label in target_labels):
        if not identity["shopee_description_digest"]:
            blockers.append("approved Shopee global description is missing")
    return identity, list(dict.fromkeys(blockers))


def _json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError("title model did not return a JSON object")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("title model response must be a JSON object")
    return parsed


def _clean_title(value: Any, *, limit: int) -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n\"'|")
    if not title:
        raise ValueError("title model returned an empty candidate")
    if len(title) > limit:
        raise ValueError(f"title candidate exceeds the {limit}-character platform limit")
    if re.search(r"[\U0001F300-\U0001FAFF]", title):
        raise ValueError("title candidate contains emoji")
    return title


def _english_title_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in _TITLE_STOPWORDS
    }


def _validate_semantic_master_title(master: str) -> None:
    if _UNAPPROVED_VARIANT_NOISE.search(master):
        raise ValueError(
            "semantic_master_en contains a variant label instead of product identity"
        )


def _validate_shopee_master_coverage(master: str, candidate: str) -> None:
    master_tokens = _english_title_tokens(master)
    candidate_tokens = _english_title_tokens(candidate)
    identity_clause = re.split(r"[,;|]", master, maxsplit=1)[0]
    identity_tokens = _english_title_tokens(identity_clause)
    if not identity_tokens or not identity_tokens.issubset(candidate_tokens):
        missing = sorted(identity_tokens - candidate_tokens)
        raise ValueError(
            "shopee:CNSC candidate drops approved product identity terms: "
            + ", ".join(missing)
        )
    coverage = (
        len(master_tokens & candidate_tokens) / len(master_tokens)
        if master_tokens
        else 0
    )
    minimum = 0.55 if len(master) > 120 else 0.70
    if coverage < minimum:
        raise ValueError(
            "shopee:CNSC candidate semantic coverage is too low "
            f"({coverage:.3f} < {minimum:.3f})"
        )


def _deterministic_shopee_title(master: str) -> str:
    """Preserve identity deterministically when the model candidate is unsafe."""

    if len(master) <= 120:
        return _clean_title(master, limit=120)
    natural_prefix = master[:120].rsplit(" ", 1)[0].rstrip(" ,;-")
    while natural_prefix:
        tail = re.findall(r"[A-Za-z0-9]+", natural_prefix.casefold())
        if not tail or tail[-1] not in _TITLE_STOPWORDS:
            break
        natural_prefix = natural_prefix.rsplit(" ", 1)[0].rstrip(" ,;-")
    natural_prefix = _clean_title(natural_prefix, limit=120)
    try:
        _validate_shopee_master_coverage(master, natural_prefix)
        return natural_prefix
    except ValueError:
        pass

    identity_clause = re.split(r"[,;|]", master, maxsplit=1)[0].strip()
    identity_tokens = _english_title_tokens(identity_clause)
    if len(identity_clause) > 120:
        identity_words: list[str] = []
        seen_identity_tokens: set[str] = set()
        for word in re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", identity_clause):
            token = word.casefold()
            if token in _TITLE_STOPWORDS or token in seen_identity_tokens:
                continue
            if token in identity_tokens:
                identity_words.append(word)
                seen_identity_tokens.add(token)
        identity_clause = " ".join(identity_words)
        if len(identity_clause) > 120:
            raise ValueError(
                "approved Shopee identity terms cannot fit the platform title limit"
            )
    dimension = re.search(
        r"\b\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\s*(?:cm|mm|m|in)\b",
        master,
        re.I,
    )
    candidate = identity_clause
    if dimension and dimension.group(0).casefold() not in candidate.casefold():
        proposed = f"{candidate}, {dimension.group(0)}"
        if len(proposed) <= 120:
            candidate = proposed

    candidate_tokens = _english_title_tokens(candidate)
    for word in re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", master):
        token = word.casefold()
        if token in _TITLE_STOPWORDS or token in candidate_tokens:
            continue
        proposed = f"{candidate}, {word}"
        if len(proposed) > 120:
            continue
        candidate = proposed
        candidate_tokens.add(token)
        try:
            _validate_shopee_master_coverage(master, candidate)
            break
        except ValueError:
            continue

    candidate = _clean_title(candidate, limit=120)
    _validate_shopee_master_coverage(master, candidate)
    return candidate


_INTERNAL_IDENTITY_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:source\s+)?(?:brand(?:\s+name)?|seller\s+sku|sku|"
    r"item\s+code|product\s+code|product\s+id|item\s+id|offer\s+id|source\s+id)\s*[:=-]",
    re.I,
)
_NON_PRODUCT_DESCRIPTION_LINE_RE = re.compile(
    r"(?:\b(?:country|place)\s+of\s+origin\b|\borigin\s*[:=-]|\bmade\s+in\b|"
    r"\bmanufacturer\s+location\b|\bpayment(?:\s+(?:support|methods?|options?))?\b|"
    r"\bcash\s+on\s+delivery\b|\bCOD\b|\bprice\s*[:=-]|\bdiscounts?\s*[:=-]|"
    r"\b(?:stock|inventory)\s*[:=-]|\b(?:shipping|delivery|logistics)\b|"
    r"\b(?:seller|storefront|supplier)(?:\s+information)?\b|"
    r"\bcontact\s+(?:information|the\s+seller)\b|\bcustomer\s+service\b|"
    r"\bwarrant(?:y|ies)\b|\bafter[- ]sales(?:\s+service)?\b|"
    r"\breturns?\s*[:=-]|\brefunds?\b|\bwholesale\b|\bexport\s+terms?\b|"
    r"\bminimum\s+order(?:\s+quantity)?\b|\bMOQ\b)",
    re.I,
)
_NON_PRODUCT_ATTRIBUTE_KEY_RE = re.compile(
    r"(?:brand|origin|country\s+of\s+origin|place\s+of\s+origin|payment|"
    r"shipping|delivery|logistics|seller|storefront|supplier|after[- ]sales|"
    r"returns?|refunds?|warrant(?:y|ies)|wholesale|minimum\s+order|moq|"
    r"品牌|产地|原产地|支付|付款|物流|发货|运输|配送|店铺|卖家|供应商|"
    r"售后|退货|退款|保修|批发|起订)",
    re.I,
)


def _clean_shopee_description(value: Any) -> str:
    description = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    description = "\n".join(
        line
        for line in description.splitlines()
        if not _INTERNAL_IDENTITY_LINE_RE.match(line)
        and not _NON_PRODUCT_DESCRIPTION_LINE_RE.search(line)
    )
    description = re.sub(r"[ \t]+", " ", description)
    description = re.sub(
        r"\b(?:source\s+)?(?:brand(?:\s+name)?|seller\s+sku|sku|item\s+code|"
        r"product\s+code|product\s+id|item\s+id|offer\s+id|source\s+id)"
        r"\s*[:=-]\s*[^.\n;]+[.;]?",
        "",
        description,
        flags=re.I,
    )
    description = re.sub(r"\n{3,}", "\n\n", description).strip()
    if len(description) < 500:
        raise ValueError("Shopee global description is too short")
    if len(description) > 3000:
        raise ValueError("Shopee global description exceeds 3000 characters")
    if re.search(r"[\u4e00-\u9fff\u0e00-\u0e7f]", description):
        raise ValueError("Shopee global description must be English")
    if not re.search(r"[A-Za-z]", description):
        raise ValueError("Shopee global description must contain English text")
    return description


def _deterministic_shopee_description(
    facts: Mapping[str, Any],
    *,
    semantic_master_en: str,
) -> str:
    """Build a conservative English fallback from verified local facts."""

    title = _clean_title(semantic_master_en, limit=180)
    selected_labels = [
        re.sub(r"\s+", " ", str(row.get("label") or "")).strip()
        for row in (facts.get("selected_skus") or ())
        if isinstance(row, Mapping)
    ]
    selected_labels = [
        label
        for label in selected_labels
        if label
        and re.search(r"[A-Za-z]", label)
        and not re.search(r"[\u4e00-\u9fff\u0e00-\u0e7f]", label)
    ]
    variant_line = (
        "Selected listing option: " + ", ".join(selected_labels) + "."
        if selected_labels
        else (
            "The available product option is identified by the product facts "
            "and images."
        )
    )
    description = (
        "PRODUCT OVERVIEW\n"
        f"{title}. Review the design, selected option and intended use to make "
        "sure this product suits the planned space or application. The images "
        "show the product appearance, colour arrangement, shape and visible "
        "included elements.\n\n"
        "PRODUCT DETAILS\n"
        f"Product type: {title}.\n"
        f"{variant_line}\n\n"
        "DESIGN AND APPEARANCE\n"
        "Use the product images as the visual reference for the pattern, colour, "
        "shape, surface appearance and included visible elements. Screen "
        "settings and room lighting may make colours appear slightly different. "
        "Check the complete image set when comparing the design with the planned "
        "setting.\n\n"
        "INTENDED USE AND APPLICATION\n"
        "Choose a suitable placement and use the product only for the purpose "
        "shown in the product details and images. Prepare the intended surface or "
        "space as appropriate for this product type. Follow any care, assembly or "
        "application instructions included with the product.\n\n"
        "SELECTION AND PACKAGE CONTENTS\n"
        "The selected option identifies the product variation covered by this "
        "description. Compare the option label with the product images before use. "
        "Package contents correspond to the selected variation and the visible "
        "included pieces shown for that option.\n\n"
        "PRODUCT NOTES\n"
        "Keep the product in conditions suitable for its material and intended "
        "purpose. Colour appearance can vary slightly between screens and room "
        "lighting. Product performance is limited to the characteristics stated "
        "in this description and shown for the selected variation."
    )
    return _clean_shopee_description(description)


def _validate_language(title: str, *, channel: str, site: str) -> None:
    if site == "TH" and not re.search(r"[\u0e00-\u0e7f]", title):
        raise ValueError(f"{channel}:{site} candidate is not Thai")
    if site == "RU" and not re.search(r"[\u0400-\u04ff]", title):
        raise ValueError(f"{channel}:{site} candidate is not Russian")
    if site in {"PH", "GB", "CNSC"} and (
        not re.search(r"[A-Za-z]", title) or re.search(r"[\u4e00-\u9fff]", title)
    ):
        raise ValueError(f"{channel}:{site} candidate is not an English title")
    if site in {"VN", "MX"} and re.search(r"[\u4e00-\u9fff\u0400-\u04ff]", title):
        raise ValueError(f"{channel}:{site} candidate uses the wrong writing system")


def _validated_model_payload(raw: str) -> tuple[str, str, list[dict[str, Any]], str]:
    parsed = _json_object(raw)
    master = _clean_title(parsed.get("semantic_master_en"), limit=180)
    if not re.search(r"[A-Za-z]", master) or re.search(r"[\u4e00-\u9fff]", master):
        raise ValueError("semantic_master_en must be English without Chinese text")
    _validate_semantic_master_title(master)
    shopee_description = _clean_shopee_description(
        parsed.get("shopee_description_en")
    )

    received: dict[tuple[str, str], dict[str, Any]] = {}
    for row in parsed.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        key = (
            str(row.get("channel") or "").strip().casefold(),
            str(row.get("site") or "").strip().upper(),
        )
        received[key] = row

    candidates: list[dict[str, Any]] = []
    for channel, site, language, limit in EXPECTED_TARGETS:
        row = received.get((channel, site))
        if row is None:
            raise ValueError(f"title model omitted {channel}:{site}")
        title = _clean_title(row.get("title"), limit=limit)
        _validate_language(title, channel=channel, site=site)
        if channel == "shopee" and site == "CNSC":
            _validate_shopee_master_coverage(master, title)
        candidates.append(
            {
                "channel": channel,
                "site": site,
                "language": language,
                "limit": limit,
                "title": title,
                "policy_check": "passed",
            }
        )
    return (
        master,
        shopee_description,
        candidates,
        str(parsed.get("notes_zh") or "").strip(),
    )


def generate_title_candidates(
    facts: dict[str, Any],
    *,
    model_call: Callable[..., str] = toapi_title_completion,
) -> dict[str, Any]:
    """Generate local candidates; never approve or write a marketplace."""

    source_title = str(facts.get("source_title_zh") or "").strip()
    if not source_title:
        raise ValueError("source_title_zh is required before title generation")
    verified_attributes = facts.get("verified_attributes")
    safe_verified_attributes = {
        key: value
        for key, value in (
            verified_attributes.items()
            if isinstance(verified_attributes, dict)
            else ()
        )
        if not _NON_PRODUCT_ATTRIBUTE_KEY_RE.search(str(key))
    }
    request_facts = {
        **facts,
        "verified_attributes": safe_verified_attributes,
        "platform_limits": [
            {
                "channel": channel,
                "site": site,
                "language": language,
                "max_characters": limit,
            }
            for channel, site, language, limit in EXPECTED_TARGETS
        ],
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Verified product facts:\n"
            + json.dumps(request_facts, ensure_ascii=False, indent=2),
        },
    ]
    raw = model_call(
        messages,
        temperature=0.25,
        max_tokens=1800,
    )
    generation_attempts = 1
    repair_performed = False
    description_fallback_used = False
    title_fallback_used = False
    try:
        master, shopee_description, candidates, notes_zh = (
            _validated_model_payload(raw)
        )
    except (json.JSONDecodeError, TypeError, ValueError) as first_error:
        repair_performed = True
        generation_attempts = 2
        repaired_raw = model_call(
            [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                    + "\n\nThis is a validation repair pass. Return a complete "
                    "replacement JSON object. Correct the stated validation "
                    "failure without weakening or inventing product facts.",
                },
                {
                    "role": "user",
                    "content": (
                        "Verified product facts:\n"
                        + json.dumps(request_facts, ensure_ascii=False, indent=2)
                        + "\n\nValidation failure:\n"
                        + str(first_error)
                        + "\n\nRejected response:\n"
                        + str(raw)
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        try:
            master, shopee_description, candidates, notes_zh = (
                _validated_model_payload(repaired_raw)
            )
        except (json.JSONDecodeError, TypeError, ValueError) as second_error:
            repaired_payload = _json_object(repaired_raw)
            repaired_master = _clean_title(
                repaired_payload.get("semantic_master_en"),
                limit=180,
            )
            if (
                not re.search(r"[A-Za-z]", repaired_master)
                or re.search(r"[\u4e00-\u9fff]", repaired_master)
            ):
                raise ValueError(
                    "title model output remained invalid after one repair: "
                    f"{second_error}"
                ) from second_error
            _validate_semantic_master_title(repaired_master)

            validation_error: Exception = second_error
            for _ in range(3):
                message = str(validation_error)
                if (
                    message.startswith("shopee:CNSC candidate")
                    and not title_fallback_used
                ):
                    rows = repaired_payload.get("candidates")
                    shopee_rows = [
                        row
                        for row in rows or ()
                        if isinstance(row, dict)
                        and str(row.get("channel") or "").casefold()
                        == "shopee"
                        and str(row.get("site") or "").upper() == "CNSC"
                    ]
                    if not isinstance(rows, list) or len(shopee_rows) != 1:
                        break
                    shopee_rows[0]["title"] = _deterministic_shopee_title(
                        repaired_master
                    )
                    title_fallback_used = True
                elif (
                    message
                    in {
                        "Shopee global description must be English",
                        "Shopee global description must contain English text",
                    }
                    and not description_fallback_used
                ):
                    repaired_payload["shopee_description_en"] = (
                        _deterministic_shopee_description(
                            facts,
                            semantic_master_en=repaired_master,
                        )
                    )
                    description_fallback_used = True
                else:
                    break
                try:
                    master, shopee_description, candidates, notes_zh = (
                        _validated_model_payload(_canonical(repaired_payload))
                    )
                    break
                except (
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                ) as followup_error:
                    validation_error = followup_error
            else:
                validation_error = ValueError(
                    "deterministic repair budget exhausted"
                )
            if "master" not in locals() or master != repaired_master:
                raise ValueError(
                    "title model output remained invalid after one repair: "
                    f"{validation_error}"
                ) from validation_error

    return {
        "schema_version": "listing-copy-candidates-v8",
        "provider": "toapi",
        "status": "draft_pending_kyle_review",
        "semantic_master_en": master,
        "shopee_description_en": shopee_description,
        "candidates": candidates,
        "notes_zh": notes_zh,
        "input_signature": fact_signature(facts),
        "fact_snapshot": fact_snapshot(facts),
        "model_input_signature": model_input_signature(facts),
        "policy_version": POLICY_VERSION,
        "model": TOAPI_TITLE_MODEL,
        "generation_attempts": generation_attempts,
        "repair_performed": repair_performed,
        "description_fallback_used": description_fallback_used,
        "title_fallback_used": title_fallback_used,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "external_writes_performed": ["language_model_request"],
        "marketplace_writes_performed": [],
    }
