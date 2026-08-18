"""Controlled additive successors for already-approved frozen ReleasePlans."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    return value.strip() if type(value) is str else ""


def build_target_only_successor_payload(predecessor_payload: Mapping[str, Any], *, additions: Mapping[str, Mapping[str, Any]], ordered_targets: tuple[str, ...]) -> dict[str, Any]:
    """Append explicit target facts without re-projecting predecessor facts."""
    if not isinstance(predecessor_payload, Mapping):
        raise TypeError("predecessor payload must be a mapping")
    if not isinstance(additions, Mapping) or not additions:
        raise ValueError("target additions are required")
    candidate = deepcopy(dict(predecessor_payload))
    existing_targets = candidate.get("targets")
    if type(existing_targets) is not list or not all(_text(value) for value in existing_targets):
        raise ValueError("predecessor targets are invalid")
    labels = tuple(additions)
    if any(not _text(label) for label in labels) or len(set(labels)) != len(labels):
        raise ValueError("target additions must use unique non-empty labels")
    if set(labels) & set(existing_targets):
        raise ValueError("target addition already exists in predecessor")
    if set(labels) - set(ordered_targets):
        raise ValueError("target addition is unsupported")
    facts, pricing = candidate.get("product_facts"), candidate.get("pricing")
    if not isinstance(facts, dict) or not isinstance(pricing, dict):
        raise ValueError("predecessor frozen facts are invalid")
    categories, selected_targets = facts.get("categories_by_target"), pricing.get("selected_targets")
    if not isinstance(categories, dict) or not isinstance(selected_targets, dict):
        raise ValueError("predecessor category or pricing facts are invalid")
    if set(categories) != set(existing_targets) or set(selected_targets) != set(existing_targets):
        raise ValueError("predecessor target facts are not complete")
    common = selected_targets.get("miaoshou:COMMON")
    common_rows: list[Mapping[str, Any]] = []
    for label in labels:
        addition = additions[label]
        if not isinstance(addition, Mapping) or not isinstance(addition.get("category"), Mapping) or not isinstance(addition.get("pricing"), Mapping):
            raise ValueError("target addition requires category and pricing")
        if addition["category"].get("target_label") != label:
            raise ValueError("target addition category identity conflicts")
        categories[label] = deepcopy(dict(addition["category"]))
        selected_targets[label] = deepcopy(dict(addition["pricing"]))
        row = addition.get("common_store_price")
        if row is not None:
            if not isinstance(common, dict) or type(common.get("store_prices")) is not list or not isinstance(row, Mapping):
                raise ValueError("Miaoshou COMMON aggregate cannot accept an appended row")
            common_rows.append(row)
    if common_rows:
        common["store_prices"] = [*common["store_prices"], *deepcopy(common_rows)]
    selected = set(existing_targets) | set(labels)
    candidate["targets"] = [label for label in ordered_targets if label in selected]
    digests = candidate.get("digests")
    if not isinstance(digests, dict):
        raise ValueError("predecessor digests are invalid")
    digests["category"] = _canonical_digest(categories)
    digests["pricing"] = _canonical_digest(pricing)
    candidate.pop("plan_id", None)
    candidate["plan_id"] = "omnichannel:" + _canonical_digest(candidate)
    return candidate


__all__ = ["build_target_only_successor_payload"]
