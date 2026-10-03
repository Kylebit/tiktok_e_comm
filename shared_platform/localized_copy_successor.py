"""Controlled target-copy successors for frozen publication plans."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping


_TARGET_LOCALES = {
    "shopee:MY": "ms-MY",
    "shopee:TH": "th-TH",
    "shopee:VN": "vi-VN",
}


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


def build_localized_copy_successor_payload(
    predecessor_payload: Mapping[str, Any],
    *,
    predecessor_business_snapshot: Mapping[str, Any],
    localized_descriptions: Mapping[str, str],
) -> dict[str, Any]:
    """Change only Shopee MY/TH/VN descriptions and their content digest.

    The predecessor plan and its approval-neutral business snapshot must agree.
    Titles, locales, PH copy, target scope and every non-copy business fact are
    retained byte-for-byte in the detached successor payload.
    """

    if not isinstance(predecessor_payload, Mapping) or not isinstance(
        predecessor_business_snapshot, Mapping
    ):
        raise TypeError("localized copy successor requires frozen predecessor facts")
    if not isinstance(localized_descriptions, Mapping) or set(
        localized_descriptions
    ) != set(_TARGET_LOCALES):
        raise ValueError("localized description coverage must be exactly Shopee MY/TH/VN")

    plan_id = str(predecessor_payload.get("plan_id") or "")
    snapshot_plan_id = str(predecessor_business_snapshot.get("plan_id") or "")
    if not plan_id or snapshot_plan_id != plan_id:
        raise ValueError("localized copy predecessor plan identity drifted")
    _sha(
        predecessor_business_snapshot.get("business_snapshot_digest"),
        "predecessor business snapshot digest",
    )

    targets = predecessor_payload.get("targets")
    snapshot_targets = predecessor_business_snapshot.get("publication_targets")
    facts = predecessor_payload.get("product_facts")
    snapshot_product = predecessor_business_snapshot.get("product")
    snapshot_target_labels = (
        [row.get("target_label") for row in snapshot_targets]
        if type(snapshot_targets) is list
        and all(isinstance(row, Mapping) for row in snapshot_targets)
        else None
    )
    if type(targets) is not list or targets != snapshot_target_labels:
        raise ValueError("localized copy predecessor target scope drifted")
    if not isinstance(facts, Mapping) or not isinstance(snapshot_product, Mapping):
        raise ValueError("localized copy predecessor facts are incomplete")
    content = facts.get("content_by_target")
    snapshot_content = snapshot_product.get("content_by_target")
    if not isinstance(content, Mapping) or content != snapshot_content:
        raise ValueError("localized copy predecessor content drifted")
    if not set(_TARGET_LOCALES).issubset(set(targets)) or "shopee:PH" not in targets:
        raise ValueError("localized copy predecessor Shopee scope is incomplete")

    normalized: dict[str, str] = {}
    for target, locale in _TARGET_LOCALES.items():
        row = content.get(target)
        if not isinstance(row, Mapping) or row.get("locale") != locale:
            raise ValueError(f"{target} predecessor locale drifted")
        description = localized_descriptions[target]
        if type(description) is not str or not description.strip():
            raise ValueError(f"{target} localized description is invalid")
        if description.strip() == row.get("description"):
            raise ValueError(f"{target} localized description did not change")
        normalized[target] = description.strip()

    candidate = deepcopy(dict(predecessor_payload))
    candidate_content = candidate["product_facts"]["content_by_target"]
    for target, description in normalized.items():
        candidate_content[target]["description"] = description
    candidate["digests"]["content"] = "sha256:" + _digest(candidate_content)
    candidate.pop("plan_id", None)
    candidate["plan_id"] = "omnichannel:" + _digest(candidate)
    return candidate


def build_localized_copy_successor_candidate(
    predecessor_candidate: Mapping[str, Any],
    *,
    compiled_candidate: Mapping[str, Any],
    localized_descriptions: Mapping[str, str],
) -> dict[str, Any]:
    """Carry a copy-only successor into the complete predecessor review packet."""

    if not isinstance(predecessor_candidate, Mapping) or not isinstance(
        compiled_candidate, Mapping
    ):
        raise TypeError("localized copy candidate requires predecessor review facts")
    if set(localized_descriptions) != set(_TARGET_LOCALES):
        raise ValueError("localized candidate coverage must be exactly Shopee MY/TH/VN")
    predecessor = deepcopy(dict(predecessor_candidate))
    supplied_digest = predecessor.pop("candidate_digest", None)
    if supplied_digest != _digest(predecessor):
        raise ValueError("predecessor candidate digest is invalid")
    predecessor["candidate_digest"] = supplied_digest
    if compiled_candidate.get("status") != "READY_FOR_FINAL_REVIEW" or compiled_candidate.get("blockers"):
        raise ValueError("compiled localized candidate is blocked")
    for key in ("offer_id", "product_revision", "target_labels", "platform_scope", "variant_count"):
        if compiled_candidate.get(key) != predecessor.get(key):
            raise ValueError(f"compiled localized candidate {key} drifted")

    compiled_copy: dict[str, Mapping[str, Any]] = {}
    for group in (compiled_candidate.get("review_manifest") or {}).get("copy_sets") or ():
        if isinstance(group, Mapping):
            for target in group.get("target_labels") or ():
                compiled_copy[str(target)] = group
    old_manifest = predecessor.get("review_manifest")
    if not isinstance(old_manifest, Mapping):
        raise ValueError("predecessor review manifest is missing")
    old_groups = old_manifest.get("copy_sets")
    if type(old_groups) is not list:
        raise ValueError("predecessor copy sets are invalid")

    new_groups: list[dict[str, Any]] = []
    replacement_ids: dict[str, str] = {}
    for old_group in old_groups:
        if not isinstance(old_group, Mapping):
            raise ValueError("predecessor copy set is invalid")
        remaining = [
            target for target in old_group.get("target_labels") or ()
            if target not in localized_descriptions
        ]
        if remaining:
            retained = deepcopy(dict(old_group))
            retained["target_labels"] = remaining
            new_groups.append(retained)
        for target in old_group.get("target_labels") or ():
            if target not in localized_descriptions:
                continue
            compiled = compiled_copy.get(target)
            if (
                not isinstance(compiled, Mapping)
                or compiled.get("title") != old_group.get("title")
                or compiled.get("language") != old_group.get("language")
                or compiled.get("description") != localized_descriptions[target]
            ):
                raise ValueError(f"{target} compiled copy identity drifted")
            copy_body = {
                "title": old_group.get("title"),
                "description": localized_descriptions[target],
                "language": old_group.get("language"),
            }
            copy_id = "copy-" + _digest(copy_body)[:12]
            replacement_ids[target] = copy_id
            new_groups.append(
                {
                    "copy_set_id": copy_id,
                    "language": old_group.get("language"),
                    "title": old_group.get("title"),
                    "description": localized_descriptions[target],
                    "target_labels": [target],
                }
            )
    if set(replacement_ids) != set(localized_descriptions):
        raise ValueError("predecessor localized copy coverage is incomplete")

    candidate = deepcopy(predecessor)
    candidate["plan_id"] = str(compiled_candidate.get("plan_id") or "")
    candidate["snapshot_digest"] = _sha(
        compiled_candidate.get("snapshot_digest"), "successor business snapshot digest"
    )
    manifest = candidate["review_manifest"]
    manifest["copy_sets"] = new_groups
    for row in manifest.get("targets") or ():
        target = row.get("target_label") if isinstance(row, Mapping) else None
        if target in replacement_ids:
            row["copy_set_id"] = replacement_ids[target]
    manifest.pop("manifest_digest", None)
    manifest["manifest_digest"] = _digest(manifest)
    candidate.pop("candidate_digest", None)
    candidate["candidate_digest"] = _digest(candidate)
    return candidate


__all__ = [
    "build_localized_copy_successor_candidate",
    "build_localized_copy_successor_payload",
]
