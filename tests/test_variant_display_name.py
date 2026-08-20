from __future__ import annotations

import pytest


def test_publication_specification_keeps_only_style_size_and_quantity():
    from domains.product_operations.variant_display import (
        normalize_publication_specification,
    )

    assert normalize_publication_specification(
        "30*90cm*2pcs/fun-c106",
        internal_identifiers=("fun-c106",),
    ) == "30 × 90 cm · 2 pcs"
    assert normalize_publication_specification(
        "Green Leaves / 44cm*3m / 单卷+纸管+塑封 / PH15-004",
        internal_identifiers=("PH15-004",),
    ) == "Green Leaves · 44 cm × 3 m"


def test_publication_specification_fails_when_only_internal_noise_remains():
    from domains.product_operations.variant_display import (
        VariantDisplayError,
        normalize_publication_specification,
    )

    with pytest.raises(VariantDisplayError, match="size, quantity, or style"):
        normalize_publication_specification(
            "fun-c106 / 单卷+纸管+塑封",
            internal_identifiers=("fun-c106",),
        )
