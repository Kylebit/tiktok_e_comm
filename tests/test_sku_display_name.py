import pytest

from domains.product_operations.sku_display_name import (
    SkuDisplayNameError,
    derive_sku_display_name,
    validate_sku_display_name,
)


def test_supplier_box_label_becomes_consumer_readable_count_and_packaging():
    assert derive_sku_display_name(";62PCS/束【纸盒装】;;") == "62 pcs · Boxed"


def test_dimensions_take_priority_over_supplier_text():
    assert derive_sku_display_name("SKU 0987 / 44CM*5M 纸盒装") == "44 cm × 5 m"


@pytest.mark.parametrize(
    "value",
    [";62PCS/束【纸盒装】;;", "SKU 0987", "0987", "花色/Paper Box"],
)
def test_internal_or_supplier_shaped_names_fail_closed(value):
    with pytest.raises(SkuDisplayNameError):
        validate_sku_display_name(value, model_sku="0987")


def test_consumer_name_allows_count_and_packaging_without_sku_identity():
    assert (
        validate_sku_display_name("62 pcs · Boxed", model_sku="0987")
        == "62 pcs · Boxed"
    )
