"""Versioned, local policy for evidence-grounded ecommerce image generation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.config import ROOT


KNOWLEDGE_PATH = ROOT / "knowledge" / "image_generation" / "v1.json"


@lru_cache(maxsize=1)
def load_knowledge() -> dict[str, Any]:
    data = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
        raise ValueError("image generation knowledge must contain a profiles list")
    return data


def resolve_profile(*, title: str = "", category: str = "") -> dict[str, Any]:
    knowledge = load_knowledge()
    haystack = f"{title} {category}".lower()
    fallback = None
    best_match: tuple[int, dict[str, Any]] | None = None
    for profile in knowledge["profiles"]:
        if not isinstance(profile, dict):
            continue
        if profile.get("id") == "generic_product":
            fallback = profile
        for term in profile.get("match_terms") or []:
            normalized = str(term).strip().lower()
            if normalized and normalized in haystack:
                # A specific product type (for example "helmet sticker") must
                # win over a broad supplier taxonomy term such as "sticker".
                score = len(normalized)
                if best_match is None or score > best_match[0]:
                    best_match = (score, profile)
    if best_match is not None:
        return best_match[1]
    if fallback is None:
        raise ValueError("image generation knowledge has no generic_product profile")
    return fallback


def profile_context(profile: dict[str, Any]) -> str:
    """Compact English policy injected into the vision planning request."""
    knowledge = load_knowledge()
    global_rules = knowledge.get("global_rules") or {}
    suite = profile.get("recommended_suite") or []
    suite_lines = [
        f"- {item.get('id')}: {item.get('type')} | {item.get('title')} | {item.get('aspect_ratio')}"
        for item in suite
        if isinstance(item, dict)
    ]
    return "\n".join(
        [
            "CATEGORY KNOWLEDGE POLICY (this policy overrides any generic image-count recipe):",
            f"Profile: {profile.get('id')} - {profile.get('description', '')}",
            "Global rules:",
            f"- Visible text language: {global_rules.get('visual_text_language', 'English only')}",
            f"- Text policy: {global_rules.get('visual_text_policy', '')}",
            f"- Facts policy: {global_rules.get('facts', '')}",
            "Category rules:",
            *[f"- {rule}" for rule in profile.get("planning_rules") or []],
            "Recommended suite (use these IDs/types unless source evidence makes an item unsafe):",
            *suite_lines,
            "Return every title and focus in English.",
        ]
    )


def prompt_rules(*, profile: dict[str, Any]) -> list[str]:
    knowledge = load_knowledge()
    global_rules = knowledge.get("global_rules") or {}
    prohibited = "; ".join(str(value) for value in global_rules.get("prohibited") or [])
    return [
        f"VISIBLE TEXT POLICY: {global_rules.get('visual_text_policy', '')}",
        f"LANGUAGE POLICY: {global_rules.get('visual_text_language', 'English only')}. Do not generate Chinese text.",
        f"FACT POLICY: {global_rules.get('facts', '')}",
        f"CATEGORY POLICY ({profile.get('id')}): " + " ".join(str(rule) for rule in profile.get("planning_rules") or []),
        f"PROHIBITED: {prohibited}.",
    ]


def supported_shot_types() -> set[str]:
    return {"white_bg", "scene", "selling_point", "macro_detail", "size_card"}
