"""待搬运列表：Ozon API 已上架 offer 应被排除。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from modules.ozon import catalog_source


class OzonMigrateListTest(unittest.TestCase):
    def test_listed_offer_excluded_from_unmigrated(self) -> None:
        cat_item = {
            "match_key": "0018",
            "tiktok": {
                "product_name": "Sample",
                "regions": [
                    {
                        "seller_sku": "880018",
                        "product_name": "Sample",
                        "price": 10,
                        "currency": "MYR",
                        "region": "MY",
                    }
                ],
            },
        }
        entry = {
            "seller_sku": "880018",
            "title": "Sample",
            "image_urls": [],
            "tk_id": "tk-0018",
            "match_key": "0018",
        }

        with patch.object(catalog_source, "sync_catalog_to_tk_map"):
            with patch.object(catalog_source, "fetch_ozon_listed_offer_ids", return_value={"0018"}):
                with patch.object(catalog_source, "iter_migrate_candidates", return_value=[cat_item]):
                    with patch.object(catalog_source, "_map_entry_from_item", return_value=entry):
                        with patch(
                            "modules.ozon.pending_drafts.dismissed_seller_skus",
                            return_value=set(),
                        ):
                            with patch(
                                "modules.ozon.pending_drafts.dismissed_offer_ids",
                                return_value=set(),
                            ):
                                rows = catalog_source.list_unmigrated_from_catalog(sync_map=False)

        self.assertEqual(rows, [])

    def test_needs_migrate_false_when_listed(self) -> None:
        item = {
            "tiktok": {
                "regions": [{"seller_sku": "880018", "product_name": "x", "price": 1, "currency": "MYR"}]
            }
        }
        self.assertFalse(catalog_source._needs_migrate(item, listed_offer_ids={"0018"}))

    def test_fetch_ozon_listed_normalizes_offer_id(self) -> None:
        self.assertEqual(catalog_source._normalize_listed_offer_id("880018"), "0018")
        self.assertTrue(
            catalog_source._is_formally_listed_on_ozon({"statuses": {"is_created": True}, "is_archived": False})
        )


if __name__ == "__main__":
    unittest.main()
