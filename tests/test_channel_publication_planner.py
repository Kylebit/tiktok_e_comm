import ast
import inspect
import socket
import sqlite3
import urllib.request

import pytest

from domains.channel_operations.publication_planner import (
    SUPPORTED_CHANNELS,
    build_publication_plan,
)
from shared_platform.contracts import (
    ApprovalRecord,
    ApprovedProductPackage,
    ContentPackage,
    ProductRecord,
)


def _product_package(*, approved: bool = True) -> ApprovedProductPackage:
    product = ProductRecord("product-1", "SKU-100", "Desk organiser")
    approval = ApprovalRecord(
        "approval-1", "product", "product-1", "approved" if approved else "pending"
    )
    return ApprovedProductPackage("package-1", product, approval)


def _content_package(*, product_id: str = "product-1") -> ContentPackage:
    approval = ApprovalRecord(
        "content-approval-1", "content_package", "content-1", "approved"
    )
    return ContentPackage(
        "content-1", product_id, {"en": "A tidy desk in seconds."}, ("https://img.example/a.jpg",), approval=approval
    )


def test_builds_deterministic_approval_gated_drafts_for_every_supported_channel():
    plan = build_publication_plan(_product_package(), _content_package())

    assert plan.dry_run is True
    assert plan.approval_required is True
    assert [draft.listing.channel for draft in plan.drafts] == list(SUPPORTED_CHANNELS)
    assert all(draft.listing.status == "draft" for draft in plan.drafts)
    assert all(draft.action == "create_draft" for draft in plan.drafts)
    assert all(not draft.missing_conditions for draft in plan.drafts)


def test_reports_missing_conditions_without_content_or_product_approval():
    plan = build_publication_plan(_product_package(approved=False), channels=("ozon", "miaoshou"))

    assert [draft.listing.listing_id for draft in plan.drafts] == [
        "draft:ozon:package-1",
        "draft:miaoshou:package-1",
    ]
    for draft in plan.drafts:
        assert draft.missing_conditions == (
            "product package approval is not approved",
            "content package is required before channel submission",
        )


def test_reports_invalid_content_without_touching_any_external_boundary():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("the pure planner must not access an external boundary")

    original_socket = socket.create_connection
    original_connect = sqlite3.connect
    original_urlopen = urllib.request.urlopen
    socket.create_connection = forbidden
    sqlite3.connect = forbidden
    urllib.request.urlopen = forbidden
    try:
        plan = build_publication_plan(_product_package(), _content_package(product_id="other-product"))
    finally:
        socket.create_connection = original_socket
        sqlite3.connect = original_connect
        urllib.request.urlopen = original_urlopen

    assert plan.drafts[0].missing_conditions == (
        "content package product id does not match product package",
    )


def test_missing_or_mismatched_approval_records_never_pass_the_plan_gate():
    no_content_approval = ContentPackage(
        "content-1",
        "product-1",
        {"en": "Copy"},
        ("https://img.example/a.jpg",),
    )
    wrong_subject = ContentPackage(
        "content-1",
        "product-1",
        {"en": "Copy"},
        ("https://img.example/a.jpg",),
        approval=ApprovalRecord("approval-2", "content_package", "other-package", "approved"),
    )

    assert build_publication_plan(
        _product_package(), no_content_approval
    ).drafts[0].missing_conditions == ("content package approval is required",)
    assert build_publication_plan(
        _product_package(), wrong_subject
    ).drafts[0].missing_conditions == (
        "content package approval subject does not match package",
    )


def test_rejects_unknown_or_duplicate_publish_destinations():
    with pytest.raises(ValueError, match="unsupported"):
        build_publication_plan(_product_package(), channels=("amazon",))
    with pytest.raises(ValueError, match="duplicates"):
        build_publication_plan(_product_package(), channels=("ozon", "ozon"))


def test_planner_has_no_channel_client_or_persistence_imports():
    import domains.channel_operations.publication_planner as planner

    imports = {
        alias.name
        for node in ast.walk(ast.parse(inspect.getsource(planner)))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(planner)))
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not {"sqlite3", "requests", "urllib", "socket"}.intersection(imports)
    assert not any(name.startswith("modules.ozon") for name in imports)
    assert not any(name.startswith("modules.shopee") for name in imports)
    assert not any(name.startswith("modules.miaoshou") for name in imports)
