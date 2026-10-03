# Normalized historical snapshot display admission

This first bounded importer is for the traced D04 2026-09-30 case. It does not
implement a generic inventory refresh or grant execution authority. The source
receipt is an engineering provenance reference, not a user's business approval.

`domains/supply_chain_operations/audited_snapshot.py::import_snapshot` is a native
Python API only; there is no HTTP importer, provider requester or live CLI. Inputs
are an explicit source Git root/commit, SHA-256 of the five actual files in
`SOURCE_PATHS`, a selected code root and explicit external runtime configuration.
Tracked dashboard pair, manual receipt and all referenced local images must match
the same source Git commit; ignored normalized inventory/orders have separate raw
byte pins. No source checkout, installed Skill or business database is changed.

The isolated configuration has exactly `runtime_root`, `artifact_root`,
`output_root`, and `mode=AUDITED_MANUAL_SNAPSHOT`. Native deployment parsing rejects
every other mode. This is opt-in; no formal configuration was enabled here.
The original three-key captured configuration and COMPLETE checks are retained.

Import recomputes four warehouse inventory digests and all eight normalized
order snapshot digests, compares quantities and complete order facts against the
dashboard, and retains exact unresolved/excluded counts. Only the audited
NO_ACTIVE_BATCH case is supported. Historical signed/shelved receipts must remain
completed and cannot add future inbound stock. The declared source lacks provider
page transcripts, exact shop/account proof and an independent raw inbound list.
Do not reconstruct these from SKU aggregates or fabricate a terminal page.

Each immutable generation records five input paths/hashes, original pair bytes,
all local `row.image` hashes, selected consumer hashes, separate inventory/order/
inbound clocks and coverage. `asset_reference_policy` is
`ROW_IMAGE_ONLY_PINNED_CONSUMERS_NO_REMOTE_MEDIA`: the two actual image render
locations are app.js SKU summary/details. The pinned inbound/transport consumers
do not render channel imageUrl or batch thumbnails. The order source's imageUrl
is input provenance only, removed by the original aggregation projection; extra
channel fields are rejected except the original seven settlement fields. No
remote image is requested or downloaded by the importer. Browser rendering must
still be accepted independently before declaring displayed images ready.

The shared data-root lease serializes local admission and reads. A separate
`audited-serving.json` index points to `audited.json`, schema
`supply-chain-audited-snapshot/v1`, grade `AUDITED_MANUAL_SNAPSHOT`. Neither is a
capture COMPLETE marker. Original data.js/inbound-plan.js bytes are preserved.
Inputs, pair, images and consumer hashes are rechecked on every display request;
missing or changed evidence cannot fall back to bundled stock. Selected code
changes produce a visible reimport/compatibility message. Selected scripts also
carry integrity hashes, and their native HTTP requests recheck the same consumer
manifest instead of falling through to changed checkout files. A page pins both data
resources and images to one manual generation. Old admitted versions remain
addressable while listed, without mixing a current image into an older page.

The original dashboard DOM and quantity math are reused. A mandatory banner says
“规范化历史快照 · 来源已核对”, shows all three clocks and explains that settlement
facts were not refreshed, their capture date remains unknown, and page/shop proof
is missing. The source grade is engineering byte/identity validation only; fields
`user_approval=false`, `provider_authenticity=NOT_ESTABLISHED`, and
`execution_authority=false` cannot become durable approval flags. Bootstrap state
is `READY_MANUAL_DISPLAY`, not COMPLETE or live readiness. An unmatched banner
insertion point rejects the page instead of silently omitting evidence.

Future use of captured stage/apply still requires its existing complete source
contracts. Native import, isolated browser acceptance and formal runtime data
activation are separate operations; no freshness or official-write authority is
inferred from passing source checks.
