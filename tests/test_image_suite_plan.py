"""Unit tests for image suite plan helpers (no network)."""

from __future__ import annotations

import unittest
from unittest import mock

from modules.sourcing.image_suite_plan import (
    _extract_json_object,
    _normalize_suite,
    chat_completions,
    enforce_category_policy,
    normalize_suite_request,
    render_plan_markdown,
    suite_request_prompt,
)


class ImageSuitePlanHelpersTest(unittest.TestCase):
    def test_extract_json_object_plain(self):
        obj = _extract_json_object('{"analysis": {"subject": "x"}, "suite": {"items": []}}')
        self.assertEqual(obj["analysis"]["subject"], "x")

    def test_extract_json_object_fenced(self):
        text = '```json\n{"analysis": {"subject": "y"}, "suite": {"summary": "s", "items": []}}\n```'
        obj = _extract_json_object(text)
        self.assertEqual(obj["analysis"]["subject"], "y")

    def test_normalize_and_markdown(self):
        raw = {
            "analysis": {
                "subject": "花环",
                "materials": ["塑料"],
                "brand_dna": ["秋色", "圆形"],
            },
            "suite": {
                "summary": "3卖点+2场景+1白底",
                "items": [
                    {
                        "id": "sp1",
                        "type": "selling_point",
                        "title": "高仿真",
                        "focus": "细节",
                        "aspect_ratio": "1:1",
                        "selected": True,
                    },
                    {
                        "id": "wb1",
                        "type": "white_bg",
                        "title": "白底主图",
                        "focus": "棚拍",
                        "aspect_ratio": "1:1",
                        "selected": True,
                    },
                    {"id": "bad", "type": "other", "title": "x"},
                ],
            },
        }
        plan = _normalize_suite(raw)
        self.assertEqual(len(plan["suite"]["items"]), 2)
        md = render_plan_markdown(plan)
        self.assertIn("花环", md)
        self.assertIn("卖点图", md)
        self.assertIn("白底图", md)

    def test_policy_drops_disallowed_sticker_shots(self):
        candidate = {
            "analysis": {"category": "helmet sticker"},
            "suite": {
                "items": [
                    {"id": "wb1", "type": "white_bg", "title": "White", "focus": "x"},
                    {"id": "sc1", "type": "scene", "title": "Helmet", "focus": "x"},
                    {"id": "dt1", "type": "macro_detail", "title": "Macro", "focus": "x"},
                ]
            },
        }
        locked = enforce_category_policy(candidate, title="Creative helmet sticker")
        self.assertEqual(locked["_policy"]["category_profile"], "product_sticker")
        self.assertEqual([row["id"] for row in locked["suite"]["items"]], ["sc1", "sc2", "sc3", "sp1"])
        self.assertEqual(locked["_policy"]["rejected_item_ids"], ["wb1", "dt1"])

    def test_operator_request_preserves_ai_storyboards_with_exact_counts(self):
        candidate = {
            "analysis": {"category": "wall decal"},
            "suite": {
                "summary": "AI planned wall-decal suite",
                "items": [
                    {
                        "id": "model-scene-a",
                        "type": "scene",
                        "title": "Quiet Reading Corner",
                        "focus": "Place the exact decal on a reading-corner wall.",
                        "operator_title_zh": "安静阅读角场景",
                        "operator_focus_zh": "将同一墙贴自然放置在阅读角墙面。",
                        "selected": True,
                    },
                    {
                        "id": "model-size-a",
                        "type": "size_card",
                        "title": "Clean Scale Reference",
                        "focus": "Leave clean space for a deterministic size overlay.",
                        "operator_title_zh": "清晰比例参考",
                        "operator_focus_zh": "保留干净留白，供本地添加确定性英文尺寸。",
                        "selected": True,
                    },
                ],
            },
        }
        request = {
            "type_counts": {"scene": 1, "size_card": 1},
            "size_card": {"confirmed": True, "dimensions": "W 34 cm, H 58 cm"},
        }
        locked = enforce_category_policy(
            candidate,
            title="Cute dog wall decal",
            suite_request=request,
        )
        self.assertEqual([row["id"] for row in locked["suite"]["items"]], ["sc1", "sz1"])
        self.assertEqual(locked["suite"]["items"][0]["title"], "Quiet Reading Corner")
        self.assertTrue(all(row["ai_planned"] for row in locked["suite"]["items"]))
        self.assertEqual(locked["_policy"]["planning_source"], "ai_with_local_pre_and_post_validation")

    def test_local_policy_rejects_prohibited_type_before_model_call(self):
        with self.assertRaisesRegex(ValueError, "does not allow AI-generated white_bg"):
            normalize_suite_request(
                {"white_bg": 1, "scene": 1},
                title="Cute dog wall decal",
            )

    def test_model_prompt_contains_exact_operator_counts(self):
        request = normalize_suite_request(
            {"scene": 2, "selling_point": 1},
            title="Decorative wall wreath",
        )
        prompt = suite_request_prompt(request)
        self.assertIn('"scene": 2', prompt)
        self.assertIn('"selling_point": 1', prompt)
        self.assertIn("Return exactly", prompt)

    def test_chat_timeout_is_actionable_and_does_not_retry(self):
        class TimeoutOpener:
            calls = 0

            def open(self, _request, timeout=0):
                self.calls += 1
                raise TimeoutError("The read operation timed out")

        opener = TimeoutOpener()
        with mock.patch(
            "modules.sourcing.image_suite_plan._load_toapis_config",
            return_value={"api_key": "test", "base_url": "https://toapis.com/v1"},
        ), mock.patch(
            "modules.sourcing.image_suite_plan.urllib.request.build_opener",
            return_value=opener,
        ):
            with self.assertRaisesRegex(RuntimeError, "没有自动重试"):
                chat_completions(
                    [{"role": "user", "content": "test"}],
                    timeout=1,
                    proxy=None,
                )
        self.assertEqual(opener.calls, 1)


if __name__ == "__main__":
    unittest.main()
