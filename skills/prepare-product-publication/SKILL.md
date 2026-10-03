---
name: prepare-product-publication
description: "Prepare an auditable round-1 candidate for one Product Center Offer ID and exact target stores with zero external writes: collect authoritative SKU and parcel facts, resolve category and pricing candidates, generate title and variant-display candidates, and propose explicit image translation/generation decisions. Use when the user asks to start, prepare, inspect, resume, or redo product preparation before image work and the sole final marketplace review."
---

# Prepare Product Publication

## Explicit source binding

For a direct Agent CLI, select a complete Git source and explicit settings/data/
report profile as described in `docs/AGENT_ENTRY_BINDING.md` in that source.
Run `<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry preparation --check-binding`,
then pass the existing arguments after `--`. A personal physical Skill copy is
not a source root. Version 1 preserves source-relative data/reports and rejects
independent roots. Version 2 supports R1 existing captured inputs with explicit
state, data, source capture, content metadata and report paths. Missing captured
state is BLOCKED; this mode never imports a server or collects upstream inputs.
Its Lingshi path is metadata discovery only. Other v2 entries are unsupported.
A binding check grants no business authority and does not prove facts ready.

## Existing-product takeover

For inspect/resume, first use the commit-bound, read-only
`scripts/publication_takeover.py` in the explicitly selected project, following
`docs/PUBLICATION_SOURCE_CONTRACT.md`. Do not bootstrap or approve an existing
product merely to inspect it. That contract governs source selection and legacy
stage labels; it does not claim newer personal workflow modes are integrated.

Turn one exact Offer ID and exact target stores into a durable round-1 candidate
packet. `first-review` remains the persisted schema name for compatibility; it
is not a human approval gate. Reuse Product Center deterministic code for facts. Use agent judgment
only for documented category research, copy candidates, and recommendations.
Never guess a missing commercial or provider fact.

## Non-negotiable round boundary

The first round always has **zero external writes**. It never writes Miaoshou,
calls a paid image service, claims or creates a shop draft, or publishes.
The second round, `prepare-product-images`, generates and reviews images only;
it does not write Miaoshou. Third-round COMMON synchronization and official
readback belong to `publish-approved-product`. See
[round ownership](references/miaoshou-baseline-sync.md) when resuming legacy packets.

R1 must not ask Kyle to approve facts, copy, category, price, stock, or the image
plan as a normal stage transition. When the active autopilot policy resolves all
checks, write the existing digest-bound auto-decision and technical snapshot with
`human_approval=false`; these artifacts authorize only later preparation. Product
pages are editing and observation surfaces, not approval authorities. The sole
normal human gate is `FINAL_MARKETPLACE_PUBLISH` after the complete frozen
marketplace candidate exists.

## Required input

Require:

- one exact `offer_id`;
- every intended target store, not only a platform or country;
- optional explicit user choices for translation positions, generated image
  concepts, and LivelyHive/HomeBloom content groups.

Preserve the exact store list. Never infer HomeBloom from LivelyHive, Shopee or
Ozon from TikTok, or all stores from an Offer ID.

## Workflow

### 1. Build the deterministic preview

Run:

```powershell
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry preparation --check-binding
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry preparation -- --offer-id <OFFER_ID> --targets <COMMA_SEPARATED_TARGETS>
```

When image work is in scope, create one explicit
`first-review-image-plan/v1` JSON file and add `--image-plan <PATH>`. The plan
lists each chosen source position as KEEP, TRANSLATE, REMOVE, or REFERENCE,
exact target languages, and proposed net-new assets. Do not use OCR to select
images. Do not call a paid API in this round.

The client accepts `--candidate-plan <PATH>` for proposed category and copy
sidecars. When either plan argument is omitted, it reuses the corresponding
`first-review-image-plan.json` or `first-review-candidate-plan.json` from
`reports/product-preparation/<OFFER_ID>/` if present. Refreshing the report
preserves those input files. Offer IDs must contain ASCII digits only.
Candidate categories do not replace bound official category evidence, and
candidate copy remains PROPOSED. If the selected SEA stores include both
LivelyHive and HomeBloom, retain separate brand content and image plans;
single-brand targets do not imply an additional brand. These compatibility
rules do not install or enable personal manual-intake or autopilot workflows.

R1 retains per-SKU parcel facts and price calculations from the supplied
Product Center preview. Positive finite cost, weight, dimensions and prices
are required for material completeness. Partial per-SKU parcel facts cannot
be replaced silently by shared defaults. Variant price rows must cover the
known SKU identities; COMMON drafts have no market-price requirement.
These calculations remain candidates and do not prove provider readback.

The round-1 candidate must display the complete governed
`publication_stock_policy`: `publication-default-stock/v1`, 200 per selected SKU,
`EACH_SELECTED_SKU`, `SYSTEM_GOVERNED_DEFAULT`, `ROUND1`. Candidate validation
rejects a missing or changed policy. Never map the legacy
`stock_per_model_sku` field to this policy or silently rewrite an approved
snapshot; a different quantity needs a separately reviewed contract.

If no local workbench exists, the client may perform the existing upstream
read and a local workbench-state write once. These are not provider mutations.
If requested targets are missing, return `DECISION_REQUIRED`; never silently
restore defaults.

Legacy `--execute-miaoshou` or `--confirm-miaoshou-write` arguments must fail
without writing. Their historical error wording is not a stage-routing authority.
`--skip-miaoshou` is a compatibility no-op; synchronization belongs to third-round COMMON.

### 2. Resolve first-review facts

For each selected target retain evidence and provenance for:

1. supplier SKU and proposed seller/Model SKU;
2. exact publishable category and required attributes;
3. reviewed price and currency;
4. platform title and final publication specification name;
5. cost, weight, and package dimensions;
6. user-selected translation positions and locale routes;
7. common content or a user-requested LivelyHive/HomeBloom split.

Read `references/knowledge-base-schema.md` before category or content work.
Prefer confirmed product-family facts, then official read-only provider trees
and metadata. Zero or multiple safe category candidates are blockers; preserve
the evidence and smallest unresolved decision without inventing a category.

Derive translation positions and brand content groups from exact task scope,
source evidence and governed defaults. If a high-risk ambiguity cannot be
resolved, keep it as a blocker rather than turning R1 into an approval prompt.
Any explicit choice already supplied by Kyle remains binding within that scope.

### 3. Persist the first-review packet

Follow `references/decision-contract.md`. Store the packet under
`reports/product-preparation/<offer_id>/first-review.json`; this runtime state
must not be committed. It contains the exact revision, targets, decisions,
image plan, blockers, and:

```json
{
  "status": "FIRST_REVIEW_READY",
  "miaoshou_sync": {"status": "DEFERRED_TO_SECOND_ROUND"},
  "external_write_count": 0,
  "request_attempted": false,
  "readback_verified": false
}
```

`DEFERRED_TO_SECOND_ROUND` is the existing client's compatibility label, not
permission for R2 to synchronize Miaoshou. Preserve it in existing packets;
resolve the current stage through the Product Center contract described in
[round ownership](references/miaoshou-baseline-sync.md).

Missing or contradictory facts yield `DECISION_REQUIRED` with the smallest
actionable decision. Never persist raw provider payloads, credentials, URLs,
or provider item identities.

### 4. Hand off

Return a compact summary with:

- Offer ID and exact Product Center revision;
- requested and observed stores;
- shared facts and per-target decisions;
- source image actions, locale routes, and proposed generated images;
- unresolved decisions;
- explicit statement: first-round Miaoshou writes `0`; R2 handles images and R3 COMMON handles synchronization/readback;
- whether existing paid authority covers R2, or the exact missing authority if it does not.

Continue into R2 without asking for an intermediate approval when the active
policy already covers its paid purpose and budget. A new or expanded paid
purpose still needs explicit authority. R1 never authorizes Miaoshou or
marketplace writes. A new agent resumes from the durable packet and current
Product Center state, not conversation memory.

## Safety and evidence

- Keep first-round provider write count exactly zero.
- Never expose credentials, raw responses, provider URLs, or exception args.
- Keep confirmed write counts separate from attempted requests.
- Do not let stale technical state erase a valid final marketplace approval receipt.
- Update knowledge only when official facts, regression evidence, and readback
  agree; never promote a one-off hypothesis into a product-family rule.
