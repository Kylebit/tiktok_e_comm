"""Product master data, SKU and approval-package ownership boundary."""

from shared_platform.contracts import ApprovedProductPackage, ProductRecord

from .adapters import approved_product_package_from_facts, product_record_from_legacy_row
from .approval_lock import ProductApprovalLockPreview, preview_product_approval_lock
from .catalog_update_preview import (
    CatalogUpdatePreview,
    SellerSkuReservation,
    preview_catalog_update,
    reservations_from_documents,
)

__all__ = [
    "ApprovedProductPackage",
    "ProductRecord",
    "approved_product_package_from_facts",
    "product_record_from_legacy_row",
    "ProductApprovalLockPreview",
    "preview_product_approval_lock",
    "CatalogUpdatePreview",
    "SellerSkuReservation",
    "preview_catalog_update",
    "reservations_from_documents",
]
