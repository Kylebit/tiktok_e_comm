from __future__ import annotations

import copy

import pytest

from domains.channel_operations.omnichannel_orchestrator import (
    build_omnichannel_publication_plan,
)
from domains.content_operations import release_listing_copy_identity
from modules.products import server as product_server
from shared_platform import release_control
from shared_platform.contracts import (
    ApprovalRecord,
    ApprovedProductPackage,
    ContentPackage,
    ProductRecord,
)
from tests.test_product_release_v1 import _dashboard


def _copy(**changes) -> dict:
    value = copy.deepcopy(_dashboard()["listing_copy"])
    value.update(changes)
    return value


def test_release_copy_identity_excludes_timestamps_but_keeps_candidate_content():
    first, blockers = release_listing_copy_identity(
        _copy(),
        approved_product_title=_dashboard()["product"]["title"],
        current_input_signature="sha256:copy-facts-v1",
        target_labels=["miaoshou:COMMON", "tiktok:MX"],
    )
    changed_time = _copy()
    changed_time["candidates"][0]["created_at"] = "2030-01-01T00:00:00Z"
    second, second_blockers = release_listing_copy_identity(
        changed_time,
        approved_product_title=_dashboard()["product"]["title"],
        current_input_signature="sha256:copy-facts-v1",
        target_labels=["miaoshou:COMMON", "tiktok:MX"],
    )
    changed_title = _copy()
    changed_title["candidates"][0]["title"] += " Removable"
    third, third_blockers = release_listing_copy_identity(
        changed_title,
        approved_product_title=_dashboard()["product"]["title"],
        current_input_signature="sha256:copy-facts-v1",
        target_labels=["miaoshou:COMMON", "tiktok:MX"],
    )

    assert blockers == second_blockers == third_blockers == []
    assert first == second
    assert first != third


def test_candidate_identity_changes_plan_id_and_token_but_timestamp_does_not():
    approval = ApprovalRecord(
        approval_id="approval:product",
        subject_type="product",
        subject_id="offer-1",
        status="approved",
        approved_by="Kyle",
    )
    product = ApprovedProductPackage(
        package_id="product:offer-1",
        product=ProductRecord(
            product_id="offer-1",
            seller_sku="0954",
            title=_dashboard()["product"]["title"],
        ),
        approval=approval,
    )
    content = ContentPackage(
        package_id="content:offer-1",
        product_id="offer-1",
        image_urls=("https://assets.example/1.jpg",),
        approval=ApprovalRecord(
            approval_id="approval:content",
            subject_type="content_package",
            subject_id="content:offer-1",
            status="approved",
            approved_by="Kyle",
        ),
    )
    base, _ = release_listing_copy_identity(
        _copy(),
        approved_product_title=product.product.title,
        current_input_signature="sha256:copy-facts-v1",
        target_labels=["miaoshou:COMMON", "tiktok:MX"],
    )
    timestamp_only = _copy()
    timestamp_only["candidates"][0]["created_at"] = "2030-01-01T00:00:00Z"
    timestamp_identity, _ = release_listing_copy_identity(
        timestamp_only,
        approved_product_title=product.product.title,
        current_input_signature="sha256:copy-facts-v1",
        target_labels=["miaoshou:COMMON", "tiktok:MX"],
    )
    candidate_change = copy.deepcopy(base)
    candidate_change["candidates"][0]["title"] += " Updated"

    def plan(identity):
        return build_omnichannel_publication_plan(
            product,
            content,
            site_selection={"miaoshou": ["COMMON"], "tiktok": ["MX"]},
            commercial_scope={"listing_copy": identity},
        )

    first = plan(base)
    same = plan(timestamp_identity)
    changed = plan(candidate_change)

    assert first.plan_id == same.plan_id
    assert first.approval.confirmation_token == same.approval.confirmation_token
    assert changed.plan_id != first.plan_id
    assert changed.approval.confirmation_token != first.approval.confirmation_token


def test_confirmed_publish_fields_are_not_blocked_by_internal_copy_metadata():
    """Internal copy workflow metadata must not repeat Kyle's confirmation."""

    dashboard = _dashboard()
    listing_copy = dashboard["listing_copy"]
    listing_copy.update(
        {
            "status": "draft_pending_kyle_review",
            "current_input_signature": "sha256:new-product-facts",
            "semantic_master_en": "A different internal English master",
        }
    )

    _identity, blockers = release_listing_copy_identity(
        listing_copy,
        approved_product_title=dashboard["product"]["title"],
        current_input_signature=listing_copy["current_input_signature"],
        target_labels=dashboard["publication_scope"]["selected_labels"],
    )

    assert blockers == []


def test_stale_internal_copy_metadata_does_not_repeat_operator_approval(
    monkeypatch,
):
    dashboard = _dashboard()
    dashboard["listing_copy"]["current_input_signature"] = "sha256:new-facts"
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    view = product_server._product_workspace_view(dashboard)

    assert view["release_v1"]["eligible_for_plan_approval"] is True
    assert view["release_v1"]["recovery_actions"] == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [(
        lambda value: value.update({"candidates": []}),
        "approved listing title candidate is missing for tiktok:MX",
    )],
)
def test_draft_missing_or_incomplete_copy_is_not_eligible(mutate, expected):
    dashboard = _dashboard()
    mutate(dashboard["listing_copy"])

    view = product_server._product_workspace_view(dashboard)

    assert view["release_v1"]["eligible_for_plan_approval"] is False
    assert view["release_v1"]["publish_ready"] is False
    assert any(expected in blocker for blocker in view["release_v1"]["blockers"])


def test_unadopted_copy_metadata_does_not_expose_redundant_recovery():
    dashboard = _dashboard()
    dashboard["listing_copy"]["status"] = "draft_pending_kyle_review"

    view = product_server._product_workspace_view(dashboard)

    assert view["release_v1"]["eligible_for_plan_approval"] is True
    assert view["release_v1"]["recovery_actions"] == []
