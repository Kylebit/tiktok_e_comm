# Shared five-core shell and local catalog review copy

The primary navigation is defined in `shared_platform/orbit_registry.py`:
catalog, publication workspace, supply chain, profit, and knowledge tools.
All five pages load the same shell script and CSS. Links navigate directly in
the same tab, with an active state and a compact menu on mobile. The old overview
and its duplicate jump cards are removed. Root/index and old `?view=` entry
routes redirect to these pages. Knowledge retains searchable tool details and
copyable methods, without the unrelated area-link lists.

`scripts/catalog_review_preview.py` serves an explicitly registered SQLite
review copy. The metadata must bind a distinct source database and a private
snapshot in the metadata directory. It reads no personal settings or credentials,
blocks outbound connections, and permits only the cost-save POST on the copy.
All other write methods are denied. The original database is never connected by
the preview process. New derived cost tables, when needed, are created only in
the copy by the existing cost editor.

Snapshot acquisition is an operator-owned local action: resolve the canonical
configuration's database field, inspect schema and aggregate counts with
`mode=ro` and `query_only`, then use SQLite online backup inside one read
transaction. Do not copy only the main DB file when WAL may be active. Record
source identity, capture time, per-table counts, and any existing record-update
times without exporting business rows. Capture time is not a platform sync time.
Do not claim that other processes could not modify the source.

The catalog API and page carry review-copy metadata. A short Chinese notice
states that costs save only to the review copy; technical source details are
collapsed. The copy does not fetch remote product images; a labeled local
placeholder is used. Actual product rows, identities, prices, costs, and counts
come from the snapshot. An unavailable database still returns an explicit error;
it is never converted to a successful empty list. Provider write controls are
disabled in review mode while the normal configured application retains them.

Validation must assert nonempty actual catalog API results and their rendered
row count, not just page HTTP status. Check same-tab navigation and active state
at 1440/390, visible cost controls, pagination, copy-only cost saves on fixtures,
and explicit errors for a missing database. Freeze code before replacing the
user preview, and bind the deployed source/asset hashes and snapshot metadata.
