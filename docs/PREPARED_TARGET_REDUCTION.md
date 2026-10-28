# Prepared publication target cancellation

`WorkbenchEngine.record_publication_target_reduction` is a trusted coordinator
intake seam for one target cancelled from a prepared, unsubmitted legacy
publication task. It has no HTTP endpoint and grants no publication authority.

The coordinator must first prove the original service owner and writer have
stopped through their supported channels. This method does not establish OS
ownership. It additionally requires the same pinned task version, an exact
checkpoint byte digest containing only `native_preparation`, and a queued or
failed task with no worker, lease, action, external attempt, owned resource lock,
attached executor or unresolved owned domain operation. It never expires a
lease, releases a lock, changes the original task scope/version/checkpoint, or
creates another task. Waiting reviews and tasks past preparation are rejected.

The exact cancellation request must match the historical review's byte digest,
offer, complete predecessor target set, configured shop ID, product revision,
and original R1/R2 identities. Only one target may be removed. COMMON, changes to
other targets, paid calls and inherited publication approval are rejected.

Intake appends one immutable record and one event. The original request is bound
by its canonical digest. Exact concurrent retries return the same record;
different requests are refused. The new table has update/delete rejection
triggers. Task views expose `effective_scope` and `target_cancellations` alongside
the original `scope`; they show `scope_reduction_pending_consumer`.

The legacy worker cannot claim an amended task. Legacy publication dispatch and
the native current-round check refuse publication of that offer with
`PUBLICATION_TARGET_REDUCTION_CONSUMER_NOT_ADOPTED`, before dashboard lease
expiration, round loading or provider activity. This hold includes the retained
targets until their original consumer has been reviewed and extended. Other
offers and other business operations keep their existing gates.

`select_marketplace_targets` is a pure compiler seam. It verifies the cancellation
record digest, offer, original targets and complete R2 identity, then returns the
retained labels in their original order. It does not adopt a task, create a plan,
replay COMMON, approve a candidate or authorize execution.

This change is deliberately pending adoption. A separately reviewed consumer
must read the durable cancellation itself, retain the original R1/R2 and COMMON
binding, compile/preflight only the retained targets, and preserve the sole final
user review. There is no activation toggle in this intake, and caller-provided
JSON is not executable proof. Formal adoption also requires the existing
controlled schema migration process for the new table and its two triggers;
this candidate does not run that migration or relax the startup schema gate.

Regression coverage is in `tests/test_prepared_target_reduction.py`: exact
product/shop scope, immutable history, concurrent idempotence, conflicting
lineage, unknown/expired writer refusal, lease/lock retention, and prevention of
legacy and native dispatch bypass. Tests use isolated SQLite files and synthetic
frozen reviews. Actual v7 input comparison is recorded separately by the
delegated review task, with formal adoption explicitly false.
