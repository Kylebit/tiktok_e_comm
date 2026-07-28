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
from domains.data_operations.release_outcomes import (
    ReleaseOutcomeContractError,
    ReleaseOutcomeFact,
    adapt_release_outcome_receipt,
    adapt_release_outcome_receipts,
    evaluate_release_outcomes,
    release_outcome_dataset,
)
from domains.data_operations.release_optimization import (
    OptimizationThresholds,
    build_release_optimization_candidates,
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
    "ReleaseOutcomeContractError",
    "ReleaseOutcomeFact",
    "adapt_release_outcome_receipt",
    "adapt_release_outcome_receipts",
    "release_outcome_dataset",
    "evaluate_release_outcomes",
    "OptimizationThresholds",
    "build_release_optimization_candidates",
]
