# Release optimization candidate contract

`release-optimization-candidate/v1` is an offline, advisory Data Operations
contract. It consumes only `release-outcome-dataset/v1` and its matching
`release-outcome-evaluation/v1`. It never reads ReleaseStore, a production
database, credentials, or a channel API.

Append-only `release-outcome-manual-acceptance/v1` resolutions must be merged
into their exact source facts before building the dataset. The merge changes
the source fact's manual decision only; it does not append a second release
fact. Candidate sample counts, dispatch/write totals, and outcome distribution
therefore continue to count the original release attempt exactly once.

Each candidate is grouped by channel, region, and policy version and contains:

- sample count and outcome/write/readback/quality coverage;
- quality blockers;
- auth, inventory, content, logistics, and other failure-category comparisons
  against the whole dataset;
- manual acceptance, reconciliation, and unknown-write rates;
- dataset, evaluation, and group-metric evidence digests;
- one advisory action code, a confidence band, and
  `requires_human_approval=true`.

`SUBMITTED_UNVERIFIED` is known outcome coverage, but it is neither success nor
manual acceptance. Optimization therefore observes it without converting a
platform-accepted submission into evidence of human acceptance.

The only action codes are `COLLECT_MORE_EVIDENCE`, `REVIEW_AUTH`,
`REVIEW_INVENTORY`, `REVIEW_CONTENT`, `REVIEW_LOGISTICS`, and
`REVIEW_POLICY`. There is no execute, retry, dispatch, or production-policy
mutation field.

## Fail-closed rules

Before producing grouped candidates, the builder independently verifies every
fact digest, the order-independent dataset snapshot digest, fact count, schema
versions, evaluation input digest, overall metrics, and grouped metrics.
Schema or digest drift returns one blocked `COLLECT_MORE_EVIDENCE` candidate
with no source rows embedded.

For a valid group, evidence collection remains the only recommendation when:

- `sample_count < min_sample_count`;
- `external_write_unknown_rate > max_unknown_write_rate`;
- quality-clear coverage is below its minimum; or
- known-outcome coverage is below its minimum.

Equality at a configured boundary passes that boundary. Thresholds are
included in the artifact and therefore covered by its digest. Passing all
thresholds permits only a human review recommendation, never automatic
execution.
