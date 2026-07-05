"""Ozon 草稿翻译 freshness：不同 TikTok 标题 → 不同俄语草稿；旧 pending 不污染 build_draft。"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from modules.ozon import catalog_draft
from modules.ozon.pending_drafts import (
    get_pending,
    list_pending,
    pending_is_fresh,
    save_pending,
    title_fingerprint,
)


def _fake_deepseek_response(title_ms: str) -> dict:
    marker = title_ms.split()[0].upper()[:12]
    return {
        "title": f"Наклейка декоративная {marker} 50x70 см",
        "description": f"Описание товара {marker}. {title_ms}",
        "type_id": 91971,
        "material": "ПВХ (поливинилхлорид)",
        "color_name": "разноцветный",
        "kit": "1 штука",
        "weight_g": 120,
        "len_cm": "50",
        "wid_cm": "70",
    }


def _enter_catalog_mocks(stack: ExitStack, title_ms: str) -> None:
    cat_item = {"match_key": "0941", "tiktok": {}}
    entry = {
        "seller_sku": "0941",
        "title": title_ms,
        "image_urls": ["http://example/img.jpg"],
        "tk_id": "tk-0941",
        "match_key": "0941",
    }
    row = {
        "seller_sku": "0941",
        "product_name": title_ms,
        "product_id": "pid-0941",
        "shop_cipher": "shop-0941",
    }
    stack.enter_context(patch("modules.ozon.catalog_draft.sync_catalog_to_tk_map"))
    stack.enter_context(
        patch("modules.ozon.catalog_draft.catalog_item_by_seller_sku", return_value=cat_item)
    )
    stack.enter_context(patch("modules.ozon.catalog_draft._map_entry_from_item", return_value=entry))
    stack.enter_context(patch("modules.ozon.catalog_draft._pick_tk_row", return_value=row))
    stack.enter_context(
        patch(
            "modules.ozon.catalog_draft.apply_variant_to_draft",
            side_effect=lambda _item, _mk, ent, pi: (ent, pi, ""),
        )
    )
    stack.enter_context(
        patch(
            "modules.ozon.catalog_draft.fetch_tk_category_info",
            return_value={"path": "Home", "leaf": "Decor", "category_id": "123"},
        )
    )
    stack.enter_context(
        patch(
            "modules.ozon.catalog_draft.match_category",
            return_value={"match_method": "rule_auto", "best_score": 0.5},
        )
    )
    stack.enter_context(patch("modules.ozon.catalog_draft.pick_tk_price", return_value=None))
    stack.enter_context(
        patch("modules.ozon.catalog_draft._lookup_material", return_value=(0, "ПВХ (поливинилхлорид)"))
    )
    stack.enter_context(
        patch("modules.ozon.catalog_draft._lookup_color", return_value=(0, "разноцветный"))
    )
    stack.enter_context(patch("modules.ozon.catalog_draft.lookup_logistics_weight", return_value=None))
    stack.enter_context(patch("modules.ozon.catalog_draft.lookup_stored", return_value=None))
    stack.enter_context(patch("modules.ozon.catalog_draft.load_category_options", return_value=[]))


class OzonDraftTranslateFreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _data_dir_patch(self):
        return patch("modules.ozon.pending_drafts.ozon_data_dir", return_value=self.data_dir)

    def test_different_titles_yield_different_draft_outputs(self) -> None:
        seen_titles: list[str] = []

        def fake_invoke(*, title_ms: str, **kwargs) -> dict:
            seen_titles.append(title_ms)
            return _fake_deepseek_response(title_ms)

        title_a = "Sticker dinding bunga mawar merah 50x70cm"
        title_b = "Wallpaper pelekat hijau daun tropikal 60x90cm"

        with ExitStack() as stack:
            _enter_catalog_mocks(stack, title_a)
            stack.enter_context(patch("modules.ozon.catalog_draft._invoke_deepseek", side_effect=fake_invoke))
            draft_a = catalog_draft.build_draft("0941")

        with ExitStack() as stack:
            _enter_catalog_mocks(stack, title_b)
            stack.enter_context(patch("modules.ozon.catalog_draft._invoke_deepseek", side_effect=fake_invoke))
            draft_b = catalog_draft.build_draft("0941")

        self.assertFalse(draft_a.get("error"))
        self.assertFalse(draft_b.get("error"))
        self.assertTrue(draft_a.get("deepseek_used"))
        self.assertTrue(draft_b.get("deepseek_used"))
        self.assertEqual(draft_a["title_ms"], title_a)
        self.assertEqual(draft_b["title_ms"], title_b)
        self.assertNotEqual(draft_a["title_fingerprint"], draft_b["title_fingerprint"])
        self.assertEqual(seen_titles, [title_a, title_b])

    def test_old_pending_does_not_pollute_build_draft(self) -> None:
        old_title = "OLD cached Malay sticker title from research phase"
        new_title = "Fresh TikTok title for SKU 0941 wall decal"

        with self._data_dir_patch():
            save_pending(
                {
                    "seller_sku": "0941",
                    "title_ms": old_title,
                    "draft_title": "Старый кэшированный русский заголовок",
                    "draft_description": "Старое кэшированное описание из исследования",
                }
            )
            self.assertIsNotNone(get_pending("0941"))

        def fake_invoke(*, title_ms: str, **kwargs) -> dict:
            return _fake_deepseek_response(title_ms)

        with ExitStack() as stack:
            _enter_catalog_mocks(stack, new_title)
            stack.enter_context(self._data_dir_patch())
            stack.enter_context(patch("modules.ozon.catalog_draft._invoke_deepseek", side_effect=fake_invoke))
            draft = catalog_draft.build_draft("0941")
            self.assertIsNone(get_pending("0941"))

        self.assertFalse(draft.get("error"))
        self.assertTrue(draft.get("deepseek_used"))
        self.assertEqual(draft["title_ms"], new_title)
        self.assertEqual(draft["title_fingerprint"], title_fingerprint(new_title))
        self.assertNotIn("Старый кэшированный", draft["draft_title"])
        self.assertNotIn("Старое кэшированное", draft["draft_description"])

    def test_list_pending_purges_stale_records(self) -> None:
        stale_title = "Stale pending title"
        current_title = "Current catalog title for 0941"

        with self._data_dir_patch():
            save_pending(
                {
                    "seller_sku": "0941",
                    "title_ms": stale_title,
                    "draft_title": "Устаревший заголовок",
                    "draft_description": "Устаревшее описание",
                }
            )
            with patch("modules.ozon.pending_drafts._catalog_title_ms", return_value=current_title):
                pending = list_pending(purge_stale=True)

            self.assertEqual(pending, [])
            self.assertIsNone(get_pending("0941"))

    def test_pending_is_fresh_uses_fingerprint(self) -> None:
        title = "Same TikTok title"
        rec = {"title_ms": title, "title_fingerprint": title_fingerprint(title)}
        self.assertTrue(pending_is_fresh(rec, title))
        self.assertFalse(pending_is_fresh(rec, "Different title"))


if __name__ == "__main__":
    unittest.main()
