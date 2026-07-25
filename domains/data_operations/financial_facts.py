"""Read-only adapters from legacy finance inputs to stable financial facts.

This module deliberately accepts caller-provided mappings or SQLite fixtures.
It never opens the production database, imports a settlement export, or writes
to a database.  Callers must resolve any data-quality issues before using the
facts for margin reporting.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from typing import Any

from shared_platform.contracts import FinancialFact


@dataclass(frozen=True)
class DataQualityIssue:
    """A source field which prevents a reliable fact or margin calculation."""

    code: str
    source: str
    record_id: str
    field: str
    message: str


@dataclass(frozen=True)
class FinancialFactAdaptation:
    facts: tuple[FinancialFact, ...]
    issues: tuple[DataQualityIssue, ...]

    def payload(self) -> dict[str, Any]:
        """Return a JSON-ready payload without losing decimal precision."""
        return {
            "facts": [
                {
                    "fact_id": fact.fact_id,
                    "fact_type": fact.fact_type,
                    "amount": str(fact.amount),
                    "currency": fact.currency,
                    "occurred_at": fact.occurred_at.isoformat(),
                    "product_id": fact.product_id,
                    "channel": fact.channel,
                }
                for fact in self.facts
            ],
            "issues": [
                {
                    "code": issue.code,
                    "source": issue.source,
                    "record_id": issue.record_id,
                    "field": issue.field,
                    "message": issue.message,
                }
                for issue in self.issues
            ],
        }


def adapt_financial_facts(
    sku_costs: Mapping[str, object] | Iterable[Mapping[str, object]],
    settlement_lines: Iterable[Mapping[str, object]],
) -> FinancialFactAdaptation:
    """Adapt legacy SKU-cost and settlement dictionaries into ``FinancialFact``.

    SKU costs use the legacy CNY invariant.  Settlement rows require an amount,
    currency, and occurrence time.  A settlement row without a valid positive
    cost is retained, but carries a ``missing_cost`` issue so a margin consumer
    cannot silently treat its cost as zero.
    """
    facts: list[FinancialFact] = []
    issues: list[DataQualityIssue] = []
    costs = _normalise_costs(sku_costs)

    for sku_id, row in costs.items():
        amount = _decimal(row.get("cost_cny", row.get("amount")))
        occurred_at = _datetime(row.get("updated_at", row.get("occurred_at")))
        if amount is None:
            issues.append(_issue("missing_cost", "sku_costs", sku_id, "cost_cny"))
            continue
        if occurred_at is None:
            issues.append(_issue("missing_occurred_at", "sku_costs", sku_id, "updated_at"))
            continue
        facts.append(FinancialFact(f"cost:{sku_id}:{occurred_at.isoformat()}", "cost", amount, "CNY", occurred_at, sku_id))

    for index, row in enumerate(settlement_lines):
        record_id = str(row.get("id") or row.get("statement_id") or row.get("order_id") or index)
        sku_id = _string(row.get("sku_id"))
        amount = _decimal(row.get("settlement_amount", row.get("amount")))
        currency = _string(row.get("currency")).upper()
        occurred_at = _datetime(row.get("statement_date", row.get("occurred_at")))
        if amount is None:
            issues.append(_issue("missing_amount", "settlement_lines", record_id, "settlement_amount"))
            continue
        if not currency:
            issues.append(_issue("missing_currency", "settlement_lines", record_id, "currency"))
            continue
        if occurred_at is None:
            issues.append(_issue("missing_occurred_at", "settlement_lines", record_id, "statement_date"))
            continue
        if not sku_id or sku_id not in costs or _decimal(costs.get(sku_id, {}).get("cost_cny")) is None:
            issues.append(_issue("missing_cost", "settlement_lines", record_id, "sku_id"))
        line_type = _string(row.get("line_type")).lower()
        fact_type = line_type if line_type in {"fee", "refund", "return"} else "settlement"
        facts.append(FinancialFact(f"settlement:{record_id}", fact_type, amount, currency, occurred_at, sku_id or None, _string(row.get("region")) or None))

    return FinancialFactAdaptation(tuple(facts), tuple(issues))


def adapt_sqlite_fixture(database: sqlite3.Connection | str | Path) -> FinancialFactAdaptation:
    """Read ``sku_costs`` and ``settlement_lines`` from a test SQLite fixture.

    The function executes only SELECT statements.  Passing a connection leaves
    ownership and lifecycle with the caller; passing a path opens it read-only.
    """
    close_connection = not isinstance(database, sqlite3.Connection)
    if close_connection:
        uri = Path(database).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    else:
        connection = database
    try:
        connection.row_factory = sqlite3.Row
        costs = [dict(row) for row in connection.execute("SELECT sku_id, cost_cny, updated_at FROM sku_costs")]
        lines = [dict(row) for row in connection.execute(
            "SELECT id, statement_id, statement_date, line_type, order_id, sku_id, region, currency, settlement_amount FROM settlement_lines"
        )]
        return adapt_financial_facts(costs, lines)
    finally:
        if close_connection:
            connection.close()


def _normalise_costs(source: Mapping[str, object] | Iterable[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    if isinstance(source, Mapping):
        return {
            str(sku_id): dict(value) if isinstance(value, Mapping) else {"cost_cny": value}
            for sku_id, value in source.items()
        }
    return {str(row.get("sku_id")): dict(row) for row in source if row.get("sku_id") is not None}


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(text), time.min)
            except ValueError:
                return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _string(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _issue(code: str, source: str, record_id: str, field: str) -> DataQualityIssue:
    return DataQualityIssue(code, source, record_id, field, f"{source} record {record_id} is missing or invalid {field}")
