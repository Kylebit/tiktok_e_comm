import pytest

from shared_platform.product_snapshot import (
    _approved_parcel_facts,
    _approved_sku_prices,
    project_approved_tiktok_category_decisions,
)


TARGETS = (
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:MX",
    "tiktok:GB",
)


def test_immutable_product_category_projects_exact_six_tiktok_decisions():
    decisions = project_approved_tiktok_category_decisions(
        {"name": "贴饰 > 墙贴", "id": "", "confidence": "approved"},
        targets=TARGETS,
    )

    assert list(decisions) == list(TARGETS)
    assert {row["category_id"] for row in decisions.values()} == {"600338"}
    assert decisions["tiktok:LH_PH"]["evidence_digest"] == (
        "01da90466a74143b065cc2a9a90fc52d51bc516316da00361181915b3047463e"
    )
    assert decisions["tiktok:GB"]["evidence_digest"] == (
        "24eb8b5d3f5dedeac07212c600140510f408e5479e9e1b80251f4e1af36a1486"
    )


@pytest.mark.parametrize(
    "category,targets",
    [
        ({"name": "unsupported"}, TARGETS),
        ({"name": "贴饰 > 墙贴"}, (*TARGETS, "tiktok:UNKNOWN")),
        ({"name": "贴饰 > 墙贴"}, (*TARGETS[:-1], TARGETS[0])),
    ],
)
def test_category_projection_fails_closed_for_unsupported_snapshot(
    category,
    targets,
):
    with pytest.raises(ValueError):
        project_approved_tiktok_category_decisions(
            category,
            targets=targets,
        )


def test_approved_main_category_can_defer_to_each_official_site_candidate():
    decisions = project_approved_tiktok_category_decisions(
        {"name": "居家布艺 > 桌旗", "id": "", "confidence": "approved"},
        targets=TARGETS,
    )

    assert list(decisions) == list(TARGETS)
    assert all(row["category_id"] is None for row in decisions.values())
    assert all(len(row["evidence_digest"]) == 64 for row in decisions.values())


def test_miaoshou_table_runner_category_defers_to_each_official_site_candidate():
    decisions = project_approved_tiktok_category_decisions(
        {
            "name": "居家布艺 > 桌旗",
            "id": "",
            "confidence": "miaoshou-source",
        },
        targets=TARGETS,
    )

    assert list(decisions) == list(TARGETS)
    assert all(row["category_id"] is None for row in decisions.values())


def test_approved_snapshot_preserves_distinct_prices_for_each_model_sku():
    payload = {
        "pricing": {
            "selected_targets": {
                "tiktok:GB": {
                    "sku_prices": [
                        {
                            "model_sku": "0963",
                            "target_key": "gb",
                            "currency": "GBP",
                            "list_price": 17,
                        },
                        {
                            "model_sku": "0964",
                            "target_key": "gb",
                            "currency": "GBP",
                            "list_price": 18,
                        },
                        {
                            "model_sku": "0965",
                            "target_key": "gb",
                            "currency": "GBP",
                            "list_price": 20,
                        },
                    ]
                }
            }
        }
    }

    assert _approved_sku_prices(payload, "tiktok:GB") == {
        "0963": "17",
        "0964": "18",
        "0965": "20",
    }


def test_red_multisku_snapshot_rejects_missing_per_sku_prices():
    payload = {
        "product_facts": {
            "selected_sku_keys": ["variant-a", "variant-b", "variant-c"]
        },
        "pricing": {
            "selected_targets": {
                "tiktok:GB": {
                    "store_prices": [
                        {
                            "target_key": "gb",
                            "currency": "GBP",
                            "list_price": 17,
                        }
                    ]
                }
            }
        },
    }

    with pytest.raises(ValueError, match="requires approved per-SKU prices"):
        _approved_sku_prices(payload, "tiktok:GB")


def test_single_sku_legacy_snapshot_binds_parent_parcel_to_only_variant():
    payload = {
        "product_facts": {
            "weight_kg": 0.1,
            "package_cm": [20, 20, 3],
        }
    }

    assert _approved_parcel_facts(
        payload,
        variant_model_skus={"default": "0954"},
    ) == (
        "0.1",
        ["20", "20", "3"],
        {
            "default": {
                "weight_kg": "0.1",
                "package_cm": ["20", "20", "3"],
            }
        },
    )


def test_multisku_snapshot_still_requires_exact_per_sku_parcels():
    payload = {
        "product_facts": {
            "weight_kg": 0.1,
            "package_cm": [20, 20, 3],
        }
    }

    with pytest.raises(ValueError, match="per-SKU parcel inputs are incomplete"):
        _approved_parcel_facts(
            payload,
            variant_model_skus={"variant-a": "0963", "variant-b": "0964"},
        )
