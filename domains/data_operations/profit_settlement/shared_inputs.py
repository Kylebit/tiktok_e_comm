"""The only runtime inputs shared by platform profit settlement engines."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _checksum(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class CostRecord:
    canonical_sku: str
    unit_cost_cny: Decimal
    version: str
    effective_at: str
    source: str

    def payload(self) -> dict[str, str]:
        return {
            "canonical_sku": self.canonical_sku,
            "unit_cost_cny": str(self.unit_cost_cny),
            "version": self.version,
            "effective_at": self.effective_at,
            "source": self.source,
        }


@dataclass(frozen=True)
class CostSnapshot:
    records: Mapping[str, CostRecord]
    snapshot_id: str
    checksum: str

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, object],
        *,
        snapshot_id: str | None = None,
        default_version: str = "unspecified",
        default_effective_at: str = "",
        source: str = "caller_supplied",
    ) -> "CostSnapshot":
        records: dict[str, CostRecord] = {}
        for raw_sku, raw_value in sorted(values.items(), key=lambda item: str(item[0])):
            sku = _text(raw_sku)
            item = raw_value if isinstance(raw_value, Mapping) else {"unit_cost_cny": raw_value}
            amount = _decimal(item.get("unit_cost_cny", item.get("cost_cny")))
            if not sku or amount is None or amount <= 0:
                continue
            records[sku] = CostRecord(
                canonical_sku=sku,
                unit_cost_cny=amount,
                version=_text(item.get("version")) or default_version,
                effective_at=_text(item.get("effective_at")) or default_effective_at,
                source=_text(item.get("source")) or source,
            )
        canonical = {sku: record.payload() for sku, record in records.items()}
        checksum = _checksum(canonical)
        return cls(records, _text(snapshot_id) or f"costs:sha256:{checksum}", checksum)

    def get(self, canonical_sku: str) -> CostRecord | None:
        return self.records.get(_text(canonical_sku))

    def payload(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "checksum": self.checksum,
            "record_count": len(self.records),
        }


@dataclass(frozen=True)
class FxSnapshot:
    rates_cny: Mapping[str, Decimal]
    source: str
    as_of: str
    snapshot_id: str
    checksum: str

    @classmethod
    def from_mapping(
        cls,
        rates_cny: Mapping[str, object],
        *,
        source: str,
        as_of: str | datetime,
        snapshot_id: str | None = None,
    ) -> "FxSnapshot":
        rates: dict[str, Decimal] = {}
        for raw_currency, raw_rate in sorted(rates_cny.items()):
            currency = _text(raw_currency).upper()
            rate = _decimal(raw_rate)
            if currency and rate is not None and rate > 0:
                rates[currency] = rate
        rates.setdefault("CNY", Decimal("1"))
        as_of_text = as_of.isoformat() if isinstance(as_of, datetime) else _text(as_of)
        canonical = {
            "rates_cny": {key: str(value) for key, value in rates.items()},
            "source": _text(source),
            "as_of": as_of_text,
        }
        checksum = _checksum(canonical)
        return cls(
            rates,
            _text(source),
            as_of_text,
            _text(snapshot_id) or f"fx:sha256:{checksum}",
            checksum,
        )

    def get(self, currency: str) -> Decimal | None:
        return self.rates_cny.get(_text(currency).upper())

    def payload(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "checksum": self.checksum,
            "source": self.source,
            "as_of": self.as_of,
            "rates_cny": {key: str(value) for key, value in sorted(self.rates_cny.items())},
        }
