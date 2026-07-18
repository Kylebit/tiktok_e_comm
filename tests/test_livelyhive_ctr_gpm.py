"""ORB-TASK-0029: MY LivelyHive CTR/GPM 选品逻辑单测。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.products import analytics as analytics_mod
from modules.products import build_page


class GpmCalcTests(unittest.TestCase):
    def test_compute_gpm_basic(self) -> None:
        # 4767.65 / 162277 * 1000 ≈ 29.38
        self.assertAlmostEqual(analytics_mod.compute_gpm(4767.65, 162277), 29.381, places=2)

    def test_compute_gpm_zero_views(self) -> None:
        self.assertEqual(analytics_mod.compute_gpm(100, 0), 0.0)


class FilterTests(unittest.TestCase):
    def test_is_boost_candidate_both_dims(self) -> None:
        # median 0.05 → CTR 门槛 0.075；GPM 门槛 20
        self.assertTrue(
            analytics_mod.is_ctr_gpm_boost_candidate(
                0.08, 25.0, 0.05, high_mult=1.5, gpm_threshold=20.0
            )
        )
        self.assertFalse(
            analytics_mod.is_ctr_gpm_boost_candidate(
                0.06, 25.0, 0.05, high_mult=1.5, gpm_threshold=20.0
            )
        )
        self.assertFalse(
            analytics_mod.is_ctr_gpm_boost_candidate(
                0.09, 10.0, 0.05, high_mult=1.5, gpm_threshold=20.0
            )
        )

    def test_filter_ctr_gpm_candidates(self) -> None:
        products = [
            {
                "id": "1",
                "click_through_rate": 0.10,
                "gmv": {"amount": "100"},
                "views": 2000,  # gpm=50
                "gpm": 50.0,
                "orders": 1,
            },
            {
                "id": "2",
                "click_through_rate": 0.02,
                "gmv": {"amount": "100"},
                "views": 2000,
                "gpm": 50.0,
                "orders": 0,
            },
            {
                "id": "3",
                "click_through_rate": 0.10,
                "gmv": {"amount": "10"},
                "views": 2000,  # gpm=5
                "gpm": 5.0,
                "orders": 0,
            },
        ]
        # median ctr among >0 = median(0.10,0.02,0.10)=0.10 → threshold 0.15 with 1.5x
        # none pass with median 0.10; use explicit median 0.04 → threshold 0.06
        out = analytics_mod.filter_ctr_gpm_candidates(
            products,
            median_ctr=0.04,
            high_mult=1.5,
            gpm_threshold=20.0,
            allow_relaxed_ctr=False,
        )
        ids = [str(p["id"]) for p in out]
        self.assertEqual(ids, ["1"])

    def test_filter_relaxed_when_strict_empty(self) -> None:
        # 中位 0.05 → 严格门槛 0.075；商品 CTR 仅 0.06 但 GPM 够 → 回退命中
        products = [
            {
                "id": "r1",
                "click_through_rate": 0.06,
                "gmv": {"amount": "100"},
                "views": 2000,
                "gpm": 50.0,
            },
            {
                "id": "r2",
                "click_through_rate": 0.02,
                "gmv": {"amount": "100"},
                "views": 2000,
                "gpm": 50.0,
            },
        ]
        out = analytics_mod.filter_ctr_gpm_candidates(
            products, median_ctr=0.05, high_mult=1.5, gpm_threshold=20.0
        )
        self.assertEqual([p["id"] for p in out], ["r1"])
        self.assertEqual(out[0]["filter_tier"], "relaxed_ctr")


class ReportBuildTests(unittest.TestCase):
    def test_build_report_writes_html_json_csv(self) -> None:
        payload = {
            "scan_time": 1782731882,
            "shop": "LivelyHive",
            "window_days": 30,
            "high_ctr_multiplier": 1.5,
            "good_gpm_threshold": 20,
            "ctr_median": 0.05,
            "ctr_threshold": 0.075,
            "gpm_median_shop": 21.0,
            "total_products": 10,
            "candidate_count": 1,
            "suggested_commission_pct": 15,
            "creator_list_dir": "data/creator_lists",
            "candidates": [
                {
                    "product_id": "pid1",
                    "sku_id": "sku1",
                    "sku_ids": ["sku1"],
                    "seller_sku": "0001",
                    "product_name": "Demo",
                    "image_url": "https://example.com/a.jpg",
                    "click_through_rate": 0.09,
                    "gpm": 30.0,
                    "gmv": 100.0,
                    "orders": 2,
                    "units_sold": 3,
                    "views": 3333,
                    "suggested_commission_pct": 15,
                    "shop_cipher": "CIPHER",
                    "region": "MY",
                    "creator_list_dir": "data/creator_lists",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(build_page, "OUTPUTS_DIR", Path(tmp)):
                paths = build_page.build_livelyhive_my_boost_report(
                    payload, date_tag="2099-01-01"
                )
            html = Path(paths["html"]).read_text(encoding="utf-8")
            self.assertIn("CTR / GPM", html)
            self.assertIn("pid1", html)
            self.assertIn("30.0", html)
            data = Path(paths["json"]).read_text(encoding="utf-8")
            self.assertIn("pid1", data)
            csv_text = Path(paths["csv"]).read_text(encoding="utf-8")
            self.assertIn("product_id", csv_text)
            self.assertIn("sku_id", csv_text)
            self.assertIn("pid1", csv_text)


if __name__ == "__main__":
    unittest.main()
