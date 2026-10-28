# S02-A: local image task recovery and request identity

Work order: `WO-20260905-ORBIT-S02-A`.
Base: `e69f71104289ae9ff204db825142208641ad9353`.
Branch: `codex/orbithive-s02-recovery-20260905`.
This change is a local recovery component, not permission to activate paid
generation. It changes the two Lingshi generators and one shared local helper,
`modules/sourcing/image_generation_checkpoint.py`. It does not change the
Lingshi client, HTTP retry configuration, server, UI, installed Skills, or any
external platform.

## Identity and attempts

The brand business identity is `(offer_id, brand_id, role)`. The localized
identity is `(source_url, locale)`, using the existing callable's available
identities. The caller must reuse the same checkpoint directory for a logical
job; changing directories is not a supported retry or recovery mechanism.

A separate request digest binds the business digest, renderer, complete actual
prompt, model, actual generation parameters, ordered references, and source
content identities. Thus brand label, positioning, brief, verified product
identity, reference count/order, model/parameters, translations and approved
source bytes cannot silently reuse a different completed request. The digest
does not contain `retry_attempt`. Full prompts and data URIs are hashed in
memory and are not written to state or event logs.

Brand local references bind their actual byte SHA256. For URL references the
optional `source_url_digests` mapping accepts upstream-verified SHA256 values.
When absent, `source_identity_complete=false` explicitly records URL-only
provenance; the implementation does not pretend a URL proves unchanged bytes.
Localized requests bind the supplied approved `source_bytes`. These hashes do
not verify that a remotely served URL still delivers those bytes at execution
time; source freezing and upstream validation remain caller responsibilities.

An unresolved request blocks a different prompt/request for the same business
identity. Once the old request is terminal, a caller with current upper-layer
authorization can execute a genuinely changed request, preserving the old
record. Repeating a completed request, even with a larger `retry_attempt`,
returns the verified existing result. A larger integer alone never authorizes
another paid creation.

## Durable state and concurrency

Each request has a short `lingshi-<kind>-v2-<24-hex>.json` filename and separate
`.identity`, `.events.jsonl`, and `.png` files. The identity sidecar stores and
checks the full 64-character business and request digests. A filename-prefix
collision fails closed against that full identity. Short filenames avoid the
Windows path-length failure reproduced during development.

A per-business thread lock plus an OS file lock covers the complete execution
window. Windows uses `msvcrt.locking`; POSIX uses `flock`. The lock file is not
deleted to recover a job. Kernel locks release on process exit, including
`os._exit`; their surviving file does not lock future workers. Another writer
waits up to 30 seconds, then gets an explicit busy/recovery error with no
creation. Read-only inspection never acquires this writer lock.

Snapshots use flush/fsync and atomic replacement. A hash-chained, monotonically
numbered event is flushed before replacing the snapshot. A persisted task
acknowledgement therefore survives a later snapshot write failure. Reads can
recover that acknowledged task from the valid journal and only query it.
Partial/corrupted journals are never silently truncated during execution.

| State | Ordinary execution |
| --- | --- |
| `READY` | Submit only when the explicitly requested attempt equals the locally authorized attempt |
| `SUBMITTING`, no task ID | Reconciliation required; a process restart cannot submit again |
| `SUBMISSION_UNKNOWN`, no task ID | Reconciliation required, including across attempt numbers and prompt changes |
| `REJECTED_BEFORE_TASK` | Retained separately, but still requires reconciliation before another create |
| `SUBMITTED`, known task ID | Query/retrieve that task only; polling/download failure keeps its ID |
| `FAILED`, known task ID | Preserve terminal failure; explicit recovery or authorized rework required |
| `COMPLETED` | Verify receipt identity and image digest; reuse the result |
| Damaged, incomplete, or orphaned artifacts | Preserve and require explicit local recovery |

A task ID in a create response is retained even if the surrounding response
code is non-success. HTTP exception strings no longer prove that no task or
charge occurred. Terminal task failures use a structured exception, not error
message prefix guessing. Provider error prose is not persisted.

A missing completed output can be fetched from the same task, and must match
the recorded digest. A different fetched output cannot overwrite the frozen
receipt or become acceptable merely by retrying. A corrupt local image stays
in place until an explicit recovery/repair action is chosen.

If a storage failure prevents all acknowledgement persistence, the durable
`SUBMITTING` marker still blocks a new create. `CheckpointPersistenceError`
returns the known task ID when available so the caller can reconcile it.
This is not a guarantee against loss of the entire filesystem or sudden
hardware power loss; no remote idempotency feature is asserted here.

## Local recovery API and verified input contract

Both functions are in `modules.sourcing.image_generation_checkpoint`:

```python
observation = inspect_image_checkpoint(exact_checkpoint_path)
# Obtain/retain independently verified provider or QA/authorization evidence
# through the governing caller. Inspection itself does not verify that evidence.
updated = reconcile_image_checkpoint(exact_checkpoint_path, evidence=verified_evidence)
```

Inspection returns the request/business digests, provider, exact snapshot,
identity-sidecar and journal byte digests, effective revision/attempt/state,
known current-attempt task IDs, output digest, and legacy file digests.
It can report damaged snapshots while still retaining independently valid
task acknowledgements. An observation made during a writer's update may be
transient; reconciliation locks and verifies the current bindings again.

The verified input must exactly bind:

- `business_digest`, `request_digest`, and provider `lingshi-media/v1`;
- `checkpoint_digest`, `identity_digest`, `journal_digest`, `revision`,
  `attempt`, `status`, `known_task_ids`, `output_digest`, and the exact
  `legacy_checkpoint_digests` mapping;
- timezone-aware `verified_at`, no earlier than the current checkpoint update;
- an upstream verifier identity, retained as a digest; a non-secret
  `audit://...` evidence reference (`fixture://...` is used only by tests),
  plus `evidence_sha256` for its retained evidence artifact;
- one of the three outcomes below, with its specific binding fields.

`verified=true`, an old observation, a different product/request/provider,
stale revision, changed file bytes, unbound task ID, or an HTTP status string
is insufficient. The complete supplied attestation is also digest-bound in
the resulting event. The API validates local binding and structural evidence
requirements; it does not authenticate a provider, query usage, or manufacture
proof that the attestation's external facts are true.

| Outcome | Required evidence and local result |
| --- | --- |
| `task_verified_for_request` | Positive verified task ID, consistent with any known IDs. Set `SUBMITTED`; the next ordinary invocation only queries that task. |
| `no_task_no_charge` | Explicit `task_id=null`, `charge_status=none`, and no contradicting known task. Set `READY` at the next attempt; the caller must pass that exact attempt under existing authorization. |
| `authorized_rework` | Terminal `COMPLETED`/`FAILED` prior attempt, its task/output/revision, `qa_rejected` or `user_requested_rework`, an `authorization_ref` and `authorization_sha256`; QA rejection also requires `qa_review_sha256`. Archive the old snapshot/output, then set `READY` for one next attempt. |

Rework is preserved as a legitimate paid operation when separately authorized
by the existing caller. A successfully paid image is not required to prove
"no charge" before QA/user-directed rework. Original artifacts remain under
`attempt-<request-prefix>-<attempt>.json/.png`. Retrying an unresolved rework
cannot reuse the earlier completed image or bypass reconciliation. Attempts
are bounded to 0–3 for this existing per-request contract; this is not a global
business budget.

Recovery itself makes **zero** provider, model, upload, or paid calls. The next
actual execution still belongs to the already governed upper-layer caller.
No new UI approval dialog or duplicate human confirmation is introduced.

## Legacy and damaged checkpoint compatibility

V1 files in the supplied checkpoint directory are conservatively inventoried
by generator kind. They do not bind a complete prompt/request, so their output
is not automatically reused as if it matched a changed request. Existing task
IDs in either the top-level state or receipt are preserved. A new v2 recovery
record identifies the exact old file digests, and the caller must attest the
task/request relationship before attaching and querying a known task. V1 bytes
and outputs remain unchanged. Unknown v1 state needs verified no-task/no-charge
evidence before a new attempt.

Explicit repair of a damaged v2 snapshot/journal first saves its exact bytes
to `preserved-<content-digest-prefix>.blob`, then records the new recovery
transition. Independently retained attempt metadata is kept. If no attempt
metadata survives, an explicit upstream `verified_attempt` is also required.
No production old files were migrated by this work.

Unsupported automatic cases remain blocked: missing/damaged identity sidecar,
conflicting legacy task IDs, or old files whose relationship cannot be verified.
These need a separately reviewed restoration/migration with original evidence;
neither moving a file aside nor deleting a lock is this API's recovery path.

## Regression evidence and remaining integration work

The exact base reproduced all original B3A probes: **3 failed**. The final
offline suite is **149 passed, 0 skipped**, including all B3A-related tests and
new evidence-binding, v1, partial-state, failed snapshot replacement, output
drift, full prompt/source identity, authorized rework, thread/process
concurrency, and abrupt process-exit cases. Real fake-client create counts
are asserted; two concurrent processes create one fixture task. An abrupt
exit after the `SUBMITTING` marker cannot resubmit on restart.

Two old assertions were intentionally strengthened after their actual failing
run was retained: HTTP 400 text is no longer a freely retryable pre-submission
failure; a higher `retry_attempt` no longer recreates a completed task.
Authorized rework has separate positive tests. Development also reproduced and
fixed missing-output digest drift and attempt reset during explicit repair.

Evidence is outside the candidate at audit `security/recovery-validation/`:
`base-three-red.xml`, `short-path-regression.xml`, `edge-red.xml`,
`edge-green.xml`, and final `release-contracts.xml` plus matching machine
records. Existing Python 3.12.8 / pytest 8.4.2 was used, imports/cwd fixed to the
new C candidate, bytecode/cache disabled, unique audit basetemps, synthetic
clients/results and scrubbed key aliases. Socket/DNS/SQLite guards recorded
zero external attempts. No external writes, real paid calls, business DB
operations, installations, services, automations, notifications, or pushes.

The coordinator's reviewed `INTERRUPTION_CASEBOOK.md` I12 records 52 paid
executions against a budget of 40, with eight localization failures. This
package does **not** solve that cross-brand/role/locale budget failure. The
next package must wire the actual round-2 Skill entry to a shared durable
attempted/unknown counter, reserve budget before every new request, reconcile
unknown outcomes, and protect concurrent total reservations. Rework must use
the same upper-layer authority and budget contract. Single-job deduplication
does not prove aggregate request-budget control.

The generators and local recovery API are still not wired to a paid server/UI
or Skill runtime in this candidate. Source-content attestation, real provider
reconciliation, upper authorization, QA decisions, aggregate budgets and the
eventual default entry-point integration remain separate work. Passing S02-A
must not open real generation or publication.
