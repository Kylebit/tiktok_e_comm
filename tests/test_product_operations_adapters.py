from datetime import datetime, timezone

import pytest

from domains.product_operations.adapters import (
    approved_product_package_from_facts,
    product_record_from_legacy_row,
)


def test_catalog_sku_row_becomes_a_product_record_without_mutating_the_fixture():
    row = {
        "product_id": "tk-product-17",
        "seller_sku": "770017-red",
        "product_name": "Red storage basket",
        "sku_id": "tk-sku-17",
        "platform": "tiktok",
        "region": "PH",
        "shop_cipher": "shop-1",
        "status": "ACTIVATE",
    }

    product = product_record_from_legacy_row(row)

    assert product.product_id == "tk-product-17"
    assert product.seller_sku == "770017-red"
    assert product.sku_ids == ("tk-sku-17",)
    assert dict(product.attributes) == {
        "platform": "tiktok",
        "region": "PH",
        "shop_cipher": "shop-1",
        "status": "ACTIVATE",
    }
    assert row["seller_sku"] == "770017-red"


def test_workbench_dictionary_and_complete_approval_fact_become_a_package():
    product_row = {
        "product_id": "collect-44",
        "itemNum": "0044",
        "title": "Reusable lunch box",
        "sku_ids": ["variant-a", "variant-b", "variant-a"],
    }
    approval_fact = {
        "package_id": "product-package-44",
        "approval_id": "approval-44",
        "subject_type": "product",
        "subject_id": "collect-44",
        "status": "approved",
        "approved_by": "operator-7",
        "approved_at": "2026-07-25T09:30:00Z",
        "source_reference": "workbench:collect-44",
    }

    package = approved_product_package_from_facts(product_row, approval_fact)

    assert package.package_id == "product-package-44"
    assert package.product.sku_ids == ("variant-a", "variant-b")
    assert package.approval.approved_at == datetime(2026, 7, 25, 9, 30, tzinfo=timezone.utc)
    assert package.source_reference == "workbench:collect-44"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"status": "pending"}, "status"),
        ({"subject_id": "another-product"}, "subject_id"),
        ({"package_id": ""}, "package_id"),
    ],
)
def test_package_is_not_created_when_approval_facts_are_incomplete(change, message):
    product_row = {"product_id": "p-1", "seller_sku": "sku-1", "product_name": "Product"}
    approval_fact = {
        "package_id": "pkg-1",
        "approval_id": "approval-1",
        "subject_type": "product",
        "subject_id": "p-1",
        "status": "approved",
    }
    approval_fact.update(change)

    with pytest.raises(ValueError, match=message):
        approved_product_package_from_facts(product_row, approval_fact)
