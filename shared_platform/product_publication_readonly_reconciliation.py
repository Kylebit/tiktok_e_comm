"""Read-only refresh evidence for completed Product Center publication runs."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from modules.ozon.approved_publication_v4 import (
    OzonDispatchFact,
    _classify_variant,
    project_ozon_v4_variants,
)


SCHEMA_VERSION = "product-publication-readonly-reconciliation/v1"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class ProductPublicationReadonlyReconciliationError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _ozon_listing_refresh(snapshot: Mapping[str, Any], dependencies: object) -> dict[str, Any]:
    readback = getattr(dependencies, "readback_variants", None)
    if not callable(readback):
        raise ProductPublicationReadonlyReconciliationError(
            "Ozon official listing readback is unavailable"
        )
    variants = project_ozon_v4_variants(
        snapshot,
        target_labels=("ozon:RU",),
        official_profile_resolver=getattr(
            dependencies, "official_profile_resolver", None
        ),
        localized_copy_resolver=getattr(
            dependencies, "localized_copy_resolver", None
        ),
    )
    offer_ids = tuple(row["offer_id"] for row in variants)
    raw_items = readback(offer_ids)
    if not isinstance(raw_items, (list, tuple)) or any(
        not isinstance(row, Mapping) for row in raw_items
    ):
        raise ProductPublicationReadonlyReconciliationError(
            "Ozon official listing readback is malformed"
        )
    by_offer: dict[str, list[Mapping[str, Any]]] = {
        offer_id: [] for offer_id in offer_ids
    }
    for row in raw_items:
        offer_id = str(row.get("offer_id") or "").strip()
        if offer_id not in by_offer:
            raise ProductPublicationReadonlyReconciliationError(
                "Ozon official listing identity escaped the frozen scope"
            )
        by_offer[offer_id].append(row)
    statuses: list[str] = []
    product_ids: list[str] = []
    for variant in variants:
        matches = by_offer[variant["offer_id"]]
        if len(matches) > 1:
            raise ProductPublicationReadonlyReconciliationError(
                "Ozon official listing identity is ambiguous"
            )
        observed = matches[0] if matches else None
        statuses.append(
            _classify_variant(
                variant, observed, OzonDispatchFact(outcome="UNKNOWN")
            )
        )
        if observed is not None:
            product_ids.append(str(observed.get("id") or ""))
    listing_status = (
        "PUBLISHED"
        if statuses and all(status == "PUBLISHED" for status in statuses)
        else "FAILED"
        if any(status == "FAILED" for status in statuses)
        else "PROCESSING"
    )

    stock_policy = snapshot.get("product", {}).get("stock_policy")
    stock_approved = (
        isinstance(stock_policy, Mapping)
        and stock_policy.get("source") == "SYSTEM_GOVERNED_DEFAULT"
    )
    stock_rows: list[Mapping[str, Any]] | None = None
    stock_reader = getattr(dependencies, "readback_stocks", None)
    if callable(stock_reader) and listing_status == "PUBLISHED":
        raw_stocks = stock_reader(offer_ids)
        if not isinstance(raw_stocks, (list, tuple)) or any(
            not isinstance(row, Mapping) for row in raw_stocks
        ):
            raise ProductPublicationReadonlyReconciliationError(
                "Ozon official stock readback is malformed"
            )
        stock_rows = [deepcopy(dict(row)) for row in raw_stocks]
    if stock_approved:
        expected = {
            row["offer_id"]: row["stock_quantity"] for row in variants
        }
        observed = {
            str(row.get("offer_id") or ""): row.get("stock")
            for row in stock_rows or ()
        }
        inventory_status = "VERIFIED" if observed == expected else "MISMATCH"
    elif stock_rows:
        inventory_status = "OBSERVED_UNAPPROVED"
    else:
        inventory_status = "UNSTOCKED_UNAPPROVED"
    current_status = (
        "PROCESSING"
        if listing_status == "PUBLISHED"
        and stock_approved
        and inventory_status != "VERIFIED"
        else listing_status
    )

    provider_facts = {
        "listing_rows": deepcopy(list(raw_items)),
        "stock_rows": stock_rows,
    }
    return {
        "platform": "OZON",
        "target_label": "ozon:RU",
        "derived_current_status": current_status,
        "listing_current_status": listing_status,
        "listing_statuses": statuses,
        "product_ids": product_ids,
        "inventory": {
            "status": inventory_status,
            "approved_policy": stock_approved,
            "observed_row_count": len(stock_rows or ()),
        },
        "provider_facts_digest": _digest(provider_facts),
    }


def reconcile_completed_processing_run_readonly(
    *,
    run_id: str,
    report_store: object,
    run_store: object,
    release_store: object,
    ozon_dependencies: object,
) -> dict[str, Any]:
    """Refresh official state without dispatching or changing the final report."""

    if type(run_id) is not str or _RUN_ID.fullmatch(run_id) is None:
        raise ProductPublicationReadonlyReconciliationError(
            "exact publication run_id is required"
        )
    report = report_store.get_report_by_run(run_id=run_id)
    run = run_store.get_run_by_id(run_id=run_id)
    if not isinstance(report, Mapping) or not isinstance(run, Mapping):
        raise ProductPublicationReadonlyReconciliationError(
            "completed publication run is unavailable"
        )
    if (
        run.get("state") != "COMPLETED"
        or run.get("final_report_id") != report.get("report_id")
        or report.get("status") != "PROCESSING"
    ):
        raise ProductPublicationReadonlyReconciliationError(
            "run is not a completed PROCESSING publication"
        )
    targets = report.get("targets")
    if (
        not isinstance(targets, list)
        or len(targets) != 1
        or targets[0].get("target_label") != "ozon:RU"
    ):
        raise ProductPublicationReadonlyReconciliationError(
            "read-only refresh currently requires one exact Ozon target"
        )
    snapshot = release_store.approved_publication_snapshot(
        offer_id=report["offer_id"], plan_id=report["plan_id"]
    )
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("snapshot_digest") != report.get("snapshot", {}).get("digest")
        or snapshot.get("product_revision") != report.get("revision")
    ):
        raise ProductPublicationReadonlyReconciliationError(
            "approved publication snapshot identity conflicts"
        )

    derived = _ozon_listing_refresh(snapshot, ozon_dependencies)
    document = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "report_id": report["report_id"],
        "offer_id": report["offer_id"],
        "revision": report["revision"],
        "plan_id": report["plan_id"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "source_final_report": {
            "status": report["status"],
            "summary_digest": report["summary_digest"],
        },
        "derived": derived,
        "external_writes_performed": [],
        "mutation_reservations": [],
    }
    document["evidence_digest"] = _digest(document)
    root = Path(report_store.reports_root).resolve()
    destination = (
        root
        / "readonly-reconciliations"
        / run_id
        / f"{document['evidence_digest'].removeprefix('sha256:')[:24]}.json"
    ).resolve()
    if root != destination and root not in destination.parents:
        raise ProductPublicationReadonlyReconciliationError(
            "reconciliation evidence path escaped the reports root"
        )
    encoded = _canonical(document)
    idempotent = destination.is_file()
    if idempotent:
        if destination.read_bytes() != encoded:
            raise ProductPublicationReadonlyReconciliationError(
                "reconciliation evidence digest collision"
            )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + f".{uuid4().hex}.tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, destination)
    return {
        "evidence": document,
        "evidence_path": str(destination),
        "idempotent": idempotent,
    }


__all__ = [
    "ProductPublicationReadonlyReconciliationError",
    "reconcile_completed_processing_run_readonly",
]
