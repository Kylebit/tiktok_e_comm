from modules.finance.sku_key import (
    same_seller_sku,
    seller_sku_tail4,
    sku_variants_for_lookup,
)


def test_numeric_seller_skus_align_by_last_four_digits():
    assert seller_sku_tail4("990021") == "0021"
    assert seller_sku_tail4("0021") == "0021"
    assert seller_sku_tail4("21") == "0021"
    assert seller_sku_tail4("660438") == "0438"
    assert same_seller_sku("990021", "0021") is True
    assert same_seller_sku("990017", "17") is True
    assert same_seller_sku("990021", "0026") is False
    assert "990021" in sku_variants_for_lookup("0021")


def test_composite_marketplace_values_do_not_become_numeric_sku_keys():
    composite = "601099843157121_Bathroom Rack(50cm long)"

    assert seller_sku_tail4(composite) == composite
    assert sku_variants_for_lookup(composite) == [composite]
    assert same_seller_sku(composite, "2150") is False


def test_non_numeric_seller_skus_only_match_exactly():
    assert same_seller_sku("SKU-0021", "SKU-0021") is True
    assert same_seller_sku("SKU-0021", "0021") is False
