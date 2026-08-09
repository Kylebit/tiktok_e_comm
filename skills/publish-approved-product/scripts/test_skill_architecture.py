from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REQUIRED_TOOLS = (
    "inspect_snapshot.py",
    "dispatch_tiktok.py",
    "dispatch_shopee.py",
    "dispatch_shopee_regions.py",
    "dispatch_ozon.py",
    "readback_tiktok.py",
    "readback_shopee.py",
    "readback_shopee_regions.py",
    "readback_ozon.py",
)
REQUIRED_REFERENCES = (
    "tiktok.md",
    "shopee.md",
    "ozon.md",
    "result-classification.md",
    "incident-patterns.md",
)


class SkillArchitectureTests(unittest.TestCase):
    def test_nine_thin_tools_exist(self) -> None:
        self.assertEqual(
            [name for name in REQUIRED_TOOLS if not (SCRIPTS / name).is_file()],
            [],
        )

    def test_platform_tools_do_not_import_each_other(self) -> None:
        for name in REQUIRED_TOOLS[1:]:
            tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module or "")
            other_platforms = {"tiktok", "shopee", "ozon"} - {
                platform for platform in ("tiktok", "shopee", "ozon") if platform in name
            }
            self.assertFalse(
                any(other in module for module in imported for other in other_platforms),
                f"{name} imports another platform: {sorted(imported)}",
            )

    def test_confirmed_knowledge_files_exist(self) -> None:
        references = ROOT / "references"
        self.assertEqual(
            [name for name in REQUIRED_REFERENCES if not (references / name).is_file()],
            [],
        )

    def test_shopee_rule_requires_official_deleted_recovery(self) -> None:
        text = (ROOT / "references" / "shopee.md").read_text(encoding="utf-8")
        for required in ("DELETED", "global_item_id", "retire", "official readback"):
            self.assertIn(required, text)

    def test_shopee_regions_require_official_readback_before_mapping(self) -> None:
        text = (ROOT / "references" / "shopee.md").read_text(encoding="utf-8")
        for required in (
            "published_regions=[]",
            "record_shop_item",
            "dispatch_shopee_regions.py",
            "readback_shopee_regions.py",
            "Global-only publication",
        ):
            self.assertIn(required, text)

    def test_ozon_rule_uses_current_identity_and_statuses(self) -> None:
        text = (ROOT / "references" / "ozon.md").read_text(encoding="utf-8")
        self.assertIn("statuses", text)
        self.assertIn("item.id", text)
        self.assertIn("product_id", text)

    def test_orchestrator_contains_bounded_deleted_recovery(self) -> None:
        text = (SCRIPTS / "publish_approved_product.py").read_text(encoding="utf-8")
        self.assertIn("_bounded_recovery", text)
        self.assertIn("retire_deleted_global_id", text)
        self.assertIn('readback.get("status") != "DELETED"', text)

    def test_orchestrator_decodes_child_tools_as_utf8_without_crashing(self) -> None:
        text = (SCRIPTS / "publish_approved_product.py").read_text(encoding="utf-8")
        self.assertIn('encoding="utf-8"', text)
        self.assertIn('errors="replace"', text)

    def test_tiktok_reference_requires_semantic_category_mapping(self) -> None:
        text = (ROOT / "references" / "tiktok.md").read_text(encoding="utf-8")
        for required in (
            "CATEGORY_CONFIRMATION_REQUIRED",
            "Product type/use outrank",
            "cid=600009",
            "cid=600204",
            "Approved fallback",
            "PROVIDER_FIELD_OMITTED",
        ):
            self.assertIn(required, text)

    def test_skill_never_trusts_miaoshou_prefilled_category(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("untrusted candidate", text)
        self.assertIn("CATEGORY_CONFIRMATION_REQUIRED", text)
        self.assertIn("cid=600009", text)
        self.assertIn("explicit user-approved fallback", text)


if __name__ == "__main__":
    unittest.main()
