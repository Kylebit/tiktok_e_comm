import pytest

from domains.channel_operations.release_executor import AdapterExecutionRequest
from modules.products import release_adapters


def _request(site="LH_PH"):
    return AdapterExecutionRequest(
        plan_id="omnichannel:test",
        confirmation_token="PUBLISH-TEST",
        approval_scope_digest="scope",
        product_id="3828811808",
        seller_sku="0953",
        product_package_id="product:3828811808:0953",
        content_package_id="content:3828811808",
        channel="tiktok",
        site=site,
        target_label=f"tiktok:{site}",
        idempotency_key=f"publish:tiktok:{site}:test",
    )


def _context():
    return {
        "payload": {
            "targets": ["miaoshou:COMMON", "tiktok:LH_PH"],
            "product_id": "3828811808",
            "seller_sku": "0953",
            "product_package_id": "product:3828811808:0953",
            "content_package_id": "content:3828811808",
            "product_facts": {
                "title": "Cute Black Line-Art Dog PVC Wall Decal 34 x 58 cm",
                "weight_kg": 0.02,
                "package_cm": [58, 34, 0.02],
                "selected_sku_keys": ["34x58"],
                "category": {"name": "Wall Stickers"},
            },
            "images": [
                {"image_url": "https://assets.example/1.jpg"},
                {"image_url": "https://assets.example/2.jpg"},
            ],
            "listing_copy": {
                "candidates": [
                    {
                        "channel": "tiktok",
                        "site": "PH",
                        "title": "Cute Black Line-Art Dog PVC Wall Decal 34 x 58 cm",
                        "policy_check": "passed",
                    }
                ]
            },
            "pricing": {
                "selected_targets": {
                    "tiktok:LH_PH": {
                        "store_prices": [
                            {
                                "target_key": "lh_ph",
                                "list_price": 257,
                                "currency": "PHP",
                            }
                        ]
                    }
                }
            },
        },
        "facts": {
            "title": "Cute Black Line-Art Dog PVC Wall Decal 34 x 58 cm",
            "weight_kg": 0.02,
            "package_cm": [58, 34, 0.02],
        },
        "images": [
            "https://assets.example/1.jpg",
            "https://assets.example/2.jpg",
        ],
    }


def test_registry_exposes_only_contract_complete_adapters():
    registry = release_adapters.production_adapter_registry()

    assert set(registry) == {
        "new_product_workbench_miaoshou_commit",
        "miaoshou_tiktok_publish",
        "shopee_cnsc_publish",
        "ozon_product_import",
    }
    assert all(registration.executable for registration in registry.values())


def test_miaoshou_publish_reads_current_region_site_result(monkeypatch):
    from modules.miaoshou import client as miaoshou_client
    from modules.sourcing import new_product_workbench as workbench

    monkeypatch.setattr(
        workbench,
        "claim_miaoshou_to_tiktok",
        lambda *_args, **_kwargs: pytest.fail("claim/create must not run"),
    )
    monkeypatch.setattr(
        release_adapters,
        "_resolve_existing_miaoshou_tiktok_detail",
        lambda *_args, **_kwargs: {
            "target_key": "lh_ph",
            "detail_id": 3224810860,
            "shop_id": 7676267,
            "shop": {"id": "lh_ph", "shop_id": 7676267, "region": "PH"},
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "_prepare_existing_miaoshou_target_from_plan",
        lambda *_args, **_kwargs: {
            "ready": True,
            "site_collect_shop_ids": ["7676267"],
            "checks": {"title": True, "price": True, "logistics": True},
        },
    )
    submitted = []
    monkeypatch.setattr(
        miaoshou_client,
        "post_open",
        lambda path, body: submitted.append((path, body))
        or {"result": "success", "code": "success"},
    )

    external_id, evidence = release_adapters._miaoshou_publish_target(
        _context()["payload"],
        site="LH_PH",
    )

    assert external_id == "3224810860:7676267"
    assert evidence["accepted"] is True
    assert evidence["pre_submit_audit"]["checks"] == {
        "immutable_identity": True,
        "approved_title": True,
        "approved_price": True,
        "approved_images": True,
        "approved_logistics": True,
        "approved_variants": True,
        "approved_category": True,
        "approved_video": True,
        "exact_shop_claim": True,
        "miaoshou_draft_ready": True,
        "miaoshou_field_checks": True,
    }
    assert submitted[0][1] == {
        "detailIds": [3224810860],
        "shopIds": [7676267],
    }


@pytest.mark.parametrize(
    ("remote_result", "expected_outcome"),
    [
        (
            {"result": "error", "code": "REMOTE_REJECTED", "message": "no"},
            "draft_saved_publish_rejected",
        ),
        (RuntimeError("publish transport unavailable"), "unknown_after_dispatch"),
    ],
)
def test_miaoshou_publish_failure_preserves_verified_draft_evidence(
    monkeypatch,
    remote_result,
    expected_outcome,
):
    from modules.miaoshou import client as miaoshou_client

    monkeypatch.setattr(
        release_adapters,
        "_resolve_existing_miaoshou_tiktok_detail",
        lambda *_args, **_kwargs: {
            "target_key": "lh_ph",
            "detail_id": 3224810860,
            "shop_id": 7676267,
            "shop": {"shop_id": 7676267},
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "_prepare_existing_miaoshou_target_from_plan",
        lambda *_args, **_kwargs: {
            "ready": True,
            "site_collect_shop_ids": ["7676267"],
            "checks": {"title": True, "price": True, "logistics": True},
        },
    )

    def publish(_path, _body):
        if isinstance(remote_result, Exception):
            raise remote_result
        return remote_result

    monkeypatch.setattr(miaoshou_client, "post_open", publish)

    with pytest.raises(
        release_adapters.MiaoshouDraftVerificationError,
    ) as raised:
        release_adapters._miaoshou_publish_target(
            _context()["payload"],
            site="LH_PH",
        )

    evidence = raised.value.external_write_evidence
    assert raised.value.external_reference == "3224810860:7676267"
    assert evidence["save_accepted"] is True
    assert evidence["verified_draft"] is True
    assert evidence["pre_submit_audit"]["checks"]["approved_title"] is True
    assert evidence["write_outcome"] == expected_outcome
    assert evidence["external_writes_performed"] == [
        "miaoshou:tiktok_detail:update"
    ]


def test_existing_detail_resolver_ignores_asymmetric_claim_shops(monkeypatch):
    from modules.sourcing import new_product_workbench as workbench

    claim_calls = []
    monkeypatch.setattr(
        workbench,
        "claim_miaoshou_to_tiktok",
        lambda *_args, **_kwargs: claim_calls.append((_args, _kwargs)),
    )
    monkeypatch.setattr(
        workbench,
        "load_miaoshou_tiktok_claim",
        lambda _offer_id: {
            "source_item_id": "1011111051454",
            "shops": {
                "mx": {
                    "shop_id": "16265910",
                    "detail_group": "lively:MX",
                }
            },
            "detail_group_detail_ids": {
                "lively:GB": 3227304421,
                "lively:MY": 3227305063,
                "lively:PH": 3227305525,
                "lively:TH": 3227306445,
                "lively:VN": 3227307552,
                "lively:MX": 3227308139,
            },
        },
    )
    requests = []

    def read(path, body):
        requests.append((path, body))
        if path == release_adapters.MIAOSHOU_TIKTOK_DETAIL_LIST_PATH:
            return {
                "result": "success",
                "data": {
                    "detailList": [
                        {
                            "commonCollectBoxDetailId": "3828811808",
                            "collectBoxDetailId": detail_id,
                            "itemNum": "0953",
                            "collectBoxDetailShopList": [
                                {"shopId": shop_id}
                            ],
                        }
                        for detail_id, shop_id in (
                            (3227304421, "10204699"),
                            (3227305063, "13295169"),
                            (3227305525, "7676267"),
                            (3227306445, "13295228"),
                            (3227307552, "13295291"),
                            (3227308139, "16265910"),
                        )
                    ]
                },
            }
        return {
            "result": "success",
            "data": {
                "shopCollectItemInfo": {
                    "detailId": 3227305525,
                    "shopId": "7676267",
                    "title": "Approved PH title",
                    "skuMap": {"34x58": {"itemNum": "0953"}},
                },
                "claimToShopIds": ["7676267"],
                "ossMd5": "readonly-md5",
            },
        }

    resolved = release_adapters._resolve_existing_miaoshou_tiktok_detail(
        _context()["payload"],
        site="LH_PH",
        post=read,
    )

    assert resolved["detail_id"] == 3227305525
    assert resolved["shop_id"] == 7676267
    assert resolved["detail_group"] == "lively:PH"
    assert resolved["common_identity"] == "3828811808"
    assert resolved["common_identity_provenance"] == "search_row"
    assert resolved["external_writes_performed"] == []
    assert requests == [
        (
            release_adapters.MIAOSHOU_SHOP_DETAIL_PATH,
            {"detailId": 3227305525, "shopId": "7676267"},
        ),
        (
            release_adapters.MIAOSHOU_TIKTOK_DETAIL_LIST_PATH,
            {
                "pageNo": 1,
                "pageSize": 100,
                "filter": {"sourceItemIdKeyword": "1011111051454"},
            },
        ),
    ]
    assert claim_calls == []


@pytest.mark.parametrize(
    ("detail_ids", "bound_shop_ids", "message"),
    [
        (
            {"lively:MX": 3227308139},
            ["7676267"],
            "detail ID is missing for lively:PH",
        ),
        (
            {
                "lively:PH": 3227305525,
                "lively:MX": 3227305525,
            },
            ["7676267"],
            "detail IDs are not unique",
        ),
        (
            {"lively:PH": 3227305525},
            ["16265910"],
            "is not bound to fixed shop 7676267",
        ),
    ],
)
def test_existing_detail_resolver_blocks_missing_duplicate_or_wrong_shop(
    monkeypatch,
    detail_ids,
    bound_shop_ids,
    message,
):
    from modules.sourcing import new_product_workbench as workbench

    monkeypatch.setattr(
        workbench,
        "load_miaoshou_tiktok_claim",
        lambda _offer_id: {
            "source_item_id": "1011111051454",
            "shops": {"mx": {"shop_id": "16265910"}},
            "detail_group_detail_ids": detail_ids,
        },
    )
    monkeypatch.setattr(
        workbench,
        "claim_miaoshou_to_tiktok",
        lambda *_args, **_kwargs: pytest.fail("claim/create must not run"),
    )

    with pytest.raises(RuntimeError, match=message):
        release_adapters._resolve_existing_miaoshou_tiktok_detail(
            _context()["payload"],
            site="LH_PH",
            post=lambda _path, _body: {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": {
                        "detailId": 3227305525,
                        "shopId": "7676267",
                        "title": "existing",
                        "commonCollectBoxDetailId": "3828811808",
                        "skuMap": {"34x58": {"itemNum": "0953"}},
                    },
                    "claimToShopIds": bound_shop_ids,
                },
            },
        )


@pytest.mark.parametrize(
    ("info_updates", "message"),
    [
        ({"detailId": 3227300000}, "does not match mapped detail"),
        ({"shopId": "16265910"}, "does not match fixed shop"),
        (
            {"skuMap": {"34x58": {"itemNum": "9999"}}},
            "variant SKU scheme",
        ),
    ],
)
def test_existing_detail_resolver_blocks_wrong_detail_shop_or_variant_sku(
    monkeypatch,
    info_updates,
    message,
):
    from modules.sourcing import new_product_workbench as workbench

    monkeypatch.setattr(
        workbench,
        "load_miaoshou_tiktok_claim",
        lambda _offer_id: {
            "source_item_id": "1011111051454",
            "detail_group_detail_ids": {
                "lively:GB": 3227304421,
                "lively:MY": 3227305063,
                "lively:PH": 3227305525,
                "lively:TH": 3227306445,
                "lively:VN": 3227307552,
                "lively:MX": 3227308139,
            },
        },
    )
    info = {
        "detailId": 3227305525,
        "shopId": "7676267",
        "commonCollectBoxDetailId": "3828811808",
        "skuMap": {"34x58": {"itemNum": "0953"}},
    }
    info.update(info_updates)

    with pytest.raises(RuntimeError, match=message):
        release_adapters._resolve_existing_miaoshou_tiktok_detail(
            _context()["payload"],
            site="LH_PH",
            post=lambda _path, _body: {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": info,
                    "claimToShopIds": ["7676267"],
                },
            },
        )


def test_existing_detail_resolver_blocks_explicit_wrong_common_identity(monkeypatch):
    from modules.sourcing import new_product_workbench as workbench

    monkeypatch.setattr(
        workbench,
        "load_miaoshou_tiktok_claim",
        lambda _offer_id: {
            "source_item_id": "1011111051454",
            "detail_group_detail_ids": {
                "lively:GB": 3227304421,
                "lively:MY": 3227305063,
                "lively:PH": 3227305525,
                "lively:TH": 3227306445,
                "lively:VN": 3227307552,
                "lively:MX": 3227308139,
            },
        },
    )

    with pytest.raises(RuntimeError, match="belongs to common product 999"):
        release_adapters._resolve_existing_miaoshou_tiktok_detail(
            _context()["payload"],
            site="LH_PH",
            post=lambda _path, _body: {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": {
                        "detailId": 3227305525,
                        "shopId": "7676267",
                        "commonCollectBoxDetailId": "999",
                        "skuMap": {"34x58": {"itemNum": "0953"}},
                    },
                    "claimToShopIds": ["7676267"],
                },
            },
        )


@pytest.mark.parametrize(
    ("row_common", "row_detail_id", "message"),
    [
        ("", 3227305525, "identity is not unique and exact"),
        ("999", 3227305525, "identity is not unique and exact"),
        ("3828811808", 3227300000, "does not exactly match the persisted"),
    ],
)
def test_existing_detail_resolver_blocks_missing_or_wrong_search_identity(
    monkeypatch, row_common, row_detail_id, message
):
    from modules.sourcing import new_product_workbench as workbench

    expected_rows = [
        (3227304421, "10204699"),
        (3227305063, "13295169"),
        (3227305525, "7676267"),
        (3227306445, "13295228"),
        (3227307552, "13295291"),
        (3227308139, "16265910"),
    ]
    monkeypatch.setattr(
        workbench,
        "load_miaoshou_tiktok_claim",
        lambda _offer_id: {
            "source_item_id": "1011111051454",
            "detail_group_detail_ids": {
                "lively:GB": 3227304421,
                "lively:MY": 3227305063,
                "lively:PH": 3227305525,
                "lively:TH": 3227306445,
                "lively:VN": 3227307552,
                "lively:MX": 3227308139,
            },
        },
    )

    def read(path, _body):
        if path == release_adapters.MIAOSHOU_TIKTOK_DETAIL_LIST_PATH:
            rows = []
            for detail_id, shop_id in expected_rows:
                rows.append(
                    {
                        "collectBoxDetailId": (
                            row_detail_id
                            if detail_id == 3227305525
                            else detail_id
                        ),
                        "commonCollectBoxDetailId": (
                            row_common
                            if detail_id == 3227305525
                            else "3828811808"
                        ),
                        "itemNum": "0953",
                        "collectBoxDetailShopList": [{"shopId": shop_id}],
                    }
                )
            return {"result": "success", "data": {"detailList": rows}}
        return {
            "result": "success",
            "data": {
                "shopCollectItemInfo": {
                    "detailId": 3227305525,
                    "shopId": "7676267",
                    "skuMap": {"34x58": {"itemNum": "0953"}},
                },
                "claimToShopIds": ["7676267"],
            },
        }

    with pytest.raises(RuntimeError, match=message):
        release_adapters._resolve_existing_miaoshou_tiktok_detail(
            _context()["payload"], site="LH_PH", post=read
        )


def test_api_less_pre_submit_audit_blocks_incomplete_approved_scope():
    payload = _context()["payload"]
    payload["product_facts"]["selected_sku_keys"] = []
    payload["listing_copy"]["candidates"][0]["site"] = "GB"
    payload["pricing"]["selected_targets"] = {
        "tiktok:GB": {
            "store_prices": [
                {
                    "target_key": "gb",
                    "list_price": 13,
                    "currency": "GBP",
                }
            ]
        }
    }

    with pytest.raises(
        RuntimeError,
        match="approved_variants",
    ):
        release_adapters._miaoshou_submission_audit(
            payload,
            site="GB",
            target_key="gb",
            detail_id=1,
            shop_id=2,
            prepared_site={
                "ready": True,
                "site_collect_shop_ids": ["2"],
                "checks": {"title": True, "price": True},
            },
        )


def test_common_readback_uses_semantic_master_not_ph_candidate():
    payload = _context()["payload"]
    payload["product_facts"]["title"] = "Approved Semantic Master Title"
    payload["listing_copy"]["candidates"][0][
        "title"
    ] = "Different TikTok PH Candidate"
    calls = []

    result = release_adapters.readback_miaoshou_common(
        payload,
        post=lambda path, body: calls.append((path, body))
        or {
            "result": "success",
            "data": {
                "editCommonCollectBoxDetail": {
                    "title": "Approved Semantic Master Title",
                    "itemNum": "0953",
                    "weight": 0.02,
                    "packageLength": 58,
                    "packageWidth": 34,
                    "packageHeight": 0.02,
                    "imgUrls": [
                        "https://assets.example/1.jpg",
                        "https://assets.example/2.jpg",
                    ],
                    "notes": (
                        '<p><img src="https://assets.example/1.jpg"></p>'
                        '<p><img src="https://assets.example/2.jpg"></p>'
                    ),
                    "mainImgVideoUrl": "",
                    "skuMap": {
                        "34x58": {
                            "itemNum": "0953",
                        }
                    },
                }
            },
        },
    )

    assert result["verified"] is True
    assert result["field_diffs"] == {}
    assert calls == [
        (
            release_adapters.MIAOSHOU_COMMON_DETAIL_PATH,
            {"commonCollectBoxDetailId": 3828811808},
        )
    ]


def test_common_readback_reports_mismatch_without_edit_call():
    payload = _context()["payload"]
    calls = []

    result = release_adapters.readback_miaoshou_common(
        payload,
        post=lambda path, body: calls.append((path, body))
        or {
            "result": "success",
            "data": {
                "editCommonCollectBoxDetail": {
                    "title": payload["product_facts"]["title"],
                    "itemNum": "0953",
                    "weight": 0.02,
                    "packageLength": 58,
                    "packageWidth": 34,
                    "packageHeight": 0.02,
                    "imgUrls": ["https://assets.example/wrong.jpg"],
                    "notes": "<img src='wrong'>",
                    "mainImgVideoUrl": "",
                    "skuMap": {"34x58": {"itemNum": "0953"}},
                }
            },
        },
    )

    assert result["verified"] is False
    assert "images" in result["field_diffs"]
    assert calls[0][0] == release_adapters.MIAOSHOU_COMMON_DETAIL_PATH
    assert all("edit_common_collect_box_detail" not in path for path, _body in calls)


def test_common_overwrite_review_is_redacted_and_fail_closed_by_identity():
    payload = _context()["payload"]
    readback = {
        "verified": False,
        "source": "miaoshou_common_readonly_detail",
        "readback_ambiguous": False,
        "existing_detail_digest": "detail-digest",
        "checks": {"title": False},
        "field_diffs": {
            "title": {
                "expected": "approved secret title",
                "actual": "existing secret title",
            }
        },
        "_comparison": {
            "title": {
                "expected": "approved secret title",
                "actual": "existing secret title",
            }
        },
    }

    review = release_adapters.miaoshou_common_overwrite_review(
        payload,
        readback,
        plan_id="plan-1",
        confirmation_token="token-1",
        payload_digest="payload-1",
        expected_revision=7,
    )

    assert review["overwrite_allowed"] is True
    assert review["identity_exact"] is True
    assert review["changed_fields"] == ["title"]
    assert "approved secret title" not in str(review)
    assert "existing secret title" not in str(review)

    readback["checks"] = {"seller_sku": False}
    readback["field_diffs"] = {
        "seller_sku": {"expected": "0953", "actual": "9999"}
    }
    blocked = release_adapters.miaoshou_common_overwrite_review(
        payload,
        readback,
        plan_id="plan-1",
        confirmation_token="token-1",
        payload_digest="payload-1",
        expected_revision=7,
    )
    assert blocked["overwrite_allowed"] is False
    assert blocked["blocking_fields"] == ["seller_sku"]
    assert "9999" not in str(blocked)


def test_common_readback_rejects_changed_description_with_same_images():
    payload = _context()["payload"]
    payload["listing_copy"][
        "shopee_description_en"
    ] = "Approved factual product description."
    images = [
        "https://assets.example/1.jpg",
        "https://assets.example/2.jpg",
    ]

    result = release_adapters.readback_miaoshou_common(
        payload,
        post=lambda _path, _body: {
            "result": "success",
            "data": {
                "editCommonCollectBoxDetail": {
                    "title": payload["product_facts"]["title"],
                    "itemNum": "0953",
                    "weight": 0.02,
                    "packageLength": 58,
                    "packageWidth": 34,
                    "packageHeight": 0.02,
                    "imgUrls": images,
                    "notes": (
                        "<p>Different unapproved description.</p>"
                        '<p><img src="https://assets.example/1.jpg"></p>'
                        '<p><img src="https://assets.example/2.jpg"></p>'
                    ),
                    "mainImgVideoUrl": "",
                    "skuMap": {"34x58": {"itemNum": "0953"}},
                }
            },
        },
    )

    assert result["verified"] is False
    assert result["checks"]["description_image_count"] is True
    assert result["checks"]["description_notes"] is False
    assert "description_notes" in result["field_diffs"]


def test_common_overwrite_uses_immutable_plan_then_exact_readback():
    payload = _context()["payload"]
    payload["product_facts"]["title"] = "Approved Semantic Master Title"
    payload["listing_copy"]["shopee_description_en"] = (
        "Approved factual product description."
    )
    payload["listing_copy"]["candidates"][0][
        "title"
    ] = "Different TikTok PH Candidate"
    calls = []
    saved = {}

    def post(path, body):
        calls.append((path, body))
        if path == release_adapters.MIAOSHOU_COMMON_EDIT_PATH:
            saved.update(body)
            return {"result": "success", "data": {}}
        if saved:
            return {
                "result": "success",
                "data": {
                    "editCommonCollectBoxDetail": dict(
                        saved["editCommonCollectBoxDetail"]
                    ),
                    "ossMd5": "after",
                },
            }
        return {
            "result": "success",
            "data": {
                "ossMd5": "before",
                "editCommonCollectBoxDetail": {
                    "title": "Old mutable title",
                    "itemNum": "0953",
                    "weight": 1,
                    "packageLength": 1,
                    "packageWidth": 1,
                    "packageHeight": 1,
                    "imgUrls": ["https://assets.example/old.jpg"],
                    "notes": "<p>old</p>",
                    "mainImgVideoUrl": "",
                    "skuMap": {
                        "34x58": {"itemNum": "old"},
                        "other": {"itemNum": "other"},
                    },
                    "colorMap": {},
                    "sizeMap": {},
                    "saleProp3Map": {},
                },
            },
        }

    result = release_adapters.write_miaoshou_common_from_plan(
        payload,
        post=post,
    )

    updated = saved["editCommonCollectBoxDetail"]
    assert result["verified"] is True
    assert updated["title"] == "Approved Semantic Master Title"
    assert updated["title"] != "Different TikTok PH Candidate"
    assert updated["imgUrls"] == [
        "https://assets.example/1.jpg",
        "https://assets.example/2.jpg",
    ]
    assert updated["notes"] == (
        "<p>Approved factual product description.</p>"
        '<p><img src="https://assets.example/1.jpg"></p>'
        '<p><img src="https://assets.example/2.jpg"></p>'
    )
    assert set(updated["skuMap"]) == {"34x58"}
    assert updated["skuMap"]["34x58"]["itemNum"] == "0953"
    assert [path for path, _body in calls] == [
        release_adapters.MIAOSHOU_COMMON_DETAIL_PATH,
        release_adapters.MIAOSHOU_COMMON_EDIT_PATH,
        release_adapters.MIAOSHOU_COMMON_DETAIL_PATH,
    ]


def test_common_overwrite_guard_rejects_changed_detail_before_edit():
    payload = _context()["payload"]
    edit_calls = []

    def post(path, _body):
        if path == release_adapters.MIAOSHOU_COMMON_EDIT_PATH:
            edit_calls.append(path)
        return {
            "result": "success",
            "data": {
                "ossMd5": "current",
                "editCommonCollectBoxDetail": {
                    "title": "changed after review",
                    "itemNum": "0953",
                    "skuMap": {"34x58": {"itemNum": "0953"}},
                },
            },
        }

    with pytest.raises(RuntimeError, match="changed after overwrite review"):
        release_adapters.write_miaoshou_common_from_plan(
            payload,
            post=post,
            overwrite_guard={
                "overwrite_allowed": True,
                "identity_exact": True,
                "readback_non_ambiguous": True,
                "existing_detail_digest": "stale-detail-digest",
            },
        )

    assert edit_calls == []


def test_common_save_success_then_verify_exception_keeps_write_evidence():
    payload = _context()["payload"]
    detail_reads = {"count": 0}

    def post(path, _body):
        if path == release_adapters.MIAOSHOU_COMMON_EDIT_PATH:
            return {"result": "success", "data": {}}
        detail_reads["count"] += 1
        if detail_reads["count"] > 1:
            raise RuntimeError("readback transport unavailable")
        return {
            "result": "success",
            "data": {
                "ossMd5": "before",
                "editCommonCollectBoxDetail": {
                    "title": "old",
                    "itemNum": "0953",
                    "skuMap": {"34x58": {"itemNum": "0953"}},
                },
            },
        }

    with pytest.raises(
        release_adapters.MiaoshouDraftVerificationError,
        match="write was accepted",
    ) as raised:
        release_adapters.write_miaoshou_common_from_plan(
            payload,
            post=post,
        )

    assert raised.value.external_reference == "3828811808"
    assert raised.value.external_write_evidence["save_accepted"] is True
    assert raised.value.external_write_evidence[
        "external_writes_performed"
    ] == ["miaoshou:COMMON:immutable_plan_write"]


def test_common_save_dispatch_exception_is_unknown_and_not_retryable():
    payload = _context()["payload"]

    def post(path, _body):
        if path == release_adapters.MIAOSHOU_COMMON_EDIT_PATH:
            raise RuntimeError("socket closed after dispatch")
        return {
            "result": "success",
            "data": {
                "ossMd5": "before",
                "editCommonCollectBoxDetail": {
                    "title": "old",
                    "itemNum": "0953",
                    "skuMap": {"34x58": {"itemNum": "0953"}},
                },
            },
        }

    with pytest.raises(
        release_adapters.MiaoshouDraftVerificationError,
        match="outcome is unknown",
    ) as raised:
        release_adapters.write_miaoshou_common_from_plan(
            payload,
            post=post,
        )

    evidence = raised.value.external_write_evidence
    assert raised.value.external_reference == "3828811808"
    assert evidence["write_outcome"] == "unknown_after_dispatch"
    assert evidence["save_accepted"] is False
    assert evidence["external_writes_performed"] == [
        "miaoshou:COMMON:immutable_plan_write"
    ]


def test_existing_detail_save_success_then_verify_exception_keeps_evidence(
    monkeypatch,
):
    from modules.sourcing import new_product_workbench as workbench

    def prepare(post, **_kwargs):
        assert post(
            "/open/v1/product/collect_box/tiktok/collect_box/"
            "save_site_collect_item_info",
            {"fixture": True},
        )["result"] == "success"
        raise RuntimeError("verification network failed")

    monkeypatch.setattr(workbench, "_prepare_site_mode_draft", prepare)

    with pytest.raises(
        release_adapters.MiaoshouDraftVerificationError,
        match="update was accepted",
    ) as raised:
        release_adapters._prepare_existing_miaoshou_target_from_plan(
            _context()["payload"],
            site="LH_PH",
            resolved={
                "detail_id": 3227305525,
                "shop_id": 7676267,
                "target_key": "lh_ph",
                "shop": {"shop_id": 7676267},
            },
            post=lambda path, _body: {
                "result": "success",
                "data": (
                    {"1": [{"warehouseId": "warehouse-ph"}]}
                    if path == release_adapters.MIAOSHOU_WAREHOUSE_PATH
                    else {}
                ),
            },
        )

    evidence = raised.value.external_write_evidence
    assert raised.value.external_reference == "3227305525:7676267"
    assert evidence["save_accepted"] is True
    assert evidence["verification_error"] == "verification network failed"
    assert evidence["external_writes_performed"] == [
        "miaoshou:tiktok_detail:update"
    ]


def test_existing_detail_save_dispatch_exception_keeps_unknown_evidence(
    monkeypatch,
):
    from modules.sourcing import new_product_workbench as workbench

    def prepare(post, **_kwargs):
        post(
            "/open/v1/product/collect_box/tiktok/collect_box/"
            "save_site_collect_item_info",
            {"fixture": True},
        )

    monkeypatch.setattr(workbench, "_prepare_site_mode_draft", prepare)

    def post(path, _body):
        if path == release_adapters.MIAOSHOU_WAREHOUSE_PATH:
            return {"result": "success", "data": {}}
        raise RuntimeError("socket closed after save dispatch")

    with pytest.raises(
        release_adapters.MiaoshouDraftVerificationError,
        match="not safely retryable",
    ) as raised:
        release_adapters._prepare_existing_miaoshou_target_from_plan(
            _context()["payload"],
            site="LH_PH",
            resolved={
                "detail_id": 3227305525,
                "shop_id": 7676267,
                "target_key": "lh_ph",
                "shop": {"shop_id": 7676267},
            },
            post=post,
        )

    evidence = raised.value.external_write_evidence
    assert evidence["save_accepted"] is False
    assert evidence["write_outcome"] == "unknown_after_dispatch"
    assert evidence["external_writes_performed"] == [
        "miaoshou:tiktok_detail:update"
    ]


def test_publish_accepted_then_official_readback_exception_keeps_submission(
    monkeypatch,
):
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(),
    )
    readbacks = {"count": 0}

    def readback(**_kwargs):
        readbacks["count"] += 1
        if readbacks["count"] == 1:
            return False, {
                "verified": False,
                "region": "PH",
                "reason": "not_found",
            }
        raise RuntimeError("official readback transport unavailable")

    monkeypatch.setattr(release_adapters, "_tiktok_readback", readback)
    monkeypatch.setattr(
        release_adapters,
        "_miaoshou_publish_target",
        lambda *_args, **_kwargs: (
            "3227305525:7676267",
            {
                "source": "miaoshou_open_api",
                "accepted": True,
                "detail_id": 3227305525,
                "shop_id": 7676267,
                "external_writes_performed": [
                    "miaoshou:tiktok_detail:update",
                    "miaoshou:tiktok_publish:submission",
                ],
            },
        ),
    )

    with pytest.raises(
        release_adapters.MiaoshouDraftVerificationError,
        match="official TikTok readback raised",
    ) as raised:
        release_adapters.execute_tiktok_target(_request())

    evidence = raised.value.external_write_evidence
    assert raised.value.external_reference == "3227305525:7676267"
    assert evidence["submission_accepted"] is True
    assert (
        evidence["write_outcome"]
        == "submission_accepted_readback_unknown"
    )
    assert evidence["detail_id"] == 3227305525
    assert evidence["external_writes_performed"] == [
        "miaoshou:tiktok_detail:update",
        "miaoshou:tiktok_publish:submission",
    ]


def test_official_title_repair_dispatch_exception_keeps_unknown_evidence(
    monkeypatch,
):
    monkeypatch.setattr(
        release_adapters,
        "_tiktok_shop",
        lambda _region: ("token", {"cipher": "cipher-ph"}),
    )
    monkeypatch.setattr(
        release_adapters,
        "tiktok_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("partial edit transport unavailable")
        ),
    )

    with pytest.raises(
        release_adapters.ReleaseAdapterWriteVerificationError,
        match="outcome is unknown",
    ) as raised:
        release_adapters._repair_tiktok_title(
            region="PH",
            product_id="tk-product-1",
            approved_title="Approved title",
        )

    assert raised.value.external_reference == "tk-product-1"
    evidence = raised.value.external_write_evidence
    assert evidence["region"] == "PH"
    assert evidence["write_outcome"] == "unknown_after_dispatch"
    assert evidence["external_writes_performed"] == [
        "tiktok:official_title_partial_edit"
    ]


def test_verified_tiktok_readback_updates_local_catalogue(monkeypatch):
    shop = {
        "id": "shop-ph",
        "cipher": "cipher-ph",
        "region": "PH",
        "name": "LivelyHive PH",
    }
    detail = {
        "id": "tk-product-1",
        "title": "Cute Black Line-Art Dog PVC Wall Decal 34 x 58 cm",
        "product_status": "ACTIVATE",
        "main_images": [{"uri": "image-1"}, {"uri": "image-2"}],
        "category_chains": [{"id": "600338"}],
        "skus": [
            {
                "id": "platform-sku-1",
                "seller_sku": "0953",
                "status_info": {"status": "LIVE"},
                "price": {"sale_price": "257", "currency": "PHP"},
                "inventory": [{"quantity": 1}],
            }
        ],
    }
    monkeypatch.setattr(
        release_adapters,
        "_tiktok_shop",
        lambda _region: ("token", shop),
    )
    monkeypatch.setattr(
        release_adapters,
        "tiktok_post",
        lambda *_args, **_kwargs: {
            "code": 0,
            "data": {"products": [{"id": "tk-product-1", "skus": detail["skus"]}]},
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "tiktok_get",
        lambda *_args, **_kwargs: {"code": 0, "data": detail},
    )
    cached = []
    monkeypatch.setattr(
        release_adapters,
        "_cache_verified_tiktok_listing",
        lambda **kwargs: cached.append(kwargs) or 1,
    )

    verified, evidence = release_adapters._tiktok_readback(
        seller_sku="0953",
        region="PH",
        expected_title=detail["title"],
        expected_price=257,
        expected_image_count=2,
        expected_category_id="600338",
    )

    assert verified is True
    assert evidence["catalog_rows_upserted"] == 1
    assert cached == [{"shop": shop, "detail": detail}]


def test_existing_exact_tiktok_readback_skips_miaoshou_publish(monkeypatch):
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(),
    )
    monkeypatch.setattr(
        release_adapters,
        "_tiktok_readback",
        lambda **_kwargs: (
            True,
            {
                "verified": True,
                "source": "official_tiktok_shop_api",
                "product_id": "tk-product-1",
                "checks": {
                    "seller_sku": True,
                    "title": True,
                    "price": True,
                    "image_count": True,
                    "category": True,
                },
            },
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_miaoshou_publish_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing exact listing must not be resubmitted")
        ),
    )

    result = release_adapters.execute_tiktok_target(_request())

    assert result.succeeded is True
    assert result.readback_verified is True
    assert result.external_reference == "tk-product-1"
    assert result.readback_evidence["source"] == "official_tiktok_shop_api"


def test_existing_tiktok_title_only_mismatch_is_safely_repaired(monkeypatch):
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(),
    )
    readbacks = iter(
        [
            (
                False,
                {
                    "verified": False,
                    "product_id": "tk-product-1",
                    "checks": {
                        "single_exact_sku": True,
                        "title": False,
                        "price": True,
                        "image_count": True,
                        "category": True,
                        "active": True,
                    },
                },
            ),
            (
                True,
                {
                    "verified": True,
                    "product_id": "tk-product-1",
                    "checks": {
                        "single_exact_sku": True,
                        "title": True,
                        "price": True,
                        "image_count": True,
                        "category": True,
                        "active": True,
                    },
                },
            ),
        ]
    )
    monkeypatch.setattr(
        release_adapters,
        "_tiktok_readback",
        lambda **_kwargs: next(readbacks),
    )
    repaired = []
    monkeypatch.setattr(
        release_adapters,
        "_repair_tiktok_title",
        lambda **kwargs: repaired.append(kwargs)
        or {
            "action": "official_tiktok_partial_edit",
            "fields": ["title"],
        },
    )
    monkeypatch.setattr(release_adapters.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        release_adapters,
        "_miaoshou_publish_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("title-only repair must not republish the item")
        ),
    )

    result = release_adapters.execute_tiktok_target(_request())

    assert result.succeeded is True
    assert result.readback_verified is True
    assert repaired == [
        {
            "region": "PH",
            "product_id": "tk-product-1",
            "approved_title": (
                "Cute Black Line-Art Dog PVC Wall Decal 34 x 58 cm"
            ),
        }
    ]
    assert result.readback_evidence["repair"]["verified"] is True


def test_title_repair_success_then_readback_exception_keeps_repair_evidence(
    monkeypatch,
):
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: _context(),
    )
    readbacks = {"count": 0}

    def readback(**_kwargs):
        readbacks["count"] += 1
        if readbacks["count"] == 1:
            return False, {
                "verified": False,
                "product_id": "tk-product-1",
                "checks": {
                    "single_exact_sku": True,
                    "title": False,
                    "price": True,
                    "image_count": True,
                    "category": True,
                    "active": True,
                },
            }
        raise RuntimeError("readback transport failed after repair")

    monkeypatch.setattr(release_adapters, "_tiktok_readback", readback)
    monkeypatch.setattr(
        release_adapters,
        "_repair_tiktok_title",
        lambda **_kwargs: {
            "action": "official_tiktok_partial_edit",
            "fields": ["title"],
            "product_id": "tk-product-1",
            "region": "PH",
            "verified": False,
            "write_outcome": "accepted",
            "external_writes_performed": [
                "tiktok:official_title_partial_edit"
            ],
        },
    )
    monkeypatch.setattr(release_adapters.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        release_adapters.ReleaseAdapterWriteVerificationError,
        match="title repair was accepted",
    ) as raised:
        release_adapters.execute_tiktok_target(_request())

    assert raised.value.external_reference == "tk-product-1"
    evidence = raised.value.external_write_evidence
    assert (
        evidence["write_outcome"]
        == "title_repair_accepted_readback_unknown"
    )
    assert evidence["readback_error"] == (
        "readback transport failed after repair"
    )
    assert evidence["external_writes_performed"] == [
        "tiktok:official_title_partial_edit"
    ]


def test_mx_submission_is_not_falsely_marked_as_verified(monkeypatch):
    context = _context()
    context["payload"]["targets"] = ["miaoshou:COMMON", "tiktok:MX"]
    context["payload"]["listing_copy"]["candidates"][0] = {
        "channel": "tiktok",
        "site": "MX",
        "title": "Calcomanía de pared de perro en PVC 34 x 58 cm",
        "policy_check": "passed",
    }
    context["payload"]["pricing"]["selected_targets"] = {
        "tiktok:MX": {
            "store_prices": [
                {
                    "target_key": "mx",
                    "list_price": 223,
                    "currency": "MXN",
                }
            ]
        }
    }
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        release_adapters,
        "_prior_unverified_tiktok_submission",
        lambda _request: None,
    )
    monkeypatch.setattr(
        release_adapters,
        "_miaoshou_publish_target",
        lambda *_args, **_kwargs: (
            "detail:shop",
            {"source": "miaoshou_open_api", "accepted": True},
        ),
    )

    result = release_adapters.execute_tiktok_target(_request("MX"))

    assert result.succeeded is True
    assert result.readback_verified is False
    assert result.submission_accepted is True
    assert "no authorised official TikTok readback" in result.detail


def test_mx_retry_reuses_durable_submission_without_resubmitting(monkeypatch):
    context = _context()
    context["payload"]["targets"] = ["miaoshou:COMMON", "tiktok:MX"]
    context["payload"]["listing_copy"]["candidates"][0] = {
        "channel": "tiktok",
        "site": "MX",
        "title": "Calcomanía de pared de perro en PVC 34 x 58 cm",
        "policy_check": "passed",
    }
    context["payload"]["pricing"]["selected_targets"] = {
        "tiktok:MX": {
            "store_prices": [
                {
                    "target_key": "mx",
                    "list_price": 223,
                    "currency": "MXN",
                }
            ]
        }
    }
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        release_adapters,
        "_prior_unverified_tiktok_submission",
        lambda _request: (
            "3224868435:16265910",
            {
                "source": "release_run_ledger",
                "accepted": True,
                "prior_submission_reused": True,
            },
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_miaoshou_publish_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a durable prior submission must not be resubmitted")
        ),
    )

    result = release_adapters.execute_tiktok_target(_request("MX"))

    assert result.succeeded is True
    assert result.readback_verified is False
    assert result.submission_accepted is True
    assert result.external_reference == "3224868435:16265910"
    assert result.readback_evidence["prior_submission_reused"] is True


def test_homebloom_uses_one_time_miaoshou_submission_not_livelyhive_api(
    monkeypatch,
):
    context = _context()
    context["payload"]["targets"] = ["miaoshou:COMMON", "tiktok:HB_PH"]
    context["payload"]["pricing"]["selected_targets"] = {
        "tiktok:HB_PH": {
            "store_prices": [
                {
                    "target_key": "hb_ph",
                    "list_price": 257,
                    "currency": "PHP",
                }
            ]
        }
    }
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        release_adapters,
        "_prior_unverified_tiktok_submission",
        lambda _request: None,
    )
    monkeypatch.setattr(
        release_adapters,
        "_tiktok_readback",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("HomeBloom must not read LivelyHive's PH API shop")
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_miaoshou_publish_target",
        lambda *_args, **_kwargs: (
            "detail-hb:shop-hb",
            {
                "source": "miaoshou_open_api",
                "accepted": True,
                "pre_submit_audit": {
                    "target_key": "hb_ph",
                    "submission_fingerprint": "audit-hb",
                },
            },
        ),
    )

    result = release_adapters.execute_tiktok_target(_request("HB_PH"))

    assert result.succeeded is True
    assert result.submission_accepted is True
    assert result.readback_verified is False
    assert result.external_reference == "detail-hb:shop-hb"
    assert result.readback_evidence["pre_submit_audit"]["target_key"] == "hb_ph"


def test_shopee_readback_uses_get_endpoints_and_item_price_info(monkeypatch):
    from modules.shopee import auth, client, publish

    monkeypatch.setattr(publish, "sync_shop_ids", lambda: {"PH": 123})
    monkeypatch.setattr(auth, "ensure_shop_token", lambda _shop_id: "token")
    calls = []

    def fake_get(path, _shop_id, _token, params):
        calls.append((path, params))
        if path.endswith("get_item_base_info"):
            return {
                "response": {
                    "item_list": [
                        {
                            "item_name": "Approved Shopee title",
                            "item_sku": "",
                            "item_status": "NORMAL",
                            "has_model": True,
                            "description": (
                                "A detailed English product description. " * 30
                            ),
                            "logistic_info": [
                                {
                                    "logistic_id": 48002,
                                    "logistic_name": "Standard International",
                                    "enabled": True,
                                }
                            ],
                            "price_info": [
                                {
                                    "original_price": 257,
                                    "current_price": 257,
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
            "response": {
                "model": [
                    {
                        "model_sku": "0953",
                        "price_info": [
                            {
                                "original_price": 257,
                                "current_price": 257,
                            }
                        ],
                    }
                ]
            }
        }

    monkeypatch.setattr(client, "shop_get", fake_get)

    verified, evidence = release_adapters._shopee_readback(
        match_key="0953",
        region="PH",
        item_id="49464903906",
        expected_title="Approved Shopee title",
        expected_price=257,
        expected_image_count=2,
        expected_description="A detailed English master description. " * 30,
    )

    assert verified is True
    assert evidence["prices"] == [257, 257, 257, 257]
    assert evidence["model_skus"] == ["0953"]
    assert evidence["checks"]["all_applicable_logistics"] is True
    assert [path for path, _params in calls] == [
        "/api/v2/product/get_item_base_info",
        "/api/v2/product/get_model_list",
    ]


def test_new_shopee_publish_uses_immutable_local_and_global_prices(monkeypatch):
    context = _context()
    context["payload"]["targets"] = ["shopee:MY"]
    context["payload"]["listing_copy"] = {
        "candidates": [
            {
                "channel": "shopee",
                "site": "CNSC",
                "title": "Approved English Shopee master",
                "policy_check": "passed",
            }
        ],
        "shopee_description_en": "Verified product description. " * 30,
    }
    context["payload"]["pricing"]["selected_targets"] = {
        "shopee:MY": {
            "target_site": "MY",
            "source": {"list_price": 45, "currency": "MYR"},
            "derived_preview": {
                "local_original_price": 45,
                "source_currency": "MYR",
                "global_original_price_cny": 78.75,
                "exchange_rate_cny_per_local": 1.75,
            },
        }
    }
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_item_id_for_match_key",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        release_adapters,
        "_discover_shopee_item_id_by_sku",
        lambda *_args, **_kwargs: None,
    )
    readbacks = iter(
        [
            (
                True,
                {
                    "verified": True,
                    "checks": {"price": True, "model_sku": True},
                },
            )
        ]
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **_kwargs: next(readbacks),
    )
    published = []
    monkeypatch.setattr(
        "modules.shopee.publish.publish_match_key",
        lambda *args, **kwargs: published.append((args, kwargs))
        or {"item_id": "my-item-0953"},
    )

    request = AdapterExecutionRequest(
        plan_id="omnichannel:test",
        confirmation_token="PUBLISH-TEST",
        approval_scope_digest="scope",
        product_id="3828811808",
        seller_sku="0953",
        product_package_id="product:3828811808:0953",
        content_package_id="content:3828811808",
        channel="shopee",
        site="MY",
        target_label="shopee:MY",
        idempotency_key="publish:shopee:MY:test",
    )
    result = release_adapters.execute_shopee_target(request)

    assert result.succeeded is True
    assert published[0][1]["local_original_price_override"] == 45
    assert published[0][1]["local_price_currency_override"] == "MYR"
    assert published[0][1]["global_original_price_cny_override"] == 78.75


def test_new_shopee_publish_rejects_stale_local_currency_before_publish(
    monkeypatch,
):
    context = _context()
    context["payload"]["targets"] = ["shopee:MY"]
    context["payload"]["listing_copy"] = {
        "candidates": [
            {
                "channel": "shopee",
                "site": "CNSC",
                "title": "Approved English Shopee master",
                "policy_check": "passed",
            }
        ],
        "shopee_description_en": "Verified product description. " * 30,
    }
    context["payload"]["pricing"]["selected_targets"] = {
        "shopee:MY": {
            "target_site": "MY",
            "source": {"list_price": 45, "currency": "MYR"},
            "derived_preview": {
                "local_original_price": 45,
                "source_currency": "CNY",
                "global_original_price_cny": 78.75,
                "exchange_rate_cny_per_local": 1.75,
            },
        }
    }
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_item_id_for_match_key",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        release_adapters,
        "_discover_shopee_item_id_by_sku",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "modules.shopee.publish.publish_match_key",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid immutable price evidence must fail before publish"
        ),
    )
    request = AdapterExecutionRequest(
        plan_id="omnichannel:test",
        confirmation_token="PUBLISH-TEST",
        approval_scope_digest="scope",
        product_id="3828811808",
        seller_sku="0953",
        product_package_id="product:3828811808:0953",
        content_package_id="content:3828811808",
        channel="shopee",
        site="MY",
        target_label="shopee:MY",
        idempotency_key="publish:shopee:MY:test",
    )

    with pytest.raises(
        RuntimeError,
        match="source currency does not match target region",
    ):
        release_adapters.execute_shopee_target(request)


def test_existing_shopee_mismatch_is_never_republished_as_a_duplicate(monkeypatch):
    context = _context()
    context["payload"]["targets"] = ["shopee:TH"]
    context["payload"]["listing_copy"]["candidates"] = [
        {
            "channel": "shopee",
            "site": "CNSC",
            "title": "Approved English Shopee master",
            "policy_check": "passed",
        }
    ]
    context["payload"]["pricing"]["selected_targets"] = {
        "shopee:TH": {
            "target_site": "TH",
            "derived_preview": {
                "global_original_price_cny": 42.59,
                "local_original_price": 192,
                "source_currency": "THB",
                "exchange_rate_cny_per_local": 0.2218,
            },
        }
    }
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_item_id_for_match_key",
        lambda *_args, **_kwargs: "48964906224",
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **_kwargs: (
            False,
            {
                "verified": False,
                "checks": {
                    "localized_title": False,
                    "price": False,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "modules.shopee.publish.publish_match_key",
        lambda *_args, **_kwargs: pytest.fail(
            "existing SKU must never enter the create/publish path"
        ),
    )

    result = release_adapters.execute_shopee_target(
        AdapterExecutionRequest(
            plan_id="omnichannel:test",
            confirmation_token="PUBLISH-TEST",
            approval_scope_digest="scope",
            product_id="3828811808",
            seller_sku="0953",
            product_package_id="product:3828811808:0953",
            content_package_id="content:3828811808",
            channel="shopee",
            site="TH",
            target_label="shopee:TH",
            idempotency_key="publish:shopee:TH:test",
        )
    )

    assert result.succeeded is False
    assert result.external_reference == "48964906224"
    assert "second publish was blocked" in result.detail


def test_existing_shopee_copy_is_repaired_in_place_without_republishing(monkeypatch):
    context = _context()
    context["payload"]["targets"] = ["shopee:TH"]
    context["payload"]["listing_copy"]["candidates"] = [
        {
            "channel": "shopee",
            "site": "CNSC",
            "title": "Approved English Shopee master",
            "policy_check": "passed",
        }
    ]
    context["payload"]["pricing"]["selected_targets"] = {
        "shopee:TH": {
            "target_site": "TH",
            "derived_preview": {
                "global_original_price_cny": 42.59,
                "local_original_price": 192,
                "source_currency": "THB",
                "exchange_rate_cny_per_local": 0.2218,
            },
        }
    }
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_item_id_for_match_key",
        lambda *_args, **_kwargs: "48964906224",
    )
    readbacks = iter(
        [
            (
                False,
                {
                    "verified": False,
                    "checks": {
                        "seller_sku": True,
                        "model_sku": True,
                        "localized_title": False,
                        "rich_localized_description": False,
                        "price": True,
                        "image_count": True,
                        "all_applicable_logistics": True,
                        "status": True,
                    },
                },
            ),
            (
                True,
                {
                    "verified": True,
                    "checks": {
                        "localized_title": True,
                        "rich_localized_description": True,
                    },
                },
            ),
        ]
    )
    monkeypatch.setattr(
        release_adapters,
        "_shopee_readback",
        lambda **_kwargs: next(readbacks),
    )
    monkeypatch.setattr(
        "modules.shopee.global_copy.localize_shopee_copy",
        lambda **_kwargs: {
            "title": "ชื่อภาษาไทยสำหรับสินค้าที่ผ่านการอนุมัติและพร้อมลงขายในร้านค้า",
            "description": "รายละเอียดภาษาไทย " * 50,
            "provider": "toapi",
            "model": "gpt-5.4-mini-official",
        },
    )
    repaired = []
    monkeypatch.setattr(
        "modules.shopee.publish.update_local_listing_copy",
        lambda **kwargs: repaired.append(kwargs)
        or {"verified": True, "item_id": str(kwargs["item_id"])},
    )
    monkeypatch.setattr(
        "modules.shopee.publish.sync_shop_ids",
        lambda: {"TH": 123},
    )
    monkeypatch.setattr(
        "modules.shopee.auth.ensure_shop_token",
        lambda _shop_id: "token",
    )
    monkeypatch.setattr(
        "modules.shopee.publish.publish_match_key",
        lambda *_args, **_kwargs: pytest.fail(
            "in-place repair must not enter the create/publish path"
        ),
    )

    result = release_adapters.execute_shopee_target(
        AdapterExecutionRequest(
            plan_id="omnichannel:test",
            confirmation_token="PUBLISH-TEST",
            approval_scope_digest="scope",
            product_id="3828811808",
            seller_sku="0953",
            product_package_id="product:3828811808:0953",
            content_package_id="content:3828811808",
            channel="shopee",
            site="TH",
            target_label="shopee:TH",
            idempotency_key="publish:shopee:TH:test",
        )
    )

    assert result.succeeded is True
    assert result.readback_verified is True
    assert result.external_reference == "48964906224"
    assert repaired[0]["item_id"] == 48964906224
    assert "repaired in place" in result.detail


def test_ozon_release_uses_verified_tiktok_images_without_third_party_rehosting(
    monkeypatch,
):
    from modules.ozon import migrate_batch

    context = _context()
    context["payload"]["targets"] = ["ozon:RU"]
    context["payload"]["listing_copy"]["candidates"] = [
        {
            "channel": "ozon",
            "site": "RU",
            "title": "ПВХ наклейка на стену с собакой 34 x 58 см",
            "policy_check": "passed",
        },
        {
            "channel": "tiktok",
            "site": "PH",
            "title": "Cute Black Line-Art Dog PVC Wall Decal 34 x 58 cm",
            "policy_check": "passed",
        },
    ]
    context["payload"]["pricing"]["selected_targets"] = {
        "ozon:RU": {
            "selected_source_target_key": "lh_ph",
            "derived_preview": {
                "price_cny": 49,
                "old_price_cny": 65,
            },
        },
        "tiktok:LH_PH": {
            "store_prices": [
                {
                    "target_key": "lh_ph",
                    "list_price": 257,
                    "currency": "PHP",
                }
            ]
        },
    }
    request = _request("RU")
    request = AdapterExecutionRequest(
        **{
            **request.__dict__,
            "channel": "ozon",
            "site": "RU",
            "target_label": "ozon:RU",
        }
    )
    monkeypatch.setattr(
        release_adapters,
        "_validated_context",
        lambda _request: context,
    )
    ozon_reads = iter(
        [
            (False, {"verified": False}),
            (
                True,
                {
                    "verified": True,
                    "product_id": "ozon-product-1",
                },
            ),
        ]
    )
    monkeypatch.setattr(
        release_adapters,
        "_ozon_readback",
        lambda **_kwargs: next(ozon_reads),
    )
    monkeypatch.setattr(
        release_adapters,
        "_ozon_set_release_stock",
        lambda **_kwargs: {"warehouse_id": "warehouse-1", "stock": 50},
    )
    monkeypatch.setattr(
        release_adapters,
        "_await_ozon_product_creation",
        lambda **_kwargs: {
            "state": "created",
            "offer_id": "0953",
            "product_id": "ozon-product-1",
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "_tiktok_readback",
        lambda **_kwargs: (
            True,
            {
                "verified": True,
                "image_urls": [
                    "https://tiktok.example/approved-1.jpg",
                    "https://tiktok.example/approved-2.jpg",
                ],
            },
        ),
    )
    calls = []
    monkeypatch.setattr(
        migrate_batch,
        "migrate_one",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or {
            "ok": True,
            "offer_id": "0953",
            "status": "imported",
            "task_id": "task-1",
            "import_request_attempted": True,
            "import_dispatch_outcome": "accepted",
        },
    )

    result = release_adapters.execute_ozon_target(request)

    assert result.succeeded is True
    assert result.readback_verified is True
    assert calls[0][1]["image_urls_override"] == [
        "https://tiktok.example/approved-1.jpg",
        "https://tiktok.example/approved-2.jpg",
    ]
    assert calls[0][1]["process_images"] is False
    assert calls[0][1]["skip_rich_content"] is True
    assert calls[0][1]["skip_mapping_write"] is True


def test_ozon_readback_requires_approved_moderation_stock_and_exact_image_count(
    monkeypatch,
):
    from modules.ozon import client

    monkeypatch.setattr(
        client,
        "ozon_post",
        lambda _path, _body: {
            "items": [
                {
                    "id": 5673889199,
                    "offer_id": "0953",
                    "sku": 5225458426,
                    "name": "Approved Ozon platform title",
                    "price": "30.00",
                    "old_price": "39.00",
                    "is_archived": False,
                    "is_autoarchived": False,
                    "primary_image": ["https://ozon/1.jpg"],
                    "images": [
                        "https://ozon/2.jpg",
                        "https://ozon/3.jpg",
                        "https://ozon/4.jpg",
                        "https://ozon/5.jpg",
                    ],
                    "errors": [],
                    "visibility_details": {"has_stock": True},
                    "statuses": {
                        "status": "price_sent",
                        "status_failed": "",
                        "moderate_status": "approved",
                        "validation_status": "success",
                        "is_created": True,
                    },
                }
            ]
        },
    )

    verified, evidence = release_adapters._ozon_readback(
        offer_id="0953",
        expected_title="Approved Ozon platform title",
        expected_price=30,
        expected_image_count=5,
    )

    assert verified is True
    assert evidence["checks"] == {
        "offer_id": True,
        "title": True,
        "price": True,
        "images": True,
        "moderation": True,
        "stock": True,
    }


def test_ozon_rich_content_repair_is_fact_only_utf8_russian():
    payload = release_adapters._ozon_audited_rich_content(
        title="Декоративная наклейка на стену",
        images=[
            "https://ozon.example/approved-1.jpg",
            "https://ozon.example/approved-2.jpg",
        ],
        width_cm=58.0,
        height_cm=34.0,
    )
    serialized = str(payload)

    assert "Описание товара" in serialized
    assert "Размер 58 × 34 см" in serialized
    assert "Перед покупкой проверьте изображения" in serialized
    assert "https://ozon.example/approved-1.jpg" in serialized
    assert not any("\u4e00" <= char <= "\u9fff" for char in serialized)


def test_ozon_stock_write_uses_only_single_eligible_non_kgt_warehouse(monkeypatch):
    from modules.ozon import client

    calls = []

    def fake_post(path, body):
        calls.append((path, body))
        if path == "/v2/warehouse/list":
            return {
                "warehouses": [
                    {
                        "warehouse_id": 1020005018928780,
                        "status": "created",
                        "is_kgt": False,
                    }
                ]
            }
        return {
            "result": [
                {
                    "warehouse_id": 1020005018928780,
                    "offer_id": "0953",
                    "updated": True,
                    "errors": [],
                }
            ]
        }

    monkeypatch.setattr(client, "ozon_post", fake_post)

    evidence = release_adapters._ozon_set_release_stock(
        offer_id="0953",
        stock=50,
    )

    assert evidence["warehouse_id"] == "1020005018928780"
    assert calls[1] == (
        "/v2/products/stocks",
        {
            "stocks": [
                {
                    "offer_id": "0953",
                    "stock": 50,
                    "warehouse_id": 1020005018928780,
                }
            ]
        },
    )


def _ozon_release_fixture():
    context = _context()
    context["payload"]["targets"] = ["ozon:RU"]
    context["payload"]["listing_copy"]["candidates"] = [
        {
            "channel": "ozon",
            "site": "RU",
            "title": "袩袙啸 薪邪泻谢械泄泻邪 薪邪 褋褌械薪褍 34 x 58 褋屑",
            "policy_check": "passed",
        },
        {
            "channel": "tiktok",
            "site": "PH",
            "title": "Cute Black Line-Art Dog PVC Wall Decal 34 x 58 cm",
            "policy_check": "passed",
        },
    ]
    context["payload"]["pricing"]["selected_targets"] = {
        "ozon:RU": {
            "selected_source_target_key": "lh_ph",
            "derived_preview": {
                "price_cny": 49,
                "old_price_cny": 65,
            },
        },
        "tiktok:LH_PH": {
            "store_prices": [
                {
                    "target_key": "lh_ph",
                    "list_price": 257,
                    "currency": "PHP",
                }
            ]
        },
    }
    base = _request("RU")
    request = AdapterExecutionRequest(
        **{
            **base.__dict__,
            "channel": "ozon",
            "site": "RU",
            "target_label": "ozon:RU",
        }
    )
    return context, request


def test_ozon_creation_waits_for_task_and_exact_product_identity(monkeypatch):
    from modules.ozon import client

    calls = []
    task_rows = iter(
        [
            {"status": "pending", "offer_id": "0953", "errors": []},
            {"status": "imported", "offer_id": "0953", "errors": []},
            {"status": "imported", "offer_id": "0953", "errors": []},
        ]
    )
    product_rows = iter(
        [
            {
                "id": 765,
                "offer_id": "0953",
                "statuses": {"is_created": False, "status": "processing"},
            },
            {
                "id": 765,
                "offer_id": "0953",
                "statuses": {"is_created": True, "status": "created"},
            },
        ]
    )

    def fake_post(path, body):
        calls.append((path, body))
        if path == "/v1/product/import/info":
            return {"result": {"items": [next(task_rows)]}}
        if path == "/v3/product/info/list":
            return {"items": [next(product_rows)]}
        raise AssertionError(f"unexpected write or endpoint: {path}")

    monkeypatch.setattr(client, "ozon_post", fake_post)
    monkeypatch.setattr(release_adapters.time, "sleep", lambda _seconds: None)

    evidence = release_adapters._await_ozon_product_creation(
        offer_id="0953",
        task_id="task-1",
        attempts=3,
        delay_seconds=0,
    )

    assert evidence["state"] == "created"
    assert evidence["product_id"] == "765"
    assert evidence["poll_attempt"] == 3
    assert [path for path, _body in calls] == [
        "/v1/product/import/info",
        "/v1/product/import/info",
        "/v3/product/info/list",
        "/v1/product/import/info",
        "/v3/product/info/list",
    ]
    assert not any(path == "/v2/products/stocks" for path, _body in calls)


def test_ozon_creation_immediate_happy_path(monkeypatch):
    from modules.ozon import client

    calls = []

    def fake_post(path, body):
        calls.append((path, body))
        if path == "/v1/product/import/info":
            return {
                "result": {
                    "items": [
                        {
                            "status": "imported",
                            "offer_id": "0953",
                            "errors": [],
                        }
                    ]
                }
            }
        return {
            "items": [
                {
                    "id": 765,
                    "offer_id": "0953",
                    "statuses": {"is_created": True, "status": "created"},
                }
            ]
        }

    monkeypatch.setattr(client, "ozon_post", fake_post)

    evidence = release_adapters._await_ozon_product_creation(
        offer_id="0953",
        task_id="task-1",
        attempts=1,
        delay_seconds=0,
    )

    assert evidence["state"] == "created"
    assert [path for path, _body in calls] == [
        "/v1/product/import/info",
        "/v3/product/info/list",
    ]


def test_ozon_create_then_creation_then_stock_then_readback(monkeypatch):
    from modules.ozon import migrate_batch

    context, request = _ozon_release_fixture()
    events = []
    reads = iter(
        [
            (
                False,
                {
                    "verified": False,
                    "reason": "offer_not_found",
                    "item_count": 0,
                },
            ),
            (
                True,
                {
                    "verified": True,
                    "product_id": "ozon-product-1",
                },
            ),
        ]
    )

    def readback(**_kwargs):
        events.append("initial_readback" if not events else "final_readback")
        return next(reads)

    monkeypatch.setattr(release_adapters, "_validated_context", lambda _r: context)
    monkeypatch.setattr(release_adapters, "_ozon_readback", readback)
    monkeypatch.setattr(
        release_adapters,
        "_tiktok_readback",
        lambda **_kwargs: (
            True,
            {
                "verified": True,
                "image_urls": [
                    "https://tiktok.example/approved-1.jpg",
                    "https://tiktok.example/approved-2.jpg",
                ],
            },
        ),
    )
    monkeypatch.setattr(
        migrate_batch,
        "migrate_one",
        lambda *_args, **_kwargs: events.append("create")
        or {
            "ok": True,
            "offer_id": "0953",
            "status": "imported",
            "task_id": "task-1",
            "import_request_attempted": True,
            "import_dispatch_outcome": "accepted",
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "_await_ozon_product_creation",
        lambda **_kwargs: events.append("creation_confirmed")
        or {
            "state": "created",
            "offer_id": "0953",
            "product_id": "ozon-product-1",
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "_ozon_set_release_stock",
        lambda **_kwargs: events.append("stock")
        or {"warehouse_id": "warehouse-1", "stock": 50},
    )

    result = release_adapters.execute_ozon_target(request)

    assert result.succeeded is True
    assert result.readback_verified is True
    assert events == [
        "initial_readback",
        "create",
        "creation_confirmed",
        "stock",
        "final_readback",
    ]
    assert result.readback_evidence["external_writes_performed"] == [
        "ozon:product_import:create",
        "ozon:stock:update",
    ]


def test_ozon_creation_timeout_never_updates_stock_and_requires_reconciliation(
    monkeypatch,
):
    from modules.ozon import migrate_batch

    context, request = _ozon_release_fixture()
    monkeypatch.setattr(release_adapters, "_validated_context", lambda _r: context)
    monkeypatch.setattr(
        release_adapters,
        "_ozon_readback",
        lambda **_kwargs: (
            False,
            {
                "verified": False,
                "reason": "offer_not_found",
                "item_count": 0,
            },
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_tiktok_readback",
        lambda **_kwargs: (
            True,
            {
                "verified": True,
                "image_urls": [
                    "https://tiktok.example/approved-1.jpg",
                    "https://tiktok.example/approved-2.jpg",
                ],
            },
        ),
    )
    monkeypatch.setattr(
        migrate_batch,
        "migrate_one",
        lambda *_args, **_kwargs: {
            "ok": True,
            "offer_id": "0953",
            "status": "imported",
            "task_id": "task-1",
            "import_request_attempted": True,
            "import_dispatch_outcome": "accepted",
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "_await_ozon_product_creation",
        lambda **_kwargs: {
            "state": "timeout",
            "offer_id": "0953",
            "task_id": "task-1",
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "_ozon_set_release_stock",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("stock must wait for exact product creation")
        ),
    )

    result = release_adapters.execute_ozon_target(request)

    assert result.succeeded is True
    assert result.submission_accepted is True
    assert result.readback_verified is False
    assert result.readback_evidence["reconciliation_required"] is True
    assert result.readback_evidence["creation"]["state"] == "timeout"
    assert result.readback_evidence["external_writes_performed"] == [
        "ozon:product_import:create"
    ]


def test_ozon_ambiguous_create_receipt_blocks_stock_and_second_create(monkeypatch):
    from modules.ozon import migrate_batch

    context, request = _ozon_release_fixture()
    monkeypatch.setattr(release_adapters, "_validated_context", lambda _r: context)
    monkeypatch.setattr(
        release_adapters,
        "_ozon_readback",
        lambda **_kwargs: (
            False,
            {
                "verified": False,
                "reason": "offer_not_found",
                "item_count": 0,
            },
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_tiktok_readback",
        lambda **_kwargs: (
            True,
            {
                "verified": True,
                "image_urls": [
                    "https://tiktok.example/approved-1.jpg",
                    "https://tiktok.example/approved-2.jpg",
                ],
            },
        ),
    )
    create_calls = []
    monkeypatch.setattr(
        migrate_batch,
        "migrate_one",
        lambda *_args, **_kwargs: create_calls.append(True)
        or {
            "ok": False,
            "offer_id": "0953",
            "step": "migrate",
            "error": "transport response lost",
            "import_request_attempted": True,
            "import_dispatch_outcome": "unknown_after_dispatch",
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "_await_ozon_product_creation",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing task identity must fail before polling")
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_ozon_set_release_stock",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambiguous create must not update stock")
        ),
    )

    result = release_adapters.execute_ozon_target(request)

    assert len(create_calls) == 1
    assert result.submission_accepted is True
    assert result.readback_evidence["creation"] == {
        "state": "ambiguous",
        "reason": "missing_import_task_id",
    }


def test_ozon_visible_pending_product_is_never_imported_twice(monkeypatch):
    from modules.ozon import migrate_batch

    context, request = _ozon_release_fixture()
    monkeypatch.setattr(release_adapters, "_validated_context", lambda _r: context)
    monkeypatch.setattr(
        release_adapters,
        "_ozon_readback",
        lambda **_kwargs: (
            False,
            {
                "verified": False,
                "item_count": 1,
                "product_id": "ozon-product-1",
                "offer_id": "0953",
                "title": "expected",
                "errors": [],
                "is_created": False,
                "checks": {
                    "offer_id": True,
                    "title": True,
                    "price": True,
                    "images": True,
                    "moderation": False,
                    "stock": False,
                },
            },
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_await_ozon_product_creation",
        lambda **_kwargs: {
            "state": "timeout",
            "offer_id": "0953",
            "product_id": "ozon-product-1",
        },
    )
    monkeypatch.setattr(
        migrate_batch,
        "migrate_one",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("visible pending product must never be imported again")
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_ozon_set_release_stock",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("pending product must not receive stock")
        ),
    )

    result = release_adapters.execute_ozon_target(request)

    assert result.submission_accepted is True
    assert result.readback_evidence["creation"]["state"] == "timeout"
    assert result.readback_evidence["external_writes_performed"] == []


def test_ozon_retry_with_invisible_prior_import_never_creates_again(monkeypatch):
    from modules.ozon import migrate_batch

    context, request = _ozon_release_fixture()
    context["target"] = {"attempts": 2, "status": "RUNNING"}
    monkeypatch.setattr(release_adapters, "_validated_context", lambda _r: context)
    monkeypatch.setattr(
        release_adapters,
        "_ozon_readback",
        lambda **_kwargs: (
            False,
            {
                "verified": False,
                "reason": "offer_not_found",
                "item_count": 0,
            },
        ),
    )
    monkeypatch.setattr(
        migrate_batch,
        "migrate_one",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a retry must never dispatch a second Ozon import")
        ),
    )
    monkeypatch.setattr(
        release_adapters,
        "_ozon_set_release_stock",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("invisible prior import must not receive stock")
        ),
    )

    result = release_adapters.execute_ozon_target(request)

    assert result.submission_accepted is True
    assert result.readback_evidence["duplicate_import_blocked"] is True
    assert result.readback_evidence["durable_attempts"] == 2
    assert result.readback_evidence["external_writes_performed"] == []
