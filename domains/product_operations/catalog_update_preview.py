"""Side-effect-free catalog update and Seller SKU reservation governance.

This module deliberately exposes no apply/write operation.  It compares two
catalog snapshots, inventories legacy reservation evidence, and returns a
deterministic preview which an integration layer may present for approval.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping, Sequence


IDENTITY_FIELDS = ("sku_id", "shop_cipher")
IGNORED_COMPARISON_FIELDS: frozenset[str] = frozenset()


def canonical_seller_sku(value: object) -> str:
    """Return the four-digit cross-region Seller SKU key, or an empty string."""
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d+", raw):
        return ""
    return raw[-4:].zfill(4)


@dataclass(frozen=True)
class CatalogChange:
    action: str
    identity: tuple[str, str]
    changed_fields: tuple[str, ...]
    before: dict[str, Any] | None
    after: dict[str, Any] | None

    def payload(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "identity": {
                "sku_id": self.identity[0],
                "shop_cipher": self.identity[1],
            },
            "changed_fields": list(self.changed_fields),
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class SellerSkuReservation:
    seller_sku: str
    offer_id: str
    source: str
    status: str

    def payload(self) -> dict[str, str]:
        return {
            "seller_sku": self.seller_sku,
            "offer_id": self.offer_id,
            "source": self.source,
            "status": self.status,
        }


@dataclass(frozen=True)
class CatalogUpdatePreview:
    current_snapshot_id: str
    incoming_snapshot_id: str
    reservation_snapshot_id: str
    preview_id: str
    source_revision: str
    changes: tuple[CatalogChange, ...]
    unchanged_count: int
    reservations: tuple[SellerSkuReservation, ...]
    conflicts: tuple[dict[str, Any], ...]
    blockers: tuple[str, ...]
    next_seller_skus: tuple[str, ...]

    @property
    def ready_for_review(self) -> bool:
        return not self.blockers

    def payload(self) -> dict[str, Any]:
        counts = {
            action: sum(change.action == action for change in self.changes)
            for action in ("add", "update", "remove")
        }
        counts["unchanged"] = self.unchanged_count
        return {
            "dry_run": True,
            "apply_allowed": False,
            "ready_for_review": self.ready_for_review,
            "source_revision": self.source_revision,
            "current_snapshot_id": self.current_snapshot_id,
            "incoming_snapshot_id": self.incoming_snapshot_id,
            "reservation_snapshot_id": self.reservation_snapshot_id,
            "preview_id": self.preview_id,
            "counts": counts,
            "changes": [change.payload() for change in self.changes],
            "reservations": [item.payload() for item in self.reservations],
            "conflicts": [dict(item) for item in self.conflicts],
            "blockers": list(self.blockers),
            "next_seller_skus": list(self.next_seller_skus),
        }


def reservations_from_documents(
    workbench_states: Mapping[str, Mapping[str, Any]] | None = None,
    tiktok_claims: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[SellerSkuReservation, ...]:
    """Extract active reservations from old workbench locks and claim records."""
    facts: set[tuple[str, str, str, str]] = set()
    for offer_id, state in sorted((workbench_states or {}).items()):
        review = state.get("review") or {}
        product_approval = state.get("product_approval") or {}
        review_sku = canonical_seller_sku(review.get("seller_sku"))
        if review_sku and review.get("fields_locked") is True:
            facts.add((review_sku, str(offer_id), "workbench_lock", "legacy_locked"))
        approval_status = str(product_approval.get("status") or "").lower()
        approval_sku = canonical_seller_sku(product_approval.get("seller_sku"))
        if approval_sku and approval_status == "approved":
            facts.add((approval_sku, str(offer_id), "product_approval", "approved"))

    for offer_id, claim in sorted((tiktok_claims or {}).items()):
        numbering = claim.get("sku_numbering") or {}
        verified = numbering.get("verified") is True
        claimed = claim.get("claimed") is True
        if not (verified or claimed):
            continue
        numbers = numbering.get("sku_item_nums") or []
        if isinstance(numbers, Mapping):
            numbers = list(numbers.values())
        if not isinstance(numbers, Sequence) or isinstance(numbers, (str, bytes)):
            numbers = []
        base_sku = canonical_seller_sku(numbering.get("base_sku"))
        if not numbers and base_sku:
            numbers = [base_sku]
        for raw in numbers:
            seller_sku = canonical_seller_sku(raw)
            if seller_sku:
                facts.add(
                    (
                        seller_sku,
                        str(offer_id),
                        "tiktok_claim",
                        "claimed" if claimed else "numbering_verified",
                    )
                )
    return tuple(
        SellerSkuReservation(*fact)
        for fact in sorted(facts, key=lambda item: (int(item[0]), item[1], item[2]))
    )


def preview_catalog_update(
    current_rows: Iterable[Mapping[str, Any]],
    incoming_rows: Iterable[Mapping[str, Any]],
    *,
    workbench_states: Mapping[str, Mapping[str, Any]] | None = None,
    tiktok_claims: Mapping[str, Mapping[str, Any]] | None = None,
    source_revision: str = "",
    complete_snapshot: bool = False,
    requested_sku_count: int = 1,
) -> CatalogUpdatePreview:
    """Create a deterministic dry-run preview; never mutates either snapshot."""
    current, current_issues = _index_rows(current_rows, "current")
    incoming, incoming_issues = _index_rows(incoming_rows, "incoming")
    reservations = reservations_from_documents(workbench_states, tiktok_claims)
    conflicts = list(current_issues + incoming_issues)
    conflicts.extend(
        _reservation_conflicts(
            tuple(current.values()) + tuple(incoming.values()), reservations
        )
    )

    changes: list[CatalogChange] = []
    unchanged_count = 0
    for identity in sorted(set(current) | set(incoming)):
        before = current.get(identity)
        after = incoming.get(identity)
        if before is None:
            changes.append(CatalogChange("add", identity, tuple(sorted(after or {})), None, after))
        elif after is None:
            changes.append(
                CatalogChange("remove", identity, tuple(sorted(before)), before, None)
            )
        else:
            changed_fields = tuple(
                key
                for key in sorted(set(before) | set(after))
                if key not in IGNORED_COMPARISON_FIELDS and before.get(key) != after.get(key)
            )
            if changed_fields:
                changes.append(
                    CatalogChange("update", identity, changed_fields, before, after)
                )
            else:
                unchanged_count += 1

    blockers = [_conflict_blocker(item) for item in conflicts]
    removal_count = sum(change.action == "remove" for change in changes)
    if removal_count and not complete_snapshot:
        blockers.append(
            f"incoming snapshot is not declared complete; {removal_count} removals are blocked"
        )
    if current and not incoming:
        blockers.append("incoming snapshot is empty while the current catalog is not")
    source_revision = str(source_revision or "").strip()
    if not source_revision:
        blockers.append("source revision/version is required")
    if requested_sku_count < 1:
        blockers.append("requested_sku_count must be at least 1")

    catalog_occupied = {
        canonical_seller_sku(row.get("seller_sku"))
        for row in tuple(current.values()) + tuple(incoming.values())
        if canonical_seller_sku(row.get("seller_sku"))
    }
    occupied = set(catalog_occupied)
    occupied.update(item.seller_sku for item in reservations)
    next_skus = (
        _next_contiguous_skus(
            occupied,
            max(requested_sku_count, 1),
            start_after=max((int(value) for value in catalog_occupied), default=0),
        )
        if requested_sku_count > 0
        else ()
    )
    current_snapshot_id = _fingerprint(list(current.values()))
    incoming_snapshot_id = _fingerprint(list(incoming.values()))
    reservation_snapshot_id = _fingerprint([item.payload() for item in reservations])
    preview_id = _fingerprint(
        {
            "current": current_snapshot_id,
            "incoming": incoming_snapshot_id,
            "reservations": reservation_snapshot_id,
            "source_revision": source_revision,
            "complete_snapshot": bool(complete_snapshot),
            "requested_sku_count": requested_sku_count,
        }
    )
    return CatalogUpdatePreview(
        current_snapshot_id=current_snapshot_id,
        incoming_snapshot_id=incoming_snapshot_id,
        reservation_snapshot_id=reservation_snapshot_id,
        preview_id=preview_id,
        source_revision=source_revision,
        changes=tuple(changes),
        unchanged_count=unchanged_count,
        reservations=reservations,
        conflicts=tuple(conflicts),
        blockers=tuple(dict.fromkeys(blockers)),
        next_seller_skus=next_skus,
    )


def _index_rows(
    rows: Iterable[Mapping[str, Any]], snapshot_name: str
) -> tuple[dict[tuple[str, str], dict[str, Any]], tuple[dict[str, Any], ...]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for index, value in enumerate(rows):
        row = dict(value)
        identity = tuple(str(row.get(field) or "").strip() for field in IDENTITY_FIELDS)
        if not all(identity):
            issues.append(
                {
                    "code": "missing_catalog_identity",
                    "snapshot": snapshot_name,
                    "row_index": index,
                    "identity": identity,
                }
            )
            continue
        if identity in indexed:
            issues.append(
                {
                    "code": "duplicate_catalog_identity",
                    "snapshot": snapshot_name,
                    "identity": identity,
                }
            )
            continue
        indexed[identity] = row
    return indexed, tuple(issues)


def _reservation_conflicts(
    catalog_rows: Iterable[Mapping[str, Any]],
    reservations: Sequence[SellerSkuReservation],
) -> tuple[dict[str, Any], ...]:
    by_sku: dict[str, set[str]] = defaultdict(set)
    sources: dict[str, set[str]] = defaultdict(set)
    for fact in reservations:
        by_sku[fact.seller_sku].add(fact.offer_id)
        sources[fact.seller_sku].add(fact.source)
    conflicts: list[dict[str, Any]] = []
    for seller_sku, offer_ids in sorted(by_sku.items()):
        if len(offer_ids) > 1:
            conflicts.append(
                {
                    "code": "overlapping_seller_sku_reservation",
                    "seller_sku": seller_sku,
                    "offer_ids": tuple(sorted(offer_ids)),
                    "sources": tuple(sorted(sources[seller_sku])),
                }
            )

    catalog_skus: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in catalog_rows:
        identity = tuple(str(row.get(field) or "").strip() for field in IDENTITY_FIELDS)
        seller_sku = canonical_seller_sku(row.get("seller_sku"))
        if seller_sku and identity not in catalog_skus[seller_sku]:
            catalog_skus[seller_sku].append(identity)
    for seller_sku in sorted(set(catalog_skus) & set(by_sku)):
        conflicts.append(
            {
                "code": "reserved_seller_sku_already_in_catalog",
                "seller_sku": seller_sku,
                "offer_ids": tuple(sorted(by_sku[seller_sku])),
                "catalog_identities": tuple(sorted(catalog_skus[seller_sku])),
            }
        )
    return tuple(conflicts)


def _next_contiguous_skus(
    occupied: set[str], requested_count: int, *, start_after: int
) -> tuple[str, ...]:
    numeric = {int(value) for value in occupied if re.fullmatch(r"\d{4}", value)}
    start = max(1, start_after + 1)
    while start + requested_count - 1 <= 9999:
        candidate = range(start, start + requested_count)
        if all(value not in numeric for value in candidate):
            return tuple(f"{value:04d}" for value in candidate)
        start += 1
    return ()


def _conflict_blocker(conflict: Mapping[str, Any]) -> str:
    code = str(conflict.get("code") or "catalog_conflict")
    seller_sku = str(conflict.get("seller_sku") or "")
    return f"{code}: {seller_sku}".rstrip(": ")


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple, set, frozenset)):
        normalized = [_json_ready(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _fingerprint(value: Any) -> str:
    raw = json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(raw).hexdigest()
