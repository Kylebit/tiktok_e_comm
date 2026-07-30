from __future__ import annotations

import copy

import pytest

from domains.channel_operations.omnichannel_orchestrator import (
    build_omnichannel_publication_plan,
)
from domains.content_operations import release_listing_copy_identity
from modules.products import server as product_server
from shared_platform import release_control, release_store
from shared_platform.contracts import (
    ApprovalRecord,
    ApprovedProductPackage,
    ContentPackage,
    ProductRecord,
)
from shared_platform.release_store import ReleaseStore
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


def test_stale_copy_blocks_plan_eligibility_and_publish_before_run_creation(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    dashboard["listing_copy"]["current_input_signature"] = "sha256:new-facts"
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    view = product_server._product_workspace_view(dashboard)

    assert view["release_v1"]["eligible_for_plan_approval"] is False
    assert view["release_v1"]["publish_ready"] is False
    assert "listing copy input signature is stale" in view["release_v1"]["blockers"]
    assert view["release_v1"]["recovery_actions"] == [
        {
            "code": "refresh_listing_copy",
            "label": "重新生成平台文案",
            "detail": (
                "商品事实或所选规格在上次采用文案后发生了变化。"
                "请按当前已批准事实重新生成候选，再由 Kyle 明确采用 EN MASTER。"
            ),
            "next_codes": ["refresh_listing_copy", "adopt_listing_copy"],
            "marketplace_writes_performed": [],
        }
    ]

    status, payload = product_server._publish_selected_release(
        {
            "offer_id": dashboard["product"]["offer_id"],
            "publication_targets": dashboard["publication_scope"][
                "selected_labels"
            ],
            "plan_id": "omnichannel:any",
            "confirmation_token": "PUBLISH-ANY",
            "confirm_publish": True,
        }
    )

    assert status == 409
    assert "listing copy input signature is stale" in payload["blockers"]
    assert not store.path.exists()


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: value.update(
                {"status": "draft_pending_kyle_review"}
            ),
            "listing copy must be adopted",
        ),
        (
            lambda value: value.update({"semantic_master_en": ""}),
            "semantic English master is missing",
        ),
        (
            lambda value: value.update({"candidates": []}),
            "approved listing title candidate is missing for tiktok:MX",
        ),
    ],
)
def test_draft_missing_or_incomplete_copy_is_not_eligible(mutate, expected):
    dashboard = _dashboard()
    mutate(dashboard["listing_copy"])

    view = product_server._product_workspace_view(dashboard)

    assert view["release_v1"]["eligible_for_plan_approval"] is False
    assert view["release_v1"]["publish_ready"] is False
    assert any(expected in blocker for blocker in view["release_v1"]["blockers"])


def test_unadopted_current_copy_exposes_an_explicit_adoption_recovery_action():
    dashboard = _dashboard()
    dashboard["listing_copy"]["status"] = "draft_pending_kyle_review"

    view = product_server._product_workspace_view(dashboard)

    assert view["release_v1"]["eligible_for_plan_approval"] is False
    assert view["release_v1"]["recovery_actions"] == [
        {
            "code": "adopt_listing_copy",
            "label": "去采用当前 EN MASTER",
            "detail": (
                "平台文案候选已经生成，但尚未绑定到当前商品事实。"
                "采用后再重新核对并批准发布计划。"
            ),
            "next_codes": ["adopt_listing_copy"],
            "marketplace_writes_performed": [],
        }
    ]
