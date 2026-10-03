# Initial COMMON config failure recovery

This is a local maintenance operation, not a publication API. Stop the old worker
before invoking `reconcile_historical_config_failure(store=..., engine=...,
task_id=..., run_id=..., source_root=..., verified_by=...)`.

Use the original ReleaseStore database and an Engine instantiated with the old
task's exact release identity. `source_root` is the preserved d6877dd7 checkout,
not the new recovery checkout. Three audited source hashes use UTF-8 text with
normalized LF line endings. The historical proof is limited to that version,
first attempt, exact config-not-found error, no submission/readback/repair or
external ID, and the same active approved COMMON plan and task owner.

The independent operator must have verified the historical configuration was
absent before the initial detail call. The stored legacy error has no traceback;
source hashes and absence of submission alone do not prove arbitrary historical
errors were pre-dispatch. The verifier identity is retained in the evidence.

Recovery appends a failure event; it does not turn the failed attempt into a
success, delete it, or manufacture provider readback. A `local-not-dispatched:`
reference closes only the original domain claim, and a task event records the
proof before the same pinned task queues. ReleaseStore and Engine writes are
ordered so recovery can resume after interruption.

For migration, cancel only after this recovery, preserve the old task, and copy
the full ReleaseStore including the approved plan, failed run, and failure event
to the new runtime. The next actual attempt retains attempt 1 and claims a new
`:attempt:2` operation. Original plan approval and current R1/R2 checks still run.
No market approval is created.

New code uses a typed config-missing exception only at the initial detail call
to persist zero-request evidence automatically. Another config failure on
attempt 2 stays unresolved; the historical attempt-1 proof cannot authorize a
third attempt. Unknown transport outcomes retain their existing locks.
