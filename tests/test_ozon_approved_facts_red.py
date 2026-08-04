"""Regression coverage for extracting the complete Ozon snapshot."""

from __future__ import annotations

from modules.products import server as product_server


def test_approved_ozon_facts_bind_weight_and_source_category():
    payload = {
        "seller_sku": "0959",
        "product_facts": {
            "package_cm": [38.0, 85.0, 2.0],
            "weight_kg": 0.3,
            "category": {"id": "123", "name": "Wall stickers"},
        },
        "listing_copy": {
            "candidates": [
                {
                    "channel": "ozon",
                    "site": "RU",
                    "policy_check": "passed",
                    "title": "Approved Russian title",
                }
            ]
        },
        "images": [
            {"position": 1, "image_url": "https://image.example/1.jpg"}
        ],
        "pricing": {
            "selected_targets": {
                "ozon:RU": {
                    "derived_preview": {"price_cny": 900, "old_price_cny": 1100}
                }
            }
        },
    }

    facts = product_server._approved_ozon_publish_facts(payload)

    assert facts["weight_kg"] == 0.3
    assert facts["source_category"] == {"id": "123", "name": "Wall stickers"}


def test_approved_ozon_facts_accept_category_name_without_legacy_tiktok_id():
    payload = {
        "seller_sku": "0959",
        "product_facts": {
            "package_cm": [38.0, 85.0, 2.0],
            "weight_kg": 0.3,
            "category": {"id": "", "name": "Wall stickers"},
        },
        "listing_copy": {
            "candidates": [
                {
                    "channel": "ozon",
                    "site": "RU",
                    "policy_check": "passed",
                    "title": "Approved Russian title",
                }
            ]
        },
        "images": [
            {"position": 1, "image_url": "https://image.example/1.jpg"}
        ],
        "pricing": {
            "selected_targets": {
                "ozon:RU": {
                    "derived_preview": {"price_cny": 900, "old_price_cny": 1100}
                }
            }
        },
    }

    facts = product_server._approved_ozon_publish_facts(payload)

    assert facts["source_category"] == {"id": "", "name": "Wall stickers"}
