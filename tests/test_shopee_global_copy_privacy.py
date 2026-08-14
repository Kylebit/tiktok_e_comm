from modules.shopee.global_copy import _clamp_description


def test_legacy_shopee_copy_removes_internal_identity_and_brand_details():
    description = """PRODUCT OVERVIEW
Decorative table runner for everyday dining and seasonal room styling.

Brand: BRUP
Seller SKU: 0966
Item code: BB3729
Product ID: 3882722296
Offer ID: 3882722296

The approved product images are the visual reference for colour, pattern,
shape and included pieces. Review the selected variation and measurements
before ordering. Screen settings can make colours appear slightly different.
Use and care for the item according to the approved product facts.
"""

    cleaned = _clamp_description(description, "0966")

    for forbidden in ("BRUP", "0966", "BB3729", "3882722296", "Seller SKU"):
        assert forbidden not in cleaned
    assert "Decorative table runner" in cleaned
