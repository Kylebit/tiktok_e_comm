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
