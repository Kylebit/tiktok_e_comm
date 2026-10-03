"""Read-only, account-bound Ozon warehouse decision for an R3 preview.

This module does not call a provider by itself. The server must supply a
transport bound to the same pinned credentials used by the Ozon executor.
The returned receipt can be kept beside an R3 plan; only its five-field
``decision`` belongs in the v4 product facts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import re
from typing import Any

from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
from shared_platform.publication_rounds import canonical_digest
from shared_platform.publication_stock_policy import default_publication_stock_policy


WAREHOUSE_LIST_PATH = "/v2/warehouse/list"
DECISION_SCHEMA = "ozon-stock-warehouse-decision/v1"
RECEIPT_SCHEMA = "ozon-stock-warehouse-readback/v1"
SELECTION_POLICY = "EXACT_UNIQUE_ACTIVE_OR_CREATED_NON_KGT"
PostBound = Callable[..., Mapping[str, Any]]


def _lineage(offer_id: str, round1: Mapping[str, Any],
             pinned: PinnedOzonCredentials) -> tuple[str, dict[str, Any]]:
    if (type(offer_id) is not str or not offer_id.isdecimal()
            or int(offer_id) <= 0 or not isinstance(round1, Mapping)
            or round1.get("schema_version") != "round1-approved-snapshot/v1"
            or round1.get("status") != "APPROVED"
            or round1.get("offer_id") != offer_id
            or not isinstance(round1.get("canonical_targets"), list)
            or "ozon:RU" not in round1["canonical_targets"]):
        raise ValueError("OZON_ROUND1_OFFER_IDENTITY_REQUIRED")
    unsigned = dict(round1)
    digest = unsigned.pop("snapshot_digest", None)
    if digest != canonical_digest(unsigned):
        raise ValueError("OZON_ROUND1_DIGEST_DRIFTED")
    stock_policy = round1.get("publication_stock_policy")
    facts = round1.get("fact_snapshot")
    if (stock_policy != default_publication_stock_policy()
            or not isinstance(facts, Mapping)
            or facts.get("publication_stock_policy") != stock_policy):
        raise ValueError("OZON_ROUND1_STOCK_POLICY_INCOMPATIBLE")
    if (type(pinned) is not PinnedOzonCredentials
            or not pinned.account_id.isdecimal() or int(pinned.account_id) <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", pinned.credentials_sha256)):
        raise ValueError("OZON_PINNED_ACCOUNT_REQUIRED")
    return digest, deepcopy(stock_policy)


def resolve_warehouse_decision(*, offer_id: str, round1: Mapping[str, Any],
                               pinned: PinnedOzonCredentials,
                               post_bound: PostBound) -> dict[str, Any]:
    """Use exactly one official read to decide an R3 warehouse, with no writes."""
    round1_digest, stock_policy = _lineage(offer_id, round1, pinned)
    if not callable(post_bound):
        raise ValueError("OZON_BOUND_READONLY_TRANSPORT_REQUIRED")
    response = post_bound(WAREHOUSE_LIST_PATH, {}, client_id=pinned.account_id,
                          api_key=pinned.api_key)
    if not isinstance(response, Mapping) or response.get("error"):
        raise ValueError("OZON_WAREHOUSE_READBACK_REJECTED")
    rows = response.get("warehouses")
    if rows is None:
        rows = response.get("result")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("OZON_WAREHOUSE_READBACK_MALFORMED")
    eligible = [row for row in rows
                if str(row.get("status") or "").strip().casefold() in {"active", "created"}
                and row.get("is_kgt") is False]
    if len(eligible) != 1:
        raise ValueError("OZON_UNIQUE_NON_KGT_WAREHOUSE_REQUIRED")
    warehouse_id = eligible[0].get("warehouse_id")
    if type(warehouse_id) is not int or warehouse_id <= 0:
        raise ValueError("OZON_WAREHOUSE_ID_INVALID")
    decision = {
        "schema_version": DECISION_SCHEMA,
        "warehouse_id": warehouse_id,
        "selection_policy": SELECTION_POLICY,
        "stock_policy_digest": canonical_digest(stock_policy),
        "source": "OFFICIAL_PROVIDER_READBACK",
    }
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "offer_id": offer_id,
        "round1_snapshot_digest": round1_digest,
        "stock_policy_digest": decision["stock_policy_digest"],
        "ozon_account_id": pinned.account_id,
        "ozon_credentials_sha256": pinned.credentials_sha256,
        "warehouse_response_digest": canonical_digest(dict(response)),
        "decision": decision,
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def validate_warehouse_receipt(receipt: Mapping[str, Any], *, offer_id: str,
                               round1: Mapping[str, Any],
                               pinned: PinnedOzonCredentials) -> dict[str, Any]:
    """Reject a persisted receipt from another Offer, R1 revision or account."""
    round1_digest, stock_policy = _lineage(offer_id, round1, pinned)
    if not isinstance(receipt, Mapping):
        raise ValueError("OZON_WAREHOUSE_RECEIPT_INVALID")
    body = dict(receipt)
    supplied_digest = body.pop("receipt_digest", None)
    if (set(body) != {"schema_version", "offer_id", "round1_snapshot_digest",
                      "stock_policy_digest", "ozon_account_id",
                      "ozon_credentials_sha256", "warehouse_response_digest", "decision"}
            or supplied_digest != canonical_digest(body)
            or body["schema_version"] != RECEIPT_SCHEMA
            or body["offer_id"] != offer_id
            or body["round1_snapshot_digest"] != round1_digest
            or body["stock_policy_digest"] != canonical_digest(stock_policy)
            or body["ozon_account_id"] != pinned.account_id
            or body["ozon_credentials_sha256"] != pinned.credentials_sha256
            or not isinstance(body["warehouse_response_digest"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", body["warehouse_response_digest"])):
        raise ValueError("OZON_WAREHOUSE_RECEIPT_IDENTITY_DRIFTED")
    decision = body["decision"]
    if (not isinstance(decision, Mapping)
            or set(decision) != {"schema_version", "warehouse_id", "selection_policy",
                                    "stock_policy_digest", "source"}
            or decision.get("schema_version") != DECISION_SCHEMA
            or type(decision.get("warehouse_id")) is not int
            or decision["warehouse_id"] <= 0
            or decision.get("selection_policy") != SELECTION_POLICY
            or decision.get("stock_policy_digest") != body["stock_policy_digest"]
            or decision.get("source") != "OFFICIAL_PROVIDER_READBACK"):
        raise ValueError("OZON_WAREHOUSE_RECEIPT_DECISION_DRIFTED")
    return deepcopy(dict(receipt))


__all__ = ["resolve_warehouse_decision", "validate_warehouse_receipt"]
