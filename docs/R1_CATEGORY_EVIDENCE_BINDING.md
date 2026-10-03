# R1 category evidence binding

This local package prevents new Shopee first reviews from becoming ready without bound category evidence. It does not add a production observation service. The production CLI has no trusted resolver: even structurally valid, self-consistent JSON produces `SOURCE_UNVERIFIED` and `DECISION_REQUIRED`. No category table, knowledge base, automatic approval policy, server, personal installation or marketplace transport changes are included.

## Contract and trust

`shared_platform.round1_category_evidence` is the common validator used by preparation and snapshot freezing. Its `shopee-category-official-review/v2` receipt binds:

- Exact offer, literal nonnegative Product Center revision, ordered requested Shopee targets, explicit source region and `SHOPEE_GLOBAL_CATEGORY` observation scope.
- A canonical input digest over offer, revision, title, category semantic, the full ordered requested target list and explicit observation context.
- Leaf ID/name, complete unique category path ending at that leaf, leaf/publishable flags, complete attribute options and selected values, mandatory attribute coverage and count.
- Observation reference/time, authority label, business-write count, optional known authentication-write and official-read counts, and observation/receipt digests.

The digests detect drift; neither a digest nor an authority label authenticates a source. `validate_receipt` requires a separately supplied service-owned resolver to resolve the observation reference and return the identical observation. Resolver exceptions fail with a constant safe code. This package supplies no production resolver and no CLI option for injecting one. All successful resolver injections are synthetic tests. A future resolver must obtain independently verifiable server-owned observations; reading another caller-provided file is not sufficient.

The source region is required, never inferred as PH or taken from the first target. It describes global category observation context. `regional_publishability` remains `NOT_VERIFIED`; this contract makes no claim that any regional listing can be published. Authentication writes may be unknown (`null`), as may the official-read count. Unknown counters stay unknown; they are not evidence of zero requests. Business writes must be literal zero.

## Consumers and migration

`prepare_product_publication.py` accepts `--category-review` as an untrusted bounded JSON reference and `--category-source-region` as explicit context. Without a path it checks the existing offer-scoped sidecar. Missing, legacy, malformed or mismatched evidence blocks a new Shopee review. Literal revision identity is retained rather than coerced from strings, booleans or missing values. Non-Shopee preparation retains its existing behavior and does not read the category reference.

With a trusted resolver, preparation embeds the validated receipt in `category_evidence_binding` and projects its leaf and receipt digest into every selected Shopee target. Freeze validates that embedded object and the target projections with the same validator, then places a deep copy in the approved snapshot's `fact_snapshot`. Freeze never rereads the mutable sidecar: replacing that file after review cannot substitute different evidence. A changed resolved observation or changed reviewed object is rejected. This is local evidence binding, not a new Product Center approval mechanism.

An unfrozen v1 category receipt is `CATEGORY_LEGACY_UNBOUND`; missing identity is never filled in. Already frozen round1 snapshot bytes and their load/R2 identity-validation paths remain unchanged and need no new category approval. New snapshots continue using the existing round1 schema with an additive fact field. Existing non-Shopee compatibility fixtures are unchanged.

## Validation scope

Before modifying production code, three actual regressions were reproduced: preparation returned READY without category evidence; freeze accepted a missing-field sidecar; freeze accepted a wrong-offer sidecar with null attributes. The same tests pass after the change.

Offline tests cover nine negative groups: identity/context, input-fact drift, leaf/path, attributes, observation metadata, receipt envelope/digests, JSON references, target shape, and freeze projections. Additional tests exercise the source trust boundary, actual prepare-to-freeze flow, actual CLI with an injected preview only, bounded reads, malformed revisions, single resolver invocation without sidecar reread, independent snapshot copies, and legacy snapshot loading/R2 consumption. Existing preparation and publication compatibility suites run alongside them.

The external evidence runner installs network/process/SQLite/business-import and business-data guards before test collection, suppresses environment repr before pytest, and directs all writes to synthetic evidence fixtures. These results establish local contracts only. Production observation integration, live API/authentication behavior, personal installation and marketplace publication remain unverified.
