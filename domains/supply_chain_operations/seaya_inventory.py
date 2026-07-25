"""Pure, read-only conversion of exported Seaya/Yacang inventory snapshots.

The adapter deliberately accepts already-loaded JSON data.  It has no
credential handling, HTTP client, database access, or warehouse write path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from shared_platform.contracts import InventorySnapshot


@dataclass(frozen=True)
class WarehouseInventoryRecord:
    """Warehouse quantities retained by supply-chain operations.

    ``available`` is the only quantity represented by the shared hand-off
    contract.  The other quantities remain here so replenishment analysis does
    not discard warehouse state just to make an ``InventorySnapshot``.
    """

    sku_id: str
    warehouse: str
    captured_at: datetime
    available: int
    allocated: int
    frozen: int
    inbound: int
    supplier_id: str | None = None

    def to_snapshot(self) -> InventorySnapshot:
        """Return the shared availability-only inventory hand-off."""
        return InventorySnapshot(
            sku_id=self.sku_id,
            quantity=self.available,
            warehouse=self.warehouse,
            captured_at=self.captured_at,
            supplier_id=self.supplier_id,
        )


@dataclass(frozen=True)
class InventoryDelta:
    sku_id: str
    warehouse: str
    previous_available: int
    current_available: int

    @property
    def available_change(self) -> int:
        return self.current_available - self.previous_available


def parse_seaya_inventory_payload(payload: Mapping[str, Any]) -> tuple[WarehouseInventoryRecord, ...]:
    """Parse a locally supplied Seaya/Yacang inventory export.

    Expected fixture/export shape::

        {"snapshot_at": "2026-07-25T08:00:00+00:00", "warehouse": "Yacang",
         "items": [{"sku_id": "SKU-1", "available": 10, "allocated": 2,
                    "frozen": 1, "inbound": 5}]}
    """
    captured_at = _parse_timestamp(_required(payload, "snapshot_at"))
    warehouse = _required_string(payload, "warehouse")
    items = _required(payload, "items")
    if not isinstance(items, list):
        raise ValueError("items must be a list")

    records = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("each inventory item must be an object")
        records.append(
            WarehouseInventoryRecord(
                sku_id=_required_string(item, "sku_id"),
                warehouse=_optional_string(item, "warehouse") or warehouse,
                captured_at=_parse_timestamp(item.get("captured_at", captured_at.isoformat())),
                available=_non_negative_int(item, "available"),
                allocated=_non_negative_int(item, "allocated"),
                frozen=_non_negative_int(item, "frozen"),
                inbound=_non_negative_int(item, "inbound"),
                supplier_id=_optional_string(item, "supplier_id"),
            )
        )
    return tuple(records)


def daily_snapshot_diff(
    previous: Iterable[WarehouseInventoryRecord], current: Iterable[WarehouseInventoryRecord]
) -> tuple[InventoryDelta, ...]:
    """Compare available quantities by SKU and warehouse without side effects."""
    previous_by_key = {_record_key(record): record for record in previous}
    current_by_key = {_record_key(record): record for record in current}
    deltas = []
    for sku_id, warehouse in sorted(previous_by_key.keys() | current_by_key.keys()):
        old = previous_by_key.get((sku_id, warehouse))
        new = current_by_key.get((sku_id, warehouse))
        deltas.append(
            InventoryDelta(
                sku_id=sku_id,
                warehouse=warehouse,
                previous_available=old.available if old else 0,
                current_available=new.available if new else 0,
            )
        )
    return tuple(deltas)


def low_stock_records(
    records: Iterable[WarehouseInventoryRecord], threshold: int
) -> tuple[WarehouseInventoryRecord, ...]:
    """Return records whose immediately available units are at or below a threshold."""
    if threshold < 0:
        raise ValueError("threshold must be non-negative")
    return tuple(
        record
        for record in records
        if record.available <= threshold
    )


def _record_key(record: WarehouseInventoryRecord) -> tuple[str, str]:
    return record.sku_id, record.warehouse


def _required(payload: Mapping[str, Any], field: str) -> Any:
    if field not in payload:
        raise ValueError(f"missing required field: {field}")
    return payload[field]


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = _required(payload, field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_string(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string when present")
    return value


def _non_negative_int(payload: Mapping[str, Any], field: str) -> int:
    value = _required(payload, field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("captured_at timestamps must be ISO-8601 strings")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("captured_at timestamps must be ISO-8601 strings") from error
    if timestamp.tzinfo is None:
        raise ValueError("captured_at timestamps must include a timezone")
    return timestamp
