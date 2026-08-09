import pytest

from modules.shopee import publish


def _row(title: str, description: str) -> dict:
    return {
        "error": "",
        "response": {
            "global_item_list": [
                {
                    "global_item_id": 51465029034,
                    "global_item_name": title,
                    "description": description,
                    "attribute_list": [],
                }
            ]
        },
    }


def test_exact_global_copy_is_read_only(monkeypatch):
    description = ("Approved factual description. " * 30).strip()
    posts = []
    monkeypatch.setattr(
        publish,
        "merchant_get",
        lambda *_args, **_kwargs: _row("Approved title", description),
    )
    monkeypatch.setattr(
        publish,
        "merchant_post",
        lambda *args, **kwargs: posts.append((args, kwargs)),
    )

    receipt = publish.ensure_global_master(
        global_item_id=51465029034,
        merchant_id=100,
        merchant_token="runtime-only",
        detail={},
        title="Approved title",
        description=description,
        ref={},
    )

    assert receipt["updated"] is False
    assert receipt["external_writes_performed"] == []
    assert posts == []


def test_copy_drift_is_updated_once_then_read_back(monkeypatch):
    description = ("Approved factual description. " * 30).strip()
    reads = iter(
        [
            _row("Old title", description),
            _row("Approved title", description),
        ]
    )
    posts = []
    monkeypatch.setattr(
        publish,
        "merchant_get",
        lambda *_args, **_kwargs: next(reads),
    )
    monkeypatch.setattr(
        publish,
        "merchant_post",
        lambda *args, **kwargs: posts.append((args, kwargs))
        or {"error": "", "response": {}},
    )
    monkeypatch.setattr(
        publish,
        "_global_attribute_list",
        lambda *_args, **_kwargs: [],
    )

    receipt = publish.ensure_global_master(
        global_item_id=51465029034,
        merchant_id=100,
        merchant_token="runtime-only",
        detail={},
        title="Approved title",
        description=description,
        ref={},
    )

    assert len(posts) == 1
    assert receipt["updated"] is True
    assert receipt["external_writes_performed"] == [
        "shopee:global_master:update"
    ]


def test_existing_global_price_is_updated_from_approved_cny_price(monkeypatch):
    description = ("Approved factual description. " * 30).strip()
    old = _row("Approved title", description)
    old["response"]["global_item_list"][0]["original_price"] = 10.0
    current = _row("Approved title", description)
    current["response"]["global_item_list"][0]["original_price"] = 40.0
    reads = iter([old, current])
    posts = []
    monkeypatch.setattr(
        publish,
        "merchant_get",
        lambda *_args, **_kwargs: next(reads),
    )
    monkeypatch.setattr(
        publish,
        "merchant_post",
        lambda *args, **kwargs: posts.append((args, kwargs))
        or {"error": "", "response": {}},
    )
    monkeypatch.setattr(
        publish,
        "_global_attribute_list",
        lambda *_args, **_kwargs: [],
    )

    receipt = publish.ensure_global_master(
        global_item_id=51465029034,
        merchant_id=100,
        merchant_token="runtime-only",
        detail={},
        title="Approved title",
        description=description,
        ref={},
        original_price=40.0,
    )

    assert posts[0][0][3]["original_price"] == 40.0
    assert receipt["updated"] is True


def test_existing_global_parcel_is_updated_from_approved_safe_envelope(monkeypatch):
    """An exact copy must not hide a stale first-SKU master parcel."""

    description = ("Approved factual description. " * 30).strip()
    old = _row("Approved title", description)
    old_item = old["response"]["global_item_list"][0]
    old_item.update({
        "weight": 0.1,
        "dimension": {
            "package_length": 20,
            "package_width": 20,
            "package_height": 3,
        },
    })
    current = _row("Approved title", description)
    current_item = current["response"]["global_item_list"][0]
    current_item.update({
        "weight": 0.2,
        "dimension": {
            "package_length": 40,
            "package_width": 20,
            "package_height": 3,
        },
    })
    reads = iter([old, current])
    posts = []
    monkeypatch.setattr(
        publish,
        "merchant_get",
        lambda *_args, **_kwargs: next(reads),
    )
    monkeypatch.setattr(
        publish,
        "merchant_post",
        lambda *args, **kwargs: posts.append((args, kwargs))
        or {"error": "", "response": {}},
    )
    monkeypatch.setattr(
        publish,
        "_global_attribute_list",
        lambda *_args, **_kwargs: [],
    )

    receipt = publish.ensure_global_master(
        global_item_id=51465029034,
        merchant_id=100,
        merchant_token="runtime-only",
        detail={
            "package_weight": {"value": 0.2, "unit": "KILOGRAM"},
            "package_dimensions": {"length": 40, "width": 20, "height": 3},
        },
        title="Approved title",
        description=description,
        ref={},
    )

    body = posts[0][0][3]
    assert body["weight"] == 0.2
    assert body["dimension"] == {
        "package_length": 40,
        "package_width": 20,
        "package_height": 3,
    }
    assert receipt["updated"] is True


def test_global_update_transport_ambiguity_preserves_write_class(monkeypatch):
    description = ("Approved factual description. " * 30).strip()
    monkeypatch.setattr(
        publish,
        "merchant_get",
        lambda *_args, **_kwargs: _row("Old title", description),
    )
    monkeypatch.setattr(
        publish,
        "merchant_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("transport timeout")
        ),
    )
    monkeypatch.setattr(
        publish,
        "_global_attribute_list",
        lambda *_args, **_kwargs: [],
    )

    with pytest.raises(
        publish.ShopeeGlobalMasterReconciliationError
    ) as caught:
        publish.ensure_global_master(
            global_item_id=51465029034,
            merchant_id=100,
            merchant_token="runtime-only",
            detail={},
            title="Approved title",
            description=description,
            ref={},
        )

    assert caught.value.external_write_evidence[
        "external_writes_performed"
    ] == []
    assert caught.value.external_write_evidence[
        "possible_external_writes_performed"
    ] == ["shopee:global_master:update"]


def test_global_update_then_regional_dispatch_unknown_preserves_both_writes():
    regional = publish.ShopeeRegionalPublishReconciliationError(
        "timeout",
        global_item_id=51465029034,
        possible_external_writes_performed=[
            "shopee:regional_publish"
        ],
        reason="regional_publish_dispatch_unknown",
    )

    merged = publish._merge_shopee_publish_write_evidence(
        regional,
        global_item_id=51465029034,
        global_master_receipt={
            "updated": True,
            "verified": True,
            "external_writes_performed": ["shopee:global_master:update"],
        },
    )

    assert merged.external_write_evidence["external_writes_performed"] == [
        "shopee:global_master:update",
    ]
    assert merged.external_write_evidence[
        "possible_external_writes_performed"
    ] == ["shopee:regional_publish"]
    assert merged.external_write_evidence["durable_state_uncertain"] is True


def test_global_update_then_regional_pre_submit_failure_preserves_only_first_write():
    merged = publish._merge_shopee_publish_write_evidence(
        ValueError("model drift before regional dispatch"),
        global_item_id=51465029034,
        global_master_receipt={
            "updated": True,
            "verified": True,
            "external_writes_performed": ["shopee:global_master:update"],
        },
    )

    assert merged.external_write_evidence["external_writes_performed"] == [
        "shopee:global_master:update"
    ]
    assert merged.external_write_evidence["submission_accepted"] is False


def test_regional_dispatch_unknown_without_global_update_preserves_regional_write():
    regional = publish.ShopeeRegionalPublishReconciliationError(
        "timeout",
        global_item_id=51465029034,
        possible_external_writes_performed=[
            "shopee:regional_publish"
        ],
        reason="regional_publish_dispatch_unknown",
    )

    merged = publish._merge_shopee_publish_write_evidence(
        regional,
        global_item_id=51465029034,
        global_master_receipt={
            "updated": False,
            "verified": True,
            "external_writes_performed": [],
        },
    )

    assert merged.external_write_evidence["external_writes_performed"] == []
    assert merged.external_write_evidence[
        "possible_external_writes_performed"
    ] == [
        "shopee:regional_publish"
    ]


@pytest.mark.parametrize("status", ["NORMAL", "DELETED"])
def test_official_global_status_accepts_only_exact_executable_states(
    monkeypatch,
    status,
):
    monkeypatch.setattr(
        publish,
        "merchant_get",
        lambda *_args, **_kwargs: {
            "error": "",
            "response": {
                "global_item_list": [
                    {
                        "global_item_id": 51465029034,
                        "global_item_status": status,
                    }
                ]
            },
        },
    )

    assert publish._official_global_item_status(
        global_item_id=51465029034,
        merchant_id=100,
        merchant_token="runtime-only",
    ) == status


@pytest.mark.parametrize(
    "response",
    [
        {"error": "timeout", "response": {}},
        {"error": "", "response": {"global_item_list": []}},
        {
            "error": "",
            "response": {
                "global_item_list": [
                    {
                        "global_item_id": 999,
                        "global_item_status": "NORMAL",
                    }
                ]
            },
        },
        {
            "error": "",
            "response": {
                "global_item_list": [
                    {
                        "global_item_id": 51465029034,
                        "global_item_status": "BANNED",
                    }
                ]
            },
        },
        {
            "error": "",
            "response": {
                "global_item_list": [
                    {
                        "global_item_id": 51465029034,
                        "global_item_status": 1,
                    }
                ]
            },
        },
    ],
)
def test_official_global_status_malformed_or_nonexecuting_fails_closed(
    monkeypatch,
    response,
):
    monkeypatch.setattr(
        publish,
        "merchant_get",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(RuntimeError):
        publish._official_global_item_status(
            global_item_id=51465029034,
            merchant_id=100,
            merchant_token="runtime-only",
        )


def test_replacement_mapping_is_persisted_before_regional_dispatch(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        publish,
        "_create_global_item",
        lambda *_args, **_kwargs: {
            "global_item_id": 90000000001,
            "global_title": "approved replacement",
        },
    )
    monkeypatch.setattr(
        publish,
        "replace_deleted_global_entry",
        lambda *args, **kwargs: calls.append(("replace", args, kwargs)),
    )

    def _regional(**_kwargs):
        calls.append(("regional", (), {}))
        raise ValueError("pre-submit regional identity drift")

    monkeypatch.setattr(publish, "_run_publish_task", _regional)

    with pytest.raises(
        publish.ShopeeRegionalPublishReconciliationError
    ) as caught:
        publish._publish_global(
            {},
            region="PH",
            shop_id=1,
            token="runtime-only",
            model_sku="0956",
            image_ids=["image"],
            ref={},
            map_match_key="0956",
            replaced_deleted_global_item_id=51465029034,
        )

    assert [call[0] for call in calls] == ["replace", "regional"]
    assert caught.value.external_write_evidence[
        "external_writes_performed"
    ] == ["shopee:global_master:create"]


def test_replacement_mapping_failure_preserves_global_create_write(
    monkeypatch,
):
    regional_calls = []
    monkeypatch.setattr(
        publish,
        "_create_global_item",
        lambda *_args, **_kwargs: {
            "global_item_id": 90000000001,
            "global_title": "approved replacement",
        },
    )
    monkeypatch.setattr(
        publish,
        "replace_deleted_global_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("local mapping write failed")
        ),
    )
    monkeypatch.setattr(
        publish,
        "_run_publish_task",
        lambda **_kwargs: regional_calls.append(True),
    )

    with pytest.raises(
        publish.ShopeeRegionalPublishReconciliationError
    ) as caught:
        publish._publish_global(
            {},
            region="PH",
            shop_id=1,
            token="runtime-only",
            model_sku="0956",
            image_ids=["image"],
            ref={},
            map_match_key="0956",
            replaced_deleted_global_item_id=51465029034,
        )

    assert regional_calls == []
    assert caught.value.external_write_evidence[
        "external_writes_performed"
    ] == ["shopee:global_master:create"]
    assert caught.value.external_write_evidence["reason"] == (
        "global_master_mapping_persistence_failed"
    )


def test_missing_regional_tiktok_row_uses_plan_price_and_semantic_source(
    monkeypatch,
):
    semantic_detail = {
        "title": "Approved semantic title",
        "description": "Approved description",
        "main_images": [{"urls": ["https://example.test/image.jpg"]}],
        "skus": [
            {
                "price": {"sale_price": 1},
                "sku_weight": {"value": 0.2, "unit": "KILOGRAM"},
                "sku_dimensions": {
                    "length": 10,
                    "width": 10,
                    "height": 2,
                },
            }
        ],
    }
    captured = {}
    monkeypatch.setattr(publish, "sync_shop_ids", lambda: {"PH": 1})
    monkeypatch.setattr(
        publish,
        "_find_tk_for_global",
        lambda *_args, **_kwargs: (
            {"seller_sku": "seller-0956"},
            semantic_detail,
            "PH",
        ),
    )
    monkeypatch.setattr(
        publish,
        "_find_tk_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("regional source row missing")
        ),
    )
    monkeypatch.setattr(
        publish,
        "_collect_image_urls",
        lambda _detail: ["https://example.test/image.jpg"],
    )
    monkeypatch.setattr(publish, "ensure_shop_token", lambda _shop: "token")
    monkeypatch.setattr(
        publish,
        "global_item_id_for_match_key",
        lambda _key: None,
    )
    monkeypatch.setattr(publish, "_reference_item", lambda *_args: {})
    monkeypatch.setattr(
        publish,
        "_logistic_info",
        lambda *_args, **_kwargs: [{"logistic_id": 1, "enabled": True}],
    )
    monkeypatch.setattr(
        publish,
        "_shop_meta",
        lambda *_args: {
            "is_cb": True,
            "merchant_id": 100,
        },
    )
    monkeypatch.setattr(
        publish,
        "_merchant_token",
        lambda *_args: "merchant-token",
    )
    monkeypatch.setattr(publish, "_upload_images", lambda _urls: ["image"])

    def _publish(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "item_id": 123,
            "global_item_id": 90000000001,
            "global_master": {
                "verified": True,
                "external_writes_performed": [
                    "shopee:global_master:create"
                ],
            },
        }

    monkeypatch.setattr(publish, "_publish_global", _publish)

    result = publish.publish_match_key(
        "0956",
        "PH",
        global_only=False,
        local_original_price_override=12.34,
        local_price_currency_override="PHP",
        global_original_price_cny_override=40.0,
        title_override="Approved title",
        description_override="Approved description",
    )

    assert result["item_id"] == 123
    assert captured["local_detail"]["title"] == "Approved title"
    assert captured["local_original_price_override"] == 12.34
    assert captured["local_price_currency_override"] == "PHP"


def test_deleted_global_identity_routes_to_one_replacement_creation(
    monkeypatch,
):
    detail = {
        "title": "Approved semantic title",
        "description": "Approved description",
        "main_images": [{"urls": ["https://example.test/image.jpg"]}],
        "skus": [
            {
                "price": {"sale_price": 12.34},
                "sku_weight": {"value": 0.2, "unit": "KILOGRAM"},
                "sku_dimensions": {
                    "length": 10,
                    "width": 10,
                    "height": 2,
                },
            }
        ],
    }
    captured = {}
    monkeypatch.setattr(publish, "sync_shop_ids", lambda: {"PH": 1})
    monkeypatch.setattr(
        publish,
        "_find_tk_for_global",
        lambda *_args, **_kwargs: ({"seller_sku": "0956"}, detail, "PH"),
    )
    monkeypatch.setattr(
        publish,
        "_find_tk_row",
        lambda *_args, **_kwargs: {"seller_sku": "0956"},
    )
    monkeypatch.setattr(publish, "_fetch_tk_detail", lambda _row: detail)
    monkeypatch.setattr(
        publish,
        "_collect_image_urls",
        lambda _detail: ["https://example.test/image.jpg"],
    )
    monkeypatch.setattr(publish, "ensure_shop_token", lambda _shop: "token")
    monkeypatch.setattr(
        publish,
        "global_item_id_for_match_key",
        lambda _key: "51465029034",
    )
    monkeypatch.setattr(publish, "_reference_item", lambda *_args: {})
    monkeypatch.setattr(
        publish,
        "_logistic_info",
        lambda *_args, **_kwargs: [{"logistic_id": 1, "enabled": True}],
    )
    monkeypatch.setattr(
        publish,
        "_shop_meta",
        lambda *_args: {
            "is_cb": True,
            "merchant_id": 100,
        },
    )
    monkeypatch.setattr(
        publish,
        "_merchant_token",
        lambda *_args: "merchant-token",
    )
    monkeypatch.setattr(
        publish,
        "_official_global_item_status",
        lambda **_kwargs: "DELETED",
    )
    monkeypatch.setattr(
        publish,
        "ensure_global_master",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("deleted global item must not be updated")
        ),
    )
    monkeypatch.setattr(publish, "_upload_images", lambda _urls: ["image"])

    def _publish(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "item_id": 123,
            "global_item_id": 90000000001,
            "global_master": {
                "verified": True,
                "created": True,
                "external_writes_performed": [
                    "shopee:global_master:create"
                ],
            },
        }

    monkeypatch.setattr(publish, "_publish_global", _publish)

    result = publish.publish_match_key(
        "0956",
        "PH",
        global_only=False,
        local_original_price_override=12.34,
        local_price_currency_override="PHP",
        global_original_price_cny_override=40.0,
        title_override="Approved title",
        description_override="Approved description",
    )

    assert captured["replaced_deleted_global_item_id"] == 51465029034
    assert captured["map_match_key"] == "0956"
    assert result["global_master"]["created"] is True


def test_active_global_identity_never_creates_replacement(monkeypatch):
    detail = {
        "title": "Approved semantic title",
        "description": "Approved description",
        "main_images": [{"urls": ["https://example.test/image.jpg"]}],
        "skus": [
            {
                "price": {"sale_price": 12.34},
                "sku_weight": {"value": 0.2, "unit": "KILOGRAM"},
                "sku_dimensions": {
                    "length": 10,
                    "width": 10,
                    "height": 2,
                },
            }
        ],
    }
    monkeypatch.setattr(publish, "sync_shop_ids", lambda: {"PH": 1})
    monkeypatch.setattr(
        publish,
        "_find_tk_for_global",
        lambda *_args, **_kwargs: ({"seller_sku": "0956"}, detail, "PH"),
    )
    monkeypatch.setattr(
        publish,
        "_find_tk_row",
        lambda *_args, **_kwargs: {"seller_sku": "0956"},
    )
    monkeypatch.setattr(publish, "_fetch_tk_detail", lambda _row: detail)
    monkeypatch.setattr(
        publish,
        "_collect_image_urls",
        lambda _detail: ["https://example.test/image.jpg"],
    )
    monkeypatch.setattr(publish, "ensure_shop_token", lambda _shop: "token")
    monkeypatch.setattr(
        publish,
        "global_item_id_for_match_key",
        lambda _key: "51465029034",
    )
    monkeypatch.setattr(publish, "_reference_item", lambda *_args: {})
    monkeypatch.setattr(
        publish,
        "_logistic_info",
        lambda *_args, **_kwargs: [{"logistic_id": 1, "enabled": True}],
    )
    monkeypatch.setattr(
        publish,
        "_shop_meta",
        lambda *_args: {
            "is_cb": True,
            "merchant_id": 100,
        },
    )
    monkeypatch.setattr(
        publish,
        "_merchant_token",
        lambda *_args: "merchant-token",
    )
    monkeypatch.setattr(
        publish,
        "_official_global_item_status",
        lambda **_kwargs: "NORMAL",
    )
    monkeypatch.setattr(
        publish,
        "ensure_global_master",
        lambda **_kwargs: {
            "verified": True,
            "updated": False,
            "external_writes_performed": [],
        },
    )
    monkeypatch.setattr(
        publish,
        "_publish_global",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("active global item must not be recreated")
        ),
    )
    monkeypatch.setattr(
        publish,
        "_publish_existing_global",
        lambda *_args, **_kwargs: {
            "item_id": 123,
            "global_item_id": 51465029034,
        },
    )

    result = publish.publish_match_key(
        "0956",
        "PH",
        global_only=False,
        local_original_price_override=12.34,
        local_price_currency_override="PHP",
        global_original_price_cny_override=40.0,
        title_override="Approved title",
        description_override="Approved description",
    )

    assert result["global_item_id"] == 51465029034
    assert result["global_master"]["updated"] is False


def test_global_only_existing_item_applies_current_approved_master(monkeypatch):
    detail = {
        "title": "Source title",
        "description": "Source description",
        "main_images": [{"urls": ["https://example.test/image.jpg"]}],
        "skus": [{"price": {"sale_price": 12.34}}],
    }
    captured = {}
    monkeypatch.setattr(publish, "sync_shop_ids", lambda: {"PH": 1})
    monkeypatch.setattr(
        publish,
        "_find_tk_for_global",
        lambda *_args, **_kwargs: ({"seller_sku": "0956"}, detail, "PH"),
    )
    monkeypatch.setattr(
        publish,
        "_find_tk_row",
        lambda *_args, **_kwargs: {"seller_sku": "0956"},
    )
    monkeypatch.setattr(publish, "_fetch_tk_detail", lambda _row: detail)
    monkeypatch.setattr(
        publish,
        "_collect_image_urls",
        lambda _detail: ["https://example.test/image.jpg"],
    )
    monkeypatch.setattr(publish, "ensure_shop_token", lambda _shop: "token")
    monkeypatch.setattr(
        publish,
        "global_item_id_for_match_key",
        lambda _key: "51465029034",
    )
    monkeypatch.setattr(publish, "_reference_item", lambda *_args: {})
    monkeypatch.setattr(
        publish,
        "_shop_meta",
        lambda *_args: {"is_cb": True, "merchant_id": 100},
    )
    monkeypatch.setattr(
        publish,
        "_merchant_token",
        lambda *_args: "merchant-token",
    )
    monkeypatch.setattr(
        publish,
        "_official_global_item_status",
        lambda **_kwargs: "NORMAL",
    )

    def _ensure(**kwargs):
        captured.update(kwargs)
        return {
            "verified": True,
            "updated": True,
            "external_writes_performed": ["shopee:global_master:update"],
        }

    monkeypatch.setattr(publish, "ensure_global_master", _ensure)

    result = publish.publish_match_key(
        "0956",
        "PH",
        global_only=True,
        global_original_price_cny_override=40.0,
        title_override="Approved title",
        description_override="Approved description",
    )

    assert captured["global_item_id"] == 51465029034
    assert captured["title"] == "Approved title"
    assert captured["description"] == "Approved description"
    assert captured["original_price"] == 40.0
    assert result["global_master"]["updated"] is True


def test_deleted_global_is_replaced_once_then_reused_across_all_regions(
    monkeypatch,
):
    detail = {
        "title": "Approved semantic title",
        "description": "Approved description",
        "main_images": [{"urls": ["https://example.test/image.jpg"]}],
        "skus": [
            {
                "price": {"sale_price": 12.34},
                "sku_weight": {"value": 0.2, "unit": "KILOGRAM"},
                "sku_dimensions": {
                    "length": 10,
                    "width": 10,
                    "height": 2,
                },
            }
        ],
    }
    active_global_item_id = {"value": 51465029034}
    created_regions = []
    reused_regions = []

    monkeypatch.setattr(
        publish,
        "sync_shop_ids",
        lambda: {"MY": 1, "PH": 2, "TH": 3, "VN": 4},
    )
    monkeypatch.setattr(
        publish,
        "_find_tk_for_global",
        lambda *_args, **_kwargs: ({"seller_sku": "0956"}, detail, "PH"),
    )
    monkeypatch.setattr(
        publish,
        "_find_tk_row",
        lambda *_args, **_kwargs: {"seller_sku": "0956"},
    )
    monkeypatch.setattr(publish, "_fetch_tk_detail", lambda _row: detail)
    monkeypatch.setattr(
        publish,
        "_collect_image_urls",
        lambda _detail: ["https://example.test/image.jpg"],
    )
    monkeypatch.setattr(
        publish,
        "ensure_shop_token",
        lambda shop_id: f"shop-token-{shop_id}",
    )
    monkeypatch.setattr(
        publish,
        "global_item_id_for_match_key",
        lambda _key: str(active_global_item_id["value"]),
    )
    monkeypatch.setattr(publish, "_reference_item", lambda *_args: {})
    monkeypatch.setattr(
        publish,
        "_parcel_facts",
        lambda _detail: (0.2, (10.0, 10.0, 2.0)),
    )
    monkeypatch.setattr(
        publish,
        "_logistic_info",
        lambda *_args, **_kwargs: [{"logistic_id": 1, "enabled": True}],
    )
    monkeypatch.setattr(
        publish,
        "_shop_meta",
        lambda *_args: {"is_cb": True, "merchant_id": 100},
    )
    monkeypatch.setattr(
        publish,
        "_merchant_token",
        lambda *_args: "merchant-token",
    )
    monkeypatch.setattr(
        publish,
        "_official_global_item_status",
        lambda **kwargs: (
            "DELETED"
            if kwargs["global_item_id"] == 51465029034
            else "NORMAL"
        ),
    )
    monkeypatch.setattr(publish, "_upload_images", lambda _urls: ["image"])
    monkeypatch.setattr(
        publish,
        "ensure_global_master",
        lambda **_kwargs: {
            "verified": True,
            "updated": False,
            "external_writes_performed": [],
        },
    )

    def _create_and_publish(*_args, **kwargs):
        created_regions.append(kwargs["region"])
        assert kwargs["replaced_deleted_global_item_id"] == 51465029034
        active_global_item_id["value"] = 90000000001
        return {
            "item_id": 7001,
            "global_item_id": 90000000001,
            "global_master": {
                "verified": True,
                "created": True,
                "external_writes_performed": [
                    "shopee:global_master:create"
                ],
            },
        }

    def _reuse_existing(global_item_id, *_args, **kwargs):
        assert global_item_id == 90000000001
        reused_regions.append(kwargs["region"])
        return {
            "item_id": 7000 + len(reused_regions) + 1,
            "global_item_id": global_item_id,
        }

    monkeypatch.setattr(publish, "_publish_global", _create_and_publish)
    monkeypatch.setattr(
        publish,
        "_publish_existing_global",
        _reuse_existing,
    )

    results = [
        publish.publish_match_key(
            "0956",
            region,
            global_only=False,
            local_original_price_override=12.34,
            local_price_currency_override={
                "MY": "MYR",
                "PH": "PHP",
                "TH": "THB",
                "VN": "VND",
            }[region],
            global_original_price_cny_override=40.0,
            title_override="Approved title",
            description_override="Approved description",
        )
        for region in ("MY", "PH", "TH", "VN")
    ]

    assert created_regions == ["MY"]
    assert reused_regions == ["PH", "TH", "VN"]
    assert [row["global_item_id"] for row in results] == [
        90000000001,
        90000000001,
        90000000001,
        90000000001,
    ]


def _patch_publish_task_preflight(monkeypatch):
    monkeypatch.setattr(
        publish,
        "_shop_meta",
        lambda *_args: {"merchant_id": 100},
    )
    monkeypatch.setattr(
        publish,
        "_merchant_token",
        lambda *_args: "merchant-token",
    )
    monkeypatch.setattr(
        publish,
        "_local_item_fields",
        lambda *_args, **_kwargs: ("title", "description", 12.34),
    )
    monkeypatch.setattr(
        publish,
        "_parcel_facts",
        lambda _detail: (0.2, (10.0, 10.0, 2.0)),
    )
    monkeypatch.setattr(
        publish,
        "ensure_single_global_model",
        lambda **_kwargs: {"publish_models": []},
    )
    monkeypatch.setattr(publish.time, "sleep", lambda _seconds: None)


def test_regional_dispatch_transport_is_possible_not_performed(
    monkeypatch,
):
    _patch_publish_task_preflight(monkeypatch)
    monkeypatch.setattr(
        publish,
        "merchant_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("transport timeout")
        ),
    )

    with pytest.raises(
        publish.ShopeeRegionalPublishReconciliationError
    ) as caught:
        publish._run_publish_task(
            global_item_id=90000000001,
            detail={"skus": [{"inventory": []}]},
            region="PH",
            shop_id=1,
            token="runtime-only",
            model_sku="0956",
            ref={},
            local_original_price_override=12.34,
            local_price_currency_override="PHP",
            global_original_price_cny_override=40.0,
            logistics_override=[{"logistic_id": 1, "enabled": True}],
        )

    evidence = caught.value.external_write_evidence
    assert evidence["external_writes_performed"] == []
    assert evidence["possible_external_writes_performed"] == [
        "shopee:regional_publish"
    ]
    assert evidence["submission_accepted"] is False


def test_regional_task_poll_failure_is_confirmed_write(monkeypatch):
    _patch_publish_task_preflight(monkeypatch)
    monkeypatch.setattr(
        publish,
        "merchant_post",
        lambda *_args, **_kwargs: {
            "error": "",
            "response": {"publish_task_id": 123},
        },
    )
    monkeypatch.setattr(
        publish,
        "merchant_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("poll timeout")
        ),
    )

    with pytest.raises(
        publish.ShopeeRegionalPublishReconciliationError
    ) as caught:
        publish._run_publish_task(
            global_item_id=90000000001,
            detail={"skus": [{"inventory": []}]},
            region="PH",
            shop_id=1,
            token="runtime-only",
            model_sku="0956",
            ref={},
            local_original_price_override=12.34,
            local_price_currency_override="PHP",
            global_original_price_cny_override=40.0,
            logistics_override=[{"logistic_id": 1, "enabled": True}],
        )

    evidence = caught.value.external_write_evidence
    assert evidence["external_writes_performed"] == [
        "shopee:regional_publish"
    ]
    assert evidence["possible_external_writes_performed"] == []
    assert evidence["submission_accepted"] is True
