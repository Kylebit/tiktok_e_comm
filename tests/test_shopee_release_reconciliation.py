import pytest

from domains.channel_operations.release_executor import AdapterExecutionRequest
from modules.products import release_adapters


def _expectation(region: str, *, cny: float, local: float, rate: float):
    currency = {"PH": "PHP", "TH": "THB"}[region]
    return release_adapters._shopee_price_expectation(
        {
            "target_site": region,
            "derived_preview": {
                "global_original_price_cny": cny,
                "local_original_price": local,
                "source_currency": currency,
                "exchange_rate_cny_per_local": rate,
            },
        },
        region=region,
    )


def _install_readback(
    monkeypatch,
    *,
    region: str,
    price_info: dict,
    logistics: list[dict] | None = None,
    base_error: str = "",
    model_error: str = "",
):
    from modules.shopee import auth, client, publish

    monkeypatch.setattr(publish, "sync_shop_ids", lambda: {region: 123})
    monkeypatch.setattr(auth, "ensure_shop_token", lambda _shop_id: "token")
    if region == "TH":
        title = "สติกเกอร์ติดผนังลายสัตว์สำหรับตกแต่งห้อง"
        description = "รายละเอียดสินค้าสำหรับตกแต่งผนังและห้องเด็ก " * 30
    elif region == "VN":
        title = "Miếng dán tường PVC hình mèo trang trí phòng"
        description = "Mô tả sản phẩm trang trí tường bằng tiếng Việt. " * 30
    else:
        title = "Approved Shopee title"
        description = "Detailed approved description. " * 30

    def fake_get(path, _shop_id, _token, _params):
        if path.endswith("get_item_base_info"):
            return {
                "error": base_error,
                "response": {
                    "item_list": [
                        {
                            "item_id": {
                                "PH": 56164935203,
                                "TH": 51564925929,
                                "VN": 47465070376,
                            }[region],
                            "item_name": title,
                            "item_sku": "",
                            "item_status": "NORMAL",
                            "currency": price_info["currency"],
                            "has_model": True,
                            "description": description,
                            "logistic_info": logistics
                            if logistics is not None
                            else [
                                {
                                    "logistic_id": 48002,
                                    "logistic_name": "Standard",
                                    "enabled": True,
                                }
                            ],
                            "image": {
                                "image_url_list": [
                                    "https://img/1.jpg",
                                    "https://img/2.jpg",
                                ]
                            },
                        }
                    ]
                }
            }
        return {
            "error": model_error,
            "response": {
                "model": [
                    {
                        "model_id": 90001,
                        "model_sku": "0954",
                        "price_info": [price_info],
                    }
                ]
            }
        }

    monkeypatch.setattr(client, "shop_get", fake_get)


@pytest.mark.parametrize(
    ("region", "item_id", "price_info", "cny", "local", "rate"),
    [
        (
            "PH",
            "56164935203",
            {
                "currency": "PHP",
                "original_price": 868,
                "current_price": 868,
                "sip_item_price": 81.69,
            },
            81.69,
            868,
            0.0941129032,
        ),
        (
            "TH",
            "51564925929",
            {
                "currency": "THB",
                "original_price": 546,
                "current_price": 546,
                "inflated_price_of_original_price": 725,
                "inflated_price_of_current_price": 725,
                "sip_item_price": 75.05,
            },
            75.05,
            546,
            0.1374542125,
        ),
    ],
)
def test_readback_gates_regional_local_price_and_observes_sip(
    monkeypatch,
    region,
    item_id,
    price_info,
    cny,
    local,
    rate,
):
    _install_readback(monkeypatch, region=region, price_info=price_info)

    verified, evidence = release_adapters._shopee_readback(
        match_key="0954",
        region=region,
        item_id=item_id,
        expected_title="Approved Shopee title",
        expected_price=_expectation(
            region,
            cny=cny,
            local=price_info["original_price"],
            rate=rate,
        ),
        expected_image_count=2,
        expected_description="Approved English description. " * 30,
    )

    assert verified is True
    assert evidence["checks"]["price"] is True
    assert evidence["expected_price"] == {
        "schema_version": "shopee-regional-price-readback/v2",
        "field": "price_info.original_price",
        "value": price_info["original_price"],
        "currency": price_info["currency"],
        "target_local_currency": price_info["currency"],
        "source_local_price": price_info["original_price"],
        "source_local_currency": price_info["currency"],
        "sip_reference_cny": cny,
        "exchange_rate_cny_per_local": rate,
        "source_field": "derived_preview.local_original_price",
        "sip_reference_source_field": (
            "derived_preview.global_original_price_cny"
        ),
    }
    assert evidence["observed_price_fields"] == [
        {
            "scope": "model",
            "model_id": "90001",
            "currency": price_info["currency"],
            "original_price": price_info["original_price"],
            "current_price": price_info["current_price"],
            "inflated_price_of_original_price": price_info.get(
                "inflated_price_of_original_price"
            ),
            "inflated_price_of_current_price": price_info.get(
                "inflated_price_of_current_price"
            ),
            "sip_item_price": price_info["sip_item_price"],
        }
    ]
    assert evidence["price_issues"] == []
    assert evidence["write_status"] == "verified"
    assert evidence["listing_price_verified"] is True
    assert evidence["derived_price_status"] == "matched"
    assert evidence["profit_status"] == "unverified"
    assert evidence["platform_derived_observation"]["writable"] is False


def test_vn_readback_accepts_enabled_channels_with_disabled_non_applicable_rows(
    monkeypatch,
):
    logistics = [
        {"logistic_id": 5004, "logistic_name": "Bulky", "enabled": True},
        {"logistic_id": 5001, "logistic_name": "Fast", "enabled": True},
        {
            "logistic_id": 58007,
            "logistic_name": "Standard International",
            "enabled": True,
        },
        {"logistic_id": 50053, "logistic_name": "Pickup", "enabled": True},
        {"logistic_id": 58018, "logistic_name": "Locker", "enabled": False},
        {"logistic_id": 50052, "logistic_name": "Locker", "enabled": False},
        {"logistic_id": 50039, "logistic_name": "Locker", "enabled": False},
    ]
    _install_readback(
        monkeypatch,
        region="VN",
        price_info={
            "currency": "VND",
            "original_price": 251000,
            "current_price": 251000,
        },
        logistics=logistics,
    )

    verified, evidence = release_adapters._shopee_readback(
        match_key="0954",
        region="VN",
        item_id="47465070376",
        expected_title="Approved English master",
        expected_price=251000,
        expected_image_count=2,
        expected_description="Approved English description. " * 30,
    )

    assert verified is True
    assert evidence["checks"]["all_applicable_logistics"] is True
    assert evidence["enabled_logistics_count"] == 4
    assert len(evidence["disabled_logistics"]) == 3
    assert evidence["logistics_verification_policy"] == (
        "shopee-official-enabled-logistics-present/v1"
    )


def test_readback_fails_closed_on_non_boolean_logistics_shape(monkeypatch):
    _install_readback(
        monkeypatch,
        region="PH",
        price_info={
            "currency": "PHP",
            "original_price": 868,
            "current_price": 868,
            "sip_item_price": 81.69,
        },
        logistics=[
            {"logistic_id": 48002, "logistic_name": "Standard", "enabled": True},
            {"logistic_id": 48003, "logistic_name": "Malformed", "enabled": 1},
        ],
    )

    verified, evidence = release_adapters._shopee_readback(
        match_key="0954",
        region="PH",
        item_id="56164935203",
        expected_title="Approved Shopee title",
        expected_price=_expectation(
            "PH",
            cny=81.69,
            local=868,
            rate=0.0941129032,
        ),
        expected_image_count=2,
    )

    assert verified is False
    assert evidence["checks"]["all_applicable_logistics"] is False


@pytest.mark.parametrize(
    ("base_error", "model_error", "expected_reason"),
    [
        ("timeout", "", "item_base_info_invalid"),
        ("", "timeout", "model_list_invalid"),
    ],
)
def test_readback_rejects_top_level_errors_even_with_matching_payload(
    monkeypatch,
    base_error,
    model_error,
    expected_reason,
):
    _install_readback(
        monkeypatch,
        region="PH",
        price_info={
            "currency": "PHP",
            "original_price": 868,
            "current_price": 868,
            "sip_item_price": 81.69,
        },
        base_error=base_error,
        model_error=model_error,
    )

    verified, evidence = release_adapters._shopee_readback(
        match_key="0954",
        region="PH",
        item_id="56164935203",
        expected_title="Approved Shopee title",
        expected_price=_expectation(
            "PH",
            cny=81.69,
            local=868,
            rate=0.0941129032,
        ),
        expected_image_count=2,
    )

    assert verified is False
    assert evidence["reason"] == expected_reason


def test_readback_rejects_duplicate_logistics_ids(monkeypatch):
    _install_readback(
        monkeypatch,
        region="PH",
        price_info={
            "currency": "PHP",
            "original_price": 868,
            "current_price": 868,
            "sip_item_price": 81.69,
        },
        logistics=[
            {"logistic_id": 48002, "logistic_name": "Standard", "enabled": True},
            {"logistic_id": 48002, "logistic_name": "Duplicate", "enabled": False},
        ],
    )

    verified, evidence = release_adapters._shopee_readback(
        match_key="0954",
        region="PH",
        item_id="56164935203",
        expected_title="Approved Shopee title",
        expected_price=_expectation(
            "PH",
            cny=81.69,
            local=868,
            rate=0.0941129032,
        ),
        expected_image_count=2,
    )

    assert verified is False
    assert evidence["checks"]["all_applicable_logistics"] is False


def test_dispatch_bound_logistics_require_exact_enabled_set(monkeypatch):
    _install_readback(
        monkeypatch,
        region="PH",
        price_info={
            "currency": "PHP",
            "original_price": 868,
            "current_price": 868,
            "sip_item_price": 81.69,
        },
        logistics=[
            {"logistic_id": 48002, "logistic_name": "Standard", "enabled": True},
            {"logistic_id": 48003, "logistic_name": "Pickup", "enabled": True},
            {"logistic_id": 48004, "logistic_name": "Locker", "enabled": False},
        ],
    )
    common = {
        "match_key": "0954",
        "region": "PH",
        "item_id": "56164935203",
        "expected_title": "Approved Shopee title",
        "expected_price": _expectation(
            "PH",
            cny=81.69,
            local=868,
            rate=0.0941129032,
        ),
        "expected_image_count": 2,
        "require_all_logistics": True,
    }

    verified, evidence = release_adapters._shopee_readback(
        **common,
        expected_enabled_logistic_ids=[48002, 48003],
    )
    missing, missing_evidence = release_adapters._shopee_readback(
        **common,
        expected_enabled_logistic_ids=[48002],
    )
    extra, extra_evidence = release_adapters._shopee_readback(
        **common,
        expected_enabled_logistic_ids=[48002, 48003, 48005],
    )

    assert verified is True
    assert evidence["checks"]["all_applicable_logistics"] is True
    assert missing is False
    assert missing_evidence["checks"]["all_applicable_logistics"] is False
    assert extra is False
    assert extra_evidence["checks"]["all_applicable_logistics"] is False


def test_readback_fails_closed_on_local_currency_mismatch(monkeypatch):
    _install_readback(
        monkeypatch,
        region="PH",
        price_info={
            "currency": "THB",
            "original_price": 81.69,
            "current_price": 81.69,
            "sip_item_price": 868,
        },
    )

    verified, evidence = release_adapters._shopee_readback(
        match_key="0954",
        region="PH",
        item_id="56164935203",
        expected_title="Approved Shopee title",
        expected_price=_expectation(
            "PH",
            cny=81.69,
            local=692.29,
            rate=0.118,
        ),
        expected_image_count=2,
    )

    assert verified is False
    assert evidence["checks"]["price"] is False
    assert evidence["price_issues"] == [
        "target_currency_price_row_is_not_unique"
    ]


def test_readback_fails_closed_on_invalid_inflated_price_semantics(monkeypatch):
    _install_readback(
        monkeypatch,
        region="TH",
        price_info={
            "currency": "THB",
            "original_price": 546,
            "current_price": 546,
            "inflated_price_of_original_price": 600,
            "inflated_price_of_current_price": 725,
            "sip_item_price": 75.05,
        },
    )

    verified, evidence = release_adapters._shopee_readback(
        match_key="0954",
        region="TH",
        item_id="51564925929",
        expected_title="Approved Shopee title",
        expected_price=_expectation(
            "TH",
            cny=75.05,
            local=546,
            rate=0.137454,
        ),
        expected_image_count=2,
    )

    assert verified is False
    assert evidence["price_issues"] == [
        "inflated_current_price_exceeds_inflated_original_price"
    ]


def test_reconcile_existing_target_is_read_only_and_uses_recorded_item(monkeypatch):
    from modules.shopee import client

    request = AdapterExecutionRequest(
        plan_id="omnichannel:test",
        confirmation_token="PUBLISH-TEST",
        approval_scope_digest="scope",
        product_id="3838616043",
        seller_sku="0954",
        product_package_id="product:3838616043:0954",
        content_package_id="content:3838616043",
        channel="shopee",
        site="PH",
        target_label="shopee:PH",
        idempotency_key="publish:shopee:PH:test",
    )
    payload = {
        "seller_sku": "0954",
        "product_facts": {
            "title": "Approved master",
            "package_cm": [40, 3, 3],
        },
        "listing_copy": {
            "shopee_description_en": "Approved description. " * 30,
            "candidates": [
                {
                    "channel": "shopee",
                    "site": "CNSC",
                    "title": "Approved Shopee title",
                    "policy_check": "passed",
                }
            ],
        },
        "pricing": {
            "selected_targets": {
                "shopee:PH": {
                    "target_site": "PH",
                    "derived_preview": {
                        "global_original_price_cny": 81.69,
                        "local_original_price": 692.29,
                        "source_currency": "PHP",
                        "exchange_rate_cny_per_local": 0.118,
                    },
                }
            }
        },
    }
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: {
            "payload": payload,
            "images": ["https://img/1.jpg", "https://img/2.jpg"],
            "target": {
                "status": "FAILED",
                "external_id": "56164935203",
            },
        },
    )
    calls = []
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **kwargs: calls.append(kwargs)
        or (
            True,
            {
                "verified": True,
                "checks": {"price": True},
            },
        ),
    )
    monkeypatch.setattr(
        "modules.shopee.publish.publish_match_key",
        lambda *_args, **_kwargs: pytest.fail("reconciliation must not publish"),
    )
    monkeypatch.setattr(
        "modules.shopee.publish.update_local_listing_copy",
        lambda *_args, **_kwargs: pytest.fail("reconciliation must not repair"),
    )
    monkeypatch.setattr(
        client,
        "shop_post",
        lambda *_args, **_kwargs: pytest.fail(
            "reconciliation must not call Shopee POST"
        ),
    )

    result = release_adapters.reconcile_existing_shopee_target(request)

    assert result.succeeded is True
    assert result.readback_verified is True
    assert result.external_reference == "56164935203"
    assert result.readback_evidence["reconciliation_mode"] == (
        "read_only_existing_item"
    )
    assert result.readback_evidence["external_writes_performed"] == []
    assert calls[0]["item_id"] == "56164935203"
    assert calls[0]["allow_token_refresh"] is False
    assert calls[0]["expected_price"]["currency"] == "PHP"
    assert calls[0]["expected_price"]["target_local_currency"] == "PHP"
    assert calls[0]["expected_price"]["sip_reference_cny"] == 81.69


def test_reconcile_existing_target_requires_failed_target_and_external_id(
    monkeypatch,
):
    request = AdapterExecutionRequest(
        plan_id="omnichannel:test",
        confirmation_token="PUBLISH-TEST",
        approval_scope_digest="scope",
        product_id="3838616043",
        seller_sku="0954",
        product_package_id="product:3838616043:0954",
        content_package_id="content:3838616043",
        channel="shopee",
        site="PH",
        target_label="shopee:PH",
        idempotency_key="publish:shopee:PH:test",
    )
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: {
            "payload": {},
            "images": [],
            "target": {"status": "SUCCEEDED", "external_id": "56164935203"},
        },
    )

    with pytest.raises(RuntimeError, match="FAILED durable target"):
        release_adapters.reconcile_existing_shopee_target(request)


def test_read_only_credentials_never_refresh_or_resync(monkeypatch):
    from modules.shopee import auth, publish

    monkeypatch.setattr(
        auth,
        "load_tokens",
        lambda: {
            "sync_shop_ids": {"PH": 123},
            "shops": {
                "123": {
                    "access_token": "existing-token",
                    "expire_at": 4_000_000_000,
                }
            },
        },
    )
    monkeypatch.setattr(
        auth,
        "ensure_shop_token",
        lambda _shop_id: pytest.fail("read-only reconciliation must not refresh"),
    )
    monkeypatch.setattr(
        publish,
        "sync_shop_ids",
        lambda: pytest.fail("read-only reconciliation must not resync shops"),
    )

    assert release_adapters._shopee_readback_credentials(
        "PH",
        allow_token_refresh=False,
    ) == (123, "existing-token")
