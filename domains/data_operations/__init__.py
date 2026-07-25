"""Costs, settlement, profit, advertising and analysis ownership boundary."""

from shared_platform.contracts import FinancialFact
from domains.data_operations.financial_facts import (
    DataQualityIssue,
    FinancialFactAdaptation,
    adapt_financial_facts,
    adapt_sqlite_fixture,
)
from domains.data_operations.weekly_profit_digest import ReportRun, build_weekly_profit_digest
from domains.data_operations.local_snapshot_adapter import (
    LocalSnapshotAdaptation,
    adapt_local_profit_snapshots,
    adapt_profit_snapshot_text,
    discover_local_profit_snapshots,
)

__all__ = [
    "DataQualityIssue",
    "FinancialFact",
    "FinancialFactAdaptation",
    "adapt_financial_facts",
    "adapt_sqlite_fixture",
    "ReportRun",
    "build_weekly_profit_digest",
    "LocalSnapshotAdaptation",
    "adapt_local_profit_snapshots",
    "adapt_profit_snapshot_text",
    "discover_local_profit_snapshots",
]
