"""Audited model-generated listing title candidates for a new product."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from modules.sourcing.image_suite_plan import chat_completions, message_content

POLICY_VERSION = "listing-title-candidates-v2"
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


def fact_signature(facts: dict[str, Any]) -> str:
    relevant = {
        "source_title_zh": facts.get("source_title_zh") or "",
        "category": facts.get("category") or {},
        "package_cm": facts.get("package_cm") or [],
        "selected_skus": facts.get("selected_skus") or [],
        "verified_attributes": facts.get("verified_attributes") or {},
    }
    return "sha256:" + hashlib.sha256(_canonical(relevant).encode("utf-8")).hexdigest()


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


def generate_title_candidates(
    facts: dict[str, Any],
    *,
    model_call: Callable[..., str] = toapi_title_completion,
) -> dict[str, Any]:
    """Generate local candidates; never approve or write a marketplace."""

    source_title = str(facts.get("source_title_zh") or "").strip()
    if not source_title:
        raise ValueError("source_title_zh is required before title generation")
    request_facts = {
        **facts,
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
    raw = model_call(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Verified product facts:\n"
                + json.dumps(request_facts, ensure_ascii=False, indent=2),
            },
        ],
        temperature=0.25,
        max_tokens=1800,
    )
    parsed = _json_object(raw)
    master = _clean_title(parsed.get("semantic_master_en"), limit=180)
    if not re.search(r"[A-Za-z]", master) or re.search(r"[\u4e00-\u9fff]", master):
        raise ValueError("semantic_master_en must be English without Chinese text")

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

    return {
        "schema_version": "listing-title-candidates-v2",
        "provider": "toapi",
        "status": "draft_pending_kyle_review",
        "semantic_master_en": master,
        "candidates": candidates,
        "notes_zh": str(parsed.get("notes_zh") or "").strip(),
        "input_signature": fact_signature(facts),
        "policy_version": POLICY_VERSION,
        "model": TOAPI_TITLE_MODEL,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "external_writes_performed": ["language_model_request"],
        "marketplace_writes_performed": [],
    }
