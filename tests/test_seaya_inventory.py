import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from domains.supply_chain_operations.seaya_inventory import (
    WarehouseInventoryRecord,
    daily_snapshot_diff,
    low_stock_records,
    parse_seaya_inventory_payload,
)


def test_local_fixture_preserves_warehouse_quantities_and_maps_available_to_contract():
    fixture_path = Path(__file__).parent / "fixtures" / "seaya_inventory_snapshot.json"
    records = parse_seaya_inventory_payload(json.loads(fixture_path.read_text(encoding="utf-8")))

    alpha = records[0]
    assert (alpha.available, alpha.allocated, alpha.frozen, alpha.inbound) == (12, 3, 1, 20)
    snapshot = alpha.to_snapshot()
    assert snapshot.quantity == 12
    assert snapshot.warehouse == "Yacang Shenzhen"
    assert snapshot.supplier_id == "supplier-7"


def test_daily_snapshot_diff_includes_increases_decreases_and_new_skus():
    previous = (_record("SKU-ALPHA", available=12), _record("SKU-GAMMA", available=4))
    current = (_record("SKU-ALPHA", available=7), _record("SKU-BETA", available=3))

    deltas = daily_snapshot_diff(previous, current)

    assert [(item.sku_id, item.previous_available, item.current_available, item.available_change) for item in deltas] == [
        ("SKU-ALPHA", 12, 7, -5),
        ("SKU-BETA", 0, 3, 3),
        ("SKU-GAMMA", 4, 0, -4),
    ]


def test_low_stock_uses_available_quantity_not_allocated_frozen_or_inbound():
    records = (
        _record("SKU-LOW", available=2, allocated=100, frozen=50, inbound=80),
        _record("SKU-HEALTHY", available=3),
    )

    assert [record.sku_id for record in low_stock_records(records, threshold=2)] == ["SKU-LOW"]


def test_parser_rejects_negative_quantities_and_low_stock_rejects_negative_threshold():
    payload = {
        "snapshot_at": "2026-07-25T08:00:00+00:00",
        "warehouse": "Yacang",
        "items": [{"sku_id": "SKU-1", "available": -1, "allocated": 0, "frozen": 0, "inbound": 0}],
    }

    with pytest.raises(ValueError, match="available"):
        parse_seaya_inventory_payload(payload)
    with pytest.raises(ValueError, match="threshold"):
        low_stock_records((), threshold=-1)


def _record(
    sku_id: str,
    *,
    available: int,
    allocated: int = 0,
    frozen: int = 0,
    inbound: int = 0,
) -> WarehouseInventoryRecord:
    return WarehouseInventoryRecord(
        sku_id=sku_id,
        warehouse="Yacang",
        captured_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        available=available,
        allocated=allocated,
        frozen=frozen,
        inbound=inbound,
    )
