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

The fact digest excludes itself and covers every other field. Replaying the
same receipt therefore produces the same digest.

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
dataset = release_outcome_dataset(facts)
evaluation = evaluate_release_outcomes(facts)
```

The Orbit/autopilot dashboard may consume `dataset` and `evaluation`; neither
payload authorizes a retry or changes production policy.
