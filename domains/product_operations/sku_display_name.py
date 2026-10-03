"""Consumer-facing SKU specification naming rules.

Raw supplier variant keys remain audit identities.  This module owns only the
short name shown to buyers and reviewers.
"""

from __future__ import annotations

import re
from typing import Mapping


class SkuDisplayNameError(ValueError):
    """Raised when a publication SKU name is internal or supplier-shaped."""


_SIZE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(cm|mm|m|inch|in|ft)\s*[x×*]\s*"
    r"(\d+(?:\.\d+)?)\s*(cm|mm|m|inch|in|ft)",
    flags=re.IGNORECASE,
)
_COUNT = re.compile(r"(?<!\d)(\d+)\s*(?:pcs?|pieces?)(?![a-z])", re.IGNORECASE)
_SKU_TOKEN = re.compile(r"\b(?:seller\s*)?(?:sku|model)\s*[:#-]?\s*[a-z0-9._-]+\b", re.IGNORECASE)
_SUPPLIER_MARKERS = (";", "【", "】", "[", "]")


def validate_sku_display_name(value: object, *, model_sku: object = None) -> str:
    """Return one canonical consumer name or fail closed.

    The shared publication name may contain dimensions, quantity and concise
    packaging/style facts.  It must not expose the Model/Seller SKU, structural
    supplier delimiters, or an untranslated slash-packed supplier label.
    """

    if type(value) is not str:
        raise SkuDisplayNameError("SKU display name must be a string")
    name = re.sub(r"\s+", " ", value).strip()
    if not name or len(name) > 80:
        raise SkuDisplayNameError("SKU display name must contain 1-80 characters")
    if name != value:
        raise SkuDisplayNameError("SKU display name whitespace is not canonical")
    if any(marker in name for marker in _SUPPLIER_MARKERS):
        raise SkuDisplayNameError("SKU display name contains supplier delimiters")
    if _SKU_TOKEN.search(name):
        raise SkuDisplayNameError("SKU display name exposes an internal SKU token")
    model = str(model_sku or "").strip()
    if model and (name == model or re.search(rf"(?<!\d){re.escape(model)}(?!\d)", name)):
        raise SkuDisplayNameError("SKU display name exposes the Model SKU")
    if "/" in name and re.search(r"[\u3400-\u9fff]", name):
        raise SkuDisplayNameError("SKU display name contains a mixed supplier label")
    if name.startswith(('/', '|', '·')) or name.endswith(('/', '|', '·')):
        raise SkuDisplayNameError("SKU display name punctuation is not canonical")
    return name


def derive_sku_display_name(source_label: object) -> str:
    """Derive the review name in the required priority order.

    Prefer dimensions, then a clean style/packaging label, then quantity.  The
    raw source label is never mutated and must be stored separately by callers.
    """

    source = re.sub(r"\s+", " ", str(source_label or "")).strip()
    size = _SIZE.search(source)
    if size:
        return validate_sku_display_name(
            f"{size.group(1)} {size.group(2).lower()} × "
            f"{size.group(3)} {size.group(4).lower()}"
        )

    count = _COUNT.search(source)
    boxed = bool(re.search(r"\bboxed\b|\bpaper\s*box\b|纸盒装", source, re.IGNORECASE))
    if count and boxed:
        return validate_sku_display_name(f"{int(count.group(1))} pcs · Boxed")

    cleaned = _SKU_TOKEN.sub("", source)
    cleaned = re.sub(r"[;【】\[\]]", " ", cleaned)
    cleaned = re.sub(r"\s*/\s*", " · ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|·/")
    if cleaned and not re.fullmatch(r"\d+", cleaned):
        try:
            return validate_sku_display_name(cleaned)
        except SkuDisplayNameError:
            pass
    if count:
        return validate_sku_display_name(f"{int(count.group(1))} pcs")
    return "1pcs"


def validate_specification_mapping(
    specification: object, *, model_sku: object = None
) -> dict[str, str]:
    if not isinstance(specification, Mapping) or not specification:
        raise SkuDisplayNameError("SKU specification must be a non-empty mapping")
    result: dict[str, str] = {}
    for key, value in specification.items():
        label = str(key or "").strip()
        if not label:
            raise SkuDisplayNameError("SKU specification label is empty")
        result[label] = validate_sku_display_name(value, model_sku=model_sku)
    return result


__all__ = [
    "SkuDisplayNameError",
    "derive_sku_display_name",
    "validate_sku_display_name",
    "validate_specification_mapping",
]
