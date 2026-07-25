import pytest

from domains.content_operations.content_package_adapter import build_content_package_handoff


def _audit(shot_id, url, *, verified=True, audit_id="audit-1"):
    return {
        "shot_id": shot_id,
        "audit_id": audit_id,
        "download_verified": verified,
        "final_response": {"result": {"data": [{"url": url}]}},
    }


def test_handoff_uses_only_approved_assets_in_selected_suite_order_with_lineage():
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
    assert handoff.content_package.approval.status == "approved"


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


def test_handoff_requires_product_id():
    with pytest.raises(ValueError, match="product_id"):
        build_content_package_handoff(
            product_id=" ", suite_plan={}, asset_decisions={}, generation_audits={}
        )
