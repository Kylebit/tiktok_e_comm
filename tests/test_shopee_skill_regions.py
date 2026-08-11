from __future__ import annotations

from copy import deepcopy

from modules.shopee.skill_regions import (
    RegionContext,
    dispatch_selected_regions,
    readback_dispatched_regions,
    selected_region_targets,
)


def _snapshot(*targets: str) -> dict:
    return {
        "schema_version": "approved-publication-snapshot/v4",
        "product": {
            "title": "Approved regional title",
            "description": "Approved regional description",
            "images": ["https://example.invalid/image"],
        },
        "publication_targets": [
            {
                "target_label": target,
                "platform": "shopee",
                "site": target.split(":", 1)[1],
                "store": "primary",
            }
            for target in targets
        ],
        "skus": [
            {
                "seller_sku": "0967",
                "model_sku": "0967",
                "parcel": {
                    "weight_kg": "0.30",
                    "package_cm": ["20", "15", "4"],
                },
                "prices": {
                    "shopee:PH": {"amount": "90.50", "currency": "PHP", "global_original_price_cny": "10.00"},
                    "shopee:MY": {"amount": "7.10", "currency": "MYR", "global_original_price_cny": "10.00"},
                    "shopee:TH": {"amount": "55.00", "currency": "THB", "global_original_price_cny": "10.00"},
                    "shopee:VN": {"amount": "41000", "currency": "VND", "global_original_price_cny": "10.00"},
                },
            },
            {
                "seller_sku": "0967",
                "model_sku": "0968",
                "parcel": {
                    "weight_kg": "0.60",
                    "package_cm": ["30", "18", "6"],
                },
                "prices": {
                    "shopee:PH": {"amount": "120.75", "currency": "PHP", "global_original_price_cny": "12.00"},
                    "shopee:MY": {"amount": "9.20", "currency": "MYR", "global_original_price_cny": "12.00"},
                    "shopee:TH": {"amount": "72.00", "currency": "THB", "global_original_price_cny": "12.00"},
                    "shopee:VN": {"amount": "52000", "currency": "VND", "global_original_price_cny": "12.00"},
                },
            },
        ],
    }


class FakeRuntime:
    def __init__(self) -> None:
        self.context_calls: list[str] = []
        self.create_calls: list[tuple[str, dict]] = []
        self.records: list[dict] = []
        self.create_failures: set[str] = set()
        self.task_status: dict[str, str] = {}
        self.missing_items: set[str] = set()
        self.disabled_logistics: set[str] = set()
        self.bad_tiers: set[str] = set()
        self.list_calls: list[tuple[str, str]] = []
        self.listed_regions: set[str] = set()
        self.blank_parent_sku: set[str] = set()
        self.bad_copy: set[str] = set()
        self.list_failures: set[str] = set()
        self.existing_items: dict[str, str] = {}
        self.logistic_enable_calls: list[tuple[str, str]] = []
        self.logistic_enable_failures: set[str] = set()
        self.provider_added_logistics: set[str] = set()

    def context(self, region: str) -> RegionContext:
        self.context_calls.append(region)
        return RegionContext(
            region=region,
            shop_id={"PH": 101, "MY": 102, "TH": 103, "VN": 104}[region],
            merchant_id=500,
            shop_token="secret-shop-token",
            merchant_token="secret-merchant-token",
        )

    def global_models(self, _context, _global_item_id):
        return [
            {"global_model_sku": "0967", "tier_index": [0]},
            {"global_model_sku": "0968", "tier_index": [1]},
        ]

    def compatible_logistics(self, _context, *, weight_kg, dimensions_cm):
        assert weight_kg == 0.6
        assert dimensions_cm == (30.0, 18.0, 6.0)
        return [2002, 2001]

    def create_publish_task(self, context, body):
        self.create_calls.append((context.region, deepcopy(dict(body))))
        if context.region in self.create_failures:
            raise TimeoutError("ambiguous transport outcome")
        return {"error": "", "response": {"publish_task_id": context.shop_id + 9000}}

    def existing_regional_item(self, context, _global_item_id):
        return self.existing_items.get(context.region)

    def publish_task_result(self, context, task_id):
        status = self.task_status.get(context.region, "success")
        return {
            "error": "",
            "response": {
                "publish_status": status,
                "item_id": str(context.shop_id + 8000) if status == "success" else None,
            },
        }

    def regional_item(self, context, item_id):
        if context.region in self.missing_items:
            return None
        return {
            "item_id": item_id,
            "item_status": (
                "NORMAL" if context.region in self.listed_regions else "UNLIST"
            ),
            "item_sku": "" if context.region in self.blank_parent_sku else "0967",
            "has_model": True,
            "item_name": (
                "Drifted title"
                if context.region in self.bad_copy
                else "Approved regional title"
            ),
            "description": "Approved regional description",
            "image": {"image_url_list": ["https://example.invalid/image"]},
            "logistic_info": [
                {
                    "logistic_id": 2001,
                    "enabled": context.region not in self.disabled_logistics,
                },
                {"logistic_id": 2002, "enabled": True},
            ]
            + (
                [{"logistic_id": 9999, "enabled": True}]
                if context.region in self.provider_added_logistics
                else []
            ),
        }

    def regional_models(self, context, _item_id):
        currency = {"PH": "PHP", "MY": "MYR", "TH": "THB", "VN": "VND"}[
            context.region
        ]
        prices = {
            "PH": ("90.50", "120.75"),
            "MY": ("7.10", "9.20"),
            "TH": ("55.00", "72.00"),
            "VN": ("41000", "52000"),
        }[context.region]
        return [
            {
                "model_id": "7001",
                "model_sku": "0967",
                "tier_index": [9] if context.region in self.bad_tiers else [0],
                "price_info": [{"currency": currency, "original_price": prices[0]}],
            },
            {
                "model_id": "7002",
                "model_sku": "0968",
                "tier_index": [1],
                "price_info": [{"currency": currency, "original_price": prices[1]}],
            },
        ]

    def resolved_global_item_id(self, _context, _item_id):
        return "60000001"

    def list_item(self, context, item_id):
        self.list_calls.append((context.region, str(item_id)))
        if context.region in self.list_failures:
            self.listed_regions.add(context.region)
            raise TimeoutError("listing response lost")
        self.listed_regions.add(context.region)

    def enable_applicable_logistics(self, context, item_id):
        self.logistic_enable_calls.append((context.region, str(item_id)))
        attempted = 1 if context.region in self.disabled_logistics else 0
        if context.region not in self.logistic_enable_failures:
            self.disabled_logistics.discard(context.region)
        return {"external_write_count": attempted}

    def record_verified_item(self, **facts):
        self.records.append(dict(facts))


def test_global_only_never_dispatches_or_prefills_regions() -> None:
    runtime = FakeRuntime()
    snapshot = _snapshot("shopee:GLOBAL")

    assert selected_region_targets(snapshot) == []
    result = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    assert result["target_count"] == 0
    assert result["targets"] == []
    assert runtime.context_calls == []
    assert runtime.create_calls == []
    assert runtime.records == []


def test_each_selected_region_is_independent_and_uses_complete_unlisted_body() -> None:
    runtime = FakeRuntime()
    runtime.create_failures.add("PH")

    result = dispatch_selected_regions(
        _snapshot("shopee:PH", "shopee:MY"),
        global_item_id="60000001",
        runtime=runtime,
    )

    assert [row["target_label"] for row in result["targets"]] == [
        "shopee:PH",
        "shopee:MY",
    ]
    assert [row["outcome"] for row in result["targets"]] == [
        "UNKNOWN",
        "ACCEPTED",
    ]
    assert [region for region, _body in runtime.create_calls] == ["PH", "MY"]
    my_body = runtime.create_calls[1][1]
    assert my_body == {
        "global_item_id": 60000001,
        "shop_id": 102,
        "shop_region": "MY",
        "item": {
            "item_name": "Approved regional title",
            "description": "Approved regional description",
            "item_status": "UNLIST",
            "item_sku": "0967",
            "original_price": 7.1,
            "logistic": [
                {"logistic_id": 2001, "enabled": True},
                {"logistic_id": 2002, "enabled": True},
            ],
            "model": [
                {"tier_index": [0], "original_price": 7.1},
                {"tier_index": [1], "original_price": 9.2},
            ],
        },
    }
    assert "TH" not in runtime.context_calls
    assert "VN" not in runtime.context_calls
    assert runtime.records == []


def test_provider_requires_unlisted_create_before_separate_listing() -> None:
    runtime = FakeRuntime()

    result = dispatch_selected_regions(
        _snapshot("shopee:PH"),
        global_item_id="60000001",
        runtime=runtime,
    )

    assert result["targets"][0]["outcome"] == "ACCEPTED"
    body = runtime.create_calls[0][1]
    assert body["item"]["item_status"] == "UNLIST"
    assert body["item"]["item_name"] == "Approved regional title"
    assert body["item"]["description"] == "Approved regional description"
    assert body["item"]["item_sku"] == "0967"
    assert type(body["item"]["original_price"]) in {int, float}
    assert all(
        type(row["original_price"]) in {int, float}
        for row in body["item"]["model"]
    )


def test_verified_existing_region_is_read_back_without_duplicate_create() -> None:
    runtime = FakeRuntime()
    runtime.existing_items["PH"] = "8101"
    runtime.listed_regions.add("PH")
    snapshot = _snapshot("shopee:PH")

    dispatch = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )
    result = readback_dispatched_regions(
        snapshot,
        dispatch,
        global_item_id="60000001",
        runtime=runtime,
    )

    assert runtime.create_calls == []
    assert dispatch["targets"][0]["existing_item_id"] == "8101"
    assert result["targets"][0]["outcome"] == "PUBLISHED"


def test_v4_region_without_per_sku_global_cny_lineage_fails_before_dispatch() -> None:
    runtime = FakeRuntime()
    snapshot = _snapshot("shopee:PH")
    del snapshot["skus"][1]["prices"]["shopee:PH"][
        "global_original_price_cny"
    ]

    result = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    assert result["targets"][0]["outcome"] == "NOT_ATTEMPTED"
    assert "CNSC CNY price lineage" in result["targets"][0]["message"]
    assert runtime.create_calls == []


def test_multisku_region_without_each_local_price_fails_before_dispatch() -> None:
    runtime = FakeRuntime()
    snapshot = {
        "platforms": {
            "shopee": {"selected": True, "targets": ["shopee:PH"]}
        },
        "skus": [
            {
                "seller_sku": "0967",
                "model_sku": "0967",
                "weight_kg": 0.3,
                "package_cm": [20, 15, 4],
            },
            {
                "seller_sku": "0967",
                "model_sku": "0968",
                "weight_kg": 0.6,
                "package_cm": [30, 18, 6],
            },
        ],
        "prices": {
            "shopee:PH": {
                "currency": "PHP",
                "sku_prices": {
                    "0967": {"list_price": "10.00", "currency": "PHP"},
                    "0968": {"list_price": "12.00", "currency": "PHP"},
                },
            }
        },
    }

    result = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    assert result["targets"][0]["outcome"] == "NOT_ATTEMPTED"
    assert "exact PHP model prices" in result["targets"][0]["message"]
    assert runtime.create_calls == []


def test_only_officially_verified_region_is_recorded() -> None:
    runtime = FakeRuntime()
    snapshot = _snapshot("shopee:PH", "shopee:MY")
    dispatch = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )
    runtime.task_status["MY"] = "failed"

    result = readback_dispatched_regions(
        snapshot,
        dispatch,
        global_item_id="60000001",
        runtime=runtime,
    )

    assert [row["outcome"] for row in result["targets"]] == [
        "PUBLISHED",
        "FAILED",
    ]
    assert result["verified_target_count"] == 1
    assert runtime.list_calls == [("PH", "8101")]
    assert runtime.records == [
        {
            "global_item_id": "60000001",
            "region": "PH",
            "shop_id": 101,
            "item_id": "8101",
            "model_id": "7001",
        }
    ]


def test_missing_official_item_never_records_region() -> None:
    runtime = FakeRuntime()
    runtime.missing_items.add("PH")
    snapshot = _snapshot("shopee:PH")
    dispatch = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    result = readback_dispatched_regions(
        snapshot,
        dispatch,
        global_item_id="60000001",
        runtime=runtime,
    )

    assert result["targets"][0]["outcome"] == "MISMATCH"
    assert result["targets"][0]["verified"] is False
    assert runtime.records == []


def test_modeled_item_allows_blank_parent_sku_after_exact_model_readback() -> None:
    runtime = FakeRuntime()
    runtime.blank_parent_sku.add("PH")
    snapshot = _snapshot("shopee:PH")
    dispatch = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    result = readback_dispatched_regions(
        snapshot,
        dispatch,
        global_item_id="60000001",
        runtime=runtime,
    )

    assert result["targets"][0]["outcome"] == "PUBLISHED"
    assert result["targets"][0]["checks"]["item_sku_exact"] is True


def test_copy_drift_is_not_published_or_recorded() -> None:
    runtime = FakeRuntime()
    runtime.bad_copy.add("PH")
    snapshot = _snapshot("shopee:PH")
    dispatch = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    result = readback_dispatched_regions(
        snapshot,
        dispatch,
        global_item_id="60000001",
        runtime=runtime,
    )

    assert result["targets"][0]["outcome"] == "MISMATCH"
    assert result["targets"][0]["checks"]["copy_exact"] is False
    assert runtime.records == []


def test_lost_listing_response_uses_authoritative_second_readback() -> None:
    runtime = FakeRuntime()
    runtime.list_failures.add("PH")
    snapshot = _snapshot("shopee:PH")
    dispatch = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    result = readback_dispatched_regions(
        snapshot,
        dispatch,
        global_item_id="60000001",
        runtime=runtime,
    )

    row = result["targets"][0]
    assert row["outcome"] == "PUBLISHED"
    assert row["listing_attempted"] is True
    assert row["listing_error_type"] == "TimeoutError"


def test_disabled_applicable_logistic_is_enabled_then_read_back() -> None:
    runtime = FakeRuntime()
    runtime.disabled_logistics.add("PH")
    snapshot = _snapshot("shopee:PH")
    dispatch = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    result = readback_dispatched_regions(
        snapshot,
        dispatch,
        global_item_id="60000001",
        runtime=runtime,
    )

    row = result["targets"][0]
    assert runtime.logistic_enable_calls == [("PH", "8101")]
    assert row["outcome"] == "PUBLISHED"
    assert row["checks"]["applicable_logistics_enabled"] is True
    assert row["external_write_count"] == 2


def test_provider_added_enabled_logistic_is_not_a_content_mismatch() -> None:
    runtime = FakeRuntime()
    runtime.provider_added_logistics.add("PH")
    snapshot = _snapshot("shopee:PH")
    dispatch = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    result = readback_dispatched_regions(
        snapshot,
        dispatch,
        global_item_id="60000001",
        runtime=runtime,
    )

    assert result["targets"][0]["outcome"] == "PUBLISHED"
    assert result["targets"][0]["checks"]["applicable_logistics_enabled"] is True


def test_tier_or_requested_logistics_drift_never_records_region() -> None:
    runtime = FakeRuntime()
    runtime.bad_tiers.add("PH")
    runtime.disabled_logistics.add("PH")
    runtime.logistic_enable_failures.add("PH")
    snapshot = _snapshot("shopee:PH")
    dispatch = dispatch_selected_regions(
        snapshot,
        global_item_id="60000001",
        runtime=runtime,
    )

    result = readback_dispatched_regions(
        snapshot,
        dispatch,
        global_item_id="60000001",
        runtime=runtime,
    )

    checks = result["targets"][0]["checks"]
    assert checks["model_tiers_exact"] is False
    assert checks["applicable_logistics_enabled"] is False
    assert result["targets"][0]["outcome"] == "MISMATCH"
    assert runtime.records == []
