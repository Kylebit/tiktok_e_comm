import argparse
import importlib.util
import json
from pathlib import Path

import pytest

from modules.sourcing import new_product_workbench as workbench


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "prepare-product-images"
    / "scripts"
    / "prepare_product_images.py"
)


def _script_module():
    spec = importlib.util.spec_from_file_location("prepare_product_images_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _summary(
    statuses,
    *,
    revision=2,
    status="REVIEW_REQUIRED",
    miaoshou_synced=True,
):
    return {
        "offer_id": "3882808027",
        "review": {
            "revision": revision,
            "status": status,
            "tasks": [
                {
                    "task_id": f"task-{index}",
                    "status": task_status,
                    "locale": locale,
                }
                for index, (task_status, locale) in enumerate(
                    zip(statuses, ["ms-MY", "th-TH"]), start=1
                )
            ],
            "external_generation_count": 2,
            "miaoshou_pre_review_sync": (
                {
                    "status": "VERIFIED",
                    "written_to_miaoshou": True,
                    "verified": True,
                    "external_write_count": 1,
                }
                if miaoshou_synced
                else None
            ),
            "product_center_mutated": False,
            "platform_writes": 0,
        },
    }


def test_explicit_chat_approval_is_recorded_in_one_action(
    monkeypatch,
):
    module = _script_module()
    current = _summary(["READY_FOR_REVIEW", "READY_FOR_REVIEW"])

    monkeypatch.setattr(workbench, "initialize_second_round_image_review", lambda _offer: current)
    monkeypatch.setattr(
        workbench,
        "generate_localized_image_review",
        lambda *args, **kwargs: current,
    )

    def approve(_offer, *, expected_revision, approved_by):
        assert expected_revision == 2
        assert approved_by == "Kyle"
        current["review"]["status"] = "APPROVED"
        current["review"]["approval_intent"] = {"approved_by": "Kyle"}
        current["review"]["revision"] = 3
        return current

    monkeypatch.setattr(workbench, "approve_localized_image_review", approve)

    result = module.run(
        argparse.Namespace(
            offer_id="3882808027",
            execute_paid=False,
            confirm_paid_generation=False,
            approve_all=True,
            approved_by="Kyle",
            execute_miaoshou=False,
            confirm_miaoshou_write=False,
        )
    )

    assert result["status"] == "APPROVED"
    assert result["approval_status"] == "APPROVED"
    assert result["platform_writes"] == 0


def test_ready_images_expose_read_only_review_before_miaoshou_sync(monkeypatch):
    module = _script_module()
    current = _summary(
        ["READY_FOR_REVIEW", "READY_FOR_REVIEW"],
        miaoshou_synced=False,
    )
    monkeypatch.setattr(
        workbench, "initialize_second_round_image_review", lambda _offer: current
    )

    result = module.run(
        argparse.Namespace(
            offer_id="3882808027",
            execute_paid=False,
            confirm_paid_generation=False,
            approve_all=False,
            approved_by="Kyle",
            execute_miaoshou=False,
            confirm_miaoshou_write=False,
        )
    )

    assert result["status"] == "MIAOSHOU_SYNC_REQUIRED"
    assert result["review_url"].endswith("new-product?offer_id=3882808027")
    assert result["result_url"].endswith("localized-image-review?offer_id=3882808027")


def test_verified_miaoshou_sync_changes_only_execution_readiness(monkeypatch):
    module = _script_module()
    current = _summary(
        ["READY_FOR_REVIEW", "READY_FOR_REVIEW"],
        miaoshou_synced=False,
    )
    calls = []
    monkeypatch.setattr(
        workbench, "initialize_second_round_image_review", lambda _offer: current
    )

    def sync(_offer, *, expected_revision):
        calls.append(expected_revision)
        current["review"]["miaoshou_pre_review_sync"] = {
            "status": "VERIFIED",
            "written_to_miaoshou": True,
            "verified": True,
            "external_write_count": 1,
        }
        current["review"]["revision"] += 1
        return current

    monkeypatch.setattr(
        workbench,
        "sync_localized_images_to_miaoshou_before_review",
        sync,
        raising=False,
    )

    result = module.run(
        argparse.Namespace(
            offer_id="3882808027",
            execute_paid=False,
            confirm_paid_generation=False,
            approve_all=False,
            approved_by="Kyle",
            execute_miaoshou=True,
            confirm_miaoshou_write=True,
        )
    )

    assert calls == [2]
    assert result["status"] == "READY_FOR_EXECUTION_CHECKS"
    assert result["miaoshou_external_write_count"] == 1
    assert result["review_url"].endswith("new-product?offer_id=3882808027")
    assert result["result_url"].endswith("localized-image-review?offer_id=3882808027")


def test_chat_approval_is_not_blocked_without_verified_miaoshou_sync(monkeypatch):
    module = _script_module()
    current = _summary(
        ["READY_FOR_REVIEW", "READY_FOR_REVIEW"],
        miaoshou_synced=False,
    )
    monkeypatch.setattr(
        workbench, "initialize_second_round_image_review", lambda _offer: current
    )
    def approve(_offer, *, expected_revision, approved_by):
        assert expected_revision == 2
        assert approved_by == "Kyle"
        current["review"]["approval_intent"] = {"approved_by": "Kyle"}
        current["review"]["status"] = "APPROVED"
        current["review"]["revision"] = 3
        return current

    monkeypatch.setattr(workbench, "approve_localized_image_review", approve)

    result = module.run(
        argparse.Namespace(
            offer_id="3882808027",
            execute_paid=False,
            confirm_paid_generation=False,
            approve_all=True,
            approved_by="Kyle",
            execute_miaoshou=False,
            confirm_miaoshou_write=False,
        )
    )

    assert result["approval_status"] == "APPROVED"
    assert result["execution_status"] == "MIAOSHOU_SYNC_REQUIRED"


def test_zero_paid_task_flow_still_requires_only_miaoshou_and_chat_approval(
    monkeypatch,
):
    module = _script_module()
    current = _summary([], status="READY_FOR_REVIEW", miaoshou_synced=False)
    current["review"]["external_generation_count"] = 0
    monkeypatch.setattr(
        workbench, "initialize_second_round_image_review", lambda _offer: current
    )

    result = module.run(
        argparse.Namespace(
            offer_id="3882808027",
            execute_paid=False,
            confirm_paid_generation=False,
            approve_all=False,
            approved_by="Kyle",
            execute_miaoshou=False,
            confirm_miaoshou_write=False,
            finalize_release_handoff=False,
            uploaded_assets=None,
        )
    )

    assert result["paid_task_count"] == 0
    assert result["status"] == "MIAOSHOU_SYNC_REQUIRED"


def test_finalize_handoff_atomically_freezes_provider_result_routes(
    monkeypatch, tmp_path
):
    module = _script_module()
    base_plan = {
        "plan_id": "base-plan",
        "payload_digest": "a" * 64,
        "targets": ["tiktok:LH_TH"],
    }
    base_snapshot = {
        "snapshot_digest": "sha256:" + "b" * 64,
        "product": {"images": ["https://assets.example/source.png"]},
    }
    calls = []

    class FakeStore:
        def create_and_approve_localized_image_successor(self, plan_id, **kwargs):
            calls.append((plan_id, kwargs))
            return {
                "plan": {
                    "plan_id": "localized-plan",
                    "payload_digest": "c" * 64,
                    "targets": ["tiktok:LH_TH"],
                },
                "publication_snapshot": {
                    "snapshot_digest": "sha256:" + "d" * 64,
                    "product": {
                        "image_routing": {
                            "routes": {
                                "tiktok:LH_TH": {
                                    "locale": "th-TH",
                                    "ordered_images": [
                                        "https://files.example/th.png"
                                    ],
                                }
                            }
                        }
                    },
                },
            }

    project = {
        "offer_id": "3882808027",
        "status": "APPROVED",
        "selected_positions": [1],
        "approved_ordered_images": ["https://assets.example/source.png"],
        "route_locales": {"tiktok:LH_TH": "th-TH"},
        "miaoshou_pre_review_sync": {
            "status": "VERIFIED",
            "written_to_miaoshou": True,
            "verified": True,
            "external_write_count": 1,
        },
        "approval_intent": {"approved_by": "Kyle"},
        "approval": {
            "approved_by": "Kyle",
            "tasks": [
                {
                    "task_id": "task-1",
                    "position": 1,
                    "locale": "th-TH",
                    "artifact_id": "localized-review-1234567890abcdef1234",
                    "output_digest": "sha256:" + "e" * 64,
                }
            ],
        },
        "tasks": [
            {
                "artifact_id": "localized-review-1234567890abcdef1234",
                "output_digest": "sha256:" + "e" * 64,
                "generation_receipt": {
                    "public_url": "https://files.example/th.png"
                },
            }
        ],
        "publication_supplement": {
            "routes": {
                "tiktok:LH_TH": {
                    "locale": "th-TH",
                    "ordered_images": [
                        {
                            "position": 1,
                            "kind": "LOCALIZED_ARTIFACT",
                            "artifact_id": "localized-review-1234567890abcdef1234",
                            "artifact_digest": "sha256:" + "e" * 64,
                            "source_url": "https://assets.example/source.png",
                        }
                    ],
                }
            }
        },
    }
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        module,
        "_approved_base_plan",
        lambda _offer, _project: (FakeStore(), base_plan, base_snapshot),
    )

    handoff = module.finalize_release_handoff(
        {"offer_id": "3882808027", "review": project}
    )

    assert handoff["status"] == "READY_TO_PUBLISH"
    assert handoff["plan_id"] == "localized-plan"
    assert calls[0][1]["uploaded_assets"] == {
        "localized-review-1234567890abcdef1234": {
            "artifact_digest": "sha256:" + "e" * 64,
            "url": "https://files.example/th.png",
        }
    }
    assert json.loads(
        (
            tmp_path
            / "reports"
            / "product-preparation"
            / "3882808027"
            / "workflow-handoff.json"
        ).read_text(encoding="utf-8")
    ) == handoff
