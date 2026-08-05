"""Regression tests for the independent Shopee global button."""

from __future__ import annotations

import pytest

from modules.products import server as product_server
from modules.shopee import approved_global_publisher
from modules.shopee import publish as shopee_publish


def _three_variant_approved_payload() -> dict:
    return {
        "seller_sku": "0960",
        "listing_copy": {
            "candidates": [{
                "channel": "shopee",
                "site": "CNSC",
                "policy_check": "passed",
                "title": "Approved three-variant title",
            }],
            "shopee_description_en": "Approved three-variant description",
        },
        "pricing": {
            "master_price_source": {
                "region": "PH",
                "target_key": "shopee:PH",
            },
            "selected_targets": {
                "shopee:PH": {
                    "source": {"target_key": "shopee:PH"},
                    "derived_preview": {
                        "global_original_price_cny": 119.65,
                    },
                    "sku_prices": [
                        {"variant_key": "variant-a", "model_sku": "0960", "derived_preview": {"global_original_price_cny": 89.5}},
                        {"variant_key": "variant-b", "model_sku": "0961", "derived_preview": {"global_original_price_cny": 119.65}},
                        {"variant_key": "variant-c", "model_sku": "0962", "derived_preview": {"global_original_price_cny": 159.25}},
                    ],
                },
            },
        },
        "product_facts": {
            "package_cm": [60, 7, 7],
            "weight_kg": 0.265,
            "sku_commercial_facts": {
                "variant-a": {"cost_cny": 15, "weight_kg": 0.15, "package_cm": [30, 7, 7]},
                "variant-b": {"cost_cny": 18, "weight_kg": 0.265, "package_cm": [60, 7, 7]},
                "variant-c": {"cost_cny": 22, "weight_kg": 0.4, "package_cm": [90, 7, 7]},
            },
            "selected_sku_keys": ["variant-a", "variant-b", "variant-c"],
            "selected_skus": [
                {"key": "variant-a", "label": "A：60cmx3m", "model_sku": "0960"},
                {"key": "variant-b", "label": "B：60cmx3m", "model_sku": "0961"},
                {"key": "variant-c", "label": "C：60cmx3m", "model_sku": "0962"},
            ],
            "sku_label_overrides": {
                "variant-a": "A：60cmx3m",
                "variant-b": "B：60cmx3m",
                "variant-c": "C：60cmx3m",
            },
        },
        "sku_lineage": {
            "assignment": {
                "seller_sku": "0960",
                "model_skus": [
                    {"variant_key": "variant-a", "model_sku": "0960"},
                    {"variant_key": "variant-b", "model_sku": "0961"},
                    {"variant_key": "variant-c", "model_sku": "0962"},
                ],
            },
        },
        "images": [
            {"position": 1, "image_url": "https://example.test/1.jpg"},
            {"position": 2, "image_url": "https://example.test/2.jpg"},
        ],
    }


def test_shopee_global_button_does_not_call_legacy_tiktok_alignment_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global-product button must not be coupled to a TikTok record."""

    facts = {
        "seller_sku": "0959", "region": "VN", "title": "Approved title",
        "description": "Approved English description", "global_original_price_cny": 10.0,
    }
    legacy_called = []
    monkeypatch.setattr(product_server, "_oneclick_approved_context", lambda _data: ({"payload": {}}, None))
    monkeypatch.setattr(product_server, "_approved_shopee_global_publish_facts", lambda _payload: facts)
    monkeypatch.setattr("modules.shopee.publish.publish_match_key", lambda *args, **kwargs: legacy_called.append((args, kwargs)) or {"ok": True})
    monkeypatch.setattr("modules.shopee.approved_global_publisher.publish_approved_global", lambda received: {"ok": received is facts})

    status, body = product_server._start_shopee_global_release({"confirm_publish": True, "offer_id": "3846511157"})

    assert status == 200
    assert body["success"] is True
    assert legacy_called == []


def test_approved_global_detail_preserves_approved_product_facts() -> None:
    detail = approved_global_publisher.approved_global_detail({
        "seller_sku": "0959", "title": "Approved title", "description": "Approved description",
        "images": ["https://example.test/1.jpg"], "package_cm": [30, 5, 5],
        "weight_kg": 0.12, "quantity": 300,
    })
    assert detail["skus"][0]["seller_sku"] == "0959"
    assert detail["skus"][0]["inventory"] == [{"quantity": 300}]
    assert detail["package_dimensions"] == {"length": 30.0, "width": 5.0, "height": 5.0}


def test_approved_global_facts_preserve_all_exact_approved_variants() -> None:
    """Regression: the Shopee boundary must not collapse 3 approved SKUs to 1."""

    facts = product_server._approved_shopee_global_publish_facts(
        _three_variant_approved_payload()
    )

    assert facts["variants"] == [
        {"variant_key": "variant-a", "option_label": "A：60cmx3m", "model_sku": "0960"},
        {"variant_key": "variant-b", "option_label": "B：60cmx3m", "model_sku": "0961"},
        {"variant_key": "variant-c", "option_label": "C：60cmx3m", "model_sku": "0962"},
    ]


def test_approved_global_facts_preserve_per_sku_price_and_parcel() -> None:
    facts = product_server._approved_shopee_global_publish_facts(
        _three_variant_approved_payload()
    )

    assert facts["sku_commercial_facts"] == {
        "variant-a": {"cost_cny": 15.0, "weight_kg": 0.15, "package_cm": [30.0, 7.0, 7.0]},
        "variant-b": {"cost_cny": 18.0, "weight_kg": 0.265, "package_cm": [60.0, 7.0, 7.0]},
        "variant-c": {"cost_cny": 22.0, "weight_kg": 0.4, "package_cm": [90.0, 7.0, 7.0]},
    }
    assert facts["sku_prices"] == {
        "variant-a": 89.5,
        "variant-b": 119.65,
        "variant-c": 159.25,
    }


def test_approved_global_detail_preserves_three_models_and_option_labels() -> None:
    """Regression: API detail must carry the exact approved model/label matrix."""

    facts = product_server._approved_shopee_global_publish_facts(
        _three_variant_approved_payload()
    )
    detail = approved_global_publisher.approved_global_detail(facts)

    assert [row["seller_sku"] for row in detail["skus"]] == [
        "0960", "0961", "0962",
    ]
    assert [row["variation_option"] for row in detail["skus"]] == [
        "A：60cmx3m", "B：60cmx3m", "C：60cmx3m",
    ]
    assert [row["original_price"] for row in detail["skus"]] == [
        89.5, 119.65, 159.25,
    ]
    assert [row["sku_weight"]["value"] for row in detail["skus"]] == [
        0.15, 0.265, 0.4,
    ]
    assert [row["sku_dimensions"]["length"] for row in detail["skus"]] == [
        30.0, 60.0, 90.0,
    ]


def test_shopee_init_tier_payload_contains_all_three_approved_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = product_server._approved_shopee_global_publish_facts(
        _three_variant_approved_payload()
    )
    detail = approved_global_publisher.approved_global_detail(facts)
    reads = iter([
        {"response": {"global_model": []}},
        {"response": {"global_model": [
            {"global_model_sku": "0960", "tier_index": [0]},
            {"global_model_sku": "0961", "tier_index": [1]},
            {"global_model_sku": "0962", "tier_index": [2]},
        ]}},
    ])
    writes = []
    monkeypatch.setattr(
        shopee_publish,
        "merchant_get",
        lambda *_args, **_kwargs: next(reads),
    )
    monkeypatch.setattr(
        shopee_publish,
        "merchant_post",
        lambda path, _mid, _token, body: writes.append((path, body)) or {"error": ""},
    )

    result = shopee_publish.ensure_global_models(
        global_item_id=456,
        merchant_id=9,
        merchant_token="token",
        detail=detail,
        original_price=119.65,
        stock=1,
    )

    assert result["model_skus"] == ["0960", "0961", "0962"]
    assert writes[0][1]["tier_variation"] == [{
        "name": "Variation",
        "option_list": [
            {"option": "A：60cmx3m"},
            {"option": "B：60cmx3m"},
            {"option": "C：60cmx3m"},
        ],
    }]
    assert [row["global_model_sku"] for row in writes[0][1]["global_model"]] == [
        "0960", "0961", "0962",
    ]
    assert [row["original_price"] for row in writes[0][1]["global_model"]] == [
        89.5, 119.65, 159.25,
    ]


def test_new_global_product_is_mapped_for_independent_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = {
        "seller_sku": "0959", "region": "PH", "title": "Approved title",
        "description": "Approved description", "images": ["https://example.test/1.jpg"],
        "package_cm": [30, 5, 5], "weight_kg": 0.12,
        "global_original_price_cny": 10.0,
    }
    monkeypatch.setattr("modules.shopee.auth.ensure_shop_token", lambda _shop_id: "token")
    monkeypatch.setattr("modules.shopee.shops.sync_shop_ids", lambda: {"PH": 1})
    monkeypatch.setattr("modules.shopee.global_sku_map.global_item_id_for_match_key", lambda _sku: None)
    monkeypatch.setattr("modules.shopee.publish._reference_item", lambda *_args: None)
    monkeypatch.setattr("modules.shopee.publish._upload_images", lambda _images: ["image-1"])
    monkeypatch.setattr("modules.shopee.publish._create_global_item", lambda *_args, **_kwargs: {"ok": True, "global_item_id": 123})
    saved = []
    monkeypatch.setattr("modules.shopee.global_sku_map.upsert_global_entry", lambda *args, **kwargs: saved.append((args, kwargs)))

    receipt = approved_global_publisher.publish_approved_global(facts)

    assert receipt["global_item_id"] == 123
    assert saved == [(('123',), {"match_key": "0959", "global_model_sku": "0959", "title": "Approved title", "published_regions": []})]


def test_existing_global_is_a_success_without_copy_readback_or_tiktok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = {
        "seller_sku": "0959", "region": "PH", "title": "Approved title",
        "description": "Approved description", "images": ["https://example.test/1.jpg"],
        "package_cm": [30, 5, 5], "weight_kg": 0.12,
        "global_original_price_cny": 10.0,
    }
    monkeypatch.setattr("modules.shopee.auth.ensure_shop_token", lambda _shop_id: "token")
    monkeypatch.setattr("modules.shopee.shops.sync_shop_ids", lambda: {"PH": 1})
    monkeypatch.setattr("modules.shopee.global_sku_map.global_item_id_for_match_key", lambda _sku: "123")
    monkeypatch.setattr("modules.shopee.publish._reference_item", lambda *_args: (_ for _ in ()).throw(AssertionError("reference read is unnecessary")))

    receipt = approved_global_publisher.publish_approved_global(facts)

    assert receipt == {"ok": True, "flow": "already_created", "global_item_id": 123, "model_sku": "0959"}


def test_existing_one_model_global_is_replaced_by_exact_three_model_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale one-model map must not hide a malformed approved product."""

    facts = product_server._approved_shopee_global_publish_facts(
        _three_variant_approved_payload()
    )
    monkeypatch.setattr("modules.shopee.auth.ensure_shop_token", lambda _shop_id: "token")
    monkeypatch.setattr("modules.shopee.shops.sync_shop_ids", lambda: {"PH": 1})
    monkeypatch.setattr(
        "modules.shopee.global_sku_map.global_item_id_for_match_key",
        lambda _sku: "123",
    )
    monkeypatch.setattr("modules.shopee.publish._shop_meta", lambda *_args: {"merchant_id": 9})
    monkeypatch.setattr("modules.shopee.publish._merchant_token", lambda *_args: "merchant-token")
    monkeypatch.setattr(
        "modules.shopee.publish.ensure_global_models",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("only one model")),
    )
    monkeypatch.setattr("modules.shopee.publish._reference_item", lambda *_args: None)
    monkeypatch.setattr("modules.shopee.publish._upload_images", lambda _images: ["image-1"])
    monkeypatch.setattr(
        "modules.shopee.publish._create_global_item",
        lambda *_args, **_kwargs: {"ok": True, "global_item_id": 456},
    )
    saved = []
    monkeypatch.setattr(
        "modules.shopee.global_sku_map.replace_inexact_global_entry",
        lambda *args, **kwargs: saved.append((args, kwargs)),
    )

    receipt = approved_global_publisher.publish_approved_global(facts)

    assert receipt["global_item_id"] == 456
    assert receipt["replaced_inexact_global_item_id"] == 123
    assert saved[0][0] == ("123", "456")
