# SKU-only commercial correction entry

This local entry creates a genuine new semantic publication Task. It does not
adopt a code-source successor, approve a marketplace candidate, replay an old
COMMON request, or create a collection-box draft. The original Task, immutable
R1/R2/QA/paid history and successful COMMON plan/run remain retained.

`shared_platform.sku_only_publication_correction` supplies these calls:

1. `admit_generation(engine, profile, store, predecessor_task_id=...,
   source_key=..., new_sku=..., common_plan_id=..., common_run_id=...,
   authority_path=..., authority_sha256=..., documents=...,
   original_paths=..., catalog_path=..., paid_context=...)`.
   The old Task must already be cancelled, the old unapproved market candidate
   superseded, and no market attempt/decision or unresolved operation may exist.
   `documents` is the original service's six R2 documents; its `first_review`
   is the original immutable prepared packet. `original_paths` maps the six
   R2 document keys to their original files, including the raw first-review
   whose original prepared packet is independently checked in SQLite.
   `paid_context` is the original `PaidRequestContext`, with its original R1
   binding. The full current catalog successor is checked against active
   durable reservations. This call appends Task events and one immutable local
   report pointer; it makes no provider request.
2. `preparation_documents(record, documents)` returns `(new_first_review,
   new_round1_snapshot)`. Only Seller/Model identity and necessary new approval
   identifiers/digests change. Category receipts remain original. The reviewed
   revision remains the original review's revision; the new state CAS receives
   the next current revision. No new capture or creative work is asserted.
3. `derive_preparation(record, documents, artifact_bytes=...)` passes these to
   the retained-image derivation producer. It binds the exact paid request IDs,
   ledger path/SHA and original owner retained during admission. It must bind the same
   original artifacts, original QA and paid owner without generation/OCR/paid
   calls. Pass its resulting document mapping to
   `apply_preparation(engine, profile, store, offer_id=...,
   documents=original_documents, derived_documents=...)` once.
   All image identity validation and immutable file writes precede the local
   state CAS. The call appends a new native preparation root, updates the local
   Product Center identity, and completes the genuine new Task's native facts
   and images steps. While still holding its lease, it calls the original
   COMMON dashboard/payload producer and `store.create_plan(payload)` to obtain
   the actual ACTIVE SKU reservation; only then does it yield at `release`.
   No run, approval or provider request is created by that reservation.
4. The existing native publication service prepares one new COMMON plan for
   that new preparation root. Keep the original successful COMMON plan/run;
   do not pass it as `supersedes_plan_id`. Execute through the existing
   `prepare_common_for_task` / managed COMMON EDIT transport. Its added
   preimage check requires the original official draft comparison to pass and
   the new immutable draft to differ only in SKU identity. The existing native
   ledger commits UNKNOWN before dispatch and retains official readback.
5. After the new COMMON's official successful readback, call
   `r3_technical_category_evidence.prepare_sku_only_category_supplement(store,
   documents=derived['documents'], common_plan_id=applied['common_plan_id'],
   common_run_id=actual_new_terminal_run_id)` once. This revalidates and retains
   the original category receipt/capture ledger literally and binds its safe
   projections to the new context using the same SKU correction proof. It
   repeats no metadata GET. The Ozon warehouse receipt is already rebound
   locally by `apply_preparation`, preserving its original official receipt,
   account, warehouse choice and stock policy with no new warehouse GET.
6. Materialize the corrected full candidate through the existing native final
   service. The existing protected final decision is the sole human gate.

The integrator is the only live writer. Keep the owned worker quiesced during
the local admission/application so it cannot race a partially prepared new
Task. A restarted worker with an unapplied intent is stopped before facts or
paid image execution. This is a local preparation blocker, not a merchant
UNKNOWN. On any partial local failure retain the pointer, events and files for
inspection; do not delete history or repeat provider work as recovery. An
actual UNKNOWN continues through the original operation's read-only recovery.

The new namespace is
`reports/product-preparation/<offer>/sku-corrections/<generation>/`. The
existing Offer directory and original files are preserved; its immutable
`sku-only-generation.json` pointer selects the current generation for consumers.
