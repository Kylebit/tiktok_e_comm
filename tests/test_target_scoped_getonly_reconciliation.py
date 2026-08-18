from __future__ import annotations

from types import SimpleNamespace
import inspect
import json

import pytest

from domains.channel_operations import target_scoped_retry_adapters as adapters
from modules.shopee import target_scoped
from shared_platform.target_scoped_release_contracts import canonical_digest


GLOBAL_ITEM_ID = "40283034166"
REGIONAL_ITEM_ID = "53914952703"
SOURCE_TITLE = "Approved English master"
SOURCE_DESCRIPTION = "Approved immutable product description. " * 8


def _request():
    command = {
        "region": "MY",
        "model_sku": "0954",
        "item_status": "NORMAL",
        "local_original_price": 33.0,
        "local_currency": "MYR",
        "approved_image_count": 6,
        "approved_master_digest": "approved-master-digest",
    }
    operation = SimpleNamespace(
        target_label="shopee:MY",
        plan_id="omnichannel:test",
        run_id="release-run:test",
        planned_command=command,
    )
    selected = [1, 2]
    original = {
        "schema_version": "target-scoped-original-proof-evidence/v1",
        "selected_logistics_ids": selected,
        "selected_logistics_count": len(selected),
        "selected_logistics_digest": canonical_digest({"ids": selected}),
        "source_semantic_evidence_digest": "source-proof-digest",
        "global_item_id": GLOBAL_ITEM_ID,
        "global_item_identity_digest": canonical_digest(
            {"provider": "shopee", "global_item_id": GLOBAL_ITEM_ID}
        ),
    }
    return SimpleNamespace(
        operation_request=operation,
        operation_digest="operation-digest",
        operation_proof_digest="operation-proof-digest",
        prior_result_digest="prior-result-digest",
        external_id=REGIONAL_ITEM_ID,
        external_identity_digest="external-identity-digest",
        original_proof_evidence=original,
        original_proof_evidence_digest=canonical_digest(original),
        global_item_identity_digest=original[
            "global_item_identity_digest"
        ],
        reconciliation_mode="official_get_only_durable_close",
        request_digest="reconciliation-request-digest",
    )


def _install_official_fixture(
    monkeypatch,
    *,
    drift: str | None = None,
):
    calls = {"shop_get": 0, "resolve_global": 0}
    monkeypatch.setattr(
        adapters,
        "_prepared_shopee_credentials",
        lambda _region: (12, "prepared-shop-token"),
    )
    monkeypatch.setattr(
        "modules.shopee.auth.load_tokens",
        lambda: {
            "shops": {"12": {"merchant_id": 34}},
            "merchants": {
                "34": {"access_token": "prepared-merchant-token"}
            },
        },
    )

    def shop_get(path, shop_id, token, query=None):
        calls["shop_get"] += 1
        assert shop_id == 12
        assert token == "prepared-shop-token"
        if drift == "timeout":
            raise TimeoutError("official GET timed out")
        if drift == "malformed":
            return {"error": "malformed"}
        if path.endswith("/get_item_base_info"):
            item_id = (
                53914950000 if drift == "item" else int(REGIONAL_ITEM_ID)
            )
            status = "UNLIST" if drift == "status" else "NORMAL"
            regional_title = "Set hiasan dinding moden"
            regional_description = (
                "Sesuai untuk bilik tidur dan ruang tamu dengan "
                "arahan penjagaan mudah."
            )
            if drift == "copy":
                regional_title = SOURCE_TITLE
                regional_description = SOURCE_DESCRIPTION
            images = [
                f"https://regional.example/rehost-{index}.jpg"
                for index in range(1, 7)
            ]
            if drift == "image_count":
                images.pop()
            if drift == "primary":
                images[0] = ""
            logistics = [
                {"logistic_id": 1, "enabled": True},
                {"logistic_id": 2, "enabled": True},
            ]
            if drift == "logistics":
                logistics[1]["logistic_id"] = 3
            if drift == "logistics_malformed":
                logistics.append("malformed")
            if drift == "logistics_enabled_int":
                logistics.append({"logistic_id": 3, "enabled": 1})
            return {
                "response": {
                    "item_list": [
                        {
                            "item_id": item_id,
                            "item_name": regional_title,
                            "description": regional_description,
                            "item_status": status,
                            "image": {"image_url_list": images},
                            "logistic_info": logistics,
                        }
                    ]
                }
            }
        if path.endswith("/get_model_list"):
            currency = "USD" if drift == "currency" else "MYR"
            price = 34 if drift == "price" else 33
            rows = [
                {
                    "model_id": 11,
                    "model_sku": "0954",
                    "price_info": [
                        {
                            "currency": currency,
                            "original_price": price,
                        }
                    ],
                }
            ]
            if drift == "model":
                rows.append(dict(rows[0], model_id=12))
            if drift == "model_malformed":
                rows.append("malformed")
            if drift == "price_malformed":
                rows[0]["price_info"].append("malformed")
            if drift in {
                "price_nan",
                "price_dict",
                "price_infinite",
                "price_zero",
                "price_negative",
            }:
                invalid = {
                    "price_nan": "not-a-number",
                    "price_dict": {"value": 1},
                    "price_infinite": "Infinity",
                    "price_zero": 0,
                    "price_negative": -1,
                }[drift]
                rows[0]["price_info"].append(
                    {
                        "currency": "USD",
                        "original_price": invalid,
                    }
                )
            return {"response": {"model": rows}}
        raise AssertionError(f"unexpected official GET {path}")

    def resolve_global(*_args):
        calls["resolve_global"] += 1
        return "999999" if drift == "global" else GLOBAL_ITEM_ID

    monkeypatch.setattr(target_scoped, "shop_get", shop_get)
    monkeypatch.setattr(
        "modules.shopee.client.resolve_global_item_id",
        resolve_global,
    )
    monkeypatch.setattr(
        target_scoped,
        "_official_global_master",
        lambda **_kwargs: {
            "title": SOURCE_TITLE,
            "description": SOURCE_DESCRIPTION,
        },
    )
    return calls


def test_getonly_reconciliation_is_exact_redacted_and_zero_write(monkeypatch):
    request = _request()
    calls = _install_official_fixture(monkeypatch)
    receipt = target_scoped.reconcile_existing_global_site(request=request)
    assert all(receipt["checks"].values())
    assert receipt["evidence"]["listing_identity_verified"] is True
    assert receipt["evidence"]["derived_translation_status"] in {
        "observed",
        "warning",
    }
    assert receipt["evidence"]["derived_image_status"] == "warning"
    assert receipt["evidence"]["manual_review_required"] is True
    assert receipt["evidence"]["profit_status"] == "unverified"

    proof = adapters.build_official_target_reconciliation_proof(
        request,
        allow_refresh=False,
    )
    result = adapters.reconcile_target_scoped_operation(request, proof)
    assert result.succeeded is True
    assert result.readback_verified is True
    assert result.external_reference == REGIONAL_ITEM_ID
    assert result.readback_evidence["external_writes_performed"] == []
    assert result.readback_evidence["reconciliation_mode"] == (
        "official_get_only_durable_close"
    )
    encoded = json.dumps(
        {"proof": proof, "result": result.readback_evidence},
        ensure_ascii=False,
        sort_keys=True,
    )
    for forbidden in (
        "prepared-shop-token",
        "prepared-merchant-token",
        SOURCE_TITLE,
        SOURCE_DESCRIPTION.strip(),
        "Set hiasan dinding moden",
        "https://regional.example",
        GLOBAL_ITEM_ID,
    ):
        assert forbidden not in encoded
    assert calls == {"shop_get": 4, "resolve_global": 2}


@pytest.mark.parametrize(
    "drift",
    [
        "item",
        "model",
        "global",
        "status",
        "price",
        "currency",
        "image_count",
        "primary",
        "logistics",
        "model_malformed",
        "price_malformed",
        "price_nan",
        "price_dict",
        "price_infinite",
        "price_zero",
        "price_negative",
        "logistics_malformed",
        "logistics_enabled_int",
        "copy",
    ],
)
def test_getonly_reconciliation_drift_never_verifies(
    monkeypatch,
    drift,
):
    _install_official_fixture(monkeypatch, drift=drift)
    receipt = target_scoped.reconcile_existing_global_site(
        request=_request()
    )
    assert not all(receipt["checks"].values())
    assert (
        receipt["checks"]["derived_observation_acceptable"] is False
        or any(
            value is False
            for key, value in receipt["checks"].items()
            if key != "derived_observation_acceptable"
        )
    )


@pytest.mark.parametrize("failure", ["timeout", "malformed"])
def test_getonly_reconciliation_official_get_failure_is_fail_closed(
    monkeypatch,
    failure,
):
    _install_official_fixture(monkeypatch, drift=failure)
    with pytest.raises((RuntimeError, TimeoutError)):
        target_scoped.reconcile_existing_global_site(request=_request())


def test_getonly_reconciliation_source_has_no_write_or_refresh_path():
    source = inspect.getsource(target_scoped.reconcile_existing_global_site)
    for forbidden in (
        "merchant_post",
        "shop_post",
        "refresh_token",
        "create_publish_task",
        "publish_existing_global_site",
    ):
        assert forbidden not in source
