import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = (
    ROOT
    / "domains"
    / "supply_chain_operations"
    / "skills"
    / "manage-seaya-replenishment"
)


def _load_validator():
    path = SKILL / "scripts" / "validate_inventory_snapshot.py"
    spec = importlib.util.spec_from_file_location("inventory_validator", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _load_shopee_applier():
    path = SKILL / "scripts" / "apply_shopee_demand.py"
    spec = importlib.util.spec_from_file_location("shopee_demand_applier", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _valid_record() -> dict:
    return {
        "seller_sku": "770820",
        "warehouse": "PH8807",
        "stock": 2,
        "available": 2,
        "allocated": 0,
        "frozen": 0,
        "inbound": 0,
        "captured_at": "2026-07-30T12:00:00+08:00",
    }


def test_inventory_validator_requires_complete_exact_identifiers():
    validator = _load_validator()

    assert validator.validate_payload([_valid_record()]) == []
    for invalid in ("7708…", "7708...", "7708****", "082X", ""):
        record = _valid_record()
        record["seller_sku"] = invalid
        assert validator.validate_payload([record])


def test_inventory_validator_rejects_bool_float_string_and_negative_quantities():
    validator = _load_validator()

    for invalid in (True, 2.0, "2", -1):
        record = _valid_record()
        record["available"] = invalid
        assert validator.validate_payload([record])


def test_country_isolation_removes_cross_country_channel_facts():
    applier = _load_shopee_applier()
    rows = [
        {
            "sku": "0007",
            "channels": {
                "tiktok": {
                    "days": 366,
                    "orders": 10,
                    "units": 12,
                    "recent30Units": 8,
                    "source": "TikTok MY settlement",
                }
            },
        },
        {
            "sku": "0004",
            "channels": {
                "tiktok": {
                    "days": 31,
                    "orders": 2,
                    "units": 2,
                    "recent30Units": 2,
                    "source": "TikTok VN settlement",
                }
            },
        },
    ]

    assert applier.enforce_country_isolation(rows, "VN") == 1
    assert rows[0]["channels"]["tiktok"] == applier._empty_tiktok_channel("VN")
    assert rows[1]["channels"]["tiktok"]["units"] == 2


def test_skill_has_ui_metadata_and_hard_no_truncation_rule():
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    ui = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert "Never create placeholder identifiers such as `082X`" in skill
    assert "BLOCKED_IDENTITY" in skill
    assert "`770821 → 0821`" in skill
    assert "PENDING_REFRESH" in skill
    assert "(item_id, model_id) -> catalog seller_sku" in skill
    assert "title or image similarity" in skill
    assert "apply_shopee_demand.py" in {
        path.name for path in (SKILL / "scripts").iterdir()
    }
    assert "install_local_skill.ps1" in {
        path.name for path in (SKILL / "scripts").iterdir()
    }
    assert "verify_dashboard_sync.py --check" in skill
    assert "$manage-seaya-replenishment" in ui


def test_skill_separates_order_quantity_from_settlement_economics():
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    contract = (SKILL / "references" / "decision-contract.md").read_text(
        encoding="utf-8"
    )

    assert "quantity_basis=valid_order" in skill
    assert "economics_basis=settlement" in skill
    assert "BLOCKED_ORDER_DATA" in skill
    assert "Never use settlement rows" in skill
    assert "Settlement-only history" in contract
    assert "escrow-release" in contract


def test_dashboard_and_skill_sync_manifest_is_current():
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL / "scripts" / "verify_dashboard_sync.py"),
            "--check",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_skill_time_phases_inbound_and_preserves_manual_override_lineage():
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    contract = (SKILL / "references" / "decision-contract.md").read_text(
        encoding="utf-8"
    )

    assert "Never treat `inbound` as available on the snapshot date" in skill
    assert "expected_sellable_at" in skill
    assert "`已入库（Reach the domestic warehouse）`" in skill
    assert "created_at + 4 days" in skill
    assert "never label that fallback as actual inbound" in skill
    assert "reversible browser `localStorage`" in skill
    assert "time_phased_projected_stock_at_new_replenishment_expected_sellable_at" in contract
    assert "preparation_days = 3" in contract
    assert "domestic_warehouse_days = 4" in contract
    assert "new_replenishment_lead_days = preparation_days + domestic_warehouse_days + country_transport_days" in contract
    assert "nearest-rank P80" in contract
    assert "FALLBACK_INSUFFICIENT_SAMPLE" in contract
    assert "inbound_status=NOT_YET_INBOUND" in contract
    assert "created_at + 4 days" in contract
