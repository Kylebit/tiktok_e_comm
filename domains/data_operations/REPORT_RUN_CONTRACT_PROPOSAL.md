# ReportRun contract proposal

`ReportRun` is currently an internal data-operations type.  It should move to
`shared_platform.contracts` only after the integrator approves a consumer and
persistence boundary.

Proposed stable fields: `run_id`, `calculation_kind`, `period` (start, end,
timezone), `input_snapshot` (identifier/checksum), raw and deduplicated row
counts, FX/cost/assumption/code versions, `status`, `idempotency_key`, and
generation timestamp.  The report content and quality findings remain owned by
data operations; Shared Platform should own scheduling, durable run records,
notification delivery, and delivery receipts.
