# Terminal failed/refunded chat reconciliation

This is a local `PaidRequestContext.reconcile_request` outcome, not a retry command or a refund API. It consumes externally verified provider facts after an unresolved chat/QA request. No image-checkpoint recovery behavior changes.

Keep the existing `observed`, `verified_at`, `verified_by`, `evidence_ref` and `evidence_sha256` fields from the paid-recovery contract. Add:

- `outcome: "failed_task_refunded"`
- `task_id`: the exact positive integer provider task ID.
- `provider_response`: the complete retained official task response.
- `provider_response_sha256`: `image_generation_checkpoint.digest(provider_response)`.

The response must identify the same task and model, state `failed`, `is_final: true`, `refunded: true`, finite zero `cost`, positive finite `refunded_amount`, and no result URL/type. Its timezone-aware completion timestamp must fall between the unresolved ledger event and the upstream verification timestamp. A known different task, retained raw chat response, image checkpoint, stale binding, pending provider state or partial refund evidence is rejected.

The caller is responsible for establishing that the official task belongs to the exact observed request, retaining its source response and an audit reference. This interface verifies the binding and records the complete response; it does not discover provider tasks or verify remote billing through network calls.

The append-only resolution is `FAILED_REFUNDED`. It remains an occupied/attempted budget slot, is counted as a confirmed terminal outcome and is separately exposed as `failed_refunded_retained`. It creates no raw success response and does not change an existing policy or its limit. Repeating identical reconciliation evidence is idempotent.

Calling the same chat attempt after reconciliation reports a known failed/refunded outcome and requires an explicit bounded retry; it does not automatically submit the next attempt. Any separately authorized next attempt still passes the existing maximum-attempt and full-product occupied-slot cap. A refund does not restore a request slot.
