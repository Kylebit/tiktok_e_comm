# Local paid request recovery

These commands use local files and have no provider factory. Run in the intended repository with its existing Python runtime. Use exact current identifiers and an existing applicable policy; the historical example is not effective.

```text
python -B -m shared_platform.publication_paid_requests --repo-root <repo> --offer-id <id> --paid-policy <existing-policy> status
python -B -m shared_platform.publication_paid_requests --repo-root <repo> --offer-id <id> --paid-policy <existing-policy> inspect-request --key <full-key> --checkpoint <exact-v2-path>
python -B -m shared_platform.publication_paid_requests --repo-root <repo> --offer-id <id> --paid-policy <existing-policy> inspect-history --index <original-record-index>
python -B -m shared_platform.publication_paid_requests --repo-root <repo> --offer-id <id> --paid-policy <existing-policy> reconcile-request --evidence <verified-evidence.json>
python -B -m shared_platform.publication_paid_requests --repo-root <repo> --offer-id <id> --paid-policy <existing-policy> reconcile-history --evidence <verified-history-evidence.json>
python -B -m shared_platform.publication_paid_requests --repo-root <repo> --offer-id <id> --paid-policy <existing-policy> activate-plan-rebuild --proposal <exact-proposal.json>
python -B -m shared_platform.publication_paid_requests --repo-root <repo> --offer-id <id> --paid-policy <existing-policy> prepare-image-successor-grant --decision <conversation-receipt.json> --decision-sha256 <exact-sha256> --checkpoint <original-unknown-v2-path> [--checkpoint <next-original-path>]
```

Omit checkpoint for chat. Inspect returns the full `observed` object: product/key, business/full request digest, provider/model/attempt, original approval binding, current ledger digest/time, original raw digest and image checkpoint/identity/journal hashes, revision, tasks and legacy hashes.

Evidence contains the unchanged `observed`, `verified_by`, timezone-aware `verified_at`, an audit:// or approval:// reference and complete evidence SHA256. Outcomes:

- `task_verified_for_request`: exact positive task_id. Include checkpoint_evidence matching the S02-A inspect_image_checkpoint contract, current hashes and the same verified task/outcome. V1 files first require per-record bind_checkpoint_ownership evidence with their complete original identity. Ownership cannot attach a task or release UNKNOWN.
- `no_task_no_charge`: task_id null and charge_status none, only without a contradictory known response/task. Images include matching checkpoint evidence. The old occupied slot remains counted conservatively; the next bound attempt is permitted within cap and retry limit.
- `raw_response_verified_for_request`: original chat response and response_sha256. Raw is preserved before parsing; it cannot overwrite different retained bytes.

These are upper-layer facts already verified against authoritative task/usage evidence. This interface verifies bindings only. A verified boolean, another product's receipt or stale revision is insufficient. If interrupted after checkpoint transition but before ledger projection, identical evidence can resume that local projection without another POST.

An explicit conversation decision can instead authorize one new image per exact
missing role while accepting possible duplicate charges. This is not a no-charge
outcome. The local `prepare-image-successor-grant` command requires the decision's
exact product/R1, original keys, maximum (at most five), per-role maximum one,
duplicate-charge risk, retained UNKNOWN and the unchanged shared cap. It binds the
original model, full request/reference digest and all checkpoint source hashes;
original bytes and accounting states are retained. Pass the resulting canonical
grant to the original direct images entry with `--execute-brand-generation
--unknown-successor-grant <grant-path>`. Existing successful assets are verified
and reused. Each successor has a distinct checkpoint identity and durable
reservation in the same ledger. Repeated execution can only resume that successor;
an unresolved successor cannot obtain another successor under this grant.
Completed successors emit a verifiable image-only receipt for native adoption.
That receipt neither resolves the originals nor applies to merchant UNKNOWNs.

Historical reconciliation uses the exact `inspect-history` observed object, unchanged original source/request SHA, current ledger revision, original provider, verified timestamp/verifier/reference, and either an exact task or a no-task/no-charge outcome. The historical slot remains occupied. This resolves the budget-history blocker only; a corresponding old image checkpoint still needs its own S02-A task/ownership evidence. It cannot silently relabel old ToAPI tasks or combine separate v1 identities.

Lock order is product phase, image/chat business, short product ledger. No ledger lock spans provider latency. Reservations and POST/ack/report crash windows retain occupancy. Active technical plans are separate from immutable R1 approval. Explicit proposal activation archives affected R2 projections and records invalidation for R3.

Legacy workbench adapters require PaidRequestContext and approved_bridge: exact offer/R1 digest, existing legacy base-package or review snapshot digest, and per-source brand/role/locale tasks with full source SHA256. Existing ToAPI checkpoints are retained and require positive mapping. Legacy retry UI must also pass the exact old artifact/task and authorized rework_basis, or receives LEGACY_REWORK_BRIDGE_REQUIRED. U02 must wire these existing inputs; this is not a new approval store.

The controlled baseline boundary is product reports plus that product's data/localized_image_reviews and data/localized_image_packs. Junctions, symlinks and reparse redirects are rejected before reading outside. Only the actual product ledger directory is excluded; nested namesakes remain inventoried. Unknown usage outside known roots requires a source-bound completeness inventory. The module makes no claim about unrecorded external work.
