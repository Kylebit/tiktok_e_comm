from datetime import datetime, timezone

from domains.product_operations.approval_lock import preview_product_approval_lock
from shared_platform.contracts import ApprovalRecord, ContentPackage


def _content(product_id="3828811808"):
    package_id = f"content:{product_id}"
    return ContentPackage(
        package_id, product_id, {"en": "Dog wall decal"}, ("https://assets.example/1.png",),
        approval=ApprovalRecord("content-approval-1", "content_package", package_id, "approved"),
    )


def _approval():
    return {
        "approval_id": "product-approval-1", "package_id": "product:3828811808",
        "subject_type": "product", "subject_id": "3828811808", "status": "approved",
        "approved_by": "kyle", "approved_at": "2026-07-25T12:00:00Z",
    }


def _product():
    return {"product_id": "3828811808", "seller_sku": "0946", "product_name": "Dog wall decal"}


def test_preview_requires_explicit_approval_and_returns_a_persistable_lock_patch():
    result = preview_product_approval_lock(
        state={"offer_id": "3828811808", "_revision": 12}, product_row=_product(),
        content_package=_content(), seller_sku="0946", known_seller_skus=(),
        user_approved=True, approval_fact=_approval(), expected_revision=12,
    )

    assert result.blockers == ()
    assert result.approved_package is not None
    assert result.state_patch["review"] == {"seller_sku": "0946", "fields_locked": True}
    assert result.state_patch["product_approval"]["content_package_id"] == "content:3828811808"
    assert result.state_patch["product_approval"]["approved_at"] == datetime(2026, 7, 25, 12, tzinfo=timezone.utc).isoformat()


def test_preview_blocks_missing_user_approval_conflict_stale_revision_and_pending_content():
    pending = ContentPackage("content:3828811808", "3828811808")
    result = preview_product_approval_lock(
        state={"offer_id": "3828811808", "_revision": 9}, product_row=_product(),
        content_package=pending, seller_sku="0946", known_seller_skus=("0946",),
        user_approved=False, approval_fact=_approval(), expected_revision=8,
    )

    assert result.approved_package is None
    assert "explicit user_approved=True is required" in result.blockers
    assert "seller_sku is already present in the catalog" in result.blockers
    assert "state revision is stale" in result.blockers
    assert "content package approval is required" in result.blockers


def test_identical_active_lock_is_idempotent_but_changed_content_supersedes_it():
    initial = preview_product_approval_lock(
        state={"offer_id": "3828811808"}, product_row=_product(), content_package=_content(),
        seller_sku="0946", known_seller_skus=(), user_approved=True, approval_fact=_approval(),
    )
    state = {"offer_id": "3828811808", "product_approval": initial.state_patch["product_approval"]}
    repeated = preview_product_approval_lock(
        state=state, product_row=_product(), content_package=_content(), seller_sku="0946",
        known_seller_skus=(), user_approved=True, approval_fact=_approval(),
    )
    changed_content = ContentPackage(
        "content:3828811808", "3828811808", {"en": "Updated copy"}, ("https://assets.example/2.png",),
        approval=_content().approval,
    )
    changed = preview_product_approval_lock(
        state=state, product_row=_product(), content_package=changed_content, seller_sku="0946",
        known_seller_skus=(), user_approved=True, approval_fact={**_approval(), "approval_id": "product-approval-2"},
    )

    assert repeated.idempotent is True
    assert repeated.state_patch == {}
    assert changed.supersedes_approval_id == "product-approval-1"
    assert changed.approved_package is not None
