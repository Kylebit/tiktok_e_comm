from modules.shopee import global_sku_map


def test_new_global_mappings_do_not_invent_regional_publications(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "shopee-map.json"
    monkeypatch.setattr(global_sku_map, "map_path", lambda: path)

    global_sku_map.upsert_global_entry(
        "90000000001",
        match_key="0967",
        global_model_sku="0967",
        published_regions=[],
    )
    global_sku_map.upsert_global_group_entry(
        "90000000002",
        match_keys=["0968", "0969"],
        models=[
            {"model_name": "small", "global_model_sku": "0968"},
            {"model_name": "large", "global_model_sku": "0969"},
        ],
    )

    data = global_sku_map.load_map()
    assert data["90000000001"]["published_regions"] == []
    assert data["90000000002"]["published_regions"] == []
    assert data["90000000001"]["shop_items"] == {}
    assert data["90000000002"]["shop_items"] == {}


def test_replacement_preserves_retired_regional_item_history(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "shopee-map.json"
    monkeypatch.setattr(global_sku_map, "map_path", lambda: path)
    global_sku_map.save_map(
        {
            "51465029034": {
                "match_key": "0956",
                "published_regions": ["PH"],
                "shop_items": {
                    "PH": {
                        "shop_id": 101,
                        "item_id": "46315060605",
                        "model_id": "old-model",
                    }
                },
            }
        }
    )

    global_sku_map.record_shop_item(
        "51465029034",
        "PH",
        shop_id=101,
        item_id="new-item-0956",
        model_id="new-model",
    )
    global_sku_map.record_shop_item(
        "51465029034",
        "PH",
        shop_id=101,
        item_id="new-item-0956",
        model_id="new-model",
    )

    entry = global_sku_map.load_map()["51465029034"]
    assert entry["shop_items"]["PH"]["item_id"] == "new-item-0956"
    assert entry["retired_shop_items"]["PH"] == [
        {
            "shop_id": 101,
            "item_id": "46315060605",
            "model_id": "old-model",
            "reason": "replaced_after_official_seller_delete",
            "replacement_item_id": "new-item-0956",
        }
    ]


def test_deleted_global_replacement_retires_old_lookup_identity(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "shopee-map.json"
    monkeypatch.setattr(global_sku_map, "map_path", lambda: path)
    global_sku_map.save_map(
        {
            "51465029034": {
                "match_key": "0956",
                "match_keys": ["0956"],
                "title": "retired title",
                "models": [{"global_model_sku": "0956"}],
                "published_regions": ["PH"],
                "shop_items": {
                    "PH": {
                        "shop_id": 101,
                        "item_id": "retired-item",
                        "model_id": "retired-model",
                    }
                },
            }
        }
    )

    global_sku_map.replace_deleted_global_entry(
        "51465029034",
        "90000000001",
        match_key="0956",
        global_model_sku="0956",
        title="approved replacement title",
    )

    data = global_sku_map.load_map()
    assert global_sku_map.global_item_id_for_match_key("0956") == "90000000001"
    assert data["51465029034"]["match_key"] == ""
    assert data["51465029034"]["match_keys"] == []
    assert data["51465029034"]["retired_match_key"] == "0956"
    assert (
        data["51465029034"]["replacement_global_item_id"]
        == "90000000001"
    )
    assert data["90000000001"]["replaces_deleted_global_item_id"] == (
        "51465029034"
    )
    assert data["90000000001"]["published_regions"] == []


def test_deleted_global_replacement_rejects_unrelated_match_key(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "shopee-map.json"
    monkeypatch.setattr(global_sku_map, "map_path", lambda: path)
    global_sku_map.save_map(
        {
            "51465029034": {
                "match_key": "0956",
                "models": [{"global_model_sku": "0956"}],
            }
        }
    )

    try:
        global_sku_map.replace_deleted_global_entry(
            "51465029034",
            "90000000001",
            match_key="0957",
            global_model_sku="0957",
        )
    except ValueError as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("unrelated global replacement must fail closed")

    assert global_sku_map.global_item_id_for_match_key("0956") == (
        "51465029034"
    )
