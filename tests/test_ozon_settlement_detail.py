from __future__ import annotations

import unittest
from unittest.mock import patch

from modules.ozon.profit_analysis import RUB_PER_CNY
from modules.ozon.settlement import summarize_transactions


def _catalog_lookup(cost_cny: float | None = 30.0) -> dict:
    if cost_cny is None:
        return {"ok": True, "found": False, "item": None}
    return {
        "ok": True,
        "found": True,
        "item": {
            "cost_cny": cost_cny,
            "tiktok": {
                "image_url": "https://catalog.example/fallback.jpg",
                "product_name": "Catalog title",
            },
        },
    }


def _product_fetcher(_path: str, _body: dict) -> dict:
    return {
        "items": [
            {
                "offer_id": "0019",
                "sku": 10019,
                "name": "Ozon detail title",
                "primary_image": "https://cdn.example/0019.jpg",
            }
        ]
    }


def _product_fetcher_list_image(_path: str, _body: dict) -> dict:
    return {
        "items": [
            {
                "offer_id": "0021",
                "sku": 10021,
                "name": "Ozon list image title",
                "primary_image": ["https://cdn.example/0021-a.jpg", "https://cdn.example/0021-b.jpg"],
            }
        ]
    }


class OzonSettlementDetailTest(unittest.TestCase):
    def test_order_detail_includes_image_fees_profit_and_margin(self) -> None:
        ops = [
            {
                "operation_date": "2026-07-01T10:00:00Z",
                "operation_type_name": "Доставка покупателю",
                "amount": 700,
                "accruals_for_sale": 1000,
                "posting": {"posting_number": "P-1"},
                "items": [{"name": "Item A", "offer_id": "0019", "sku": 10019, "price": 1000}],
                "services": [
                    {"name": "Комиссия за продажу", "price": -120},
                    {"name": "Логистика", "price": -80},
                    {"name": "Эквайринг", "price": -25},
                    {"name": "Реклама", "price": -220},
                    {"name": "Услуга обработки", "price": -15},
                ],
            }
        ]
        with patch("modules.catalog.listings.lookup_sku", return_value=_catalog_lookup(30.0)):
            summary = summarize_transactions(ops, product_detail_fetcher=_product_fetcher)

        order = summary["orders"][0]
        self.assertEqual(order["product_image"], "https://cdn.example/0019.jpg")
        self.assertEqual(order["product_name"], "Ozon detail title")
        self.assertEqual(order["sale_price_rub"], 1000)
        self.assertEqual(order["cost_cny"], 30.0)
        self.assertEqual(order["commission"], 120)
        self.assertEqual(order["logistics"], 80)
        self.assertEqual(order["acquiring"], 25)
        self.assertEqual(order["ad"], 220)
        self.assertEqual(order["agent_fee"], 15)
        expected_profit = round(
            1000 / RUB_PER_CNY
            - 30
            - (120 + 80 + 25 + 220 + 15) / RUB_PER_CNY,
            2,
        )
        self.assertEqual(order["profit_cny"], expected_profit)
        self.assertEqual(order["margin_pct"], round(expected_profit / order["sale_price_cny"] * 100, 1))

    def test_missing_cost_keeps_fee_detail_but_profit_is_unknown(self) -> None:
        ops = [
            {
                "operation_date": "2026-07-01T10:00:00Z",
                "operation_type_name": "Комиссия",
                "amount": -12,
                "accruals_for_sale": 100,
                "posting": {"posting_number": "P-2"},
                "items": [{"name": "Item B", "offer_id": "0020", "sku": 10020}],
            }
        ]
        with patch("modules.catalog.listings.lookup_sku", return_value=_catalog_lookup(None)):
            summary = summarize_transactions(ops, product_detail_fetcher=lambda *_: {"items": []})

        order = summary["orders"][0]
        self.assertEqual(order["commission"], 12)
        self.assertIsNone(order["cost_cny"])
        self.assertIsNone(order["profit_cny"])
        self.assertIsNone(order["margin_pct"])

    def test_primary_image_list_is_normalized_to_string(self) -> None:
        ops = [
            {
                "operation_date": "2026-07-01T10:00:00Z",
                "operation_type_name": "Доставка покупателю",
                "amount": 50,
                "accruals_for_sale": 100,
                "posting": {"posting_number": "P-3"},
                "items": [{"name": "Item C", "offer_id": "0021", "sku": 10021}],
            }
        ]
        with patch("modules.catalog.listings.lookup_sku", return_value=_catalog_lookup(10.0)):
            summary = summarize_transactions(ops, product_detail_fetcher=_product_fetcher_list_image)

        self.assertEqual(summary["orders"][0]["product_image"], "https://cdn.example/0021-a.jpg")


if __name__ == "__main__":
    unittest.main()
