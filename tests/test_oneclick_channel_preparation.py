import json

import pytest
from dataclasses import dataclass

from domains.channel_operations import oneclick_channel_preparation as subject
from domains.channel_operations import oneclick_release_adapters as adapter_subject


@dataclass(frozen=True)
class _PrepareRequestFixture:
    target_label: str
    idempotency_key: str = "publish:fixture"
    source_identity_digest: str = "sha256:" + "a" * 64
    source_identity: dict[str, object] | None = None

    def __post_init__(self):
        if self.source_identity is None:
            object.__setattr__(
                self,
                "source_identity",
                {
                    "schema_version": "source-product-identity/v1",
                    "source_offer_id": "986159122616",
                    "source_item_code": "JD5047（38*45cm）",
                    "identity_digest": "sha256:" + "a" * 64,
                },
            )


def test_tiktok_source_query_uses_offer_id_not_human_item_code():
    prepared = subject.prepare_tiktok_source_query(
        collect_box={"source_item_id": "986159122616"},
        source_record={"source_id": "986159122616", "source_item_code": "JD5047（38*45cm）"},
    )
    assert prepared["filter"] == {"sourceItemIdKeyword": "986159122616"}
    assert "JD5047" not in repr(prepared)
    assert prepared["external_writes_performed"] == []


def test_tiktok_source_query_accepts_controlplane_canonical_identity():
    prepared = subject.prepare_tiktok_source_query_from_canonical_identity(
        {
            "schema_version": "source-product-identity/v1",
            "source_offer_id": "986159122616",
            "source_item_code": "JD5047（38*45cm）",
            "identity_digest": "a" * 64,
        }
    )
    assert prepared["filter"] == {"sourceItemIdKeyword": "986159122616"}
    assert "JD5047" not in repr(prepared)


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"source_offer_id": "JD5047", "identity_digest": "a" * 64},
        {"source_offer_id": "986159122616", "identity_digest": "bad"},
        {"source_offer_id": True, "identity_digest": "a" * 64},
    ],
)
def test_tiktok_controlplane_identity_invalid_is_systemic(identity):
    with pytest.raises(subject.OneClickPreparationError, match="SYSTEMIC_IDENTITY"):
        subject.prepare_tiktok_source_query_from_canonical_identity(identity)


@pytest.mark.parametrize(
    "label",
    ["miaoshou:COMMON", "tiktok:LH_PH", "tiktok:MX", "tiktok:HB_VN"],
)
def test_tiktok_miaoshou_prepare_seed_uses_controlplane_identity(label):
    seed = adapter_subject.build_tiktok_miaoshou_prepare_seed(
        _PrepareRequestFixture(target_label=label)
    )
    assert seed.target_label == label
    assert seed.command["source_query"]["filter"] == {
        "sourceItemIdKeyword": "986159122616"
    }
    assert "JD5047" not in repr(seed.command)


def test_shopee_prepare_seed_binds_native_command_without_legacy_lookup():
    seed = adapter_subject.build_shopee_prepare_seed(
        _PrepareRequestFixture(target_label="shopee:MY"),
        _shopee_command(),
    )
    assert seed.command["prepared"]["legacy_tiktok_dependency"] is False
    assert seed.command["prepared"]["approved"]["target_pricing"]["currency"] == "MYR"


def test_prepare_seed_rejects_target_drift_before_provider_invocation():
    with pytest.raises(adapter_subject.OneClickAdapterInputError):
        adapter_subject.build_shopee_prepare_seed(
            _PrepareRequestFixture(target_label="shopee:MY"),
            {**_shopee_command(), "target_label": "shopee:VN"},
        )


def _provider(*, prepared=None, dispatched=None):
    prepared = prepared or {
        "command": {"schema_version": "fixture/v1"},
        "proof": {"schema_version": "fixture-proof/v1"},
        "external_writes_performed": [],
    }
    dispatched = dispatched or {
        "canonical_status": "SUCCEEDED",
        "reason_category": "CAPABILITY",
        "reason_scope": "TARGET",
        "reason_code": "fixture_verified",
        "reason_detail": "fixture readback verified",
        "external_writes": (),
        "external_id": "fixture-1",
        "readback_verified": True,
    }
    return adapter_subject.OneClickProvider(
        prepare_tiktok_miaoshou=lambda *_args: prepared,
        dispatch_tiktok_miaoshou=lambda _request: dispatched,
        prepare_shopee=lambda *_args: prepared,
        dispatch_shopee=lambda _request: dispatched,
    )


def test_tiktok_prepare_uses_injected_readonly_provider_and_manual_branch():
    result = adapter_subject.prepare_oneclick_target(
        _PrepareRequestFixture(target_label="tiktok:MX"),
        provider_factory=lambda: _provider(),
    )
    assert result["classification"] == "READY_SUBMIT_MANUAL"
    assert result["manual_after_submit"] is True
    assert result["command"]["seed"]["source_query"]["filter"] == {
        "sourceItemIdKeyword": "986159122616"
    }


def test_shopee_prepare_uses_injected_provider_and_blocks_legacy_command():
    request = _PrepareRequestFixture(target_label="shopee:MY")
    object.__setattr__(request, "immutable_plan_payload", {"oneclick_shopee_command": _shopee_command()})
    result = adapter_subject.prepare_oneclick_target(
        request,
        provider_factory=lambda: _provider(),
    )
    assert result["classification"] == "EXACT_READY_AUTOMATIC"
    assert result["command"]["seed"]["prepared"]["plan_native"] is True


def test_shopee_multi_model_lineage_is_blocked_before_provider_or_write():
    request = _PrepareRequestFixture(target_label="shopee:MY")
    object.__setattr__(
        request,
        "immutable_plan_payload",
        {
            "product_facts": {
                "weight_kg": "0.12",
                "package_cm": [40, 3, 3],
            },
            "listing_copy": {
                "shopee_title_en": "Approved",
                "shopee_description_en": "Approved factual description",
            },
            "images": [
                {"image_url": "https://assets.example/one.jpg"},
            ],
            "seller_sku": "0954",
            "sku_lineage": {
                "assignment": {
                    "model_skus": [
                        {"variant_key": "red", "model_sku": "0954"},
                        {"variant_key": "blue", "model_sku": "0955"},
                    ]
                }
            },
            "pricing": {
                "selected_targets": {
                    "shopee:MY": {
                        "store_prices": [
                            {"list_price": "33", "currency": "MYR"},
                        ]
                    }
                }
            },
        },
    )
    provider_calls = {"prepare": 0}
    provider = _provider()
    object.__setattr__(
        provider,
        "prepare_shopee",
        lambda *_args: provider_calls.__setitem__(
            "prepare", provider_calls["prepare"] + 1
        ),
    )

    result = adapter_subject.prepare_oneclick_target(
        request,
        provider_factory=lambda: provider,
    )

    assert result["classification"] == "BLOCKED_CAPABILITY"
    assert result["reason_category"] == "CONTENT"
    assert result["command"] is None
    assert result["proof"] is None
    assert provider_calls["prepare"] == 0


def test_ozon_is_inventory_blocked_without_default_stock():
    result = adapter_subject.prepare_oneclick_target(
        _PrepareRequestFixture(target_label="ozon:RU"),
        provider_factory=lambda: _provider(),
    )
    assert result["classification"] == "BLOCKED_INVENTORY"
    assert result["command"] is None


def test_provider_prepare_write_is_rejected_before_dispatch():
    with pytest.raises(adapter_subject.OneClickAdapterInputError, match="read_only"):
        adapter_subject.prepare_oneclick_target(
            _PrepareRequestFixture(target_label="tiktok:LH_MY"),
            provider_factory=lambda: _provider(prepared={
                "command": {"schema_version": "fixture/v1"},
                "proof": {"schema_version": "fixture-proof/v1"},
                "external_writes_performed": ["miaoshou:claim"],
            }),
        )


def test_provider_prepare_typed_dispatch_error_cannot_be_downgraded_to_blocker():
    provider = _provider()
    object.__setattr__(
        provider,
        "prepare_tiktok_miaoshou",
        lambda *_args: (_ for _ in ()).throw(
            adapter_subject.OneClickProviderDispatchError(
                "prepare callback invoked an external write",
                external_writes=("miaoshou:tiktok_detail:update",),
                dispatch_outcome_unknown=True,
            )
        ),
    )
    with pytest.raises(
        adapter_subject.OneClickAdapterInputError,
        match="prepare_provider_reported_external_write",
    ):
        adapter_subject.prepare_oneclick_target(
            _PrepareRequestFixture(target_label="tiktok:LH_MY"),
            provider_factory=lambda: provider,
        )


def test_dispatch_returns_single_provider_receipt_without_generic_retry():
    dispatched = _provider().dispatch_tiktok_miaoshou(None)
    result = adapter_subject.dispatch_oneclick_target(
        _PrepareRequestFixture(target_label="tiktok:LH_MY"),
        provider_factory=lambda: _provider(dispatched=dispatched),
    )
    assert result["external_id"] == "fixture-1"
    assert result["canonical_status"] == "SUCCEEDED"


def test_dispatch_provider_postwrite_error_preserves_actual_write_classes():
    provider = _provider()
    provider = adapter_subject.OneClickProvider(
        prepare_tiktok_miaoshou=provider.prepare_tiktok_miaoshou,
        prepare_shopee=provider.prepare_shopee,
        dispatch_shopee=provider.dispatch_shopee,
        dispatch_tiktok_miaoshou=lambda _request: (_ for _ in ()).throw(
            adapter_subject.OneClickProviderDispatchError(
                "accepted response lost",
                external_writes=("miaoshou:tiktok_detail:update", "miaoshou:tiktok_publish:submission"),
                external_id="internal-ref",
                dispatch_outcome_unknown=True,
            )
        ),
    )
    from shared_platform.oneclick_release_controlplane import DispatchInvocationError
    with pytest.raises(DispatchInvocationError) as error:
        adapter_subject.dispatch_oneclick_target(
            _PrepareRequestFixture(target_label="tiktok:LH_MY"),
            provider_factory=lambda: provider,
        )
    assert error.value.external_writes == (
        "miaoshou:tiktok_detail:update", "miaoshou:tiktok_publish:submission"
    )


def test_final_typed_registry_is_owned_by_channel_operations():
    registry = adapter_subject.production_adapter_registry(
        provider_factory=lambda: _provider()
    )
    assert set(registry) == {
        "new_product_workbench_miaoshou_commit",
        "miaoshou_tiktok_publish",
        "shopee_cnsc_publish",
        "ozon_product_publish",
        "postpublish_promotion",
    }
    assert registry["shopee_cnsc_publish"].preparation_available is True
    assert registry["miaoshou_tiktok_publish"].dispatch_available is True
    promotion = registry["postpublish_promotion"]
    assert promotion.preparation_available is True
    assert promotion.dispatch_available is True
    assert set(promotion.target_labels) == {
        "promotion:tiktok:LH_PH",
        "promotion:tiktok:LH_MY",
        "promotion:tiktok:LH_TH",
        "promotion:tiktok:LH_VN",
        "promotion:shopee:PH",
        "promotion:shopee:MY",
        "promotion:shopee:TH",
        "promotion:shopee:VN",
    }


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
    copy = prepared["approved"]["listing_copy"]
    assert len(copy["approved_master_digest"]) == 64
    assert len(
        prepared["approved"]["approved_source_image_manifest_digest"]
    ) == 64
    assert copy["approved_master_digest"] != copy["approved_copy_digest"]


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


def _shopee_create_facts():
    return {
        "category_id": 101157,
        "attribute_list": [
            {
                "attribute_id": 1,
                "attribute_value_list": [{"value_id": 2}],
            }
        ],
        "brand": {"brand_id": 0, "original_brand_name": "NoBrand"},
        "seller_stock": {"location_id": "CNZ", "stock": 200},
        "original_price_cny": "9.5",
        "condition": "NEW",
        "pre_order": {"days_to_ship": 2},
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


def test_shopee_prepared_payload_rejects_more_than_platform_image_limit():
    command = _shopee_command()
    command["images"] = [
        {
            "position": position,
            "image_url": f"https://assets.example/{position}.jpg",
        }
        for position in range(1, 11)
    ]
    with pytest.raises(subject.OneClickPreparationError, match="images"):
        subject.prepare_shopee_plan_native_first_attempt(command)


@pytest.mark.parametrize(
    ("title_length", "description_length", "allowed"),
    [
        (120, 500, True),
        (120, 3000, True),
        (121, 500, False),
        (120, 499, False),
        (120, 3001, False),
    ],
)
def test_shopee_global_create_copy_boundaries_are_checked_before_write(
    title_length, description_length, allowed
):
    command = _shopee_command()
    command["listing_copy"] = {
        "title": "T" * title_length,
        "description": "D" * description_length,
    }
    command["global_create"] = _shopee_create_facts()
    if allowed:
        prepared = subject.prepare_shopee_plan_native_first_attempt(command)
        assert prepared["approved"]["listing_copy"]["description"] == (
            "D" * description_length
        )
    else:
        with pytest.raises(
            subject.OneClickPreparationError, match="global_create_copy"
        ):
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


def test_shopee_source_lineage_digests_survive_json_and_change_with_image_order():
    command = _shopee_command()
    command["images"] = [
        {
            "position": 1,
            "image_url": "https://assets.example/one.jpg",
        },
        {
            "position": 2,
            "image_url": "https://assets.example/two.jpg",
        },
    ]
    prepared = subject.prepare_shopee_plan_native_first_attempt(command)
    restored = json.loads(json.dumps(prepared, sort_keys=True))
    assert restored["approved"]["listing_copy"][
        "approved_master_digest"
    ] == prepared["approved"]["listing_copy"]["approved_master_digest"]
    swapped = _shopee_command()
    swapped["images"] = [
        {
            "position": 1,
            "image_url": "https://assets.example/two.jpg",
        },
        {
            "position": 2,
            "image_url": "https://assets.example/one.jpg",
        },
    ]
    changed = subject.prepare_shopee_plan_native_first_attempt(swapped)
    assert changed["approved"]["listing_copy"][
        "approved_master_digest"
    ] != prepared["approved"]["listing_copy"]["approved_master_digest"]
    assert changed["approved"][
        "approved_source_image_manifest_digest"
    ] != prepared["approved"]["approved_source_image_manifest_digest"]


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
