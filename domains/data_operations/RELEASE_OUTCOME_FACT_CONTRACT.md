# Release outcome fact contract proposal

`release-outcome-fact/v1` is a Data Operations-owned offline analytics
contract. It does not change release execution, retry policy, the release
store, or a channel adapter.

## Input boundary

The pure adapter accepts `release-outcome-receipt/v1` mappings supplied by a
public, redacted release snapshot. Required identity values are SHA-256
digests for the plan, run, and target. The receipt may contain public
dimensions (`channel`, `region`, adapter version, policy version), enumerated
statuses, counters, and evidence digests.

Raw plan/run/product/SKU or marketplace identifiers, tokens, copy, URLs, image
identifiers, and raw responses are rejected. Unsupported schema versions and
invalid or negative counts are also rejected. An absent external-write count
is represented as `null` with an `UNKNOWN` class and a quality issue; it is
never converted to zero.

## Fact and evaluation semantics

The fact records:

- plan/run/target identity digests and source-receipt digest;
- adapter and policy versions;
- outcome class and dispatch boundary;
- explicit external-write count and classes;
- official readback, manual review, and reconciliation states;
- redacted error category/code/type;
- latency, attempts, dispatch/readback/manual/reconciliation counts;
- duplicate-prevention status and evidence digests.

`SUBMITTED_UNVERIFIED` means the marketplace accepted the submission but no
official readback or person has accepted the result. The adapter also accepts
the platform spelling `ACCEPTED_UNVERIFIED` and normalizes it to this public
class. Its manual status is always `PENDING`; reconciliation is
`NOT_REQUIRED` unless a separate receipt explicitly says `REQUIRED` or
`RESOLVED`. It is not a success and does not contribute to manual acceptance.

The fact digest excludes itself and covers every other field. Replaying the
same receipt therefore produces the same digest.

## Append-only manual acceptance resolution

Manual acceptance happens after the original release attempt has already
produced its immutable `release-outcome-receipt/v1`. Shared Platform exports a
separate redacted `release-outcome-manual-acceptance/v1` resolution. It is not
a second release sample and must never be passed to
`adapt_release_outcome_receipt`.

`adapt_release_outcome_manual_acceptance` accepts only the exact platform
shape:

- the original outcome receipt digest;
- a redacted target-attempt identity digest;
- a redacted acceptance-evidence digest;
- `manual.status=ACCEPTED`;
- `manual.reviewer_role=approved_release_actor`; and
- an exact empty `external_writes_performed` list.

It returns a deterministic
`release-outcome-manual-acceptance-fact/v1`. Unsupported schemas, extra
fields, raw marketplace identities, tokens, URLs, responses, non-lowercase
digests, non-approved actors, rejection statuses, and any claimed external
write fail closed.

`merge_release_outcome_manual_acceptances` resolves each acceptance by the
exact source receipt digest and updates that one existing fact to
`manual_status=ACCEPTED`. The original outcome class, dispatch boundary,
external-write count/classes, readback, reconciliation, error, attempt/count,
and source-receipt facts remain unchanged. The acceptance evidence and
resolution-fact digests are appended to the redacted evidence digest set and
the merged fact digest is recomputed.

The merge returns the same number of release facts it received. This is
important for API-less TikTok submissions and verified Shopee MY/VN warning
acceptance: a later human decision changes manual-decision metrics but never
double-counts a publication, a dispatch, or an external write. Duplicate
source facts, duplicate resolutions, missing/mismatched source receipts,
tampered fact digests, non-pending manual status, and ineligible source
outcomes all fail closed.

The offline evaluator reports success, official readback, manual acceptance,
reconciliation, duplicate prevention, explicit external writes, unknown-write
coverage, and auth/inventory/content/logistics/other error distributions.
Metrics can be grouped by channel, region, and policy version. Rates retain
their numerator and denominator so an empty or unknown population cannot look
like a zero rate.

## Shared Platform wiring note

Shared Platform should export the redacted receipt after it has resolved the
durable run/target/operation receipt. It should compute public identity and
evidence digests at that boundary, attach the executing adapter/policy
versions, and explicitly report write count even when the count is zero.
Data Operations can then run:

```python
facts = adapt_release_outcome_receipts(public_receipts)
resolutions = tuple(
    adapt_release_outcome_manual_acceptance(item)
    for item in public_manual_acceptance_resolutions
)
facts = merge_release_outcome_manual_acceptances(facts, resolutions)
dataset = release_outcome_dataset(facts)
evaluation = evaluate_release_outcomes(facts)
```

Shared Platform may persist the resolution fact digest as observational
consumer metadata. It must keep the original outcome receipt immutable and
must not enqueue the resolution as another outcome sample.

The Orbit/autopilot dashboard may consume `dataset` and `evaluation`; neither
payload authorizes a retry or changes production policy.
