"""Unit tests for shot prompt builder (no network)."""

from __future__ import annotations

import unittest

from modules.sourcing.image_shot_prompts import (
    build_shot_prompts,
    selected_items,
)


SAMPLE = {
    "analysis": {
        "subject": "粉色玫瑰墙贴",
        "category": "墙贴",
        "theme": "田园",
        "materials": ["PVC"],
        "colors": ["粉色", "绿色"],
        "craft_details": ["光泽印刷"],
        "structure": "平面贴纸",
        "brand_dna": ["粉色玫瑰", "木栅栏"],
        "style_lock": "必须保持粉色玫瑰与木栅栏图案不变",
    },
    "suite": {
        "summary": "demo",
        "items": [
            {
                "id": "sp1",
                "type": "selling_point",
                "title": "光泽细节",
                "focus": "微距花瓣",
                "aspect_ratio": "1:1",
                "selected": True,
            },
            {
                "id": "sc1",
                "type": "scene",
                "title": "卧室门",
                "focus": "贴在门上",
                "aspect_ratio": "1:1",
                "selected": False,
            },
            {
                "id": "wb1",
                "type": "white_bg",
                "title": "白底",
                "focus": "纯白居中",
                "aspect_ratio": "1:1",
                "selected": True,
            },
        ],
    },
    "_meta": {
        "title": "Demo Product",
        "image_url": "https://example.com/a.jpg",
        "model": "test",
    },
}


class ShotPromptTest(unittest.TestCase):
    def test_selected_only(self):
        items = selected_items(SAMPLE)
        self.assertEqual([i["id"] for i in items], ["sp1", "wb1"])

    def test_build_contains_style_lock(self):
        bundle = build_shot_prompts(SAMPLE)
        self.assertEqual(bundle["count"], 2)
        sp1 = bundle["shots"][0]
        self.assertIn("STYLE-LOCK", sp1["prompt"])
        self.assertIn("粉色玫瑰", sp1["prompt"])
        self.assertEqual(sp1["reference_image_url"], "https://example.com/a.jpg")
        self.assertEqual(sp1["aspect_ratio"], "1:1")

    def test_only_ids(self):
        bundle = build_shot_prompts(SAMPLE, only_ids=["wb1"])
        self.assertEqual(bundle["count"], 1)
        self.assertEqual(bundle["shots"][0]["id"], "wb1")
        self.assertIn("white-background", bundle["shots"][0]["prompt"].lower())
        self.assertIn("pure white", bundle["shots"][0]["prompt"].lower())

    def test_prompt_injects_global_language_and_fact_rules(self):
        bundle = build_shot_prompts(SAMPLE, only_ids=["sp1"])
        prompt = bundle["shots"][0]["prompt"]
        self.assertIn("Do not generate Chinese text", prompt)
        self.assertIn("FACT POLICY", prompt)
        self.assertIn("ABSOLUTE NO-TEXT REQUIREMENT", prompt)
        self.assertEqual(bundle["shots"][0]["category_profile"], "wall_decal")
        self.assertEqual(bundle["_source_meta"]["category_profile"], "wall_decal")

    def test_forces_square_output_even_when_plan_requests_portrait(self):
        plan = {**SAMPLE, "suite": {**SAMPLE["suite"], "items": [
            {**SAMPLE["suite"]["items"][0], "aspect_ratio": "2:3"},
        ]}}
        shot = build_shot_prompts(plan)["shots"][0]
        self.assertEqual(shot["aspect_ratio"], "1:1")
        self.assertIn("Output aspect ratio: 1:1.", shot["prompt"])

    def test_operator_confirmed_size_card_overrides_default_wall_decal_exclusion(self):
        plan = {**SAMPLE, "suite": {"summary": "", "items": [{
            "id": "sz_operator", "type": "size_card", "title": "Verified size",
            "focus": "technical base", "selected": True, "human_override": True,
            "human_dimensions": "16 cm x 28 cm", "human_dimensions_confirmed": True,
        }]}}
        prompt = build_shot_prompts(plan)["shots"][0]["prompt"]
        self.assertIn("HUMAN-APPROVED SIZE-CARD EXCEPTION", prompt)
        self.assertIn("16 cm x 28 cm", prompt)
        self.assertIn("dimension guide lines and arrowheads", prompt)
        self.assertIn("do not add any numbers", prompt)
        self.assertNotIn("Do not generate white-background hero images", prompt)

    def test_size_card_normalizes_chinese_dimensions_for_english_prompt(self):
        plan = {**SAMPLE, "suite": {"summary": "", "items": [{
            "id": "sz_operator", "type": "size_card", "title": "Verified size",
            "focus": "technical base", "selected": True, "human_override": True,
            "human_dimensions": "长34cm 宽58cm", "human_dimensions_confirmed": True,
        }]}}
        prompt = build_shot_prompts(plan)["shots"][0]["prompt"]
        self.assertIn("L 34 cm  |  W 58 cm", prompt)
        self.assertNotIn("长34cm", prompt)


if __name__ == "__main__":
    unittest.main()
