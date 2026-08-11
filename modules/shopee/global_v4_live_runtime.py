"""Official Shopee CNSC runtime for the frozen-v4 global master.

This module is the provider edge for :mod:`modules.shopee.global_v4_executor`.
It never reads mutable Product Center facts.  Category selection starts from
the frozen user-approved semantic category and may choose only one official,
publishable Shopee leaf with an exact semantic alias.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from copy import deepcopy
import re
import unicodedata
from typing import Any


class ShopeeGlobalV4LiveRuntimeError(RuntimeError):
    """Official Shopee facts are missing, ambiguous, or conflict with v4."""


def _semantic_key(value: object) -> str:
    if type(value) is not str:
        return ""
    return re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+",
        "",
        unicodedata.normalize("NFKC", value).strip().casefold(),
    )


_EXACT_CATEGORY_ALIASES = {
    "冰箱贴": frozenset(
        {
            "冰箱贴",
            "fridgemagnet",
            "fridgemagnets",
            "refrigeratormagnet",
            "refrigeratormagnets",
        }
    ),
    "fridgemagnet": frozenset(
        {
            "冰箱贴",
            "fridgemagnet",
            "fridgemagnets",
            "refrigeratormagnet",
            "refrigeratormagnets",
        }
    ),
    "fridgemagnets": frozenset(
        {
            "冰箱贴",
            "fridgemagnet",
            "fridgemagnets",
            "refrigeratormagnet",
            "refrigeratormagnets",
        }
    ),
    "墙贴": frozenset({"墙贴", "wallsticker", "wallstickers", "walldecal", "walldecals"}),
    "wallsticker": frozenset({"墙贴", "wallsticker", "wallstickers", "walldecal", "walldecals"}),
    "wallstickers": frozenset({"墙贴", "wallsticker", "wallstickers", "walldecal", "walldecals"}),
}


def _approved_semantic_aliases(main_category: Mapping[str, Any]) -> frozenset[str]:
    if not isinstance(main_category, Mapping) or type(main_category.get("name")) is not str:
        raise ShopeeGlobalV4LiveRuntimeError("approved main category is unavailable")
    leaf = re.split(r"\s*(?:>|＞|/|／)\s*", main_category["name"].strip())[-1]
    key = _semantic_key(leaf)
    if not key:
        raise ShopeeGlobalV4LiveRuntimeError("approved main category is unavailable")
    return _EXACT_CATEGORY_ALIASES.get(key, frozenset({key}))


def select_exact_official_category(
    main_category: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return one exact official leaf; never infer from title or description."""

    aliases = _approved_semantic_aliases(main_category)
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or candidate.get("publishable") is not True:
            continue
        category_id = str(candidate.get("id") or "").strip()
        name = str(candidate.get("name") or "").strip()
        path = candidate.get("path")
        if (
            not category_id.isdigit()
            or int(category_id) <= 0
            or not name
            or not isinstance(path, list)
            or not path
            or any(not isinstance(row, Mapping) for row in path)
            or _semantic_key(name) not in aliases
        ):
            continue
        normalized_path = [
            {"id": str(row.get("id") or "").strip(), "name": str(row.get("name") or "").strip()}
            for row in path
        ]
        if any(not row["id"] or not row["name"] for row in normalized_path):
            continue
        if normalized_path[-1] != {"id": category_id, "name": name}:
            continue
        matches.append(
            {
                "id": category_id,
                "name": name,
                "path": normalized_path,
                **{
                    key: deepcopy(candidate[key])
                    for key in (
                        "required_attributes",
                        "missing_required_attributes",
                    )
                    if key in candidate
                },
            }
        )
    if len(matches) != 1:
        raise ShopeeGlobalV4LiveRuntimeError(
            "Shopee exact semantic category is unavailable or ambiguous"
        )
    return matches[0]


class OfficialShopeeGlobalV4Runtime:
    """Production-shaped runtime with injectable deterministic provider edges."""

    def __init__(
        self,
        *,
        context_resolver: Callable[[Mapping[str, Any]], Mapping[str, object]],
        official_fact_reader: Callable[
            [Mapping[str, Any], Mapping[str, object]], Mapping[str, object]
        ],
        mapping_lookup: Callable[[str], str | None],
    ) -> None:
        if not all(
            callable(value)
            for value in (context_resolver, official_fact_reader, mapping_lookup)
        ):
            raise TypeError("Shopee live runtime dependencies must be callable")
        self._context_resolver = context_resolver
        self._official_fact_reader = official_fact_reader
        self._mapping_lookup = mapping_lookup
        self._active: ContextVar[tuple[Mapping[str, Any], Mapping[str, object]] | None] = (
            ContextVar("shopee_global_v4_active", default=None)
        )

    def lookup_global_item_ids(
        self, command: Mapping[str, Any]
    ) -> Mapping[str, object]:
        if not isinstance(command, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global command is invalid")
        models = command.get("models")
        if not isinstance(models, list) or not models or any(
            not isinstance(row, Mapping)
            or type(row.get("model_sku")) is not str
            or not row["model_sku"].strip()
            for row in models
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global models are invalid")
        context = self._context_resolver(command)
        if not isinstance(context, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee merchant context is invalid")
        self._active.set((deepcopy(dict(command)), deepcopy(dict(context))))
        return {
            row["model_sku"]: self._mapping_lookup(row["model_sku"])
            for row in models
        }

    def prepare_creation(
        self, command: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        active = self._active.get()
        if active is None or active[0] != command:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee execution context is unavailable")
        facts = self._official_fact_reader(command, active[1])
        if (
            not isinstance(facts, Mapping)
            or facts.get("authority") != "SHOPEE_OFFICIAL"
            or not isinstance(facts.get("candidates"), list)
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee official facts are invalid")
        category = select_exact_official_category(
            command.get("main_category"), facts["candidates"]
        )
        policy = command.get("policy")
        brand = facts.get("brand")
        warehouse = facts.get("warehouse")
        if not isinstance(policy, Mapping) or not isinstance(brand, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee official policy facts are invalid")
        if (
            brand.get("brand_id") != (policy.get("brand") or {}).get("brand_id")
            or brand.get("original_brand_name")
            != (policy.get("brand") or {}).get("original_brand_name")
            or not isinstance(warehouse, Mapping)
            or warehouse.get("display_name")
            != (policy.get("warehouse") or {}).get("display_name")
            or not str(warehouse.get("location_id") or "").strip()
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee official policy facts drifted")
        return {
            "authority": "SHOPEE_OFFICIAL",
            "recommendation_count": 1,
            "category": {
                "id": category["id"],
                "name": category["name"],
                "path": deepcopy(category["path"]),
            },
            "required_attributes": deepcopy(category.get("required_attributes", [])),
            "missing_required_attributes": deepcopy(
                category.get("missing_required_attributes", [])
            ),
            "warehouse": {
                "location_id": str(warehouse["location_id"]),
                "display_name": str(warehouse["display_name"]),
            },
        }


__all__ = [
    "OfficialShopeeGlobalV4Runtime",
    "ShopeeGlobalV4LiveRuntimeError",
    "select_exact_official_category",
]
