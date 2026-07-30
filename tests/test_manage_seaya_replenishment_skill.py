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


def test_skill_has_ui_metadata_and_hard_no_truncation_rule():
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    ui = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert "Never create placeholder identifiers such as `082X`" in skill
    assert "BLOCKED_IDENTITY" in skill
    assert "verify_dashboard_sync.py --check" in skill
    assert "$manage-seaya-replenishment" in ui


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
