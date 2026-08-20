"""Safe publication-facing variant display names.

Source option text often mixes a useful size or style with supplier item codes
and packing instructions.  Marketplace display names must keep only the facts
that distinguish one sellable variant from another.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


class VariantDisplayError(ValueError):
    """The source label cannot produce a safe publication specification."""


_DIMENSION_RE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*(?P<ua>mm|cm|m|毫米|厘米|米)?\s*(?:宽)?\s*"
    r"[x×*]\s*(?P<b>\d+(?:\.\d+)?)\s*(?P<ub>mm|cm|m|毫米|厘米|米)?\s*(?:长)?",
    re.IGNORECASE,
)
_QUANTITY_RE = re.compile(
    r"(?P<count>\d+)\s*(?P<unit>pcs?|pieces?|个|件|片|张|卷|套)",
    re.IGNORECASE,
)
_PACKAGING_RE = re.compile(
    r"(?:单卷|纸管|塑封|包装|外箱|内盒|packaging?|shipping\s*carton)",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"\s*(?:[/;|,+]|、|，|；|[()（）])\s*")


def _number(value: str) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number).rstrip("0").rstrip(".")


def _unit(value: str | None) -> str:
    aliases = {"毫米": "mm", "厘米": "cm", "米": "m"}
    clean = str(value or "").casefold()
    return aliases.get(clean, clean)


def _dimension_label(match: re.Match[str]) -> str:
    left_unit = _unit(match.group("ua"))
    right_unit = _unit(match.group("ub"))
    if not left_unit and right_unit:
        return f"{_number(match.group('a'))} × {_number(match.group('b'))} {right_unit}"
    if left_unit and not right_unit:
        right_unit = left_unit
    left = f"{_number(match.group('a'))} {left_unit}".strip()
    right = f"{_number(match.group('b'))} {right_unit}".strip()
    return f"{left} × {right}"


def _quantity_label(match: re.Match[str]) -> str:
    unit = match.group("unit").casefold()
    aliases = {
        "piece": "pcs",
        "pieces": "pcs",
        "pc": "pcs",
        "pcs": "pcs",
        "个": "pcs",
        "件": "pcs",
        "片": "pcs",
        "张": "pcs",
        "卷": "roll",
        "套": "set",
    }
    return f"{int(match.group('count'))} {aliases.get(unit, unit)}"


def normalize_publication_specification(
    value: object,
    *,
    internal_identifiers: Iterable[object] = (),
) -> str:
    """Return ``style · size · quantity`` without supplier noise.

    The function deliberately does not infer a missing commercial fact.  If an
    explicitly-known internal identifier and packing text consume the entire
    label, the caller must ask for a useful size, quantity, colour or style.
    """

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        raise VariantDisplayError("publication specification is empty")

    working = text
    for raw_identifier in internal_identifiers:
        identifier = unicodedata.normalize("NFKC", str(raw_identifier or "")).strip()
        if identifier:
            working = re.sub(re.escape(identifier), " ", working, flags=re.IGNORECASE)

    dimensions: list[str] = []

    def take_dimension(match: re.Match[str]) -> str:
        label = _dimension_label(match)
        if label.casefold() not in {value.casefold() for value in dimensions}:
            dimensions.append(label)
        return " "

    working = _DIMENSION_RE.sub(take_dimension, working)

    quantities: list[str] = []

    def take_quantity(match: re.Match[str]) -> str:
        label = _quantity_label(match)
        if label.casefold() not in {value.casefold() for value in quantities}:
            quantities.append(label)
        return " "

    working = _QUANTITY_RE.sub(take_quantity, working)
    working = _PACKAGING_RE.sub(" ", working)

    styles: list[str] = []
    for fragment in _SEPARATOR_RE.split(working):
        clean = " ".join(fragment.strip(" -_·.*").split())
        if not clean:
            continue
        if clean.casefold() not in {value.casefold() for value in styles}:
            styles.append(clean)

    result = " · ".join([*styles, *dimensions, *quantities])
    if not result:
        raise VariantDisplayError(
            "publication specification requires size, quantity, or style information"
        )
    if len(result) > 50:
        raise VariantDisplayError("publication specification exceeds 50 characters")
    return result
