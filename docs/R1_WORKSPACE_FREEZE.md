# R1 workspace category and immutable review consumer

The facts pane now consumes the explicit options/capture contract in [R1_INITIAL_OPTIONS](R1_INITIAL_OPTIONS.md). Initial page load has no region or category selection. Selecting a region reads local context only. A separate click requests official options, the operator selects a category and attributes, and another click captures the final observation. Recommendations are never selections. Names, paths and values are rendered as text.

## Trusted local preparation

`POST /api/product-workspace/round1-category/prepare` accepts exactly:

```text
offer_id, product_center_revision, requested_targets, source_region,
account_identity_digest, context_digest, observer_reference, request_id
```

The server resolves the stored observation and regenerates the complete review packet from the actual current Product Center dashboard. The image plan comes from the existing local `reports/product-preparation/<offer>/first-review.json`, whose full document digest and reviewed revision are bound. Missing or unresolved plans fail; this endpoint does not invent image decisions. The request has no browser fact, receipt, snapshot, root or actor overlay.

An immutable `round1_workspace_preparations` record is stored in the existing release store. It contains the exact request, packet, unbound current-input projection, original state, image-document digest, approval fingerprint and controlled actor. Its reference and document digest are checked on read; update/delete triggers reject mutation. Request ID reuse with different input fails. Same-ID preparation rechecks current state rather than claiming a stale preparation is ready.

The response has `status: PREPARED`, `prepared_reference`, `request_id`, the full reviewable `packet`, `review_digest`, `approval_actor` and `external_write_count: 0`. The facts pane displays the complete packet before directing the operator to the existing approval button. This is preparation, not business approval or publication.

## One approval and the revision transition

The existing `POST /api/product-workspace/approve` accepts the R1 form:

```text
offer_id, prepared_reference, user_approved: true, approved_by
```

The current local single-operator authority remains unchanged. The UI consumes the server's `approval_actor`; a browser-supplied alternative is rejected. This does not add a multi-user identity system or change `publication_rounds` actor validation. Existing legacy product-facts-only approval requests remain supported and do not claim that a round-1 snapshot was frozen.

The R1 path uses the existing approval lock, workbench lock, state write lock, actual approval preview and `save_state` CAS. Approval records bind `round1_prepared_reference` and `round1_review_digest`. The only accepted state transition is the recorded pre-state to revision n+1 with the existing approval fields, locked review and allocated SKU; unrelated state must remain identical. Current regenerated packet inputs are compared without the revision field, while actual state revision is checked separately. This models the approval increment explicitly; it does not replace the current revision with an old one or fabricate an approved state. Current image-plan bytes, ordered targets, account identity when available, and the trusted observation are rechecked. UNKNOWN account metadata blocks. An explicitly reused valid stored observation may remain usable with UNPREPARED credentials, without making the account READY or refreshing credentials.

The server calls the existing `build_round1_snapshot` and `persist_round1_snapshot`, then reads and compares the actual immutable file. Only this readback produces `FROZEN` with `persisted_readback: true`. Successful and partial approval responses also carry a fresh actual dashboard, so the existing facts controls reflect the new revision and lock. No marketplace plan, provider write or image generation is part of this operation.

## Reconciliation and partial persistence

`GET /api/product-workspace/round1-category/prepare-status?offer_id=...&request_id=...` is a local read-only reconciliation entry. It reopens the prepared record, rechecks current state and, when present, validates the persisted snapshot. It returns PREPARED, APPROVED or FROZEN; failures are BLOCKED/UNKNOWN, never a green freeze. It does not create a missing store or approve anything.

If approval saved but snapshot persistence failed, the action returns `APPROVED_NOT_FROZEN`, `ok: false`, and `R1_PERSISTENCE_REQUIRES_RECONCILIATION`. The operator first reads the original status. An APPROVED result permits `POST .../round1-category/freeze` with exactly `offer_id, prepared_reference` to complete the technical persistence. It has no new business-approval parameter and never calls `save_state` for an already-bound approved state. Concurrent and repeated requests serialize and reuse that approval. A changed scope cannot reuse it. An existing conflicting/partial immutable file is not overwritten or deleted automatically.

Browser operation identity, exact request and scope are saved before POST. Only compact result status/reference/progress metadata is retained; full dashboards and review documents are recovered from the service. Lost responses and malformed JSON require a status read, not another POST. A restarted service uses the same durable preparation and operation IDs. An old operation can be inspected but cannot bind another product, target order, region or account. The server-verified n→n+1 approval result is the narrowly allowed recovery case after a dashboard refresh.

Official transport-unavailable outcomes are UNKNOWN consistently for initial options, v3 capture and v2 capture. Known input failures and explicit provider/technical-limit rejections remain FAILED. UNKNOWN, IN_PROGRESS and NOT_STARTED do not enable another capture or approval. Missing progress is valid for NOT_STARTED; legacy counters display as unknown. Context refresh never recaptures automatically. Historical observation reuse is an explicit local resolve operation.

## Consumer fixes and validation boundary

The actual dashboard emits a category object. Preparation now projects only a valid textual `category.name` into the category evidence's string semantic field, while retaining legal string input compatibility. Empty, missing, numeric or unknown-shaped values remain invalid; no `str(dict)` or default category is used. The full R1 browser path preserves the existing approval button's child label so subsequent dashboard renders remain valid.

Focused tests use actual Handler, dashboard, state CAS, release store, approval and snapshot services with temporary product state/config/output and synthetic provider credentials/transport. They cover 5 options GETs + 2 capture GETs, zero additional official GETs for preparation/freeze/reconciliation, HTTP service reopening, disk byte stability on status reads, concurrent approval, partial persistence, stale/forged scope, actor rejection, v2/v3 UNKNOWN, and actual dense DOM at 1440/390. Browser fault cases include late A→B, dirty facts, candidate reset, lost options/capture/approval responses, malformed JSON, 200 FAILED, 409 UNKNOWN, NOT_STARTED, legacy counters, untrusted labels and valid/invalid history. These are offline integration proofs, not live marketplace/category readiness or APP release evidence.
