# -*- coding: utf-8 -*-
import unittest

from modules.finance.sku_key import same_seller_sku, seller_sku_tail4, sku_variants_for_lookup


class SkuKeyTests(unittest.TestCase):
    def test_tail4(self):
        self.assertEqual(seller_sku_tail4("990021"), "0021")
        self.assertEqual(seller_sku_tail4("0021"), "0021")
        self.assertEqual(seller_sku_tail4("21"), "0021")
        self.assertEqual(seller_sku_tail4("660438"), "0438")

    def test_same(self):
        self.assertTrue(same_seller_sku("990021", "0021"))
        self.assertTrue(same_seller_sku("990017", "17"))
        self.assertFalse(same_seller_sku("990021", "0026"))

    def test_variants(self):
        v = sku_variants_for_lookup("0021")
        self.assertIn("0021", v)
        self.assertIn("990021", v)


if __name__ == "__main__":
    unittest.main()
