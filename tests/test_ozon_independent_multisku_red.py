from modules.products import server as product_server

def test_approved_ozon_boundary_expands_every_selected_sku():
    payload = {
        "seller_sku": "0963",
        "listing_copy": {"candidates": [{
            "channel": "ozon", "site": "RU", "policy_check": "passed",
            "title": "Approved Ozon title",
        }]},
        "images": [{"position": 1, "image_url": "https://example.test/1.jpg"}],
        "product_facts": {
            "package_cm": [20, 20, 3], "weight_kg": 0.2,
            "category": {"id": "", "name": "Table runner"},
            "selected_sku_keys": ["a", "b", "c"],
            "selected_skus": [
                {"key": "a", "label": "35*140", "model_sku": "0963"},
                {"key": "b", "label": "35*200", "model_sku": "0964"},
                {"key": "c", "label": "35*300", "model_sku": "0965"},
            ],
        },
        "sku_lineage": {"assignment": {"model_skus": [
            {"variant_key": "a", "model_sku": "0963"},
            {"variant_key": "b", "model_sku": "0964"},
            {"variant_key": "c", "model_sku": "0965"},
        ]}},
        "pricing": {"selected_targets": {"ozon:RU": {"derived_preview": {
            "price_cny": 100, "old_price_cny": 130,
        }}}},
    }

    rows = product_server._approved_ozon_variant_publish_facts(payload)

    assert [row["seller_sku"] for row in rows] == ["0963", "0964", "0965"]
