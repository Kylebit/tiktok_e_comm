from modules.catalog.sku_edit import _build_local_product_edit_body


def _detail() -> dict:
    return {
        "title": "Approved wall sticker",
        "description": "<p>Approved</p>",
        "category_chains": [{"id": "600338", "category_version": "v2"}],
        "main_images": [{"uri": "img-1"}],
        "skus": [
            {
                "id": "sku-1",
                "seller_sku": "old",
                "price": {
                    "amount": "593",
                    "sale_price": "524",
                    "currency": "PHP",
                },
                "inventory": [{"warehouse_id": "wh-1", "quantity": 200}],
            }
        ],
    }


def test_content_edit_omits_price_and_inventory_by_default() -> None:
    body = _build_local_product_edit_body(_detail(), "sku-1", "0953")

    assert body["skus"] == [{"id": "sku-1", "seller_sku": "0953"}]


def test_commerce_fields_require_an_explicit_opt_in() -> None:
    body = _build_local_product_edit_body(
        _detail(),
        "sku-1",
        "0953",
        include_commerce_fields=True,
    )

    assert body["skus"][0]["price"] == {
        "amount": "524",
        "currency": "PHP",
    }
    assert body["skus"][0]["inventory"] == [
        {"warehouse_id": "wh-1", "quantity": 200}
    ]
