from __future__ import annotations

import unittest

from modules.sourcing.image_generation_knowledge import profile_context, resolve_profile


class ImageGenerationKnowledgeTests(unittest.TestCase):
    def test_wreath_profile_matches_chinese_title(self):
        profile = resolve_profile(title="\u79cb\u5b63\u5357\u74dc\u67ab\u53f6\u82b1\u73af\u95e8\u7a97\u6302\u9970")
        self.assertEqual(profile["id"], "autumn_wreath")
        self.assertIn("size_card", profile_context(profile))

    def test_wall_decal_profile_matches_chinese_category(self):
        profile = resolve_profile(category="\u5899\u8d34")
        self.assertEqual(profile["id"], "wall_decal")
        context = profile_context(profile)
        self.assertNotIn("white_bg", context)
        self.assertNotIn("size_card", context)
        self.assertNotIn("macro_detail", context)
        self.assertIn("Bathroom Wall Accent", context)
        self.assertIn("human operator", context)

    def test_specific_helmet_sticker_beats_broad_wall_sticker_taxonomy(self):
        profile = resolve_profile(
            title="Creative WiFi signal helmet sticker",
            category="flat wall sticker",
        )
        self.assertEqual(profile["id"], "product_sticker")
        context = profile_context(profile)
        self.assertNotIn("white_bg", context)
        self.assertNotIn("size_card", context)
        self.assertNotIn("macro_detail", context)
        self.assertIn("Motorcycle Helmet Application", context)

    def test_unknown_category_uses_generic_profile(self):
        self.assertEqual(resolve_profile(title="Stainless travel mug")["id"], "generic_product")


if __name__ == "__main__":
    unittest.main()
