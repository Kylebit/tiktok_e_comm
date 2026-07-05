"""Ozon 20% 利润率定价公式单元测试。"""
from __future__ import annotations

import unittest

from modules.ozon.price_convert import (
    OZON_RUB_PER_CNY,
    OZON_TARGET_MARGIN,
    ozon_logistics_cny,
    ozon_price_formula,
)


class OzonPricingTest(unittest.TestCase):
    def test_logistics_formula(self) -> None:
        logistics = ozon_logistics_cny(200)
        agent = 15 / OZON_RUB_PER_CNY
        self.assertAlmostEqual(logistics, round(3 + 0.045 * 200 + agent, 2))

    def test_price_formula_core(self) -> None:
        cost = 30.0
        weight = 140
        logistics = ozon_logistics_cny(weight)
        denom = 1 - 0.12 - 0.025 - 0.22 - OZON_TARGET_MARGIN
        self.assertAlmostEqual(denom, 0.435, places=3)

        result = ozon_price_formula(cost_cny=cost, weight_g=weight)
        expected_cny = round((cost + logistics) / denom, 2)
        expected_rub = round(expected_cny / OZON_RUB_PER_CNY)

        self.assertEqual(result["price_cny"], expected_cny)
        self.assertEqual(result["price_rub"], expected_rub)
        self.assertEqual(result["min_price_cny"], expected_cny)
        self.assertGreaterEqual(result["price_cny"], expected_cny)

    def test_min_price_not_below_formula_when_tk_lower(self) -> None:
        result = ozon_price_formula(cost_cny=40, weight_g=120, tk_price_cny=20)
        self.assertGreaterEqual(result["price_cny"], result["min_price_cny"])

    def test_uses_tk_when_higher_than_formula(self) -> None:
        result = ozon_price_formula(cost_cny=10, weight_g=50, tk_price_cny=999)
        self.assertEqual(result["price_cny"], 999)


if __name__ == "__main__":
    unittest.main()
