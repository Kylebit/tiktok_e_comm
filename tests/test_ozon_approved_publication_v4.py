from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from modules.ozon.approved_publication_v4 import (
    OzonDispatchFact,
    build_ozon_v4_executor,
    execute_ozon_v4_publication,
)


def _snapshot() -> dict:
    return {
        "schema_version": "approved-publication-snapshot/v4",
        "snapshot_digest": "sha256:" + "a" * 64,
        "offer_id": "3882722296",
        "product_revision": 40,
        "plan_id": "release-plan:3882722296:r40",
        "publication_targets": [
            {
                "target_label": "ozon:RU",
                "platform": "ozon",
                "site": "RU",
                "store": "RU",
            }
        ],
        "product": {
            "title": "Decorative Resin Fridge Magnet, Floral Countryside Style",
            "description": "A factual approved resin fridge-magnet description.",
            "images": [
                "https://images.example/product-1.jpg",
                "https://images.example/product-2.jpg",
            ],
            "main_category": {
                "id": "home-fridge-magnets",
                "name": "Home > Fridge Magnets",
            },
        },
        "categories_by_target": {
            "ozon:RU": {
                "target_label": "ozon:RU",
                "platform": "ozon",
                "site": "RU",
                "store": "RU",
                "category": {
                    "id": "17028913",
                    "name": "Fridge Magnets",
                    "path": [
                        {"id": "14500", "name": "Home"},
                        {"id": "17028913", "name": "Fridge Magnets"},
                    ],
                },
                "decision": {
                    "status": "APPROVED",
                    "decision_digest": "sha256:" + "b" * 64,
                },
            }
        },
        "skus": [
            {
                "variant_key": "floral-red",
                "seller_sku": "0967",
                "model_sku": "0967",
                "specification": {"design": "Floral red", "size": "7 x 7 cm"},
                "cost": {"amount": "4", "currency": "CNY"},
                "parcel": {
                    "weight_kg": "0.1",
                    "package_cm": ["10", "10", "2"],
                },
                "prices": {
                    "ozon:RU": {
                        "amount": "40",
                        "currency": "CNY",
                        "old_price_cny": "52",
                    }
                },
                "variant_images": ["https://images.example/red.jpg"],
            },
            {
                "variant_key": "floral-blue",
                "seller_sku": "0967",
                "model_sku": "0968",
                "specification": {"design": "Floral blue", "size": "8 x 8 cm"},
                "cost": {"amount": "5", "currency": "CNY"},
                "parcel": {
                    "weight_kg": "0.12",
                    "package_cm": ["11", "11", "2.5"],
                },
                "prices": {
                    "ozon:RU": {
                        "amount": "46",
                        "currency": "CNY",
                        "old_price_cny": "60",
                    }
                },
                "variant_images": [
                    "https://images.example/blue-1.jpg",
                    "https://images.example/blue-2.jpg",
                ],
            },
            {
                "variant_key": "floral-green",
                "seller_sku": "0967",
                "model_sku": "0969",
                "specification": {"design": "Floral green", "size": "9 x 9 cm"},
                "cost": {"amount": "6", "currency": "CNY"},
                "parcel": {
                    "weight_kg": "0.15",
                    "package_cm": ["12", "12", "3"],
                },
                "prices": {
                    "ozon:RU": {
                        "amount": "54",
                        "currency": "CNY",
                        "old_price_cny": "70",
                    }
                },
                "variant_images": ["https://images.example/green.jpg"],
            },
        ],
    }


def _published_item(payload: dict, *, item_id: object) -> dict:
    return {
        "offer_id": payload["offer_id"],
        "id": item_id,
        # Legacy lookalikes deliberately disagree and must be ignored.
        "product_id": "legacy-product-id",
        "status": "ERROR",
        "statuses": {
            "is_created": True,
            "status": "PUBLISHED",
            "status_failed": "",
        },
        "name": payload["title"],
        "description": payload["description"],
        "price": payload["price"],
        "old_price": payload["old_price"],
        "images": [f"provider-image-{index}" for index in range(payload["image_count"])],
        "category_id": payload["category"]["id"],
        "type_id": str(payload.get("official_profile", {}).get("type_id") or ""),
        "weight_kg": payload["parcel"]["weight_kg"],
        "package_cm": payload["parcel"]["package_cm"],
    }


def _target(status: str, *, attempted: bool, count: int | None, readback: bool) -> dict:
    stage = "READBACK" if readback else "DISPATCH" if attempted else "PREPARATION"
    return {
        "target_label": "ozon:RU",
        "status": status,
        "evidence": {
            "target_label": "ozon:RU", "status": status, "stage": stage,
            "provider_code": "ozon_preparation_failed" if stage == "PREPARATION" else "ozon_result",
            "provider_reason": "Ozon preparation failed" if stage == "PREPARATION" else "Ozon result classified",
            "request_attempted": attempted, "outcome_unknown": count is None,
            "external_write_count": count,
        },
    }


def test_v4_ozon_dispatch_preserves_every_approved_sku_fact_independently() -> None:
    snapshot = _snapshot()
    submitted: list[dict] = []

    def dispatch(payload: dict) -> OzonDispatchFact:
        submitted.append(deepcopy(payload))
        return OzonDispatchFact(outcome="ACCEPTED", task_id="task-" + payload["offer_id"])

    def readback(offer_ids: tuple[str, ...]) -> list[dict]:
        assert offer_ids == ("0967", "0968", "0969")
        return [
            _published_item(payload, item_id=9000 + index)
            for index, payload in enumerate(submitted)
        ]

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        dispatch_variant=dispatch,
        readback_variants=readback,
    )

    assert result == {
        "schema_version": "product-publication-platform-result/v1",
        "platform": "OZON",
        "targets": [_target("PUBLISHED", attempted=True, count=3, readback=True)],
        "dispatch_attempted": True,
        "readback_completed": True,
        "external_write_count": 3,
        "requires_human_action": False,
    }
    assert [row["offer_id"] for row in submitted] == ["0967", "0968", "0969"]
    assert [row["price"] for row in submitted] == ["40", "46", "54"]
    assert [row["old_price"] for row in submitted] == ["52", "60", "70"]
    assert [row["parcel"] for row in submitted] == [
        {"weight_kg": "0.1", "package_cm": ["10", "10", "2"]},
        {"weight_kg": "0.12", "package_cm": ["11", "11", "2.5"]},
        {"weight_kg": "0.15", "package_cm": ["12", "12", "3"]},
    ]
    assert [row["images"] for row in submitted] == [
        ["https://images.example/red.jpg"],
        ["https://images.example/blue-1.jpg", "https://images.example/blue-2.jpg"],
        ["https://images.example/green.jpg"],
    ]
    assert all(row["category"]["id"] == "17028913" for row in submitted)
    assert submitted[1]["specification"] == {
        "design": "Floral blue",
        "size": "8 x 8 cm",
    }


def test_missing_ozon_old_price_lineage_fails_before_any_provider_call() -> None:
    snapshot = _snapshot()
    del snapshot["skus"][1]["prices"]["ozon:RU"]["old_price_cny"]
    calls: list[str] = []

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        dispatch_variant=lambda _payload: calls.append("dispatch"),
        readback_variants=lambda _ids: calls.append("readback"),
    )

    assert calls == []
    assert result["targets"] == [
        _target("FAILED", attempted=False, count=0, readback=False)
    ]
    assert result["dispatch_attempted"] is False
    assert result["readback_completed"] is False
    assert result["external_write_count"] == 0
    assert result["requires_human_action"] is True


def test_dispatch_exception_does_not_skip_readback_and_never_becomes_success() -> None:
    snapshot = _snapshot()
    submitted: list[dict] = []
    readback_calls: list[tuple[str, ...]] = []

    def dispatch(payload: dict) -> OzonDispatchFact:
        submitted.append(payload)
        if payload["offer_id"] == "0968":
            raise RuntimeError("transport outcome unknown")
        return OzonDispatchFact(outcome="ACCEPTED", task_id="task-" + payload["offer_id"])

    def readback(offer_ids: tuple[str, ...]) -> list[dict]:
        readback_calls.append(offer_ids)
        # The ambiguous variant is missing; accepted variants are still visible.
        return [
            _published_item(payload, item_id="item-" + payload["offer_id"])
            for payload in submitted
            if payload["offer_id"] != "0968"
        ]

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        dispatch_variant=dispatch,
        readback_variants=readback,
    )

    assert [row["offer_id"] for row in submitted] == ["0967", "0968", "0969"]
    assert readback_calls == [("0967", "0968", "0969")]
    assert result["targets"] == [
        _target("PROCESSING", attempted=True, count=None, readback=True)
    ]
    assert result["external_write_count"] is None
    assert result["requires_human_action"] is False


def test_imported_and_offer_validated_are_processing_not_published() -> None:
    snapshot = _snapshot()
    submitted: list[dict] = []

    def dispatch(payload: dict) -> OzonDispatchFact:
        submitted.append(payload)
        return OzonDispatchFact(outcome="ACCEPTED", task_id="task-" + payload["offer_id"])

    def readback(_offer_ids: tuple[str, ...]) -> list[dict]:
        states = ("IMPORTED", "OFFER_VALIDATED", "PROCESSING")
        return [
            {
                **_published_item(payload, item_id="item-" + payload["offer_id"]),
                "statuses": {
                    "is_created": False,
                    "status": states[index],
                    "status_failed": "",
                },
            }
            for index, payload in enumerate(submitted)
        ]

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        dispatch_variant=dispatch,
        readback_variants=readback,
    )

    assert result["targets"] == [
        _target("PROCESSING", attempted=True, count=3, readback=True)
    ]
    assert result["requires_human_action"] is False


def test_accepted_update_with_stale_created_readback_is_processing_not_failed() -> None:
    snapshot = _snapshot()
    submitted: list[dict] = []

    def dispatch(payload: dict) -> OzonDispatchFact:
        submitted.append(payload)
        return OzonDispatchFact(outcome="ACCEPTED", task_id="task-" + payload["offer_id"])

    def readback(_offer_ids: tuple[str, ...]) -> list[dict]:
        rows = [
            _published_item(payload, item_id="item-" + payload["offer_id"])
            for payload in submitted
        ]
        # Ozon has accepted the update but its list/read models still expose
        # the prior stored copy during asynchronous processing.
        rows[0]["name"] = "Previous provider title"
        return rows

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        dispatch_variant=dispatch,
        readback_variants=readback,
    )

    assert result["targets"] == [
        _target("PROCESSING", attempted=True, count=3, readback=True)
    ]
    assert result["requires_human_action"] is False


def test_created_item_mismatch_or_ambiguous_identity_is_never_whole_product_success() -> None:
    snapshot = _snapshot()
    submitted: list[dict] = []

    def dispatch(payload: dict) -> OzonDispatchFact:
        submitted.append(payload)
        return OzonDispatchFact(outcome="ACCEPTED", task_id="task-" + payload["offer_id"])

    def readback(_offer_ids: tuple[str, ...]) -> list[dict]:
        first = _published_item(submitted[0], item_id="item-0967")
        second = _published_item(submitted[1], item_id="item-0968")
        second["price"] = "999"
        third = _published_item(submitted[2], item_id="")
        third["product_id"] = "legacy-id-must-not-count"
        return [first, second, third]

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        dispatch_variant=dispatch,
        readback_variants=readback,
    )

    assert result["targets"] == [
        _target("FAILED", attempted=True, count=3, readback=True)
    ]
    assert result["readback_completed"] is True
    assert result["requires_human_action"] is True


def test_ozon_v4_boundary_has_no_other_platform_runtime_dependency() -> None:
    source = __import__(
        "inspect"
    ).getsource(__import__("modules.ozon.approved_publication_v4", fromlist=["*"]))
    assert "modules.tiktok" not in source
    assert "modules.shopee" not in source
    assert "miaoshou" not in source.casefold()


def test_runner_executor_uses_only_the_detached_request_snapshot_and_scope() -> None:
    snapshot = _snapshot()
    submitted: list[dict] = []

    def dispatch(payload: dict) -> OzonDispatchFact:
        submitted.append(payload)
        return OzonDispatchFact(outcome="ACCEPTED", task_id="task-" + payload["offer_id"])

    executor = build_ozon_v4_executor(
        dispatch_variant=dispatch,
        readback_variants=lambda _ids: [
            _published_item(payload, item_id="item-" + payload["offer_id"])
            for payload in submitted
        ],
    )

    result = executor(
        SimpleNamespace(
            platform="OZON",
            target_labels=("ozon:RU",),
            snapshot=snapshot,
        )
    )

    assert result["platform"] == "OZON"
    assert result["targets"] == [
        _target("PUBLISHED", attempted=True, count=3, readback=True)
    ]


def test_deferred_ozon_category_uses_one_exact_official_profile_receipt() -> None:
    snapshot = _snapshot()
    snapshot["categories_by_target"]["ozon:RU"]["category"] = None
    snapshot["categories_by_target"]["ozon:RU"]["decision"] = {
        "status": "DEFERRED_TO_SKILL",
        "decision_digest": "sha256:" + "c" * 64,
    }
    submitted: list[dict] = []
    resolver_calls: list[dict] = []

    def resolve_profile(value: dict) -> dict:
        resolver_calls.append(value)
        return {
            "schema_version": "ozon-official-profile-resolution/v1",
            "resolution": "EXACT",
            "description_category_id": 17028743,
            "category_name": "Souvenirs and Gifts",
            "category_path": [
                {"id": "17027901", "name": "House & Garden"},
                {"id": "17028743", "name": "Souvenirs and Gifts"},
            ],
            "type_id": 93785,
            "type_name": "Fridge Magnet",
            "required_attributes": {
                "brand": {
                    "attribute_id": 85,
                    "dictionary_value_id": 126745801,
                    "value": "No Brand",
                },
                "model_name": {"attribute_id": 9048},
                "product_type": {
                    "attribute_id": 8229,
                    "dictionary_value_id": 93785,
                    "value": "Fridge Magnet",
                },
            },
        }

    def dispatch(payload: dict) -> OzonDispatchFact:
        submitted.append(payload)
        return OzonDispatchFact(outcome="ACCEPTED", task_id="task-0967")

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        official_profile_resolver=resolve_profile,
        localized_copy_resolver=lambda value: {
            "schema_version": "ozon-localized-copy/v1",
            "source_snapshot_digest": value["snapshot_digest"],
            "language": "ru",
            "title": "Декоративный магнит на холодильник из смолы, 7 на 7 см",
            "description": "Декоративный магнит из синтетической смолы для холодильника.",
        },
        dispatch_variant=dispatch,
        readback_variants=lambda _ids: [
            _published_item(payload, item_id="item-" + payload["offer_id"])
            for payload in submitted
        ],
    )

    assert result["targets"] == [
        _target("PUBLISHED", attempted=True, count=3, readback=True)
    ]
    assert resolver_calls == [snapshot]
    assert submitted[0]["category"] == {
        "id": "17028743",
        "name": "Souvenirs and Gifts",
        "path": [
            {"id": "17027901", "name": "House & Garden"},
            {"id": "17028743", "name": "Souvenirs and Gifts"},
        ],
    }
    assert submitted[0]["official_profile"]["type_id"] == 93785
    assert submitted[0]["title"] == (
        "Декоративный магнит на холодильник из смолы, 7 на 7 см"
    )
    assert submitted[0]["description"].startswith("Декоративный магнит")


def test_deferred_ozon_category_without_exact_receipt_is_zero_write_failure() -> None:
    snapshot = _snapshot()
    snapshot["categories_by_target"]["ozon:RU"]["category"] = None
    snapshot["categories_by_target"]["ozon:RU"]["decision"]["status"] = (
        "DEFERRED_TO_SKILL"
    )
    calls: list[str] = []

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        official_profile_resolver=lambda _snapshot: {
            "schema_version": "ozon-official-profile-resolution/v1",
            "resolution": "AMBIGUOUS",
        },
        dispatch_variant=lambda _payload: calls.append("dispatch"),
        readback_variants=lambda _ids: calls.append("readback"),
    )

    assert calls == []
    assert result["dispatch_attempted"] is False
    assert result["external_write_count"] == 0
    assert result["targets"] == [_target("FAILED", attempted=False, count=0, readback=False)]


def test_exact_fridge_magnet_profile_without_russian_copy_is_zero_write_failure() -> None:
    snapshot = _snapshot()
    snapshot["categories_by_target"]["ozon:RU"]["category"] = None
    snapshot["categories_by_target"]["ozon:RU"]["decision"]["status"] = (
        "DEFERRED_TO_SKILL"
    )
    calls: list[str] = []

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        official_profile_resolver=lambda _snapshot: {
            "schema_version": "ozon-official-profile-resolution/v1",
            "resolution": "EXACT",
            "description_category_id": 17028743,
            "category_name": "Souvenirs and Gifts",
            "category_path": [
                {"id": "17027901", "name": "House & Garden"},
                {"id": "17028743", "name": "Souvenirs and Gifts"},
            ],
            "type_id": 93785,
            "type_name": "Fridge Magnet",
            "required_attributes": {
                "brand": {
                    "attribute_id": 85,
                    "dictionary_value_id": 126745801,
                    "value": "No Brand",
                },
                "model_name": {"attribute_id": 9048},
                "product_type": {
                    "attribute_id": 8229,
                    "dictionary_value_id": 93785,
                    "value": "Fridge Magnet",
                },
            },
        },
        dispatch_variant=lambda _payload: calls.append("dispatch"),
        readback_variants=lambda _ids: calls.append("readback"),
    )

    assert calls == []
    assert result["dispatch_attempted"] is False
    assert result["external_write_count"] == 0


def test_ozon_localized_title_with_provider_stripped_multiply_sign_is_zero_write() -> None:
    snapshot = _snapshot()
    snapshot["categories_by_target"]["ozon:RU"]["category"] = None
    snapshot["categories_by_target"]["ozon:RU"]["decision"]["status"] = (
        "DEFERRED_TO_SKILL"
    )
    calls: list[str] = []
    profile = {
        "schema_version": "ozon-official-profile-resolution/v1",
        "resolution": "EXACT",
        "description_category_id": 17028743,
        "category_name": "Souvenirs and Gifts",
        "category_path": [
            {"id": "17027901", "name": "House & Garden"},
            {"id": "17028743", "name": "Souvenirs and Gifts"},
        ],
        "type_id": 93785,
        "type_name": "Fridge Magnet",
        "required_attributes": {
            "brand": {
                "attribute_id": 85,
                "dictionary_value_id": 126745801,
                "value": "No Brand",
            },
            "model_name": {"attribute_id": 9048},
            "product_type": {
                "attribute_id": 8229,
                "dictionary_value_id": 93785,
                "value": "Fridge Magnet",
            },
        },
    }

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        official_profile_resolver=lambda _snapshot: profile,
        localized_copy_resolver=lambda value: {
            "schema_version": "ozon-localized-copy/v1",
            "source_snapshot_digest": value["snapshot_digest"],
            "language": "ru",
            "title": "Декоративный магнит, 7 × 7 см",
            "description": "Декоративный магнит на холодильник из синтетической смолы.",
        },
        dispatch_variant=lambda _payload: calls.append("dispatch"),
        readback_variants=lambda _ids: calls.append("readback"),
    )

    assert calls == []
    assert result["dispatch_attempted"] is False
    assert result["external_write_count"] == 0


def test_dispatch_rejection_with_empty_readback_retains_dispatch_evidence() -> None:
    result = execute_ozon_v4_publication(
        _snapshot(),
        target_labels=("ozon:RU",),
        dispatch_variant=lambda _payload: OzonDispatchFact(
            outcome="REJECTED",
            provider_code="ozon_business_rejected",
            provider_reason="Ozon import was rejected",
        ),
        readback_variants=lambda _ids: [],
    )

    assert result["targets"] == [
        {
            "target_label": "ozon:RU",
            "status": "FAILED",
            "evidence": {
                "target_label": "ozon:RU",
                "status": "FAILED",
                "stage": "DISPATCH",
                "provider_code": "ozon_business_rejected",
                "provider_reason": "Ozon import was rejected",
                "request_attempted": True,
                "outcome_unknown": False,
                "external_write_count": 0,
            },
        }
    ]


def _governed_stock_snapshot() -> dict:
    snapshot = _snapshot()
    snapshot["product"]["stock_policy"] = {
        "schema_version": "publication-default-stock/v1",
        "quantity_per_sku": 200,
        "scope": "EACH_SELECTED_SKU",
        "source": "SYSTEM_GOVERNED_DEFAULT",
        "frozen_at_round": "ROUND1",
    }
    return snapshot


def test_ozon_projection_carries_the_round1_reviewed_stock_policy() -> None:
    snapshot = _snapshot()
    snapshot["product"]["stock_policy"] = {
        "schema_version": "publication-default-stock/v1",
        "quantity_per_sku": 200,
        "scope": "EACH_SELECTED_SKU",
        "source": "SYSTEM_GOVERNED_DEFAULT",
        "frozen_at_round": "ROUND1",
    }

    variants = project_ozon_v4_variants(
        snapshot,
        target_labels=("ozon:RU",),
    )

    assert {row["stock_quantity"] for row in variants} == {200}
    assert {row["stock_policy_schema_version"] for row in variants} == {
        "publication-default-stock/v1"
    }
    assert {row["stock_policy_source"] for row in variants} == {
        "SYSTEM_GOVERNED_DEFAULT"
    }


def test_governed_stock_requires_transports_before_any_provider_call() -> None:
    calls: list[str] = []

    result = execute_ozon_v4_publication(
        _governed_stock_snapshot(),
        target_labels=("ozon:RU",),
        dispatch_variant=lambda _payload: calls.append("dispatch"),
        readback_variants=lambda _ids: calls.append("readback"),
    )

    assert calls == []
    assert result["targets"][0]["status"] == "FAILED"
    assert result["targets"][0]["evidence"]["provider_code"] == (
        "ozon_stock_transport_unavailable"
    )
    assert result["external_write_count"] == 0


def test_existing_listing_with_exact_governed_stock_is_zero_write_published() -> None:
    snapshot = _governed_stock_snapshot()
    variants = project_ozon_v4_variants(snapshot, target_labels=("ozon:RU",))
    stock_updates: list[object] = []

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        dispatch_variant=lambda _payload: (_ for _ in ()).throw(
            AssertionError("existing products must not be imported")
        ),
        readback_variants=lambda _ids: [
            _published_item(row, item_id="item-" + row["offer_id"])
            for row in variants
        ],
        update_stocks=lambda rows: stock_updates.append(rows),
        readback_stocks=lambda _ids: [
            {"offer_id": row["offer_id"], "stock": 200} for row in variants
        ],
        read_before_dispatch=True,
    )

    assert stock_updates == []
    assert result["targets"][0]["status"] == "PUBLISHED"
    assert result["targets"][0]["evidence"]["provider_code"] == (
        "ozon_stock_verified"
    )
    assert result["external_write_count"] == 0


def test_existing_listing_stock_mismatch_updates_once_and_requires_exact_readback() -> None:
    snapshot = _governed_stock_snapshot()
    variants = project_ozon_v4_variants(snapshot, target_labels=("ozon:RU",))
    stock_updates: list[tuple[dict, ...]] = []
    stock_reads = iter(
        [
            [{"offer_id": row["offer_id"], "stock": 0} for row in variants],
            [{"offer_id": row["offer_id"], "stock": 200} for row in variants],
        ]
    )

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        dispatch_variant=lambda _payload: (_ for _ in ()).throw(
            AssertionError("existing products must not be imported")
        ),
        readback_variants=lambda _ids: [
            _published_item(row, item_id="item-" + row["offer_id"])
            for row in variants
        ],
        update_stocks=lambda rows: (
            stock_updates.append(rows)
            or OzonStockDispatchFact(outcome="ACCEPTED")
        ),
        readback_stocks=lambda _ids: next(stock_reads),
        read_before_dispatch=True,
    )

    assert len(stock_updates) == 1
    assert [row["stock"] for row in stock_updates[0]] == [200, 200, 200]
    assert result["targets"][0]["status"] == "PUBLISHED"
    assert result["external_write_count"] == 1


def test_governed_stock_unknown_update_is_reconciled_without_retry() -> None:
    snapshot = _governed_stock_snapshot()
    variants = project_ozon_v4_variants(snapshot, target_labels=("ozon:RU",))
    updates: list[tuple[dict, ...]] = []
    stock_reads = iter(
        [
            [{"offer_id": row["offer_id"], "stock": 0} for row in variants],
            [{"offer_id": row["offer_id"], "stock": 200} for row in variants],
        ]
    )

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        dispatch_variant=lambda _payload: (_ for _ in ()).throw(
            AssertionError("existing products must not be imported")
        ),
        readback_variants=lambda _ids: [
            _published_item(row, item_id="item-" + row["offer_id"])
            for row in variants
        ],
        update_stocks=lambda rows: (
            updates.append(rows) or OzonStockDispatchFact(outcome="UNKNOWN")
        ),
        readback_stocks=lambda _ids: next(stock_reads),
        read_before_dispatch=True,
    )

    assert len(updates) == 1
    assert result["targets"][0]["status"] == "PUBLISHED"
    assert result["external_write_count"] is None
    assert result["requires_human_action"] is True


def test_runner_executor_does_not_reimport_existing_terminal_variants() -> None:
    snapshot = _snapshot()
    projected = project_ozon_v4_variants(
        snapshot,
        target_labels=("ozon:RU",),
    )
    existing = [
        {
            **_published_item(variant, item_id="item-" + variant["offer_id"]),
            "statuses": {
                "status": "unmatched",
                "status_failed": "offer_validated",
                "validation_status": "fail",
                "is_created": False,
            },
            "validation_errors": [
                {
                    "code": "ML_INCORRECT_VOLUME_WEIGHT",
                    "field": "weight",
                    "state": "offer_validated",
                }
            ],
        }
        for variant in projected
    ]
    submitted: list[dict] = []
    executor = build_ozon_v4_executor(
        dispatch_variant=lambda payload: submitted.append(payload),
        readback_variants=lambda _ids: deepcopy(existing),
    )

    result = executor(
        SimpleNamespace(
            platform="OZON",
            target_labels=("ozon:RU",),
            snapshot=snapshot,
        )
    )

    assert submitted == []
    assert result["targets"][0]["status"] == "FAILED"
    assert result["targets"][0]["evidence"]["provider_code"] == (
        "ML_INCORRECT_VOLUME_WEIGHT"
    )
    assert result["dispatch_attempted"] is False
    assert result["readback_completed"] is True
    assert result["external_write_count"] == 0


def test_approved_leaf_only_category_path_is_enriched_from_exact_official_tree() -> None:
    snapshot = _snapshot()
    category = snapshot["categories_by_target"]["ozon:RU"]["category"]
    category["path"] = [
        {"id": category["id"], "name": category["name"]}
    ]

    profile = {
        "schema_version": "ozon-official-profile-resolution/v1",
        "resolution": "EXACT",
        "description_category_id": 17028913,
        "category_name": "Fridge Magnets",
        "category_path": [
            {"id": "14500", "name": "Home"},
            {"id": "17028913", "name": "Fridge Magnets"},
        ],
        "type_id": 93785,
        "type_name": "Fridge Magnet",
        "required_attributes": {
            "brand": {
                "attribute_id": 85,
                "dictionary_value_id": 126745801,
                "value": "No Brand",
            },
            "model_name": {"attribute_id": 9048},
            "product_type": {
                "attribute_id": 8229,
                "dictionary_value_id": 93785,
                "value": "Fridge Magnet",
            },
        },
    }

    variants = project_ozon_v4_variants(
        snapshot,
        target_labels=("ozon:RU",),
        official_profile_resolver=lambda _snapshot: profile,
        localized_copy_resolver=lambda value: {
            "schema_version": "ozon-localized-copy/v1",
            "source_snapshot_digest": value["snapshot_digest"],
            "language": "ru",
            "title": "Декоративный магнит на холодильник из смолы",
            "description": "Декоративный магнит из синтетической смолы для холодильника.",
        },
    )

    assert variants[0]["category"]["path"] == profile["category_path"]


def test_localized_approved_leaf_name_can_follow_same_official_category_id() -> None:
    snapshot = _snapshot()
    category = snapshot["categories_by_target"]["ozon:RU"]["category"]
    category["name"] = "Магниты на холодильник"
    category["path"] = [
        {"id": category["id"], "name": category["name"]}
    ]
    profile = {
        "schema_version": "ozon-official-profile-resolution/v1",
        "resolution": "EXACT",
        "description_category_id": 17028913,
        "category_name": "Fridge Magnets",
        "category_path": [
            {"id": "14500", "name": "Home"},
            {"id": "17028913", "name": "Fridge Magnets"},
        ],
        "type_id": 93785,
        "type_name": "Fridge Magnet",
        "required_attributes": {
            "brand": {
                "attribute_id": 85,
                "dictionary_value_id": 126745801,
                "value": "No Brand",
            },
            "model_name": {"attribute_id": 9048},
            "product_type": {
                "attribute_id": 8229,
                "dictionary_value_id": 93785,
                "value": "Fridge Magnet",
            },
        },
    }

    variants = project_ozon_v4_variants(
        snapshot,
        target_labels=("ozon:RU",),
        official_profile_resolver=lambda _snapshot: profile,
        localized_copy_resolver=lambda value: {
            "schema_version": "ozon-localized-copy/v1",
            "source_snapshot_digest": value["snapshot_digest"],
            "language": "ru",
            "title": "Декоративный магнит на холодильник из смолы",
            "description": "Декоративный магнит из синтетической смолы для холодильника.",
        },
    )

    assert variants[0]["category"]["name"] == "Fridge Magnets"
    assert variants[0]["category"]["path"] == profile["category_path"]


def test_approved_category_with_conflicting_ancestor_is_not_enriched() -> None:
    snapshot = _snapshot()
    snapshot["categories_by_target"]["ozon:RU"]["category"]["path"][0] = {
        "id": "99999",
        "name": "Conflicting Parent",
    }
    profile = {
        "schema_version": "ozon-official-profile-resolution/v1",
        "resolution": "EXACT",
        "description_category_id": 17028913,
        "category_name": "Fridge Magnets",
        "category_path": [
            {"id": "14500", "name": "Home"},
            {"id": "17028913", "name": "Fridge Magnets"},
        ],
        "type_id": 93785,
        "type_name": "Fridge Magnet",
        "required_attributes": {
            "brand": {
                "attribute_id": 85,
                "dictionary_value_id": 126745801,
                "value": "No Brand",
            },
            "model_name": {"attribute_id": 9048},
            "product_type": {
                "attribute_id": 8229,
                "dictionary_value_id": 93785,
                "value": "Fridge Magnet",
            },
        },
    }

    result = execute_ozon_v4_publication(
        snapshot,
        target_labels=("ozon:RU",),
        official_profile_resolver=lambda _snapshot: profile,
        localized_copy_resolver=lambda _snapshot: {},
        dispatch_variant=lambda _payload: OzonDispatchFact(
            outcome="ACCEPTED", task_id="must-not-dispatch"
        ),
        readback_variants=lambda _ids: [],
    )

    assert result["dispatch_attempted"] is False
    assert result["external_write_count"] == 0


def test_approved_combined_category_and_type_label_uses_exact_official_pair() -> None:
    snapshot = _snapshot()
    category = snapshot["categories_by_target"]["ozon:RU"]["category"]
    category["id"] = "17027906"
    category["name"] = (
        "House & Garden > Decor & Interior > Interior Sticker"
    )
    category["path"] = [{"id": "17027906", "name": category["name"]}]
    profile = {
        "schema_version": "ozon-official-profile-resolution/v1",
        "resolution": "EXACT",
        "description_category_id": 17027906,
        "category_name": "Decor & Interior",
        "category_path": [
            {"id": "17027494", "name": "House & Garden"},
            {"id": "17027906", "name": "Decor & Interior"},
        ],
        "type_id": 91971,
        "type_name": "Interior Sticker",
        "required_attributes": {
            "brand": {
                "attribute_id": 85,
                "dictionary_value_id": 126745801,
                "value": "No Brand",
            },
            "model_name": {"attribute_id": 9048},
            "product_type": {
                "attribute_id": 8229,
                "dictionary_value_id": 91971,
                "value": "Interior Sticker",
            },
        },
    }

    variants = project_ozon_v4_variants(
        snapshot,
        target_labels=("ozon:RU",),
        official_profile_resolver=lambda _snapshot: profile,
    )

    assert variants[0]["category"] == {
        "id": "17027906",
        "name": "Decor & Interior",
        "path": profile["category_path"],
    }
    assert variants[0]["official_profile"]["type_id"] == 91971


from modules.ozon.approved_publication_v4 import OzonStockDispatchFact, project_ozon_v4_variants
