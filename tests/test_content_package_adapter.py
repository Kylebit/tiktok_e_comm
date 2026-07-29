import pytest

from domains.content_operations.content_package_adapter import (
    SOURCE_ONLY_FINAL_APPROVAL_SCHEMA,
    build_content_package_handoff,
    build_workbench_content_package_handoff,
    source_only_final_approval_digest,
    source_only_review_signature,
)


def _audit(shot_id, url, *, verified=True, audit_id="audit-1"):
    return {
        "shot_id": shot_id,
        "audit_id": audit_id,
        "download_verified": verified,
        "final_response": {"result": {"data": [{"url": url}]}},
    }


def test_partial_handoff_keeps_approved_assets_but_remains_pending_with_missing_shots():
    handoff = build_content_package_handoff(
        product_id="product-42",
        suite_plan={"suite": {"items": [
            {"id": "hero", "selected": True},
            {"id": "scene", "selected": True},
            {"id": "detail", "selected": True},
        ]}},
        asset_decisions={
            "detail_r1": {"decision": "approved"},
            "hero_r1": {"decision": "approved"},
            "scene_r1": {"decision": "rejected"},
        },
        generation_audits={
            "detail_r1": _audit("detail", "https://assets.example/detail.png", audit_id="audit-detail"),
            "scene_r1": _audit("scene", "https://assets.example/scene.png"),
            "hero_r1": _audit("hero", "https://assets.example/hero.png", audit_id="audit-hero"),
        },
        copy={"en": "Approved copy"},
    )

    assert handoff.content_package.product_id == "product-42"
    assert handoff.content_package.copy == {"en": "Approved copy"}
    assert handoff.content_package.image_urls == (
        "https://assets.example/hero.png", "https://assets.example/detail.png",
    )
    assert [(row.artifact_id, row.audit_id) for row in handoff.asset_lineage] == [
        ("hero_r1", "audit-hero"), ("detail_r1", "audit-detail"),
    ]
    assert handoff.content_package.approval.status == "pending"
    assert handoff.missing_shot_ids == ("scene",)


def test_handoff_is_approved_only_when_every_selected_shot_is_verified_and_approved():
    handoff = build_content_package_handoff(
        product_id="product-42",
        suite_plan={"suite": {"items": [
            {"id": "hero", "selected": True},
            {"id": "detail", "selected": True},
        ]}},
        asset_decisions={
            "hero_r1": {"decision": "approved"},
            "detail_r1": {"decision": "approved"},
        },
        generation_audits={
            "hero_r1": _audit("hero", "https://assets.example/hero.png"),
            "detail_r1": _audit("detail", "https://assets.example/detail.png"),
        },
    )

    assert handoff.content_package.approval.status == "approved"
    assert handoff.missing_shot_ids == ()


def test_handoff_excludes_unreviewed_unverified_and_unselected_assets():
    handoff = build_content_package_handoff(
        product_id="product-42",
        suite_plan={"suite": {"items": [
            {"id": "pending", "selected": True},
            {"id": "unverified", "selected": True},
            {"id": "not-selected", "selected": False},
        ]}},
        asset_decisions={
            "pending_r1": {"decision": "pending"},
            "unverified_r1": {"decision": "approved"},
            "not_selected_r1": {"decision": "approved"},
        },
        generation_audits={
            "pending_r1": _audit("pending", "https://assets.example/pending.png"),
            "unverified_r1": _audit("unverified", "https://assets.example/unverified.png", verified=False),
            "not_selected_r1": _audit("not-selected", "https://assets.example/skip.png"),
        },
    )

    assert handoff.content_package.image_urls == ()
    assert handoff.asset_lineage == ()
    assert handoff.content_package.approval.status == "pending"
    assert handoff.missing_shot_ids == ("pending", "unverified")


def test_handoff_requires_product_id():
    with pytest.raises(ValueError, match="product_id"):
        build_content_package_handoff(
            product_id=" ", suite_plan={}, asset_decisions={}, generation_audits={}
        )


def _workbench_state(*, new_size_action="review", written_urls=None):
    return {
        "content_package": {
            "product_id": "3828811808",
            "fact_card_approved": True,
            "planning_scope_approved": True,
            "suite_approved": True,
            "storyboard_reviews": {"sc1": {"decision": "approved"}, "sz1": {"decision": "approved"}},
            "generated_image_miaoshou_decisions": {
                "sc1_r4": {"action": "keep", "status": "reviewed_locally"},
                "sz1_r4": {"action": "keep", "status": "reviewed_locally"},
                "sz1_r6_1784961073473": {"action": new_size_action, "status": "reviewed_locally"},
            },
            "dimension_overlay_upgrade": {"artifact_id": "sz1_r6_1784961073473", "overlay_version": "v4"},
            "miaoshou_ordered_images_write": {"status": "verified", "ordered_image_urls": written_urls or ["https://assets.example/sc1-r4.png", "https://assets.example/sz1-r4.png"]},
        },
        "review": {},
    }


def _workbench_audits():
    return {
        "sc1_r4": _audit("sc1", "https://assets.example/sc1-r4.png", audit_id="audit-sc1"),
        "sz1_r4": _audit("sz1", "https://assets.example/sz1-r4.png", audit_id="audit-size-r4"),
        "sz1_r6_1784961073473": _audit("sz1", "https://assets.example/sz1-r6.png", audit_id="audit-size-r6"),
    }


def test_workbench_handoff_does_not_reuse_old_keep_for_current_size_revision():
    handoff = build_workbench_content_package_handoff(
        product_id="3828811808", state=_workbench_state(),
        suite_plan={"suite": {"items": [{"id": "sc1"}, {"id": "sz1"}]}},
        generation_audits=_workbench_audits(), copy={"en": "Puppy wall sticker"},
    )

    assert handoff.content_package.approval.status == "pending"
    assert handoff.missing_shot_ids == ("sz1",)
    assert "sz1_r6_1784961073473 lacks final content approval" in " ".join(handoff.blockers)
    assert "sz1_r4" in handoff.superseded_artifact_ids
    assert handoff.stale_external_write is True


def test_workbench_handoff_approves_current_version_in_suite_order_and_marks_stale_write():
    handoff = build_workbench_content_package_handoff(
        product_id="3828811808", state=_workbench_state(new_size_action="keep"),
        suite_plan={"suite": {"items": [{"id": "sc1"}, {"id": "sz1"}]}},
        generation_audits=_workbench_audits(), copy={"en": "Puppy wall sticker"},
    )

    assert handoff.content_package.approval.status == "approved"
    assert handoff.content_package.image_urls == ("https://assets.example/sc1-r4.png", "https://assets.example/sz1-r6.png")
    assert [(row.artifact_id, row.audit_id) for row in handoff.asset_lineage] == [
        ("sc1_r4", "audit-sc1"), ("sz1_r6_1784961073473", "audit-size-r6"),
    ]
    assert handoff.stale_external_write is True


def test_workbench_handoff_does_not_block_explicitly_rejected_removed_shot():
    state = _workbench_state(new_size_action="keep")
    content = state["content_package"]
    content["asset_decisions"] = {
        "sc1_r4": {"decision": "approved"},
        "sz1_r6_1784961073473": {"decision": "rejected"},
    }


def _approve_source_only_state(state):
    content = state["content_package"]
    review = state["review"]
    signature = source_only_review_signature(
        review["image_actions"], review["image_order"]
    )
    video_action = str(review.get("video_action") or "none")
    video_url = str(review.get("video_url") or "")
    content.update(
        {
            "fact_card_approved": True,
            "planning_scope_approved": True,
            "source_only_review_signature": signature,
            "source_only_final_approval": {
                "schema_version": SOURCE_ONLY_FINAL_APPROVAL_SCHEMA,
                "status": "approved",
                "approved_by": "Kyle",
                "source_only_review_signature": signature,
                "video_action": video_action,
                "video_identity_digest": (
                    "sha256:e3b0c44298fc1c149afbf4c8996fb924"
                    "27ae41e4649b934ca495991b7852b855"
                ),
                "approval_digest": source_only_final_approval_digest(
                    review_signature=signature,
                    video_action=video_action,
                    video_url=video_url,
                    approved_by="Kyle",
                ),
                "approved_at": "2026-07-29T00:00:00+00:00",
            },
        }
    )
    return state
    content["generated_image_miaoshou_decisions"][
        "sz1_r6_1784961073473"
    ] = {
        "action": "remove",
        "status": "reviewed_locally",
    }
    state["review"] = {
        "image_actions": [
            {"action": "keep", "url": "https://assets.example/source-1.jpg"},
        ],
        "image_order": [
            "https://assets.example/source-1.jpg",
            "https://assets.example/sc1-r4.png",
        ],
    }

    handoff = build_workbench_content_package_handoff(
        product_id="3828811808",
        state=state,
        suite_plan={"suite": {"items": [{"id": "sc1"}, {"id": "sz1"}]}},
        generation_audits=_workbench_audits(),
        copy={"en": "Puppy wall sticker"},
    )

    assert handoff.missing_shot_ids == ()
    assert not any("sz1" in blocker for blocker in handoff.blockers)
    assert handoff.content_package.approval.status == "approved"
    assert handoff.content_package.image_urls == (
        "https://assets.example/source-1.jpg",
        "https://assets.example/sc1-r4.png",
    )


def test_source_only_handoff_uses_exact_source_order_and_ignores_generated_history():
    source_a = "https://assets.example/source-a.jpg"
    source_b = "https://assets.example/source-b.jpg"
    generated = "https://assets.example/generated.png"
    state = _approve_source_only_state({
        "content_package": {
            "product_id": "source-product",
            "content_strategy": "source_only",
            "suite_approved": False,
            "miaoshou_ordered_images_write": {
                "status": "verified",
                "ordered_image_urls": [source_a, source_b, generated],
            },
        },
        "review": {
            "image_actions": [
                {"action": "keep", "url": source_a},
                {"action": "remove", "url": "https://assets.example/rejected.jpg"},
                {"action": "keep", "url": source_b},
            ],
            "image_order": [source_b, source_a],
            "video_action": "none",
            "video_url": "",
        },
    })

    handoff = build_workbench_content_package_handoff(
        product_id="source-product",
        state=state,
        suite_plan={"suite": {"items": [{"id": "ai-shot"}]}},
        generation_audits={
            "ai-shot_r1": _audit("ai-shot", generated),
        },
        copy={"en": "Source-approved copy"},
    )

    assert handoff.content_package.approval.status == "approved"
    assert handoff.content_package.image_urls == (source_b, source_a)
    assert all(row.asset_type == "source" for row in handoff.asset_lineage)
    assert handoff.missing_shot_ids == ()
    assert handoff.superseded_artifact_ids == ()
    assert handoff.stale_external_write is True


@pytest.mark.parametrize(
    ("image_actions", "image_order"),
    [
        (
            [
                {"action": "keep", "url": "https://assets.example/source.jpg"},
                {"action": "review", "url": "https://assets.example/pending.jpg"},
            ],
            ["https://assets.example/source.jpg"],
        ),
        (
            [{"action": "keep", "url": "https://assets.example/source.jpg"}],
            [
                "https://assets.example/source.jpg",
                "https://assets.example/generated.png",
            ],
        ),
    ],
)
def test_source_only_handoff_rejects_incomplete_review_or_non_source_order(
    image_actions, image_order
):
    handoff = build_workbench_content_package_handoff(
        product_id="source-product",
        state={
            "content_package": {
                "content_strategy": "source_only",
                "fact_card_approved": True,
                "planning_scope_approved": True,
            },
            "review": {
                "image_actions": image_actions,
                "image_order": image_order,
            },
        },
        suite_plan={},
        generation_audits={},
        copy={"en": "Source-approved copy"},
    )

    assert handoff.content_package.approval.status == "pending"
    assert handoff.blockers


def test_source_only_handoff_rejects_stale_approval_after_order_or_video_drift():
    source_a = "https://assets.example/source-a.jpg"
    source_b = "https://assets.example/source-b.jpg"
    state = _approve_source_only_state(
        {
            "content_package": {"content_strategy": "source_only"},
            "review": {
                "image_actions": [
                    {"action": "keep", "url": source_a},
                    {"action": "keep", "url": source_b},
                ],
                "image_order": [source_a, source_b],
                "video_action": "none",
                "video_url": "",
            },
        }
    )
    state["review"]["image_order"] = [source_b, source_a]

    handoff = build_workbench_content_package_handoff(
        product_id="source-product",
        state=state,
        suite_plan={},
        generation_audits={},
        copy={"en": "Source-approved copy"},
    )

    assert handoff.content_package.approval.status == "pending"
    assert "source-only final content approval is missing or stale" in handoff.blockers


def test_overlay_artifact_resolves_its_non_sz1_shot_from_the_audit():
    state = _workbench_state()
    content = state["content_package"]
    content["storyboard_reviews"] = {"dimension-card": {"decision": "approved"}}
    content["generated_image_miaoshou_decisions"] = {
        "dimension-card_r4": {"action": "keep", "status": "reviewed_locally"},
        "dimension-card_r6": {"action": "keep", "status": "reviewed_locally"},
    }
    content["dimension_overlay_upgrade"] = {"artifact_id": "dimension-card_r6", "overlay_version": "v4"}
    content.pop("miaoshou_ordered_images_write")
    audits = {
        "dimension-card_r4": _audit("dimension-card", "https://assets.example/dimension-r4.png"),
        "dimension-card_r6": _audit("dimension-card", "https://assets.example/dimension-r6.png"),
    }

    handoff = build_workbench_content_package_handoff(
        product_id="3828811808", state=state,
        suite_plan={"suite": {"items": [{"id": "dimension-card"}]}},
        generation_audits=audits, copy={"en": "Puppy wall sticker"},
    )

    assert handoff.content_package.approval.status == "approved"
    assert handoff.asset_lineage[0].artifact_id == "dimension-card_r6"


def test_workbench_handoff_compares_iso_created_at_as_instants_across_offsets():
    state = {
        "content_package": {
            "product_id": "p-1", "fact_card_approved": True,
            "planning_scope_approved": True, "suite_approved": True,
            "storyboard_reviews": {"scene": {"decision": "approved"}},
            "generated_image_miaoshou_decisions": {
                "scene_r9": {"action": "keep", "status": "reviewed_locally"},
                "scene_r1": {"action": "keep", "status": "reviewed_locally"},
            },
        },
    }
    earlier = _audit("scene", "https://assets.example/scene-earlier.png")
    earlier["created_at"] = "2026-07-25T10:00:00+08:00"
    later = _audit("scene", "https://assets.example/scene-later.png")
    later["created_at"] = "2026-07-25T03:00:00+00:00"
    handoff = build_workbench_content_package_handoff(
        product_id="p-1", state=state, suite_plan={"suite": {"items": [{"id": "scene"}]}},
        generation_audits={"scene_r9": earlier, "scene_r1": later}, copy={"en": "Copy"},
    )

    assert handoff.asset_lineage[0].artifact_id == "scene_r1"


def test_workbench_handoff_merges_kyles_final_five_images_in_saved_order():
    state = _workbench_state(new_size_action="keep", written_urls=[
        "https://assets.example/sc1-r4.png", "https://assets.example/source-2.jpg",
        "https://assets.example/source-1.jpg", "https://assets.example/source-3.jpg",
        "https://assets.example/sz1-r6.png", "https://assets.example/removed-legacy.jpg",
    ])
    state["review"] = {
        "image_actions": [
            {"action": "keep", "url": "https://assets.example/source-1.jpg"},
            {"action": "keep", "url": "https://assets.example/source-2.jpg"},
            {"action": "keep", "url": "https://assets.example/source-3.jpg"},
            {"action": "remove", "url": "https://assets.example/source-4.jpg"},
        ],
        "image_order": [
            "https://assets.example/sc1-r4.png",
            "https://assets.example/source-2.jpg",
            "https://assets.example/source-1.jpg",
            "https://assets.example/source-3.jpg",
            "https://assets.example/sz1-r6.png",
        ],
    }
    state["content_package"]["generated_image_miaoshou_decisions"]["sz1_r4"] = {
        "action": "remove", "status": "reviewed_locally"
    }
    state["content_package"]["asset_decisions"] = {"sc1_r4": {"decision": "approved"}}
    handoff = build_workbench_content_package_handoff(
        product_id="3828811808", state=state,
        suite_plan={"suite": {"items": [{"id": "sc1"}, {"id": "sz1"}]}},
        generation_audits=_workbench_audits(), copy={"en": "Puppy wall sticker"},
    )

    assert handoff.content_package.approval.status == "approved"
    assert handoff.content_package.image_urls == (
        "https://assets.example/sc1-r4.png", "https://assets.example/source-2.jpg",
        "https://assets.example/source-1.jpg", "https://assets.example/source-3.jpg",
        "https://assets.example/sz1-r6.png",
    )
    assert [row.asset_type for row in handoff.asset_lineage] == [
        "generated", "source", "source", "source", "generated",
    ]
    assert handoff.asset_lineage[1].audit_id == "review.image_actions[1]"
    assert handoff.asset_lineage[0].decision_source == "asset_decisions.approved"
    assert handoff.asset_lineage[-1].decision_source == "generated_image_miaoshou_decisions.keep_reviewed_locally"
    assert handoff.stale_external_write is True


def test_workbench_handoff_pending_source_and_unknown_order_url_block_approval():
    state = _workbench_state(new_size_action="keep")
    state["review"] = {
        "image_actions": [
            {"action": "keep", "url": "https://assets.example/source-kept.jpg"},
            {"action": "review", "url": "https://assets.example/source-pending.jpg"},
        ],
        "image_order": [
            "https://assets.example/source-kept.jpg",
            "https://assets.example/unknown.jpg",
        ],
    }
    handoff = build_workbench_content_package_handoff(
        product_id="3828811808", state=state,
        suite_plan={"suite": {"items": [{"id": "sc1"}, {"id": "sz1"}]}},
        generation_audits=_workbench_audits(), copy={"en": "Puppy wall sticker"},
    )

    assert "https://assets.example/source-pending.jpg" not in handoff.content_package.image_urls
    assert handoff.content_package.approval.status == "pending"
    assert any("source image 2" in blocker for blocker in handoff.blockers)
    assert any("unknown URL" in blocker for blocker in handoff.blockers)


def test_workbench_handoff_deduplicates_source_and_generated_urls_deterministically():
    state = _workbench_state(new_size_action="keep")
    state["review"] = {
        "image_actions": [
            {"action": "keep", "url": "https://assets.example/sc1-r4.png"},
            {"action": "keep", "url": "https://assets.example/source-2.jpg"},
        ],
        "image_order": ["https://assets.example/sc1-r4.png"],
    }
    handoff = build_workbench_content_package_handoff(
        product_id="3828811808", state=state,
        suite_plan={"suite": {"items": [{"id": "sc1"}, {"id": "sz1"}]}},
        generation_audits=_workbench_audits(), copy={"en": "Puppy wall sticker"},
    )

    assert handoff.content_package.image_urls.count("https://assets.example/sc1-r4.png") == 1
    assert handoff.asset_lineage[0].asset_type == "source"
