from __future__ import annotations

from copy import deepcopy

import pytest

from modules.sourcing.localized_image_packs import (
    LocalizedImagePackError,
    LocalizedImagePackStore,
)


def _snapshot(*, digest_suffix: str = "a") -> dict:
    return {
        "schema_version": "approved-publication-snapshot/v4",
        "offer_id": "3900088343",
        "plan_id": "omnichannel:approved-wallpaper",
        "snapshot_digest": f"sha256:{digest_suffix * 64}",
        "product": {
            "images": [
                "https://assets.example/master-01.png",
                "https://assets.example/master-02.png",
                "https://assets.example/master-03.png",
            ]
        },
        "publication_targets": [
            {"target_label": "miaoshou:COMMON"},
            {"target_label": "tiktok:LH_PH"},
            {"target_label": "tiktok:LH_MY"},
            {"target_label": "tiktok:LH_TH"},
            {"target_label": "tiktok:LH_VN"},
            {"target_label": "tiktok:MX"},
            {"target_label": "tiktok:GB"},
            {"target_label": "shopee:PH"},
            {"target_label": "shopee:MY"},
            {"target_label": "shopee:TH"},
            {"target_label": "shopee:VN"},
            {"target_label": "ozon:RU"},
        ],
    }


def test_initializes_independent_locale_packs_from_approved_master(tmp_path):
    store = LocalizedImagePackStore(tmp_path)
    snapshot = _snapshot()
    original = deepcopy(snapshot)

    project = store.initialize_from_approved_snapshot(snapshot)

    assert snapshot == original
    assert project["schema_version"] == "localized-image-project/v1"
    assert project["offer_id"] == "3900088343"
    assert project["release_plan_id"] == snapshot["plan_id"]
    assert project["approved_snapshot_digest"] == snapshot["snapshot_digest"]
    assert project["base_package"]["ordered_image_urls"] == snapshot["product"]["images"]
    assert set(project["packs"]) == {
        "en-master",
        "ms-MY",
        "th-TH",
        "vi-VN",
        "ru-RU",
        "es-MX",
    }
    assert project["packs"]["en-master"]["status"] == "READY_BASE"
    assert project["packs"]["th-TH"]["status"] == "PENDING_TEXT_REVIEW"
    assert [
        row["source_url"] for row in project["packs"]["vi-VN"]["images"]
    ] == snapshot["product"]["images"]
    assert all(
        row["output_url"] is None
        for row in project["packs"]["ru-RU"]["images"]
    )


def test_builds_country_routes_without_changing_product_release_plan(tmp_path):
    project = LocalizedImagePackStore(tmp_path).initialize_from_approved_snapshot(
        _snapshot()
    )

    routes = project["route_draft"]["routes"]
    assert routes["miaoshou:COMMON"]["locale"] == "en-master"
    assert routes["tiktok:LH_PH"]["locale"] == "en-master"
    assert routes["tiktok:LH_MY"]["locale"] == "ms-MY"
    assert routes["tiktok:LH_TH"]["locale"] == "th-TH"
    assert routes["tiktok:LH_VN"]["locale"] == "vi-VN"
    assert routes["tiktok:MX"]["locale"] == "es-MX"
    assert routes["tiktok:GB"]["locale"] == "en-master"
    assert routes["shopee:PH"]["locale"] == "en-master"
    assert routes["shopee:MY"]["locale"] == "ms-MY"
    assert routes["shopee:TH"]["locale"] == "th-TH"
    assert routes["shopee:VN"]["locale"] == "vi-VN"
    assert routes["ozon:RU"]["locale"] == "ru-RU"
    assert project["route_draft"]["status"] == "DRAFT"
    assert "approved_by" not in project["route_draft"]


def test_same_snapshot_is_idempotent_but_new_snapshot_cannot_overwrite(tmp_path):
    store = LocalizedImagePackStore(tmp_path)
    first = store.initialize_from_approved_snapshot(_snapshot())
    same = store.initialize_from_approved_snapshot(_snapshot())
    assert same == first

    with pytest.raises(LocalizedImagePackError, match="different approved snapshot"):
        store.initialize_from_approved_snapshot(_snapshot(digest_suffix="b"))

    assert store.load("3900088343") == first


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(schema_version="approved-publication-snapshot/v3"),
        lambda row: row["product"].update(images=[]),
        lambda row: row["product"].update(
            images=["https://assets.example/master-01.png"] * 2
        ),
        lambda row: row["publication_targets"].append(
            {"target_label": "tiktok:UNKNOWN"}
        ),
    ],
)
def test_rejects_unsafe_or_ambiguous_approved_snapshot(tmp_path, mutation):
    snapshot = _snapshot()
    mutation(snapshot)

    with pytest.raises(LocalizedImagePackError):
        LocalizedImagePackStore(tmp_path).initialize_from_approved_snapshot(snapshot)
