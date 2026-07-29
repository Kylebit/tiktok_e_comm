import hashlib

import pytest

from domains.content_operations.content_package_adapter import (
    SOURCE_ONLY_FINAL_APPROVAL_SCHEMA,
    build_content_package_handoff,
    build_workbench_content_package_handoff,
    source_only_final_approval_digest,
    source_only_review_signature,
)


def _source_only_state(*, video_action, video_url=""):
    state = {
        "content_package": {
            "product_id": "product-1",
            "content_strategy": "source_only",
            "fact_card_approved": True,
            "planning_scope_approved": True,
        },
        "review": {
            "image_actions": [
                {
                    "action": "keep",
                    "url": "https://assets.example/source.jpg",
                }
            ],
            "image_order": ["https://assets.example/source.jpg"],
            "video_action": video_action,
            "video_url": video_url,
        },
    }
    signature = source_only_review_signature(
        state["review"]["image_actions"], state["review"]["image_order"]
    )
    state["content_package"]["source_only_review_signature"] = signature
    state["content_package"]["source_only_final_approval"] = {
        "schema_version": SOURCE_ONLY_FINAL_APPROVAL_SCHEMA,
        "status": "approved",
        "approved_by": "Kyle",
        "source_only_review_signature": signature,
        "video_action": video_action,
        "video_identity_digest": (
            "sha256:"
            + hashlib.sha256(video_url.encode("utf-8")).hexdigest()
        ),
        "approval_digest": source_only_final_approval_digest(
            review_signature=signature,
            video_action=video_action,
            video_url=video_url,
        ),
        "approved_at": "2026-07-29T00:00:00+00:00",
    }
    return state


def test_workbench_content_package_carries_explicitly_kept_https_video():
    handoff = build_workbench_content_package_handoff(
        product_id="product-1",
        state=_source_only_state(
            video_action="keep",
            video_url="https://assets.example/product.mp4",
        ),
        suite_plan={},
        generation_audits={},
        copy={"en": "Approved copy"},
    )

    assert handoff.content_package.approval.status == "approved"
    assert handoff.content_package.video_urls == (
        "https://assets.example/product.mp4",
    )
    assert not any("video" in blocker for blocker in handoff.blockers)


@pytest.mark.parametrize("video_url", ["", "http://assets.example/product.mp4"])
def test_workbench_keep_without_https_video_is_blocked(video_url):
    handoff = build_workbench_content_package_handoff(
        product_id="product-1",
        state=_source_only_state(
            video_action="keep",
            video_url=video_url,
        ),
        suite_plan={},
        generation_audits={},
        copy={"en": "Approved copy"},
    )

    assert handoff.content_package.approval.status == "pending"
    assert handoff.content_package.video_urls == ()
    assert "video_action=keep requires an approved HTTPS video URL" in handoff.blockers


@pytest.mark.parametrize("video_action", ["none", "remove"])
def test_workbench_explicit_no_video_keeps_an_empty_video_contract(video_action):
    handoff = build_workbench_content_package_handoff(
        product_id="product-1",
        state=_source_only_state(video_action=video_action),
        suite_plan={},
        generation_audits={},
        copy={"en": "Approved copy"},
    )

    assert handoff.content_package.approval.status == "approved"
    assert handoff.content_package.video_urls == ()
    assert not any("video" in blocker for blocker in handoff.blockers)


def test_workbench_pending_video_decision_blocks_content_approval():
    handoff = build_workbench_content_package_handoff(
        product_id="product-1",
        state=_source_only_state(
            video_action="review",
            video_url="https://assets.example/product.mp4",
        ),
        suite_plan={},
        generation_audits={},
        copy={"en": "Approved copy"},
    )

    assert handoff.content_package.approval.status == "pending"
    assert handoff.content_package.video_urls == ()
    assert "video requires an explicit keep, remove, or none decision" in handoff.blockers


def test_generic_content_adapter_applies_the_same_video_gate():
    handoff = build_content_package_handoff(
        product_id="product-1",
        suite_plan={"suite": {"items": [{"id": "hero"}]}},
        asset_decisions={"hero-r1": {"decision": "approved"}},
        generation_audits={
            "hero-r1": {
                "shot_id": "hero",
                "download_verified": True,
                "final_response": {
                    "result": {
                        "data": [{"url": "https://assets.example/hero.jpg"}]
                    }
                },
            }
        },
        video_action="keep",
        video_url="",
    )

    assert handoff.content_package.approval.status == "pending"
    assert handoff.content_package.video_urls == ()
    assert handoff.blockers == (
        "video_action=keep requires an approved HTTPS video URL",
    )
