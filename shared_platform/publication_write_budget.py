"""Fail-closed mutation-attempt budgets for approved product publication.

The release candidate already limits confirmed writes.  This ledger adds the
missing pre-mutation boundary: every provider-changing request must reserve an
attempt before the transport call.  A timeout or unknown provider outcome does
not refund the reservation because retrying it may duplicate a real write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


class PublicationWriteBudgetExceeded(RuntimeError):
    """A provider mutation would exceed the frozen release-candidate budget."""


def _nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _operation(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 80
        or not value.isascii()
        or any(not (character.islower() or character.isdigit() or character == "_") for character in value)
    ):
        raise ValueError("operation must be a lower-case ASCII identifier")
    return value


class PublicationWriteBudgetLedger:
    """In-memory, request-scoped reservation ledger with no provider I/O."""

    def __init__(
        self,
        *,
        platform: str,
        target_labels: Sequence[str],
        budget: Mapping[str, Any],
    ) -> None:
        if type(platform) is not str or not platform or platform != platform.upper():
            raise ValueError("platform must be an upper-case identifier")
        labels = tuple(target_labels)
        if (
            not labels
            or any(type(label) is not str or not label.strip() for label in labels)
            or len(labels) != len(set(labels))
        ):
            raise ValueError("target_labels must contain unique approved targets")
        if not isinstance(budget, Mapping):
            raise TypeError("budget must be a mapping")
        budget_labels = budget.get("target_labels")
        if budget_labels is not None and tuple(budget_labels) != labels:
            raise ValueError("budget target_labels conflict with approved targets")

        self.platform = platform
        self.target_labels = labels
        self.shared_maximum = _nonnegative_int(
            budget.get("shared_maximum"), "shared_maximum"
        )
        self.per_target_maximum = _nonnegative_int(
            budget.get("per_target_maximum"), "per_target_maximum"
        )
        self._shared_attempt_count = 0
        self._target_attempt_counts = {label: 0 for label in labels}
        self._reservations: list[dict[str, Any]] = []

    @property
    def shared_attempt_count(self) -> int:
        return self._shared_attempt_count

    @property
    def target_attempt_counts(self) -> dict[str, int]:
        return dict(self._target_attempt_counts)

    @property
    def total_attempt_count(self) -> int:
        return self._shared_attempt_count + sum(self._target_attempt_counts.values())

    @property
    def reservations(self) -> list[dict[str, Any]]:
        return deepcopy(self._reservations)

    def snapshot(self) -> dict[str, Any]:
        """Return a redacted, JSON-safe audit projection for durable reports."""

        return {
            "schema_version": "publication-mutation-budget/v1",
            "platform": self.platform,
            "limits": {
                "shared_maximum": self.shared_maximum,
                "per_target_maximum": self.per_target_maximum,
            },
            "attempts": {
                "shared": self.shared_attempt_count,
                "per_target": self.target_attempt_counts,
                "total": self.total_attempt_count,
            },
            "reservations": self.reservations,
        }

    def reserve_shared(self, operation: str, *, count: int = 1) -> dict[str, Any]:
        safe_operation = _operation(operation)
        safe_count = _nonnegative_int(count, "count")
        if safe_count == 0:
            raise ValueError("count must be positive")
        if self._shared_attempt_count + safe_count > self.shared_maximum:
            raise PublicationWriteBudgetExceeded(
                f"{self.platform} shared mutation attempt budget exceeded"
            )
        self._shared_attempt_count += safe_count
        return self._record(
            scope="shared",
            target_label=None,
            operation=safe_operation,
            attempt_count=safe_count,
        )

    def reserve_target(
        self, target_label: str, operation: str, *, count: int = 1
    ) -> dict[str, Any]:
        if target_label not in self._target_attempt_counts:
            raise ValueError("target_label is not an approved target")
        safe_operation = _operation(operation)
        safe_count = _nonnegative_int(count, "count")
        if safe_count == 0:
            raise ValueError("count must be positive")
        current = self._target_attempt_counts[target_label]
        if current + safe_count > self.per_target_maximum:
            raise PublicationWriteBudgetExceeded(
                f"{self.platform} target {target_label} mutation attempt budget exceeded"
            )
        self._target_attempt_counts[target_label] = current + safe_count
        return self._record(
            scope="target",
            target_label=target_label,
            operation=safe_operation,
            attempt_count=safe_count,
        )

    def assert_confirmed_writes_within_attempts(self, confirmed_write_count: int) -> None:
        safe_count = _nonnegative_int(confirmed_write_count, "confirmed_write_count")
        if safe_count > self.total_attempt_count:
            raise PublicationWriteBudgetExceeded(
                "confirmed writes exceed reserved mutation attempts"
            )

    def _record(
        self,
        *,
        scope: str,
        target_label: str | None,
        operation: str,
        attempt_count: int,
    ) -> dict[str, Any]:
        reservation = {
            "sequence": len(self._reservations) + 1,
            "scope": scope,
            "target_label": target_label,
            "operation": operation,
            "attempt_count": attempt_count,
        }
        self._reservations.append(reservation)
        return deepcopy(reservation)


__all__ = [
    "PublicationWriteBudgetExceeded",
    "PublicationWriteBudgetLedger",
]
