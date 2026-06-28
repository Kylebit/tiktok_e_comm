"""UK 上品前检查（体积重 vs 实重等）。"""
from __future__ import annotations

from typing import Any

UK_EXIT_VOLUMETRIC_NEEDS_CONFIRM = 4


class VolumetricWeightNeedsConfirmation(Exception):
    """体积重大于实重，需用户确认尺寸/是否继续上架。"""

    def __init__(self, quote: Any):
        self.quote = quote
        super().__init__(format_volumetric_warning(quote))


def volumetric_dominates(*, volumetric_kg: float, actual_weight_kg: float) -> bool:
    if volumetric_kg <= 0:
        return False
    return volumetric_kg > actual_weight_kg + 1e-6


def quote_volumetric_dominates(quote: Any) -> bool:
    return volumetric_dominates(
        volumetric_kg=float(quote.volumetric_kg),
        actual_weight_kg=float(quote.weight_kg),
    )


def format_volumetric_warning(quote: Any) -> str:
    sku = getattr(quote, "seller_sku", "?")
    suffix = sku[-4:] if len(sku) >= 4 else sku
    return (
        f"{sku}（{suffix}）：实重 {quote.weight_kg:.3f}kg，"
        f"体积重 {quote.volumetric_kg:.3f}kg（{quote.package_cm}）→ "
        f"计费 {quote.billable_kg}kg，4PL {quote.merchant_logistics_gbp} GBP，"
        f"POP 折后 {quote.sale_price_gbp:.2f} GBP。"
        f" 请确认包裹尺寸是否正确，或提供手动尺寸后再上架。"
    )


def assert_volumetric_confirmed(
    quote: Any,
    *,
    volumetric_confirmed: bool = False,
    publish: bool = True,
) -> None:
    if not publish:
        return
    if quote_volumetric_dominates(quote) and not volumetric_confirmed:
        raise VolumetricWeightNeedsConfirmation(quote)
