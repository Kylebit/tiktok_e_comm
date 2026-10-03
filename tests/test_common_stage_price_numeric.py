from modules.products.server import _common_stage_price_cny


def test_common_stage_integral_price_restores_numeric_contract():
    assert _common_stage_price_cny("6", common_stage=True) == 6
    assert type(_common_stage_price_cny("6", common_stage=True)) is int


def test_common_stage_fractional_price_remains_numeric():
    assert _common_stage_price_cny("6.25", common_stage=True) == 6.25
    assert type(_common_stage_price_cny("6.25", common_stage=True)) is float


def test_non_common_stage_preserves_string_contract():
    assert _common_stage_price_cny("6", common_stage=False) == "6"


def test_non_string_price_is_unchanged():
    assert _common_stage_price_cny(6, common_stage=True) == 6
