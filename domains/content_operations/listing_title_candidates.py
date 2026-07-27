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

POLICY_VERSION = "listing-copy-candidates-v4"
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

SYSTEM_PROMPT = """You are a senior cross-border ecommerce listing strategist.
This is not a literal translation task. Use the verified Chinese source facts
to create commercially useful, platform-native product titles. Preserve the
product identity, but optimize word order, search intent, local vocabulary,
readability, and platform conventions for each market.

Use only the verified product facts supplied by the user. Never invent
material, dimensions, quantity, certification, waterproof/removable claims,
brand, compatibility, or performance. Produce natural search-friendly titles,
not keyword lists or translated source-platform noise.

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
  verified facts.
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
    return "sha256:" + hashlib.sha256(
        _canonical(fact_snapshot(facts)).encode("utf-8")
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

    blockers: list[str] = []
    if identity["status"] != "adopted_in_product_facts":
        blockers.append(
            "listing copy must be adopted in approved product facts before release"
        )
    if not identity["input_signature"]:
        blockers.append("listing copy input signature is missing")
    elif identity["input_signature"] != str(current_input_signature or "").strip():
        blockers.append("listing copy input signature is stale")
    approved_title = str(approved_product_title or "").strip()
    if not identity["semantic_master_en"]:
        blockers.append("listing copy semantic English master is missing")
    elif identity["semantic_master_en"] != approved_title:
        blockers.append(
            "listing copy semantic English master differs from approved product title"
        )
    if not identity["policy_version"]:
        blockers.append("listing copy policy version is missing")
    if not identity["model"]:
        blockers.append("listing copy model identity is missing")

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


def _clean_shopee_description(value: Any) -> str:
    description = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    description = "\n".join(
        line
        for line in description.splitlines()
        if not (
            re.search(r"[\u4e00-\u9fff]", line)
            and re.match(r"\s*(?:[-*]\s*)?(?:source\s+)?brand\s*:", line, re.I)
        )
    )
    description = re.sub(r"[ \t]+", " ", description)
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
        if "brand" not in str(key).casefold()
        and "品牌" not in str(key)
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
            raise ValueError(
                "title model output remained invalid after one repair: "
                f"{second_error}"
            ) from second_error

    return {
        "schema_version": "listing-copy-candidates-v4",
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
        "created_at": datetime.now(timezone.utc).isoformat(),
        "external_writes_performed": ["language_model_request"],
        "marketplace_writes_performed": [],
    }
