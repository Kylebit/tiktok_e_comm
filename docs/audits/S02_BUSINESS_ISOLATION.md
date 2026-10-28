# S02-A-FIX: checkpoint ownership and business isolation

This is a local recovery extension to `image_generation_checkpoint.py`. It does
not call providers, prove provider state, authorize paid requests, clear UNKNOWN,
or replace the R2 workflow. The existing S02-A evidence contract remains required
before attaching a known task or opening a rework attempt.

## Different v2 businesses

The old peer scan loaded every sidecar before comparing business identities.
A damaged A sidecar therefore blocked unrelated B. The scan now attributes a
peer using an independently sealed full snapshot or identity first. If the
snapshot and identity both validate but disagree about kind, full business,
request, model or source-completeness metadata, the conflict blocks execution.
Short filename prefixes never establish complete identity.

Positive evidence that a damaged record belongs to A scopes its recovery block
to A. A itself still reads/validates its ordinary state and fails until recovered.
Peers with missing snapshots are discovered through remaining identity, journal,
output and pending snapshot artifacts. An unidentifiable peer is never skipped.

`inspect_checkpoint_ownership(exact_path)` returns the current source binding and
any independently provable owner. The source binding includes the exact filename
and SHA-256 of checkpoint, identity, journal, output, and pending snapshot (null
for absent files). A lost main file may be inspected through its exact expected
JSON path when retained sidecars prove that checkpoint exists.

For data with no independently provable owner, an upstream verified attribution
can be recorded with `bind_checkpoint_ownership`. Supply the full stable business
identity, full request SHA-256, the inspection binding, provider, verifier,
timezone-aware verification time, non-secret `audit://...` reference, and evidence
SHA-256. The verification must not predate current source bytes or be in the
future. `verified=true` alone has no meaning.

The function writes an immutable `owner-<name hash>-<binding hash>.json` receipt
under a local registry lock. It binds full values inside the short filename,
retains an evidence digest, and does not modify the source artifacts. Repeated
equivalent attribution reuses the original receipt; conflicting attribution is
rejected. Any bound source SHA changes invalidate the old receipt. Corrupt
ownership receipts fail closed. This receipt says only
`OWNERSHIP_ONLY_NO_TASK_OR_CHARGE_FINDING`.

The new receipt can let independently attributed B continue while A remains
blocked. It does not repair A's bad sidecar, restore a task ID, or turn an
unverified external claim into a verified task/cost fact. Restore a verified
original sidecar through a separate audited restoration when necessary, then
use S02-A `inspect_image_checkpoint` / `reconcile_image_checkpoint` with fresh
exact evidence. Conflicting independently valid ownership sources must be
resolved by preserving and reviewing all originals; an ownership receipt cannot
override either one.

## Individual v1 association

The preserved r4 v1 producer stored `identity_digest`, client business ID,
status/task and possibly a receipt, but did not persist the complete business
identity in the checkpoint. A filename or offer directory is insufficient to
partition these files safely.

First construct the intended current v2 request without submitting. A normal
execution encounter creates its READY/RECOVERY_REQUIRED local state and blocks
on ambiguous v1 files. Then provide the complete **original** identity used by
the old producer, plus independent evidence relating that legacy request to the
specific current target. The helper recomputes the old canonical identity hash
and checks the original filename, schema, `identity_digest`, client business ID,
business fields and any stored provider. It also verifies the existing current
target's full business and request digests. It does not assert that the old
limited prompt identity is sufficient to prove every current prompt/fact; that
relationship remains part of the upper-layer verified evidence.

Brand v1 identity fields (exact producer schema):

```text
schema_version = lingshi-brand-image/v1
offer_id, brand_id, role, brief, source_identities,
product_reference_count, model, size, quality
```

Localized v1 identity fields:

```text
schema_version = lingshi-localized-image/v1
source_url, source_digest, locale, translations, model, retry_attempt
```

The business identity supplied to `bind_checkpoint_ownership` is exactly
`{offer_id, brand_id, role}` for brand or `{source_url, locale}` for the existing
localized v2 contract. Supply that original identity through `legacy_identity`.
If the original identity cannot be recovered, attribution remains blocked; do
not reconstruct it by guessing a filename, task number, prompt, or SKU. This is
a technical evidence recovery need, not a repeat approval of business facts.

Example offline call sequence (all evidence here represents upstream verified
input, not an automatic external verification):

```python
observed = inspect_checkpoint_ownership(old_path)
owner = bind_checkpoint_ownership(
    old_path,
    business_identity=stable_business,
    request_digest=current_request_digest,
    legacy_identity=complete_original_v1_identity,
    evidence={
        **observed,
        "business_digest": current_business_digest,
        "request_digest": current_request_digest,
        "provider": "lingshi-media/v1",
        "verified_by": upstream_verifier,
        "verified_at": current_aware_timestamp,
        "evidence_ref": "audit://image-recovery/original-identity",
        "evidence_sha256": verified_evidence_sha256,
    },
)
```

Create one association per original file. A task101 file for A and task202 for B
can then be reconciled independently. No task IDs are combined across files
assigned to other businesses. A single legacy file with conflicting top-level
and receipt IDs is rejected. A same-business legacy record mapped to another
request still blocks unless that target demonstrably resolved the exact original
SHA; deleting the target does not erase the legacy unknown gate.

Association itself leaves the current task unset. Apply a separate S02-A
`task_verified_for_request` recovery with fresh exact checkpoint evidence to
attach the independently verified task ID; the next generator invocation only
polls that ID and retrieves its result. Original v1 checkpoint/output bytes and
ownership receipts remain unchanged. Paid rework continues to use
`authorized_rework` after terminal output/QA or user-rework authority binding,
and will require the upper-layer budget integration in S02-B.

## Validation scope

The paired tests cover original A/B isolation, conflicting valid facts, missing
snapshots/sidecars, unknown owners, source-hash drift, invalid evidence and target,
full v1 identity reconstruction, distinct task recovery without create, localized
v1 association, receipt corruption/idempotence, and the real brand generator
wrapper. The previous S02-A unknown/retry, journal/crash, concurrent single-create
and authorized-rework tests remain unchanged.

S02-B's synthetic 52/40 and false-success/false-count rework entry regressions are
still pending real R2 integration. This FIX does not provide a total paid budget
or enable provider calls.
