import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "domains" / "supply_chain_operations" / "skills" / "manage-seaya-replenishment" / "scripts" / "apply_inventory_snapshot.py"
SPEC = importlib.util.spec_from_file_location("apply_inventory_snapshot", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_country_prefixed_and_canonical_rows_merge_without_clipping():
    payload = {
        "records": [
            {"seller_sku": "0026", "warehouse": "TH8806", "stock": 0, "available": 0, "allocated": 0, "frozen": 0, "inbound": 600},
            {"seller_sku": "990026", "warehouse": "TH8806", "stock": 4, "available": 3, "allocated": 1, "frozen": 0, "inbound": 0},
        ]
    }

    fact = MODULE.aggregate_snapshot(payload)["TH"]["0026"]

    assert fact == {
        "stock": 4,
        "available": 3,
        "allocated": 1,
        "frozen": 0,
        "inbound": 600,
        "warehouse": "TH8806",
        "sourceAliases": ["0026", "990026"],
    }


def test_inventory_identity_rejects_cross_country_and_truncated_values():
    for value in ("880026", "002…", "0026..."):
        try:
            MODULE.canonical_inventory_sku(value, "TH")
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected {value!r} to be rejected")


def test_exact_duplicate_inventory_identity_is_blocked_without_position_identity():
    row = {
        "seller_sku": "880006",
        "warehouse": "VN8805",
        "stock": 39,
        "available": 39,
        "allocated": 0,
        "frozen": 0,
        "inbound": 0,
    }

    try:
        MODULE.aggregate_snapshot({"records": [row, dict(row)]})
    except ValueError as exc:
        assert str(exc) == (
            "BLOCKED_INVENTORY: duplicate raw inventory identity lacks source position identity"
        )
    else:
        raise AssertionError("expected duplicate inventory identity to fail closed")


def test_conflicting_duplicate_inventory_identity_fails_closed():
    first = {
        "seller_sku": "880006",
        "warehouse": "VN8805",
        "stock": 39,
        "available": 39,
        "allocated": 0,
        "frozen": 0,
        "inbound": 0,
    }
    second = {**first, "available": 38, "allocated": 1}

    try:
        MODULE.aggregate_snapshot({"records": [first, second]})
    except ValueError as exc:
        assert str(exc) == (
            "BLOCKED_INVENTORY: duplicate raw inventory identity lacks source position identity"
        )
    else:
        raise AssertionError("expected conflicting duplicate inventory identity to fail closed")


def test_same_sku_in_different_warehouses_remains_isolated():
    row = {
        "seller_sku": "880006",
        "warehouse": "VN8805",
        "stock": 39,
        "available": 39,
        "allocated": 0,
        "frozen": 0,
        "inbound": 0,
    }
    other = {**row, "warehouse": "TH8806", "seller_sku": "990006", "stock": 7, "available": 7}

    facts = MODULE.aggregate_snapshot({"records": [row, other]})

    assert facts["VN"]["0006"]["available"] == 39
    assert facts["TH"]["0006"]["available"] == 7
