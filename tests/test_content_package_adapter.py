import pytest

from domains.content_operations.content_package_adapter import (
    build_content_package_handoff,
    build_workbench_content_package_handoff,
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
            "miaoshou_ordered_images_write": {"verified": True, "ordered_image_urls": written_urls or ["https://assets.example/sc1-r4.png", "https://assets.example/sz1-r4.png"]},
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
