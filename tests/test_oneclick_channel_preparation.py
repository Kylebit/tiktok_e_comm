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
    prepared = subject.prepare_shopee_plan_native_first_attempt(_shopee_command())
    assert prepared["plan_native"] is True
    assert prepared["legacy_tiktok_dependency"] is False
    assert prepared["approved"]["target_pricing"]["currency"] == "MYR"


def test_shopee_native_prepare_rejects_legacy_dependency_marker():
    with pytest.raises(subject.OneClickPreparationError):
        subject.prepare_shopee_plan_native_first_attempt(
            {**_shopee_command(), "publish_match_key": "hidden-legacy"}
        )


def _shopee_command():
    return {
        "target_label": "shopee:MY", "seller_sku": "0954", "model_sku": "0954",
        "listing_copy": {"title": "Approved", "description": "Approved factual description"},
        "images": [{"position": 1, "image_url": "https://assets.example/one.jpg"}],
        "parcel": {"weight_kg": "0.12", "package_cm": [40, 3, 3]},
        "target_pricing": {"local_original_price": "33", "currency": "MYR"},
        "policy": {"schema_version": "shopee-policy/v1", "policy_digest": "a" * 64},
    }


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("listing_copy", "title"), ""),
        (("images",), [{"position": 1, "image_url": "https://x"}, False]),
        (("images",), [{"position": True, "image_url": "https://x"}]),
        (("parcel", "weight_kg"), True),
        (("parcel", "package_cm"), [40, 3, False]),
        (("target_pricing", "local_original_price"), "NaN"),
        (("target_pricing", "currency"), "MY"),
        (("policy", "policy_digest"), "not-a-digest"),
    ],
)
def test_shopee_prepared_payload_rejects_malformed_mixed_shapes(path, value):
    command = _shopee_command()
    if len(path) == 1:
        command[path[0]] = value
    else:
        command[path[0]][path[1]] = value
    with pytest.raises(subject.OneClickPreparationError):
        subject.prepare_shopee_plan_native_first_attempt(command)


def test_shopee_prepared_digest_changes_for_each_approved_write_field():
    baseline = subject.prepare_shopee_plan_native_first_attempt(_shopee_command())
    for path, value in (
        (("listing_copy", "description"), "Changed"),
        (("images",), [{"position": 1, "image_url": "https://assets.example/two.jpg"}]),
        (("parcel", "weight_kg"), "0.13"),
        (("target_pricing", "local_original_price"), "34"),
        (("model_sku",), "0955"),
        (("policy", "policy_digest"), "b" * 64),
    ):
        command = _shopee_command()
        if len(path) == 1:
            command[path[0]] = value
        else:
            command[path[0]][path[1]] = value
        assert subject.prepare_shopee_plan_native_first_attempt(command)["prepared_digest"] != baseline["prepared_digest"]


def test_shopee_description_is_exact_but_title_is_nfc_trimmed():
    command = _shopee_command()
    command["listing_copy"] = {
        "title": "  Cafe\u0301 decal  ",
        "description": "\n Exact approved description; do not trim. \n",
    }
    prepared = subject.prepare_shopee_plan_native_first_attempt(command)
    copy = prepared["approved"]["listing_copy"]
    assert copy["title"] == "Café decal"
    assert copy["description"] == "\n Exact approved description; do not trim. \n"
    from shared_platform.target_scoped_release_contracts import approved_shopee_copy_digest
    assert copy["approved_copy_digest"] == approved_shopee_copy_digest(
        "Café decal", "\n Exact approved description; do not trim. \n"
    )


@pytest.mark.parametrize(
    ("target", "currency"),
    [
        ("shopee:PH", "MYR"), ("shopee:MY", "PHP"),
        ("shopee:TH", "VND"), ("shopee:VN", "THB"),
    ],
)
def test_shopee_currency_is_exact_for_target(target, currency):
    command = _shopee_command()
    command["target_label"] = target
    command["target_pricing"]["currency"] = currency
    with pytest.raises(subject.OneClickPreparationError, match="pricing"):
        subject.prepare_shopee_plan_native_first_attempt(command)


@pytest.mark.parametrize(
    ("global_state", "regional_state", "writes", "outcome"),
    [
        ("not_started", "not_started", [], "FAILED_PRE_SUBMIT"),
        ("accepted", "not_started", ["shopee:global_master:update"], "RECONCILIATION_REQUIRED"),
        ("unknown", "not_started", ["shopee:global_master:update"], "RECONCILIATION_REQUIRED"),
        ("accepted", "unknown", ["shopee:global_master:update", "shopee:regional_publish"], "RECONCILIATION_REQUIRED"),
        ("accepted", "accepted", ["shopee:global_master:update", "shopee:regional_publish"], "POST_DISPATCH_READBACK_REQUIRED"),
    ],
)
def test_shopee_multistage_receipt_never_loses_prior_global_write(
    global_state,
    regional_state,
    writes,
    outcome,
):
    receipt = subject.classify_shopee_dispatch_boundary(
        global_master_state=global_state,
        regional_state=regional_state,
    )
    assert receipt["external_writes_performed"] == writes
    assert receipt["outcome"] == outcome


def test_shopee_batch_replay_returns_only_pristine_unfinished_regions():
    remaining = subject.remaining_shopee_regions(
        {
            "shopee:PH": {"status": "SUCCEEDED", "attempts": 1},
            "shopee:MY": {"status": "RECONCILIATION_REQUIRED", "attempts": 1},
            "shopee:TH": {"status": "PENDING", "attempts": 0},
            "shopee:VN": {"status": "FAILED", "attempts": 1},
        }
    )
    assert remaining == ("shopee:TH",)
