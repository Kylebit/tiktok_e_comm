from copy import deepcopy

from copy import deepcopy

import pytest

from shared_platform.localized_image_successor import (
    build_localized_image_successor_payload,
)


def _facts():
    predecessor = {
        "plan_id": "omnichannel:" + "a" * 64,
        "product_id": "3899705757",
        "targets": ["tiktok:LH_MY", "ozon:RU"],
        "product_facts": {"title": "Frozen", "image_urls": ["https://img.example/1.png", "https://img.example/2.png"]},
        "pricing": {"frozen": True},
    }
    snapshot = {
        "offer_id": "3899705757",
        "snapshot_digest": "sha256:" + "b" * 64,
        "product": {"images": ["https://img.example/1.png", "https://img.example/2.png"]},
    }
    supplement = {
        "schema_version": "publication-image-supplement/v1",
        "status": "APPROVED_LOCAL_ASSETS",
        "platform_writes": 0,
        "product_center_mutated": False,
        "offer_id": "3899705757",
        "release_plan_id": predecessor["plan_id"],
        "approved_snapshot_digest": snapshot["snapshot_digest"],
        "approval_digest": "sha256:" + "c" * 64,
        "supplement_digest": "sha256:" + "d" * 64,
        "routes": {
            "tiktok:LH_MY": {
                "locale": "ms-MY",
                "ordered_images": [
                    {
                        "position": 1,
                        "kind": "LOCALIZED_ARTIFACT",
                        "artifact_id": "ms-1",
                        "artifact_digest": "sha256:" + "e" * 64,
                        "source_url": "https://img.example/1.png",
                    },
                    {"position": 2, "kind": "APPROVED_BASE_URL", "url": "https://img.example/2.png"},
                ],
            },
            "ozon:RU": {
                "locale": "ru-RU",
                "ordered_images": [
                    {"position": 1, "kind": "APPROVED_BASE_URL", "url": "https://img.example/1.png"},
                    {"position": 2, "kind": "APPROVED_BASE_URL", "url": "https://img.example/2.png"},
                ],
            },
        },
    }
    uploads = {"ms-1": {"artifact_digest": "sha256:" + "e" * 64, "url": "https://cdn.example/ms-1.png"}}
    return predecessor, snapshot, supplement, uploads


def test_builds_image_only_successor_without_recomputing_predecessor_facts():
    predecessor, snapshot, supplement, uploads = _facts()

    successor = build_localized_image_successor_payload(
        predecessor,
        predecessor_snapshot=snapshot,
        supplement=supplement,
        uploaded_assets=uploads,
    )

    for key in predecessor:
        if key != "plan_id":
            assert successor[key] == predecessor[key]
    assert successor["plan_id"].startswith("omnichannel:")
    assert successor["plan_id"] != predecessor["plan_id"]
    assert successor["localized_image_routing"]["routes"]["tiktok:LH_MY"]["ordered_images"] == [
        "https://cdn.example/ms-1.png",
        "https://img.example/2.png",
    ]


def test_rejects_missing_upload_and_route_drift():
    predecessor, snapshot, supplement, uploads = _facts()
    with pytest.raises(ValueError, match="coverage"):
        build_localized_image_successor_payload(
            predecessor,
            predecessor_snapshot=snapshot,
            supplement=supplement,
            uploaded_assets={},
        )

    drifted = deepcopy(supplement)
    drifted["routes"].pop("ozon:RU")
    with pytest.raises(ValueError, match="coverage"):
        build_localized_image_successor_payload(
            predecessor,
            predecessor_snapshot=snapshot,
            supplement=drifted,
            uploaded_assets=uploads,
        )
