"""Offline official-readback contract; never contacts Ozon or writes commerce data."""

from copy import deepcopy

import pytest

from domains.product_operations.approved_publication_snapshot import (
    _ozon_stock_decision,
    _publication_stock_policy,
)
from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
from shared_platform.ozon_stock_warehouse_decision import (
    resolve_warehouse_decision,
    validate_warehouse_receipt,
)
from shared_platform.publication_rounds import canonical_digest
from shared_platform.publication_stock_policy import default_publication_stock_policy


def round1(offer="3956742887"):
    policy = default_publication_stock_policy()
    body = {
        "schema_version": "round1-approved-snapshot/v1",
        "status": "APPROVED",
        "offer_id": offer,
        "canonical_targets": ["ozon:RU"],
        "publication_stock_policy": policy,
        "fact_snapshot": {"publication_stock_policy": deepcopy(policy)},
    }
    return {**body, "snapshot_digest": canonical_digest(body)}


def pinned(account="12345"):
    return PinnedOzonCredentials(account, "fake-secret-never-serialized", "a" * 64)


def official(rows, calls):
    def post(path, body, *, client_id, api_key):
        calls.append((path, body, client_id, api_key))
        return {"warehouses": deepcopy(rows)}
    return post


def test_exact_unique_warehouse_is_bound_to_offer_r1_policy_and_account():
    calls = []
    source = round1()
    identity = pinned()
    receipt = resolve_warehouse_decision(
        offer_id=source["offer_id"], round1=source, pinned=identity,
        post_bound=official([
            {"warehouse_id": 71, "status": "active", "is_kgt": False},
            {"warehouse_id": 72, "status": "active", "is_kgt": True},
            {"warehouse_id": 73, "status": "disabled", "is_kgt": False},
        ], calls),
    )
    assert calls == [("/v2/warehouse/list", {}, "12345", "fake-secret-never-serialized")]
    assert receipt["decision"] == {
        "schema_version": "ozon-stock-warehouse-decision/v1",
        "warehouse_id": 71,
        "selection_policy": "EXACT_UNIQUE_ACTIVE_OR_CREATED_NON_KGT",
        "stock_policy_digest": canonical_digest(default_publication_stock_policy()),
        "source": "OFFICIAL_PROVIDER_READBACK",
    }
    assert _ozon_stock_decision(
        receipt["decision"],
        stock_policy=_publication_stock_policy(source["publication_stock_policy"]),
    ) == receipt["decision"]
    assert validate_warehouse_receipt(receipt, offer_id=source["offer_id"],
                                      round1=source, pinned=identity) == receipt
    assert "fake-secret-never-serialized" not in str(receipt)


@pytest.mark.parametrize("rows,error", [
    ([], "OZON_UNIQUE_NON_KGT_WAREHOUSE_REQUIRED"),
    ([{"warehouse_id": 71, "status": "active", "is_kgt": False},
      {"warehouse_id": 72, "status": "created", "is_kgt": False}],
     "OZON_UNIQUE_NON_KGT_WAREHOUSE_REQUIRED"),
    ([{"warehouse_id": 0, "status": "active", "is_kgt": False}],
     "OZON_WAREHOUSE_ID_INVALID"),
    ([{"warehouse_id": True, "status": "active", "is_kgt": False}],
     "OZON_WAREHOUSE_ID_INVALID"),
    ([{"warehouse_id": 71, "status": "active", "is_kgt": True}],
     "OZON_UNIQUE_NON_KGT_WAREHOUSE_REQUIRED"),
])
def test_no_guess_on_missing_ambiguous_or_bad_warehouse(rows, error):
    calls = []
    source = round1()
    with pytest.raises(ValueError, match=error):
        resolve_warehouse_decision(offer_id=source["offer_id"], round1=source,
                                   pinned=pinned(), post_bound=official(rows, calls))
    assert len(calls) == 1 and calls[0][0] == "/v2/warehouse/list"


@pytest.mark.parametrize("change", ["missing", "legacy_two", "tamper"])
def test_incompatible_or_changed_r1_never_calls_provider(change):
    source = round1()
    if change == "missing":
        source["publication_stock_policy"] = {}
    elif change == "legacy_two":
        source["publication_stock_policy"] = {"stock_per_model_sku": 2}
        source["fact_snapshot"]["publication_stock_policy"] = source["publication_stock_policy"]
        source["snapshot_digest"] = canonical_digest({key: value for key, value in source.items()
                                                       if key != "snapshot_digest"})
    else:
        source["offer_id"] = "other"
    calls = []
    with pytest.raises(ValueError):
        resolve_warehouse_decision(offer_id="3956742887", round1=source,
                                   pinned=pinned(), post_bound=official([], calls))
    assert calls == []


def test_reloaded_receipt_rejects_offer_account_policy_and_decision_drift():
    source = round1()
    receipt = resolve_warehouse_decision(offer_id=source["offer_id"], round1=source,
                                         pinned=pinned(), post_bound=official([
                                             {"warehouse_id": 71, "status": "created", "is_kgt": False}], []))
    for changed in (
        {**receipt, "offer_id": "other"},
        {**receipt, "ozon_account_id": "other"},
        {**receipt, "stock_policy_digest": "sha256:" + "0" * 64},
        {**receipt, "decision": {**receipt["decision"], "warehouse_id": 72}},
    ):
        with pytest.raises(ValueError):
            validate_warehouse_receipt(changed, offer_id=source["offer_id"],
                                       round1=source, pinned=pinned())
    with pytest.raises(ValueError, match="OZON_WAREHOUSE_RECEIPT_IDENTITY_DRIFTED"):
        validate_warehouse_receipt(receipt, offer_id=source["offer_id"], round1=source,
                                   pinned=pinned("98765"))
