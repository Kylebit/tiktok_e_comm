from copy import deepcopy

import pytest

from domains.product_operations import build_approved_publication_snapshot
from modules.shopee.global_v4_executor import (
    ShopeeGlobalV4Error,
    ShopeeGlobalV4Resolver,
    project_shopee_global_v4_command,
)
from shared_platform.product_publication_runner import PublicationPlatformRequest
from shared_platform.product_publication_executors import build_shopee_region_executor
from test_approved_publication_snapshot import _approved_plan
from unittest.mock import patch


def _request() -> PublicationPlatformRequest:
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    return PublicationPlatformRequest(
        run_id="run-shopee-v4-1",
        report_id="publication-report:run-shopee-v4-1",
        platform="SHOPEE",
        target_labels=("shopee:PH",),
        snapshot=snapshot,
    )


class _Runtime:
    def __init__(self, *, mapped=None, existing_status="NORMAL"):
        self.mapped = mapped or {}
        self.existing_status = existing_status
        self.calls = []
        self.command = None
        self.image_bindings = {}
        self.global_item_id = "9001"
        self.item_override = None
        self.models_override = None
        self.prepare_override = None
        self.mutate_item = None
        self.mutate_models = None
        self.fail_initialize = False
        self.persisted_images = []
        self.persisted_globals = []
        self.persisted_models = []
        self.retired = []

    def lookup_global_item_ids(self, command):
        self.calls.append("lookup")
        self.command = deepcopy(command)
        return {row["model_sku"]: self.mapped.get(row["model_sku"])
                for row in command["models"]}

    def prepare_creation(self, command):
        self.calls.append("prepare")
        self.command = deepcopy(command)
        if self.prepare_override is not None:
            return deepcopy(self.prepare_override)
        result = {
            "authority": "SHOPEE_OFFICIAL",
            "recommendation_count": 1,
            "category": {
                "id": "101",
                "name": "Wall Stickers",
                "path": [
                    {"id": "10", "name": "Home"},
                    {"id": "101", "name": "Wall Stickers"},
                ],
            },
            "required_attributes": [],
            "missing_required_attributes": [],
            "warehouse": {
                "location_id": "CN-A",
                "display_name": self.command["policy"]["warehouse"]["display_name"],
            },
        }
        return result

    def upload_global_images(self, image_urls):
        self.calls.append("upload")
        self.image_bindings = {
            url: f"image-{index + 1}" for index, url in enumerate(image_urls)
        }
        return deepcopy(self.image_bindings)

    def persist_image_identities(self, request, bindings):
        self.calls.append("persist_images")
        self.persisted_images.append((request.run_id, deepcopy(bindings)))

    def create_global_item(self, payload):
        self.calls.append("create")
        self.created_payload = deepcopy(payload)
        return self.global_item_id

    def persist_global_identity(self, request, global_item_id, models):
        self.calls.append("persist_global")
        self.persisted_globals.append(
            (request.run_id, str(global_item_id), deepcopy(models))
        )

    def initialize_global_models(self, global_item_id, payload):
        self.calls.append("initialize")
        self.initialized_payload = deepcopy(payload)
        if self.fail_initialize:
            raise RuntimeError("simulated model write failure")
        result = {
            row["model_sku"]: str(9101 + index)
            for index, row in enumerate(payload["models"])
        }
        return result

    def persist_global_model_identities(self, request, global_item_id, identities):
        self.calls.append("persist_models")
        self.persisted_models.append(
            (request.run_id, str(global_item_id), deepcopy(identities))
        )

    def retire_global_identity(self, request, global_item_id, model_skus, reason):
        self.calls.append("retire")
        self.retired.append(
            (request.run_id, str(global_item_id), tuple(model_skus), reason)
        )

    def read_global_item(self, global_item_id):
        self.calls.append("read_item")
        if self.item_override is not None:
            return deepcopy(self.item_override)
        command = self.command
        approved_images = list(command["product"]["images"])
        for model in command["models"]:
            if model["variant_image_url"] not in approved_images:
                approved_images.append(model["variant_image_url"])
        bindings = self.image_bindings or {
            url: f"mapped-{index + 1}" for index, url in enumerate(approved_images)
        }
        status = self.existing_status if str(global_item_id) != self.global_item_id else "NORMAL"
        result = {
            "global_item_id": str(global_item_id),
            "status": status,
            "title": command["product"]["title"],
            "description": command["product"]["description"],
            "image_urls": list(command["product"]["images"]),
            "image_ids": [bindings[url] for url in command["product"]["images"]],
            "approved_image_bindings": deepcopy(bindings),
            "parcel": deepcopy(command["parcel"]),
        }
        if self.mutate_item is not None:
            self.mutate_item(result)
        return result

    def read_global_models(self, global_item_id):
        self.calls.append("read_models")
        if self.models_override is not None:
            return deepcopy(self.models_override)
        approved_images = list(self.command["product"]["images"])
        for model in self.command["models"]:
            if model["variant_image_url"] not in approved_images:
                approved_images.append(model["variant_image_url"])
        bindings = self.image_bindings or {
            url: f"mapped-{index + 1}" for index, url in enumerate(approved_images)
        }
        result = {
            "variation_names": list(self.command["variation_names"]),
            "models": [
                {
                    "model_sku": row["model_sku"],
                    "option_values": list(row["option_values"]),
                    "price_cny": row["price_cny"],
                    "variant_image_url": row["variant_image_url"],
                    "variant_image_id": bindings[row["variant_image_url"]],
                    "status": "NORMAL",
                }
                for index, row in enumerate(self.command["models"])
            ],
        }
        if self.mutate_models is not None:
            self.mutate_models(result)
        return result


def test_red_no_mapping_creates_complete_multisku_master_and_verifies_readback():
    request = _request()
    runtime = _Runtime()
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)

    global_item_id = resolver(request)

    assert global_item_id == "9001"
    assert runtime.calls == [
        "lookup",
        "prepare",
        "upload",
        "persist_images",
        "create",
        "persist_global",
        "initialize",
        "persist_models",
        "read_item",
        "read_models",
    ]
    assert [row["model_sku"] for row in runtime.initialized_payload["models"]] == [
        "0958",
        "0959",
    ]
    assert [row["price_cny"] for row in runtime.initialized_payload["models"]] == [
        "40.12",
        "41.25",
    ]
    assert all(row["variant_image_id"] for row in runtime.initialized_payload["models"])
    assert runtime.created_payload["parcel"] == {
        "weight_kg": "0.21",
        "package_cm": ["38", "45", "0.2"],
    }
    assert [row["model_sku"] for row in runtime.created_payload["models"]] == [
        "0958",
        "0959",
    ]
    assert runtime.persisted_images
    assert runtime.persisted_globals == [
        ("run-shopee-v4-1", "9001", ["0958", "0959"])
    ]
    assert runtime.persisted_models == [
        (
            "run-shopee-v4-1",
            "9001",
            {"0958": "9101", "0959": "9102"},
        )
    ]
    assert resolver.write_count(request) == 3


def test_existing_exact_normal_mapping_is_read_before_any_write():
    request = _request()
    runtime = _Runtime(mapped={"0958": "8001", "0959": "8001"})
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)

    assert resolver(request) == "8001"
    assert runtime.calls == ["lookup", "read_item", "read_models"]
    assert resolver.write_count(request) == 0


def test_provider_cdn_urls_do_not_override_exact_uploaded_image_id_lineage():
    request = _request()
    runtime = _Runtime(mapped={"0958": "8001", "0959": "8001"})
    runtime.mutate_item = lambda row: row.update(
        image_urls=[
            f"https://provider-cdn.example/{index}.jpg"
            for index, _value in enumerate(row["image_urls"], start=1)
        ]
    )
    runtime.mutate_models = lambda row: [
        model.update(
            variant_image_url=f"https://provider-cdn.example/variant-{index}.jpg"
        )
        for index, model in enumerate(row["models"], start=1)
    ]

    assert ShopeeGlobalV4Resolver(runtime=runtime)(request) == "8001"
    assert runtime.calls == ["lookup", "read_item", "read_models"]


def test_deleted_exact_mapping_is_retired_before_safe_rebuild():
    request = _request()
    runtime = _Runtime(
        mapped={"0958": "8001", "0959": "8001"},
        existing_status="DELETED",
    )
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)

    assert resolver(request) == "9001"
    assert runtime.calls[:4] == ["lookup", "read_item", "retire", "prepare"]
    assert runtime.retired == [
        (
            "run-shopee-v4-1",
            "8001",
            ("0958", "0959"),
            "SHOPEE_OFFICIAL_DELETED",
        )
    ]
    assert resolver.write_count(request) == 3


def test_global_identity_survives_failure_after_provider_accepts_create():
    request = _request()
    runtime = _Runtime()
    runtime.fail_initialize = True
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)

    with pytest.raises(RuntimeError, match="model write failure"):
        resolver(request)

    assert runtime.persisted_globals == [
        ("run-shopee-v4-1", "9001", ["0958", "0959"])
    ]
    assert resolver.write_count(request) is None


def test_missing_model_in_official_readback_never_verifies():
    request = _request()
    runtime = _Runtime()
    runtime.models_override = {
        "variation_names": ["color", "size"],
        "models": [
            {
                "model_sku": "0958",
                "option_values": ["Blue", "38x45cm"],
                "price_cny": "40.12",
                "variant_image_id": "image-1",
                "status": "NORMAL",
            }
        ],
    }
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)

    with pytest.raises(ShopeeGlobalV4Error, match="SKU coverage"):
        resolver(request)

    assert resolver.write_count(request) == 3


@pytest.mark.parametrize(
    "surface,mutate,error",
    [
        ("item", lambda row: row.update(title="Drifted"), "title or description"),
        ("item", lambda row: row.update(description="Drifted"), "title or description"),
        ("item", lambda row: row.update(image_ids=["wrong-image-id"]), "image"),
        ("item", lambda row: row["parcel"].update(weight_kg="9"), "parcel"),
        ("models", lambda row: row["models"][0].update(price_cny="99"), "price"),
        ("models", lambda row: row["models"][0].update(variant_image_id=""), "variant image"),
    ],
)
def test_any_official_master_fact_drift_prevents_verification(surface, mutate, error):
    request = _request()
    runtime = _Runtime()
    if surface == "item":
        runtime.mutate_item = mutate
    else:
        runtime.mutate_models = mutate
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)

    with pytest.raises(ShopeeGlobalV4Error, match=error):
        resolver(request)

    assert resolver.write_count(request) == 3


def test_partial_local_mapping_is_never_reused_or_duplicated():
    request = _request()
    runtime = _Runtime(mapped={"0958": "8001"})
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)

    with pytest.raises(ShopeeGlobalV4Error, match="mapping is partial"):
        resolver(request)

    assert runtime.calls == ["lookup"]
    assert resolver.write_count(request) == 0


@pytest.mark.parametrize(
    "prepare,error",
    [
        (
            {
                "authority": "SHOPEE_OFFICIAL",
                "recommendation_count": 2,
                "category": {"id": "101", "name": "A", "path": []},
                "required_attributes": [],
                "missing_required_attributes": [],
                "warehouse": {"location_id": "CN-A", "display_name": "China"},
            },
            "category recommendation",
        ),
        (
            {
                "authority": "SHOPEE_OFFICIAL",
                "recommendation_count": 1,
                "category": {"id": "101", "name": "A", "path": [{"id": "101", "name": "A"}]},
                "required_attributes": [],
                "missing_required_attributes": [{"attribute_id": 501}],
                "warehouse": {"location_id": "CN-A", "display_name": "China"},
            },
            "required attributes",
        ),
        (
            {
                "authority": "SHOPEE_OFFICIAL",
                "recommendation_count": 1,
                "category": {"id": "101", "name": "A", "path": [{"id": "101", "name": "A"}]},
                "required_attributes": [],
                "missing_required_attributes": [],
                "warehouse": {"location_id": "", "display_name": "China"},
            },
            "warehouse",
        ),
    ],
)
def test_unconfirmed_official_creation_facts_fail_before_any_provider_write(prepare, error):
    request = _request()
    runtime = _Runtime()
    runtime.prepare_override = prepare
    resolver = ShopeeGlobalV4Resolver(runtime=runtime)

    with pytest.raises(ShopeeGlobalV4Error, match=error):
        resolver(request)

    assert runtime.calls == ["lookup", "prepare"]
    assert resolver.write_count(request) == 0


def test_projection_is_derived_only_from_the_frozen_v4_snapshot():
    request = _request()

    command = project_shopee_global_v4_command(request.snapshot)

    assert command["snapshot_digest"] == request.snapshot["snapshot_digest"]
    assert command["master_schema_version"] == "shopee-global-master/v1"
    assert command["product"] == {
        "title": "Bear Peekaboo PVC Wall Sticker",
        "description": "Removable waterproof wall sticker for nursery decor.",
        "images": [
            "https://img.example/main-1.jpg",
            "https://img.example/main-2.jpg",
        ],
    }
    assert [row["variant_image_url"] for row in command["models"]] == [
        "https://img.example/main-1.jpg",
        "https://img.example/main-2.jpg",
    ]
    assert command["main_category"] == request.snapshot["product"]["main_category"]
    assert command["price_source"] == request.snapshot["shopee_global_master"][
        "price_source"
    ]


def test_region_executor_preserves_global_master_writes_when_resolution_fails():
    request = _request()

    class FailingResolver:
        def __call__(self, _request):
            raise RuntimeError("readback mismatch")

        def write_count(self, _request):
            return 3

    result = build_shopee_region_executor(
        global_item_id_resolver=FailingResolver(),
        runtime=object(),
    )(request)

    assert result["external_write_count"] == 3
    assert result["dispatch_attempted"] is True
    assert result["readback_completed"] is False
    assert result["targets"] == [
        {"target_label": "shopee:PH", "status": "FAILED"}
    ]


def test_region_executor_adds_global_master_and_regional_write_counts():
    request = _request()

    class Resolver:
        def __call__(self, _request):
            return "9001"

        def write_count(self, _request):
            return 3

    dispatch = {
        "targets": [
            {
                "target_label": "shopee:PH",
                "attempted": True,
                "accepted": True,
                "outcome": "ACCEPTED",
            }
        ]
    }
    readback = {
        "targets": [{"target_label": "shopee:PH", "outcome": "PUBLISHED"}]
    }
    with (
        patch(
            "shared_platform.product_publication_executors.dispatch_selected_regions",
            return_value=dispatch,
        ),
        patch(
            "shared_platform.product_publication_executors.readback_dispatched_regions",
            return_value=readback,
        ),
    ):
        result = build_shopee_region_executor(
            global_item_id_resolver=Resolver(),
            runtime=object(),
        )(request)

    assert result["external_write_count"] == 4
    assert result["targets"] == [
        {"target_label": "shopee:PH", "status": "PUBLISHED"}
    ]
