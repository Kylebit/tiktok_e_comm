from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "product_publication_workflow.py"


def _module():
    spec = importlib.util.spec_from_file_location("product_publication_workflow", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _first() -> dict:
    return {
        "schema": "publication-preparation-decision/v1",
        "offer_id": "3900000001",
        "status": "FIRST_REVIEW_READY",
        "product_center_revision": 7,
    }


def _review(*, tasks: list[dict] | None = None, approved=False, synced=False) -> dict:
    return {
        "initialized": True,
        "review": {
            "revision": 3,
            "status": "APPROVED" if approved else "IN_REVIEW",
            "approval_intent": {"approved_by": "Kyle"} if approved else None,
            "tasks": tasks if tasks is not None else [{"status": "PENDING_GENERATION"}],
            "miaoshou_pre_review_sync": (
                {
                    "status": "VERIFIED",
                    "written_to_miaoshou": True,
                    "verified": True,
                    "external_write_count": 1,
                }
                if synced
                else None
            ),
        },
    }


def test_status_has_one_deterministic_next_command_for_each_takeover_stage():
    module = _module()
    offer = "3900000001"

    missing = module.classify_workflow(offer_id=offer)
    assert missing["stage"] == "FIRST_ROUND_REQUIRED"
    assert "prepare-product-publication" in missing["next_command"]

    second = module.classify_workflow(offer_id=offer, first_review=_first())
    assert second["stage"] == "SECOND_ROUND_REQUIRED"
    assert "prepare_product_images.py" in second["next_command"]

    generation = module.classify_workflow(
        offer_id=offer, first_review=_first(), localized=_review()
    )
    assert generation["stage"] == "IMAGE_GENERATION_REQUIRED"
    assert "--execute-paid" in generation["next_command"]

    sync = module.classify_workflow(
        offer_id=offer,
        first_review=_first(),
        localized=_review(tasks=[{"status": "READY_FOR_REVIEW"}]),
    )
    assert sync["stage"] == "MIAOSHOU_SYNC_REQUIRED"
    assert "--execute-miaoshou" in sync["next_command"]

    approval = module.classify_workflow(
        offer_id=offer,
        first_review=_first(),
        localized=_review(tasks=[{"status": "READY_FOR_REVIEW"}], synced=True),
    )
    assert approval["stage"] == "CHAT_APPROVAL_REQUIRED"
    assert "--approve-all" in approval["next_command"]

    handoff = module.classify_workflow(
        offer_id=offer,
        first_review=_first(),
        localized=_review(
            tasks=[{"status": "PASSED"}], approved=True, synced=True
        ),
    )
    assert handoff["stage"] == "LOCALIZED_HANDOFF_REQUIRED"
    assert "--finalize-release-handoff" in handoff["next_command"]

    ready = module.classify_workflow(
        offer_id=offer,
        first_review=_first(),
        localized=_review(
            tasks=[{"status": "PASSED"}], approved=True, synced=True
        ),
        handoff={
            "schema_version": "product-publication-workflow-handoff/v1",
            "offer_id": offer,
            "status": "READY_TO_PUBLISH",
            "plan_id": "omnichannel:abc",
            "snapshot_digest": "sha256:" + "a" * 64,
        },
    )
    assert ready["stage"] == "READY_TO_PUBLISH"
    assert "skills\\publish-approved-product\\scripts\\product_center_publication.py" in ready["next_command"]
    assert "--plan-id omnichannel:abc" in ready["next_command"]


def test_status_projection_does_not_leak_urls_or_provider_payloads():
    module = _module()
    result = module.classify_workflow(
        offer_id="3900000001",
        first_review=_first(),
        localized={
            **_review(),
            "raw_provider_payload": {"url": "https://secret.example/image"},
        },
    )

    rendered = json.dumps(result, ensure_ascii=False).lower()
    assert "https://" not in rendered
    assert "provider_payload" not in rendered
    assert set(result) == {
        "schema_version",
        "offer_id",
        "stage",
        "first_review_revision",
        "second_round_revision",
        "task_counts",
        "approval_recorded",
        "miaoshou_verified",
        "active_plan_id",
        "snapshot_digest",
        "next_command",
        "requires_reconciliation",
    }
