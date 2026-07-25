from datetime import datetime, timezone
from decimal import Decimal
import json

from domains.channel_operations import ChannelListing
from domains.content_operations import ContentPackage
from domains.data_operations import FinancialFact
from domains.product_operations import ApprovedProductPackage, ProductRecord
from domains.supply_chain_operations import InventorySnapshot
from shared_platform.contracts import ApprovalRecord, contract_payload
from shared_platform.registry import cli_registry, http_registry, owner_for_http_path


def test_domain_contracts_are_importable_and_serialize_without_adapters():
    approval = ApprovalRecord("apr-1", "product", "prod-1", "approved")
    product = ProductRecord("prod-1", "SKU-1", "Demo product", ("SKU-1",))
    package = ApprovedProductPackage("pkg-1", product, approval)
    content = ContentPackage("content-1", "prod-1", {"en": "Demo"})
    listing = ChannelListing("tiktok", "listing-1", package.package_id, content.package_id, "draft")
    inventory = InventorySnapshot("SKU-1", 3, "Seaya", datetime.now(timezone.utc))
    occurred_at = datetime.now(timezone.utc)
    fact = FinancialFact("fact-1", "cost", Decimal("10.00"), "CNY", occurred_at)

    assert contract_payload(package)["product"]["seller_sku"] == "SKU-1"
    assert listing.content_package_id == content.package_id
    assert inventory.warehouse == "Seaya"
    fact_payload = contract_payload(fact)
    assert fact.amount == Decimal("10.00")
    assert fact_payload["amount"] == "10.00"
    assert fact_payload["occurred_at"] == occurred_at.isoformat()
    json.dumps(fact_payload)


def test_registry_maps_existing_entry_points_without_claiming_new_routes():
    assert cli_registry()["products"] == "product_operations"
    assert cli_registry()["finance"] == "data_operations"
    assert cli_registry()["sourcing"] == "product_operations"
    assert "sourcing" not in {
        command
        for command, owner in cli_registry().items()
        if owner == "supply_chain_operations"
    }
    assert http_registry()["/api/sourcing"] == "product_operations"
    assert owner_for_http_path("/api/ozon/draft/123") == "channel_operations"
    assert owner_for_http_path("/api/shopee/profit/status") == "data_operations"
    assert owner_for_http_path("/api/health") == "shared_platform"
    assert owner_for_http_path("/not-a-route") is None
