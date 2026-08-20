from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
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
    def test_production_control_wrapper_uses_only_frozen_v4_runner_routes(self) -> None:
        source = (SCRIPTS / "product_center_publication.py").read_text(
            encoding="utf-8"
        )
        for required in (
            "/api/product-workspace/publish-tiktok",
            "/api/product-workspace/publish-shopee-global",
            "/api/product-workspace/publish-ozon",
            "/api/product-workspace/publication-report?",
            "product-publication-start/v1",
            "approved-publication-snapshot/v4",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "/api/product-workspace/collectbox-action/start",
            '"/api/product-workspace/publish"',
            "dashboard(",
            "dispatch_tiktok",
            "dispatch_shopee",
            "dispatch_ozon",
            "inspect_snapshot",
        ):
            self.assertNotIn(forbidden, source)

    def test_skill_declares_runner_wrapper_as_only_production_command(self) -> None:
        english = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        chinese = (ROOT / "references" / "SKILL.zh-CN.md").read_text(
            encoding="utf-8"
        )
        for text in (english, chinese):
            self.assertIn("product_center_publication.py", text)
            self.assertIn("--plan-id <EXACT_PLAN_ID>", text)
            self.assertIn("product-publication-start/v1", text)
            self.assertIn("publication-report", text)
        self.assertIn("deprecated compatibility", english)
        self.assertIn("已弃用兼容", chinese)

    def test_direct_tools_are_explicitly_deprecated_compatibility(self) -> None:
        for name in ("publish_approved_product.py", *REQUIRED_TOOLS):
            source = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn("DEPRECATED COMPATIBILITY", source, name)

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

    def test_confirmed_ozon_provider_normalizations_are_permanent_rules(self) -> None:
        ozon = (ROOT / "references" / "ozon.md").read_text(encoding="utf-8")
        incidents = (ROOT / "references" / "incident-patterns.md").read_text(
            encoding="utf-8"
        )
        chinese = (ROOT / "references" / "SKILL.zh-CN.md").read_text(
            encoding="utf-8"
        )
        for text in (ozon, incidents, chinese):
            for required in (
                "1000",
                "color_image",
                "/v4/product/info/attributes",
                "/v1/product/info/description",
                "7 на 7",
            ):
                self.assertIn(required, text)

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
            "placemat",
            "Festive Decoration",
            "Direct approved category",
            "PROVIDER_FIELD_OMITTED",
        ):
            self.assertIn(required, text)

    def test_skill_never_trusts_miaoshou_prefilled_category(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("untrusted candidate", text)
        self.assertIn("CATEGORY_CONFIRMATION_REQUIRED", text)
        self.assertIn("cid=600009", text)
        self.assertIn("explicit user-approved fallback", text)

    def test_v4_runner_never_enters_legacy_collectbox_start_or_plan_parser(self) -> None:
        source = (
            REPO_ROOT / "shared_platform" / "product_publication_executors.py"
        ).read_text(encoding="utf-8")
        self.assertIn("build_tiktok_v4_executor", source)
        self.assertIn("project_tiktok_v4_execution_plan", source)
        for forbidden in (
            "/api/product-workspace/collectbox-action/start",
            "prepare_tiktok_collectbox",
            "_approved_common",
            "_approved_site",
        ):
            self.assertNotIn(forbidden, source)

    def test_confirmed_v4_claim_and_platform_scope_incidents_are_documented(self) -> None:
        english = (ROOT / "references" / "incident-patterns.md").read_text(
            encoding="utf-8"
        )
        chinese = (ROOT / "references" / "SKILL.zh-CN.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "approved-publication-snapshot/v4",
            "legacy ReleasePlan parser",
            "provider idempotency",
            "platform detail ID",
            "platform_scope",
            "unselected platforms",
        ):
            self.assertIn(required, english)
        for required in (
            "approved-publication-snapshot/v4",
            "旧 ReleasePlan 解析器",
            "平台幂等",
            "平台明细 ID",
            "platform_scope",
            "未选择平台",
        ):
            self.assertIn(required, chinese)

    def test_fridge_magnet_category_requires_six_site_tree_and_metadata_proof(self) -> None:
        text = (ROOT / "references" / "tiktok.md").read_text(encoding="utf-8")
        for required in (
            "cid=854536",
            "PH/MY/TH/VN/MX/GB",
            "per-site tree",
            "exact-shop metadata",
            "冰箱贴",
        ):
            self.assertIn(required, text)

    def test_tiktok_exact_provider_readback_invariants_remain(self) -> None:
        text = (ROOT / "references" / "tiktok.md").read_text(encoding="utf-8")
        for required in (
            "remembered `detail_id` belongs to that target and approved offer",
            "every approved model SKU and option name is present exactly once",
            "every model SKU price equals its own approved per-SKU price",
            "category equals the approved site candidate",
            "same target-specific `detail_id`",
        ):
            self.assertIn(required, text)

    def test_homebloom_sea_targets_use_exact_miaoshou_shop_identities(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        tiktok = (ROOT / "references" / "tiktok.md").read_text(encoding="utf-8")
        chinese = (ROOT / "references" / "SKILL.zh-CN.md").read_text(
            encoding="utf-8"
        )
        publisher = (
            REPO_ROOT / "modules" / "miaoshou" / "tiktok_publisher.py"
        ).read_text(encoding="utf-8")
        exact_targets = {
            "tiktok:HB_PH": "15173238",
            "tiktok:HB_MY": "16770639",
            "tiktok:HB_TH": "16770557",
            "tiktok:HB_VN": "16783702",
        }
        for target, shop_id in exact_targets.items():
            self.assertIn(target, skill)
            self.assertIn(target, tiktok)
            self.assertIn(target, chinese)
            self.assertIn(f'"{target}": "{shop_id}"', publisher)
            self.assertIn(shop_id, tiktok)
        for text in (skill, tiktok):
            self.assertIn("Miaoshou Open API", text)
            self.assertIn("must not use the TikTok official API", text)
            self.assertIn("independent execution target", text)
        self.assertIn("妙手开放 API", chinese)
        self.assertIn("不得改走 TikTok 官方 API", chinese)

    def test_shopee_regional_language_rule_never_overrides_provider_translation(self) -> None:
        shopee = (ROOT / "references" / "shopee.md").read_text(
            encoding="utf-8"
        )
        incidents = (ROOT / "references" / "incident-patterns.md").read_text(
            encoding="utf-8"
        )
        chinese = (ROOT / "references" / "SKILL.zh-CN.md").read_text(
            encoding="utf-8"
        )
        for text in (shopee, incidents):
            self.assertIn(
                "omit `item_name` and `description`",
                text.casefold(),
            )
            for required in (
                "English for PH/MY",
                "Thai for TH",
                "Vietnamese for VN",
                "duplicate",
            ):
                self.assertIn(required, text)
        for required in (
            "不得主动发送",
            "TH 必须回读为泰语",
            "VN 必须回读为越南语",
            "不得为了修复语言再创建一个重复商品",
        ):
            self.assertIn(required, chinese)

    def test_confirmed_tiktok_variant_and_warehouse_incidents_are_documented(self) -> None:
        tiktok = (ROOT / "references" / "tiktok.md").read_text(encoding="utf-8")
        incidents = (ROOT / "references" / "incident-patterns.md").read_text(
            encoding="utf-8"
        )
        for text in (tiktok, incidents):
            self.assertIn("structural semicolon delimiters", text)
            self.assertIn("shopIdToWarehouseIdAndStockMap", text)
            self.assertIn("positive provider stock", text)
        chinese = (ROOT / "references" / "SKILL.zh-CN.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("结构分号", chinese)
        self.assertIn("shopIdToWarehouseIdAndStockMap", chinese)
        self.assertIn("妙手现有的正库存", chinese)

    def test_skill_documents_explicit_canonical_install_parity_workflow(self) -> None:
        english = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        chinese = (ROOT / "references" / "SKILL.zh-CN.md").read_text(
            encoding="utf-8"
        )
        for text in (english, chinese):
            self.assertIn("sync_product_publication_skills.py --check", text)
            self.assertIn("sync_product_publication_skills.py --install", text)
        self.assertIn("Never install implicitly", english)
        self.assertIn("不得隐式安装", chinese)


if __name__ == "__main__":
    unittest.main()
