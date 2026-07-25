# -*- coding: utf-8 -*-
"""Unit tests for SKU profit model core math."""

from __future__ import annotations

import unittest

from modules.finance.sku_profit_model import (
    DEFAULT_AD_RATE,
    apply_affiliate_quantile_split,
    build_three_scenarios,
    enrich_comp,
    is_outlier_comp,
    mark_outliers,
    pick_main_conclusion,
    profit_from_settlement,
    reprice_scenarios,
    weighted_settle_ratio,
)


class SkuProfitModelTests(unittest.TestCase):
    def test_profit_from_settlement(self):
        r = profit_from_settlement(
            settlement_local=200,
            sale_local=400,
            cost_cny=13.5,
            fx_cny_per_local=0.2,
            ad_rate=0.22,
        )
        # 200*0.2 - 400*0.22*0.2 - 13.5 = 40 - 17.6 - 13.5 = 8.9
        self.assertAlmostEqual(r["profit_cny"], 8.9, places=2)
        self.assertEqual(r["ad_rate"], 0.22)

    def test_default_ad_rate(self):
        self.assertEqual(DEFAULT_AD_RATE, 0.22)

    def test_outlier(self):
        self.assertTrue(is_outlier_comp({"settlement_local": 0, "settle_ratio": 0.5}))
        self.assertTrue(is_outlier_comp({"settlement_local": 10, "settle_ratio": 0.05}))
        self.assertFalse(is_outlier_comp({"settlement_local": 100, "settle_ratio": 0.4}))

    def test_weighted_ratio_prefers_newer(self):
        comps = [
            {"settle_ratio": 0.9, "outlier": False},
            {"settle_ratio": 0.1, "outlier": False},
        ]
        # newer first => weight 2 on 0.9, weight 1 on 0.1 => (1.8+0.1)/3=0.633...
        w = weighted_settle_ratio(comps)
        self.assertAlmostEqual(w, (0.9 * 2 + 0.1 * 1) / 3, places=4)

    def test_three_scenarios_and_main(self):
        comps = []
        for i, (sale, settle, aff) in enumerate(
            [
                (300, 120, 20),
                (300, 210, 0),
                (310, 125, 22),
                (290, 200, 0),
                (305, 118, 18),
                (300, 205, 0),
            ]
        ):
            comps.append(
                enrich_comp(
                    order_id=str(i),
                    statement_date=f"2026/07/{20 - i:02d}",
                    sale_local=sale,
                    settlement_local=settle,
                    cost_cny=10,
                    fx=0.2,
                    affiliate_local=aff,
                )
            )
        comps = mark_outliers(comps)
        sc = build_three_scenarios(
            sale_local=300,
            cost_cny=10,
            fx=0.2,
            comps=comps,
            prior_with_creator={"profit_cny": 1, "profit_local": 5, "margin_pct": 1, "est_settlement_local": 100},
            prior_no_creator={"profit_cny": 2, "profit_local": 10, "margin_pct": 2, "est_settlement_local": 150},
        )
        self.assertGreaterEqual(sc["sample_counts"]["all_usable"], 5)
        self.assertIsNotNone(sc["with_affiliate"])
        self.assertIsNotNone(sc["no_affiliate"])
        self.assertIsNotNone(sc["recent_weighted"])
        main = pick_main_conclusion(scenarios=sc, usable_n=sc["sample_counts"]["all_usable"])
        self.assertEqual(main["key"], "recent_weighted")
        self.assertEqual(main["confidence"], "posterior")

    def test_affiliate_quantile_split(self):
        comps = mark_outliers(
            [
                enrich_comp(order_id="a", statement_date="2026/07/01", sale_local=100, settlement_local=70, cost_cny=1, fx=0.2),
                enrich_comp(order_id="b", statement_date="2026/07/02", sale_local=100, settlement_local=30, cost_cny=1, fx=0.2),
            ]
        )
        split = apply_affiliate_quantile_split(comps)
        flags = {c["order_id"]: c["has_affiliate"] for c in split}
        self.assertTrue(flags["b"])
        self.assertFalse(flags["a"])

    def test_sparse_main_falls_back_to_affiliate(self):
        comps = [
            enrich_comp(
                order_id="1",
                statement_date="2026/07/01",
                sale_local=100,
                settlement_local=40,
                cost_cny=5,
                fx=0.2,
                affiliate_local=10,
            )
        ]
        comps = mark_outliers(comps)
        sc = build_three_scenarios(
            sale_local=100,
            cost_cny=5,
            fx=0.2,
            comps=comps,
            prior_with_creator={"profit_cny": -1, "profit_local": -5, "margin_pct": -5, "est_settlement_local": 40},
            prior_no_creator={"profit_cny": 2, "profit_local": 10, "margin_pct": 10, "est_settlement_local": 70},
        )
        main = pick_main_conclusion(scenarios=sc, usable_n=sc["sample_counts"]["all_usable"])
        self.assertEqual(main["key"], "with_affiliate")
        self.assertEqual(main["confidence"], "prior_or_sparse")

    def test_reprice_scenarios(self):
        comps = mark_outliers(
            [
                enrich_comp(order_id="a", statement_date="2026/07/01", sale_local=100, settlement_local=50, cost_cny=5, fx=0.2, affiliate_local=5),
                enrich_comp(order_id="b", statement_date="2026/07/02", sale_local=100, settlement_local=70, cost_cny=5, fx=0.2),
            ]
        )
        view = reprice_scenarios(
            sale_local=120,
            cost_cny=5,
            fx=0.2,
            comps=comps,
            prior_with={"profit_cny": 1, "profit_local": 5, "margin_pct": 4, "est_settlement_local": 50},
            prior_no={"profit_cny": 2, "profit_local": 10, "margin_pct": 8, "est_settlement_local": 70},
            ad_rate=0.22,
            label="test",
        )
        self.assertEqual(view["label"], "test")
        self.assertEqual(view["sale_local"], 120)
        self.assertIn("main", view)
        self.assertIn("scenarios", view)


if __name__ == "__main__":
    unittest.main()
