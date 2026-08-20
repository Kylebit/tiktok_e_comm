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


def _verified_miaoshou_receipt() -> dict:
    return {
        "status": "VERIFIED",
        "written_to_miaoshou": True,
        "verified": True,
        "external_write_count": 1,
        "written_image_count": 7,
        "claimed": False,
        "published": False,
    }


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


def test_initializes_second_round_from_approved_first_review(tmp_path):
    project = LocalizedImageReviewStore(tmp_path).initialize_from_first_review(
        offer_id="3882808027",
        first_review_id=f"first-review:{'b' * 20}",
        input_digest=f"sha256:{'c' * 64}",
        ordered_images=[
            f"https://assets.example/source-{position:02d}.png"
            for position in range(1, 7)
        ],
        selected_positions=[6],
        publication_targets=[
            "tiktok:LH_MY",
            "tiktok:LH_TH",
            "tiktok:LH_VN",
            "tiktok:MX",
            "ozon:RU",
        ],
        target_locales=["ms-MY", "th-TH", "vi-VN", "es-MX", "ru-RU"],
    )

    assert project["input_schema_version"] == "approved-first-review-image-input/v1"
    assert project["selected_positions"] == [6]
    assert len(project["tasks"]) == 5
    assert {row["position"] for row in project["tasks"]} == {6}
    assert {row["locale"] for row in project["tasks"]} == {
        "ms-MY", "th-TH", "vi-VN", "es-MX", "ru-RU",
    }
    assert project["platform_writes"] == 0


def test_chat_approval_auto_accepts_generated_images_without_miaoshou_dependency(tmp_path):
    store = LocalizedImageReviewStore(tmp_path)
    project = store.initialize(_snapshot(), selected_positions=[1, 5, 6, 7])
    generated = store.save_generation_bundle(
        "3899705757",
        expected_revision=project["revision"],
        items=_generation_rows(project),
    )

    assert {row["status"] for row in generated["tasks"]} == {"READY_FOR_REVIEW"}
    assert generated["external_generation_count"] == 20
    approved = store.approve(
        "3899705757",
        expected_revision=generated["revision"],
        approved_by="Kyle",
    )

    assert approved["status"] == "APPROVED"
    assert all(row["status"] == "PASSED" for row in approved["tasks"])
    assert all(row["decision"] == "CHAT_APPROVED" for row in approved["tasks"])
    assert approved["approval_intent"]["approved_by"] == "Kyle"
    assert approved["approval"]["approved_by"] == "Kyle"
    assert approved["approval"]["approval_digest"].startswith("sha256:")
    assert approved["publication_supplement"]["status"] == "APPROVED_LOCAL_ASSETS"
    assert approved["publication_supplement"]["release_plan_id"] == _snapshot()["plan_id"]
    assert approved["publication_supplement"]["platform_writes"] == 0


def test_chat_approval_is_recorded_before_generation_and_reconciles_later(tmp_path):
    store = LocalizedImageReviewStore(tmp_path)
    project = store.initialize(_snapshot(), selected_positions=[1])
    approved_early = store.approve(
        "3899705757",
        expected_revision=project["revision"],
        approved_by="Kyle",
    )

    assert approved_early["status"] == "APPROVAL_RECORDED"
    assert approved_early["approval_intent"]["approved_by"] == "Kyle"
    assert "approval" not in approved_early
    assert "publication_supplement" not in approved_early

    reconciled = store.save_generation_bundle(
        "3899705757",
        expected_revision=approved_early["revision"],
        items=_generation_rows(project),
    )

    assert reconciled["status"] == "APPROVED"
    assert all(row["status"] == "PASSED" for row in reconciled["tasks"])
    assert reconciled["publication_supplement"]["status"] == "APPROVED_LOCAL_ASSETS"
    assert "miaoshou_pre_review_sync" not in reconciled


def test_retry_decision_never_falls_back_to_the_english_source(tmp_path):
    store = LocalizedImageReviewStore(tmp_path)
    project = store.initialize(_snapshot(), selected_positions=[1, 5, 6, 7])
    generated = store.save_generation_bundle(
        "3899705757",
        expected_revision=project["revision"],
        items=_generation_rows(project),
    )
    task = generated["tasks"][0]
    generated = store.record_miaoshou_pre_review_sync(
        "3899705757",
        expected_revision=generated["revision"],
        receipt=_verified_miaoshou_receipt(),
    )

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
