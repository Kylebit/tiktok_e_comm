"""Keep the business Skills out of the offline tool distribution."""

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = {
    "prepare-product-publication": "skills/prepare-product-publication",
    "prepare-product-images": "skills/prepare-product-images",
    "publish-approved-product": "skills/publish-approved-product",
    "delist-products-by-sku": "skills/delist-products-by-sku",
    "apply-product-discounts": "skills/apply-product-discounts",
    "manage-seaya-replenishment": "domains/supply_chain_operations/skills/manage-seaya-replenishment",
    "manage-profit-settlement": "domains/data_operations/skills/manage-profit-settlement",
}


def test_portable_allowlist_excludes_business_skills_and_legacy_publisher():
    source = (ROOT / "scripts/package_agent_tools.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"CORE_FILES", "PORTABLE_SKILLS"}
    }
    assert set(assignments) == {"CORE_FILES", "PORTABLE_SKILLS"}
    assert set(CORE).isdisjoint(assignments["PORTABLE_SKILLS"])
    allowed_client = "skills/publish-approved-product/scripts/close_product_publication.py"
    assert allowed_client in assignments["CORE_FILES"]
    assert all(
        not file.startswith(tuple(path + "/" for path in CORE.values()))
        or file == allowed_client
        for file in assignments["CORE_FILES"]
    )
    assert "skills/publish-approved-product/scripts/publish_approved_product.py" not in assignments["CORE_FILES"]


def test_all_seven_are_catalogued_as_full_runtime_capabilities():
    catalog = json.loads((ROOT / "config/capability_catalog.json").read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in catalog["skills"]}
    for skill_id, source_path in CORE.items():
        assert rows[skill_id]["source_path"] == source_path
        assert rows[skill_id]["stage"] == "WORKFLOW_RUNTIME_REQUIRED"
        assert (ROOT / source_path / "SKILL.md").is_file()
    assert rows["publish-approved-product"]["installable"] is False
