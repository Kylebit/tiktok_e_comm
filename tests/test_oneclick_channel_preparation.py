import pytest

from domains.channel_operations import oneclick_channel_preparation as subject


def test_tiktok_source_query_uses_offer_id_not_human_item_code():
    prepared = subject.prepare_tiktok_source_query(
        collect_box={"source_item_id": "986159122616"},
        source_record={"source_id": "986159122616", "source_item_code": "JD5047（38*45cm）"},
    )
    assert prepared["filter"] == {"sourceItemIdKeyword": "986159122616"}
    assert "JD5047" not in repr(prepared)
    assert prepared["external_writes_performed"] == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"collect_box": {"source_item_id": "JD5047（38*45cm）"}},
        {"collect_box": {"source_item_id": True}},
        {"collect_box": {"source_item_id": "986159122616"}, "source_record": {"source_id": "986159122617"}},
    ],
)
def test_invalid_source_identity_is_systemic_and_zero_write(kwargs):
    with pytest.raises(subject.OneClickPreparationError, match="SYSTEMIC_IDENTITY"):
        subject.prepare_tiktok_source_query(**kwargs)


def test_source_pages_require_complete_nonlooping_shape():
    result = subject.validate_complete_source_pages(
        [
            {"result": "success", "data": {"detailList": [{"id": 1}], "totalCount": 2, "hasNextPage": True, "nextPageToken": 2}},
            {"result": "success", "data": {"detailList": [{"id": 2}], "totalCount": 2, "hasNextPage": False}},
        ]
    )
    assert result["complete"] is True
    assert result["row_count"] == 2


@pytest.mark.parametrize(
    "pages",
    [
        [],
        [{"result": "error", "data": {}}],
        [{"result": "success", "data": {"detailList": [], "totalCount": 1, "hasNextPage": False}}],
        [{"result": "success", "data": {"detailList": [], "totalCount": 0, "hasNextPage": True, "nextPageToken": 1}}],
    ],
)
def test_source_page_faults_fail_closed(pages):
    with pytest.raises(subject.OneClickPreparationError):
        subject.validate_complete_source_pages(pages)


def test_shopee_native_prepare_has_no_tiktok_or_legacy_dependency():
    prepared = subject.prepare_shopee_plan_native_first_attempt(
        {
            "target_label": "shopee:MY",
            "seller_sku": "0954",
            "listing_copy": {"title": "Approved"},
            "images": [{"position": 1}],
            "parcel": {"weight_kg": 0.12, "package_cm": [40, 3, 3]},
            "target_pricing": {"currency": "MYR", "price": 33},
        }
    )
    assert prepared["plan_native"] is True
    assert prepared["legacy_tiktok_dependency"] is False


def test_shopee_native_prepare_rejects_legacy_dependency_marker():
    with pytest.raises(subject.OneClickPreparationError):
        subject.prepare_shopee_plan_native_first_attempt(
            {
                "target_label": "shopee:MY", "seller_sku": "0954",
                "listing_copy": {"title": "Approved"}, "images": [1],
                "parcel": {"weight_kg": 1}, "target_pricing": {"price": 1},
                "publish_match_key": "hidden-legacy",
            }
        )
