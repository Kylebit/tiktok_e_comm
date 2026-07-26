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
        state={"offer_id": "3828811808", "_revision": 12, "review": {"image_order": ["kept.png"], "image_actions": [{"action": "keep"}]}}, product_row=_product(),
        content_package=_content(), seller_sku="0946", known_seller_skus=(),
        user_approved=True, approval_fact=_approval(), expected_revision=12,
    )

    assert result.blockers == ()
    assert result.approved_package is not None
    assert result.state_patch["review"] == {"image_order": ["kept.png"], "image_actions": [{"action": "keep"}], "seller_sku": "0946", "fields_locked": True}
    assert result.state_patch["product_approval"]["content_package_id"] == "content:3828811808"
    assert result.state_patch["product_approval"]["approved_at"] == datetime(2026, 7, 25, 12, tzinfo=timezone.utc).isoformat()


class _SqliteStyleRow:
    def __init__(self, values):
        self.values = values

    def keys(self):
        return self.values.keys()

    def __getitem__(self, key):
        return self.values[key]


def test_preview_accepts_sqlite_style_approval_facts_and_records_source_reference():
    fact = _SqliteStyleRow({**_approval(), "source_reference": "workbench:3828811808"})
    result = preview_product_approval_lock(
        state={"offer_id": "3828811808"}, product_row=_product(), content_package=_content(),
        seller_sku="0946", known_seller_skus=(), user_approved=True, approval_fact=fact,
    )

    assert result.approved_package is not None
    assert result.state_patch["product_approval"]["source_reference"] == "workbench:3828811808"


def test_preview_blocks_user_approval_conflict_and_stale_revision_but_not_content():
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
    assert "content package approval is required" not in result.blockers


def test_numeric_seller_sku_conflicts_on_last_four_digits_but_non_numeric_values_are_exact():
    numeric = preview_product_approval_lock(
        state={"offer_id": "3828811808"}, product_row=_product(), content_package=_content(),
        seller_sku="0946", known_seller_skus=("990946",), user_approved=True, approval_fact=_approval(),
    )
    short_numeric_product = {**_product(), "seller_sku": "946"}
    short_numeric = preview_product_approval_lock(
        state={"offer_id": "3828811808"}, product_row=short_numeric_product, content_package=_content(),
        seller_sku="946", known_seller_skus=("0946",), user_approved=True, approval_fact=_approval(),
    )
    short_seventeen_product = {**_product(), "seller_sku": "17"}
    short_seventeen = preview_product_approval_lock(
        state={"offer_id": "3828811808"}, product_row=short_seventeen_product, content_package=_content(),
        seller_sku="17", known_seller_skus=("990017",), user_approved=True, approval_fact=_approval(),
    )
    non_numeric_product = {**_product(), "seller_sku": "DOG-0946"}
    non_numeric = preview_product_approval_lock(
        state={"offer_id": "3828811808"}, product_row=non_numeric_product, content_package=_content(),
        seller_sku="DOG-0946", known_seller_skus=("CAT-0946",), user_approved=True, approval_fact=_approval(),
    )

    assert "seller_sku is already present in the catalog" in numeric.blockers
    assert "seller_sku is already present in the catalog" in short_numeric.blockers
    assert "seller_sku is already present in the catalog" in short_seventeen.blockers
    assert non_numeric.approved_package is not None


def test_preview_requires_named_and_timestamped_approval_and_handles_bad_revision_as_blocker():
    missing_audit_fields = preview_product_approval_lock(
        state={"offer_id": "3828811808", "_revision": "not-a-number"}, product_row=_product(),
        content_package=_content(), seller_sku="0946", known_seller_skus=(), user_approved=True,
        approval_fact={**_approval(), "approved_by": "", "approved_at": ""},
    )

    assert "approval approved_by is required" in missing_audit_fields.blockers
    assert "approval approved_at is required" in missing_audit_fields.blockers
    assert "state _revision must be an integer" in missing_audit_fields.blockers


def test_content_changes_do_not_supersede_product_facts_but_product_changes_do():
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
    changed_content_result = preview_product_approval_lock(
        state=state, product_row=_product(), content_package=changed_content, seller_sku="0946",
        known_seller_skus=(), user_approved=True, approval_fact={**_approval(), "approval_id": "product-approval-2"},
    )
    changed_product = preview_product_approval_lock(
        state=state,
        product_row={**_product(), "product_name": "Updated dog wall decal"},
        content_package=changed_content,
        seller_sku="0946",
        known_seller_skus=(),
        user_approved=True,
        approval_fact={**_approval(), "approval_id": "product-approval-3"},
    )

    assert repeated.idempotent is True
    assert repeated.state_patch == {}
    assert changed_content_result.idempotent is True
    assert changed_content_result.supersedes_approval_id is None
    assert changed_product.supersedes_approval_id == "product-approval-1"
    assert changed_product.approved_package is not None


def test_equivalent_integral_float_facts_do_not_supersede_approval():
    initial = preview_product_approval_lock(
        state={"offer_id": "3828811808"},
        product_row=_product(),
        content_package=_content(),
        seller_sku="0946",
        known_seller_skus=(),
        user_approved=True,
        approval_fact=_approval(),
        approval_input_facts={"package_cm": [58, 34, 0.02]},
    )
    state = {
        "offer_id": "3828811808",
        "product_approval": initial.state_patch["product_approval"],
    }

    repeated = preview_product_approval_lock(
        state=state,
        product_row=_product(),
        content_package=_content(),
        seller_sku="0946",
        known_seller_skus=(),
        user_approved=True,
        approval_fact=_approval(),
        approval_input_facts={"package_cm": [58.0, 34.0, 0.02]},
    )

    assert repeated.idempotent is True
    assert repeated.state_patch == {}
