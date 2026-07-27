"""Copy, images and future-video ownership boundary."""

from shared_platform.contracts import ContentPackage
from domains.content_operations.content_package_adapter import (
    ContentAssetLineage,
    ContentPackageHandoff,
    build_content_package_handoff,
    build_workbench_content_package_handoff,
)
from domains.content_operations.listing_title_candidates import (
    fact_signature as listing_title_fact_signature,
    fact_snapshot as listing_title_fact_snapshot,
    generate_title_candidates,
    release_listing_copy_identity,
    model_input_signature as listing_title_model_input_signature,
)

__all__ = [
    "ContentPackage",
    "ContentAssetLineage",
    "ContentPackageHandoff",
    "build_content_package_handoff",
    "build_workbench_content_package_handoff",
    "generate_title_candidates",
    "listing_title_fact_signature",
    "release_listing_copy_identity",
    "listing_title_fact_snapshot",
    "listing_title_model_input_signature",
]
