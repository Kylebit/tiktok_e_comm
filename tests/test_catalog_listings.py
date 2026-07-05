import unittest

from modules.catalog import listings


class CatalogListingsTests(unittest.TestCase):
    def test_merge_platform_rows_prefers_stable_tiktok_image_host(self):
        rows = [
            {
                "region": "PH",
                "product_name": "Demo product",
                "image_url": "https://p16-oec-general.tiktokcdn.com/example-a.jpeg",
            },
            {
                "region": "MY",
                "product_name": "Demo product",
                "image_url": "https://p16-oec-sg.ibyteimg.com/example-b.jpeg",
            },
            {
                "region": "GB",
                "product_name": "Demo product",
                "image_url": "https://p19-oec-eu-common-no.tiktokcdn-eu.com/example-c.jpeg",
            },
        ]

        merged = listings._merge_platform_rows(rows)

        self.assertIsNotNone(merged)
        self.assertEqual(
            merged["image_url"],
            "https://p16-oec-sg.ibyteimg.com/example-b.jpeg",
        )
        self.assertEqual(merged["region_count"], 3)

    def test_merge_platform_rows_keeps_first_name_with_no_image_candidates(self):
        rows = [
            {"region": "PH", "product_name": "Demo product", "image_url": ""},
            {"region": "MY", "product_name": "", "image_url": ""},
        ]

        merged = listings._merge_platform_rows(rows)

        self.assertIsNotNone(merged)
        self.assertEqual(merged["image_url"], "")
        self.assertEqual(merged["product_name"], "Demo product")


if __name__ == "__main__":
    unittest.main()
