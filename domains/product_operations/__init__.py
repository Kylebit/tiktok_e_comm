"""Product master data, SKU and approval-package ownership boundary."""

from shared_platform.contracts import ApprovedProductPackage, ProductRecord

from .approved_publication_snapshot import (
    APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION,
    ApprovedPublicationSnapshot,
    ApprovedPublicationSnapshotError,
    approved_publication_snapshot_from_payload,
    build_approved_publication_snapshot,
    validate_approved_publication_snapshot,
)
from .adapters import approved_product_package_from_facts, product_record_from_legacy_row
from .approval_lock import ProductApprovalLockPreview, preview_product_approval_lock
from .catalog_update_preview import (
    CatalogUpdatePreview,
    SellerSkuReservation,
    preview_catalog_update,
    reservations_from_documents,
)
from .product_facts import (
    FieldSourceCandidate,
    FieldSourceEvidence,
    ProductFactsSnapshot,
    SelectedSkuPriceFact,
    build_product_facts_snapshot,
)
from .source_identity import (
    BLOCKED_SOURCE_IDENTITY,
    SCHEMA_VERSION as SOURCE_PRODUCT_IDENTITY_SCHEMA_VERSION,
    SourceIdentityEvidence,
    SourceProductIdentity,
    SourceProductIdentityResolution,
    resolve_source_product_identity,
)
from .sku_lineage import (
    BLOCKED_SKU_LINEAGE,
    NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION,
    SKU_LINEAGE_SCHEMA_VERSION,
    ModelSkuAssignment,
    NewSourceSkuReservation,
    NewSourceSkuReservationResolution,
    SkuAssignment,
    SkuLineageReservation,
    SkuLineageResolution,
    finalize_new_source_sku_reservation,
    new_source_sku_reservation_digest,
    resolve_sku_lineage_reservation,
)

__all__ = [
    "APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION",
    "ApprovedPublicationSnapshot",
    "ApprovedPublicationSnapshotError",
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
    "FieldSourceCandidate",
    "FieldSourceEvidence",
    "ProductFactsSnapshot",
    "SelectedSkuPriceFact",
    "build_product_facts_snapshot",
    "BLOCKED_SOURCE_IDENTITY",
    "SOURCE_PRODUCT_IDENTITY_SCHEMA_VERSION",
    "SourceIdentityEvidence",
    "SourceProductIdentity",
    "SourceProductIdentityResolution",
    "resolve_source_product_identity",
    "BLOCKED_SKU_LINEAGE",
    "NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION",
    "SKU_LINEAGE_SCHEMA_VERSION",
    "ModelSkuAssignment",
    "NewSourceSkuReservation",
    "NewSourceSkuReservationResolution",
    "SkuAssignment",
    "SkuLineageReservation",
    "SkuLineageResolution",
    "finalize_new_source_sku_reservation",
    "new_source_sku_reservation_digest",
    "resolve_sku_lineage_reservation",
    "approved_publication_snapshot_from_payload",
    "build_approved_publication_snapshot",
    "validate_approved_publication_snapshot",
]
