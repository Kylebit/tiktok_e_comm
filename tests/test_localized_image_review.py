from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from modules.sourcing.localized_image_review import (
    LocalizedImageReviewError,
    LocalizedImageReviewStore,
)


def _png(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def _snapshot() -> dict:
    return {
        "schema_version": "approved-publication-snapshot/v4",
        "offer_id": "3899705757",
        "plan_id": "omnichannel:approved-wallpaper",
        "snapshot_digest": f"sha256:{'a' * 64}",
        "product": {
            "images": [
                f"https://assets.example/master-{position:02d}.png"
                for position in range(1, 8)
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


def _generation_rows(project: dict) -> list[dict]:
    rows = []
    for index, task in enumerate(project["tasks"]):
        rows.append(
            {
                "task_id": task["task_id"],
                "translations": [
                    {
                        "region_id": "text-aaaaaaaaaaaaaaaaaaaa",
                        "source_text": "Easy to install",
                        "translated_text": f"translated-{task['locale']}",
                    }
                ],
                "image_bytes": _png(("red", "green", "blue", "yellow", "purple")[index % 5]),
                "receipt": {
                    "status": "COMPLETED",
                    "provider": "toapis-images/v1",
                    "model": "gpt-image-2-official",
                    "task_id": f"provider-{index}",
                    "client_business_id": f"localized-{index}",
                    "request_attempted": True,
                    "outcome_unknown": False,
                    "external_generation_count": 1,
                },
            }
        )
    return rows


def test_initializes_only_requested_positions_and_country_locales(tmp_path):
    project = LocalizedImageReviewStore(tmp_path).initialize(
        _snapshot(), selected_positions=[1, 5, 6, 7]
    )

    assert project["schema_version"] == "localized-image-review/v1"
    assert project["selected_positions"] == [1, 5, 6, 7]
    assert len(project["tasks"]) == 20
    assert {row["position"] for row in project["tasks"]} == {1, 5, 6, 7}
    assert {row["locale"] for row in project["tasks"]} == {
        "ms-MY",
        "th-TH",
        "vi-VN",
        "es-MX",
        "ru-RU",
    }
    assert project["paid_generation_budget"] == 20
    assert project["platform_writes"] == 0
    assert project["product_center_mutated"] is False


def test_generated_images_require_human_pass_before_approval(tmp_path):
    store = LocalizedImageReviewStore(tmp_path)
    project = store.initialize(_snapshot(), selected_positions=[1, 5, 6, 7])
    generated = store.save_generation_bundle(
        "3899705757",
        expected_revision=project["revision"],
        items=_generation_rows(project),
    )

    assert {row["status"] for row in generated["tasks"]} == {"READY_FOR_REVIEW"}
    assert generated["external_generation_count"] == 20
    with pytest.raises(LocalizedImageReviewError, match="human review"):
        store.approve(
            "3899705757",
            expected_revision=generated["revision"],
            approved_by="Kyle",
        )

    current = generated
    for task in generated["tasks"]:
        current = store.decide(
            "3899705757",
            expected_revision=current["revision"],
            task_id=task["task_id"],
            decision="PASS",
        )
    approved = store.approve(
        "3899705757",
        expected_revision=current["revision"],
        approved_by="Kyle",
    )

    assert approved["status"] == "APPROVED"
    assert approved["approval"]["approved_by"] == "Kyle"
    assert approved["approval"]["approval_digest"].startswith("sha256:")
    assert approved["publication_supplement"]["status"] == "APPROVED_LOCAL_ASSETS"
    assert approved["publication_supplement"]["release_plan_id"] == _snapshot()["plan_id"]
    assert approved["publication_supplement"]["platform_writes"] == 0


def test_retry_decision_never_falls_back_to_the_english_source(tmp_path):
    store = LocalizedImageReviewStore(tmp_path)
    project = store.initialize(_snapshot(), selected_positions=[1, 5, 6, 7])
    generated = store.save_generation_bundle(
        "3899705757",
        expected_revision=project["revision"],
        items=_generation_rows(project),
    )
    task = generated["tasks"][0]

    retried = store.decide(
        "3899705757",
        expected_revision=generated["revision"],
        task_id=task["task_id"],
        decision="RETRY",
    )

    changed = next(row for row in retried["tasks"] if row["task_id"] == task["task_id"])
    assert changed["status"] == "RETRY_REQUESTED"
    assert changed["artifact_id"] is None
    assert changed["output_digest"] is None
    assert retried["status"] == "REVIEW_REQUIRED"
