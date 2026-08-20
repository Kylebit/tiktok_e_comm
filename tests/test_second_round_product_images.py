import json

import pytest

from modules.sourcing import new_product_workbench as workbench
from modules.sourcing.localized_image_review import LocalizedImageReviewStore


def _state(*, revision: int = 6) -> dict:
    return {
        "_revision": revision,
        "product_approval": {
            "status": "approved",
            "approved_by": "Kyle",
            "input_fingerprint": "sha256:approved-first-review",
        },
        "review": {
            "image_actions": [
                {"url": "https://assets.example/source-01.png", "action": "keep"},
                {"url": "https://assets.example/source-02.png", "action": "remove"},
                {"url": "https://assets.example/source-03.png", "action": "keep"},
                {"url": "https://assets.example/source-04.png", "action": "keep"},
                {"url": "https://assets.example/source-05.png", "action": "keep"},
                {"url": "https://assets.example/source-06.png", "action": "keep"},
            ]
        },
    }


def _packet(*, revision: int = 6) -> dict:
    return {
        "schema": "publication-preparation-decision/v1",
        "offer_id": "3882808027",
        "product_center_revision": revision,
        "status": "FIRST_REVIEW_READY",
        "target_selection": {
            "requested": [
                "tiktok:LH_MY",
                "tiktok:LH_TH",
                "tiktok:LH_VN",
                "tiktok:MX",
                "ozon:RU",
            ]
        },
        "image_execution_plan": {
            "schema_version": "first-review-image-plan/v1",
            "status": "PROPOSED",
            "source_actions": [
                {
                    "position": 6,
                    "action": "TRANSLATE",
                    "target_languages": [
                        "ms-MY",
                        "th-TH",
                        "vi-VN",
                        "es-MX",
                        "ru-RU",
                    ],
                }
            ],
        },
    }


def _install_packet(tmp_path, packet: dict) -> None:
    path = (
        tmp_path
        / "reports"
        / "product-preparation"
        / "3882808027"
        / "first-review.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(packet), encoding="utf-8")


def test_second_round_binds_current_approval_and_remaps_kept_position(
    monkeypatch, tmp_path
):
    _install_packet(tmp_path, _packet())
    store = LocalizedImageReviewStore(tmp_path / "localized")
    monkeypatch.setattr(workbench, "ROOT", tmp_path)
    monkeypatch.setattr(workbench, "LOCALIZED_IMAGE_REVIEWS_DIR", tmp_path / "localized")
    monkeypatch.setattr(workbench, "load_state", lambda _offer_id: _state())
    monkeypatch.setattr(workbench, "_localized_image_review_store", lambda: store)

    summary = workbench.initialize_second_round_image_review("3882808027")

    review = summary["review"]
    assert review["input_schema_version"] == "approved-first-review-image-input/v1"
    assert review["selected_positions"] == [5]
    assert review["approved_ordered_images"] == [
        "https://assets.example/source-01.png",
        "https://assets.example/source-03.png",
        "https://assets.example/source-04.png",
        "https://assets.example/source-05.png",
        "https://assets.example/source-06.png",
    ]
    assert len(review["tasks"]) == 5
    assert {row["locale"] for row in review["tasks"]} == {
        "ms-MY",
        "th-TH",
        "vi-VN",
        "es-MX",
        "ru-RU",
    }
    assert summary["platform_writes"] == 0


def test_second_round_rejects_a_stale_first_review_packet(monkeypatch, tmp_path):
    _install_packet(tmp_path, _packet(revision=5))
    store = LocalizedImageReviewStore(tmp_path / "localized")
    monkeypatch.setattr(workbench, "ROOT", tmp_path)
    monkeypatch.setattr(workbench, "load_state", lambda _offer_id: _state(revision=6))
    monkeypatch.setattr(workbench, "_localized_image_review_store", lambda: store)

    with pytest.raises(ValueError, match="missing or stale"):
        workbench.initialize_second_round_image_review("3882808027")


def test_second_round_resumes_frozen_review_after_its_own_revision_writes(
    monkeypatch, tmp_path
):
    _install_packet(tmp_path, _packet())
    store = LocalizedImageReviewStore(tmp_path / "localized")
    current = {"value": _state(revision=6)}
    monkeypatch.setattr(workbench, "ROOT", tmp_path)
    monkeypatch.setattr(workbench, "LOCALIZED_IMAGE_REVIEWS_DIR", tmp_path / "localized")
    monkeypatch.setattr(workbench, "load_state", lambda _offer_id: current["value"])
    monkeypatch.setattr(workbench, "_localized_image_review_store", lambda: store)

    first = workbench.initialize_second_round_image_review("3882808027")
    current["value"] = _state(revision=12)
    resumed = workbench.initialize_second_round_image_review("3882808027")

    assert resumed["review"]["approved_snapshot_digest"] == first["review"][
        "approved_snapshot_digest"
    ]
    assert resumed["review"]["revision"] == first["review"]["revision"]


def test_second_round_without_translation_is_a_valid_zero_paid_task_flow(
    monkeypatch, tmp_path
):
    packet = _packet()
    packet["image_execution_plan"]["status"] = "SKIPPED"
    packet["image_execution_plan"]["source_actions"] = [
        {
            "position": 1,
            "action": "KEEP",
            "target_languages": [],
        }
    ]
    _install_packet(tmp_path, packet)
    store = LocalizedImageReviewStore(tmp_path / "localized")
    monkeypatch.setattr(workbench, "ROOT", tmp_path)
    monkeypatch.setattr(workbench, "LOCALIZED_IMAGE_REVIEWS_DIR", tmp_path / "localized")
    monkeypatch.setattr(workbench, "load_state", lambda _offer_id: _state())
    monkeypatch.setattr(workbench, "_localized_image_review_store", lambda: store)

    summary = workbench.initialize_second_round_image_review("3882808027")

    assert summary["review"]["selected_positions"] == []
    assert summary["review"]["tasks"] == []
    assert summary["review"]["status"] == "READY_FOR_REVIEW"
    assert summary["platform_writes"] == 0


def test_pre_review_sync_writes_common_baseline_once_and_records_readback(monkeypatch):
    project = {
        "offer_id": "3882808027",
        "revision": 2,
        "tasks": [{"status": "READY_FOR_REVIEW"}],
    }
    recorded = []

    class FakeStore:
        def load(self, _offer_id):
            return project

        def record_miaoshou_pre_review_sync(
            self, _offer_id, *, expected_revision, receipt
        ):
            recorded.append((expected_revision, receipt))
            project["miaoshou_pre_review_sync"] = dict(receipt)
            project["revision"] += 1
            return project

    writes = []

    def writer(_offer_id, *, post=None):
        writes.append(post)
        return {
            "written_to_miaoshou": True,
            "verified": True,
            "claimed": False,
            "published": False,
            "draft": {"imgUrls": ["https://assets.example/master.png"]},
        }

    monkeypatch.setattr(workbench, "_localized_image_review_store", FakeStore)
    monkeypatch.setattr(
        workbench,
        "localized_image_review_summary",
        lambda _offer_id: {"offer_id": "3882808027", "review": project},
    )

    summary = workbench.sync_localized_images_to_miaoshou_before_review(
        "3882808027",
        expected_revision=2,
        post=lambda *_args, **_kwargs: {},
        writer=writer,
    )

    assert len(writes) == 1
    assert recorded[0][0] == 2
    assert recorded[0][1] == {
        "status": "VERIFIED",
        "written_to_miaoshou": True,
        "verified": True,
        "external_write_count": 1,
        "written_image_count": 1,
        "claimed": False,
        "published": False,
    }
    assert summary["review"]["miaoshou_pre_review_sync"]["verified"] is True
