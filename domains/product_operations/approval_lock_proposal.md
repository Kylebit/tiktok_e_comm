# Product approval lock persistence proposal

The existing `data/new_product_workbench/<offer_id>.json` document has a
`review.fields_locked` flag but no durable product-approval identity, actor,
timestamp, content-package reference, or input fingerprint.  Its current
on-disk instance for product `3828811808` also could not be decoded by the
standard JSON parser during the read-only audit, so it is not a reliable final
approval record.

The owning workbench/UI should add an atomic, revision-checked write of the
`product_approval` object produced by `preview_product_approval_lock`.  It
should record `approval_id`, `package_id`, subject, SKU, approved actor/time,
content approval identity, and `input_fingerprint`; a changed fingerprint
invalidates the old approval.  This uses the existing state document and
requires no production table.  A platform-owned audit/migration can replace
this document-level record only after the shared approval contract is versioned
by the integrator.
