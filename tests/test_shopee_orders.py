"""Tests for Shopee monthly order profit calculation (ORB-TASK-0027)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.catalog.sku_key import shopee_match_key
from modules.shopee import orders as orders_mod


class ShopeeOrdersHelpersTests(unittest.TestCase):
    def test_parse_month_june_2026(self) -> None:
        start, end = orders_mod.parse_month("2026-06")
        self.assertEqual(start, 1780272000)  # 2026-06-01 00:00:00 UTC
        # June has 30 days → 2026-06-30 23:59:59 UTC
        self.assertGreater(end, start)
        self.assertEqual(end - start, 30 * 24 * 3600 - 1)

    def test_parse_month_rejects_bad(self) -> None:
        with self.assertRaises(ValueError):
            orders_mod.parse_month("2026/06")

    def test_iter_time_windows_splits_over_15_days(self) -> None:
        start, end = orders_mod.parse_month("2026-06")
        windows = orders_mod.iter_time_windows(start, end)
        self.assertGreaterEqual(len(windows), 2)
        self.assertEqual(windows[0][0], start)
        self.assertEqual(windows[-1][1], end)
        for a, b in windows:
            self.assertLessEqual(b - a, orders_mod.MAX_RANGE_SEC)
            self.assertLessEqual(a, b)

    def test_shopee_match_key_last_four(self) -> None:
        self.assertEqual(shopee_match_key("990117"), "0117")
        self.assertEqual(shopee_match_key("0117"), "0117")
        self.assertEqual(shopee_match_key("6060117_红"), "0117")

    def test_resolve_primary_shops_fallback(self) -> None:
        with mock.patch.object(orders_mod, "list_sync_shops", return_value=[]):
            shops = orders_mod.resolve_primary_shops()
        regions = {s["region"] for s in shops}
        self.assertEqual(regions, {"MY", "VN", "TH", "PH"})
        ids = {s["region"]: s["shop_id"] for s in shops}
        self.assertEqual(ids["MY"], 1561117812)
        self.assertEqual(ids["PH"], 1527371343)
        self.assertEqual(ids["TH"], 1561124013)
        self.assertEqual(ids["VN"], 1723948773)


class ShopeeProfitCalcTests(unittest.TestCase):
    def test_build_order_profit_row_uses_escrow_and_cost(self) -> None:
        order = {
            "order_sn": "260601ABCDEF",
            "order_status": "COMPLETED",
            "create_time": 1780300000,
            "total_amount": 50.0,
            "item_list": [
                {
                    "item_name": "Test Plant Sticker",
                    "model_sku": "990117",
                    "model_quantity_purchased": 2,
                    "model_discounted_price": 25.0,
                }
            ],
        }
        escrow = {
            "order_sn": "260601ABCDEF",
            "order_income": {
                "escrow_amount": 40.0,
                "buyer_total_amount": 50.0,
                "commission_fee": 5.0,
                "transaction_fee": 1.5,
                "campaign_fee": 2.0,
                "actual_shipping_fee": 3.0,
                "items": [
                    {
                        "item_name": "Test Plant Sticker",
                        "model_sku": "990117",
                        "quantity_purchased": 2,
                        "discounted_price": 25.0,
                    }
                ],
            },
        }
        # match_key 0117 → 8 CNY each → 16 CNY total
        with mock.patch(
            "modules.finance.profit_engine.exchange_rate_for",
            return_value=1.75,
        ):
            row = orders_mod.build_order_profit_row(
                order=order,
                escrow=escrow,
                region="MY",
                shop_id=1561117812,
                key_costs={"0117": 8.0},
            )
        self.assertEqual(row.order_sn, "260601ABCDEF")
        self.assertEqual(row.region, "MY")
        self.assertEqual(row.match_key, "0117")
        self.assertEqual(row.product_cost_cny, 16.0)
        self.assertEqual(row.ad_cost_local, 2.0)
        self.assertEqual(row.commission_local, 5.0)
        self.assertEqual(row.transaction_fee_local, 1.5)
        self.assertEqual(row.settlement_local, 40.0)
        # profit_cny = 40*1.75 - 16 - 2*1.75 = 70 - 16 - 3.5 = 50.5
        self.assertAlmostEqual(row.profit_cny, 50.5, places=2)
        self.assertIsNotNone(row.margin_pct)

    def test_fetch_order_details_batches_50(self) -> None:
        sns = [f"SN{i:03d}" for i in range(55)]
        calls: list[str] = []

        def fake_detail(shop_id, token, params):
            calls.append(params["order_sn_list"])
            chunk = params["order_sn_list"].split(",")
            return {
                "error": "",
                "response": {"order_list": [{"order_sn": sn, "order_status": "COMPLETED"} for sn in chunk]},
            }

        with mock.patch.object(orders_mod.shopee_client, "get_order_detail", side_effect=fake_detail):
            details = orders_mod.fetch_order_details(1, "tok", sns)
        self.assertEqual(len(details), 55)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[0].split(",")), 50)
        self.assertEqual(len(calls[1].split(",")), 5)

    def test_collect_month_orders_four_shops_mocked(self) -> None:
        shops = [
            {"region": "MY", "shop_id": 1561117812, "shop_name": "MY"},
            {"region": "PH", "shop_id": 1527371343, "shop_name": "PH"},
            {"region": "TH", "shop_id": 1561124013, "shop_name": "TH"},
            {"region": "VN", "shop_id": 1723948773, "shop_name": "VN"},
        ]

        def fake_list(shop_id, token, *, time_from, time_to, time_range_field="create_time"):
            return [f"{shop_id}-A"]

        def fake_details(shop_id, token, order_sns):
            return [
                {
                    "order_sn": sn,
                    "order_status": "COMPLETED",
                    "create_time": 1780300000,
                    "total_amount": 10,
                    "item_list": [{"item_name": "X", "model_sku": "0001", "model_quantity_purchased": 1}],
                }
                for sn in order_sns
            ]

        def fake_escrow(shop_id, token, order_sn):
            return {
                "order_sn": order_sn,
                "order_income": {
                    "escrow_amount": 8,
                    "buyer_total_amount": 10,
                    "commission_fee": 1,
                    "transaction_fee": 0.5,
                    "campaign_fee": 0,
                    "items": [{"item_name": "X", "model_sku": "0001", "quantity_purchased": 1}],
                },
            }

        with mock.patch.object(orders_mod, "ensure_shop_token", return_value="tok"), mock.patch.object(
            orders_mod, "fetch_order_list", side_effect=fake_list
        ), mock.patch.object(orders_mod, "fetch_order_details", side_effect=fake_details), mock.patch.object(
            orders_mod, "fetch_escrow_detail", side_effect=fake_escrow
        ), mock.patch(
            "modules.finance.profit_engine.exchange_rate_for", return_value=1.0
        ):
            report = orders_mod.collect_month_orders(
                "2026-06",
                shops=shops,
                key_costs={"0001": 2.0},
            )
        self.assertEqual(report.order_count, 4)
        regions = {r.region for r in report.rows}
        self.assertEqual(regions, {"MY", "PH", "TH", "VN"})

    def test_write_profit_html_contains_columns(self) -> None:
        report = orders_mod.ProfitReport(month="2026-06")
        report.rows.append(
            orders_mod.OrderProfitRow(
                order_sn="SN1",
                region="MY",
                currency="MYR",
                order_status="COMPLETED",
                sku="0001",
                product_name="Demo",
                match_key="0001",
                sale_price_local=10,
                product_cost_cny=2,
                ad_cost_local=0,
                commission_local=1,
                transaction_fee_local=0.5,
                settlement_local=8,
                profit_cny=6,
                margin_pct=60.0,
            )
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "shopee_profit_2026-06.html"
            out = orders_mod.write_profit_report(report, path)
            text = out.read_text(encoding="utf-8")
            self.assertIn("利润CNY", text)
            self.assertIn("SN1", text)
            self.assertIn("总订单数：1", text)
            self.assertTrue(out.with_suffix(".json").is_file())


if __name__ == "__main__":
    unittest.main()
