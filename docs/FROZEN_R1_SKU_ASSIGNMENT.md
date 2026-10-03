# Frozen R1 SKU assignment

`build_release_dashboard(frozen_round1=...)` is an explicit domain-only option.
The caller must first authenticate the complete current R1/R2 document identity
through the existing R2 consumer. Do not expose a request-body snapshot as this
argument. This option does not approve facts or skip the COMMON/R3 comparison.

The dashboard validates the supplied snapshot digest and approved status against
the current workbench offer, approval ID/fingerprint, locked parent SKU, and
target scope. Every selected source key must occur exactly once in both the
frozen variant list and current source. Variant numbers are mapped by source key,
not array order or a guessed increment. Canonical source resolution must be ready.

For a NEW_SOURCE reservation, these frozen model numbers replace the allocator's
new suggestions. Reservation conflict checks remain active. Existing lineage is
not invented; if present it must agree with the frozen assignment. Calls without
the argument retain the previous allocator behavior.

Validation uses only synthetic local state/databases and retained R1 fixtures.
No source snapshot, live database, approval, or marketplace is modified.
