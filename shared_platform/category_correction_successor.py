from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha(value: object, name: str) -> str:
    text = str(value or "").strip().removeprefix("sha256:")
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{name} is invalid")
    return "sha256:" + text


def build_category_correction_successor_payload(
    predecessor_payload: Mapping[str, Any],
    *,
    expected_previous_category: Mapping[str, str],
    corrected_category: Mapping[str, str],
    corrected_category_digest: str,
) -> dict[str, Any]:
    """Clone an approved plan and correct only one demonstrably stale semantic category."""

    if not isinstance(predecessor_payload, Mapping):
        raise TypeError("category correction requires predecessor facts")
    facts = predecessor_payload.get("product_facts")
    digests = predecessor_payload.get("digests")
    if not isinstance(facts, Mapping) or not isinstance(digests, Mapping):
        raise ValueError("category correction predecessor is incomplete")
    if facts.get("category") != dict(expected_previous_category):
        raise ValueError("category correction predecessor drifted")
    if set(corrected_category) != {"id", "name"} or any(
        type(corrected_category.get(field)) is not str
        or not corrected_category[field].strip()
        for field in ("id", "name")
    ):
        raise ValueError("corrected category is invalid")
    title = str(facts.get("title") or "").casefold()
    description = str(facts.get("description") or "").casefold()
    evidence = title + "\n" + description
    if not all(token in evidence for token in ("self-adhesive", "pvc", "wallpaper")):
        raise ValueError("wallpaper category correction lacks exact product evidence")
    if corrected_category["name"] not in {
        "背景墙 > 墙纸、壁纸",
        "Home Supplies > Home Decor > Wallpapers & Wall Coverings",
    }:
        raise ValueError("corrected category is not the approved wallpaper semantic")

    candidate = deepcopy(dict(predecessor_payload))
    candidate["product_facts"]["category"] = dict(corrected_category)
    candidate["digests"]["category"] = _sha(
        corrected_category_digest, "corrected category digest"
    )
    candidate.pop("plan_id", None)
    candidate["plan_id"] = "omnichannel:" + _digest(candidate)
    return candidate


__all__ = ["build_category_correction_successor_payload"]
