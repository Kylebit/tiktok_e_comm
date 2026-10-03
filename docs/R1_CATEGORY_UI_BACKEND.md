# R1 category UI backend

This package extends f2728ddb without changing any frontend file. It adds local context, explicit-region prepared account metadata, correlated capture requests and read-only status. All source authority remains in the existing internal observation store and capture/resolve functions. No approval authority, automatic approval or provider-refresh fallback is added.

## Local context

GET `/api/product-workspace/round1-category/context` requires exactly `offer_id`, `product_center_revision`, `requested_targets` (a JSON array string preserving order), and `source_region`. Region is explicit; no PH/first-target default. The response schema is `round1-category-workspace-context/v1`.

Current server product facts determine identity and target selection. `source_account` contains only region, shop/merchant identity, identity digest, readiness, checked_at and a safe reason code. The helper reads already prepared credentials using the existing no-refresh boundary; it never creates a transport or invokes provider GET. READY is based on current prepared credentials, never inferred from historical observations. Missing credentials are UNPREPARED; unexpected local lookup failures are UNKNOWN. Tokens and exception details are not projected.

The context digest binds the R1 input digest, current account identity and readiness, excluding volatile checked_at. Observation indexing is exact offer/revision/ordered-input/region; each record exposes its account identity. A valid record from a different currently READY account is ACCOUNT_MISMATCH. Unprepared current credentials do not invalidate an otherwise usable historical record: any reuse still requires the existing resolver's explicit intended account digest. Invalidated and corrupt records are shown as blocked states. Context and capture-status reads do not initialize a missing database.

`category_intent.status` remains `INTENT_REQUIRED`. This package intentionally does not invent first-use category/attribute choices. The next required package must add the frontend and the explicit first-time options/intent phase together; a UI that leaves every first-time product permanently without a way to choose attributes is not the final product delivery.

## Correlated capture

New UI callers POST to the existing capture route with the original exact identity/category/attribute fields plus:

```text
schema_version: round1-category-capture-request/v2
request_id: unique caller correlation ID (1–96 ASCII letters/digits/_/-)
context_digest: exact digest returned by the current local context
```

The server persists the immutable request identity before any official GET. It rechecks current context and account before invoking the existing capture function. Capture result and request-to-observation linkage commit in one SQLite transaction. A failed transaction cannot leave an unlinked captured observation. Existing observation tables and legacy capture transactions retain their behavior; the request table is initialized only by correlated requests (or the normal full-store schema).

Repeated identical request ID/body returns the existing state and never invokes metadata or provider reads again. Changed parameters for the same ID return 409. Concurrent same-ID submissions have one capture owner. Request identity is immutable; terminal rows cannot change or be deleted. If the server instance changes while a request is IN_PROGRESS, its public state is UNKNOWN. Neither status reads nor another POST resume that request.

GET `/api/product-workspace/round1-category/capture-status?offer_id=...&request_id=...` returns NOT_STARTED, IN_PROGRESS, SUCCEEDED, FAILED or UNKNOWN. SUCCEEDED includes the existing stored receipt. FAILED/UNKNOWN contain only safe codes. HTTP 200 means the request state was read successfully, not that capture succeeded; consumers must check `status`. An uncertain outcome must be reconciled, not automatically retried with a new ID.

Legacy capture requests without version/correlation fields remain supported by the original contract. The new UI must use v2 correlation. Arbitrary observation bodies, paths, extra fields and mixed malformed versions cannot register trusted evidence. Default data paths remain service-owned.

## Validation and limits

The dedicated API suite uses the complete Handler, including a controlled actual loopback port, synthetic credential reads/lowest-level GET, and temporary SQLite. It checks request persistence before GET, atomic rollback, duplicate/concurrent request IDs, conflict rejection, restart UNKNOWN, stale context, missing/unknown account readiness, exact observation indexing, explicit invalidation and local-only reads. Actual trigger counts are retained in the external evidence. The original observer/Handler suites remain regression coverage.

No frontend, U01 WIP, production credentials/database, real provider, installation or publishing was touched. Initial category/attribute options and frontend integration remain required follow-up work. The previously preserved real-capture preparation still has no authorized real product/region/account selection; synthetic identities must not be used to fill those gaps.
