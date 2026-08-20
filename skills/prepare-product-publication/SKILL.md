---
name: prepare-product-publication
description: "Prepare the first human review for one Product Center Offer ID and exact target stores with zero external writes: collect authoritative SKU and parcel facts, resolve category and pricing candidates, generate title and variant-display candidates, and propose explicit image translation/generation decisions. Use when the user asks to start, prepare, inspect, resume, or redo the first round before paid image generation, Miaoshou synchronization, and publication."
---

# Prepare Product Publication

Turn one exact Offer ID and exact target stores into a durable first-review
packet. Reuse Product Center deterministic code for facts. Use agent judgment
only for documented category research, copy candidates, and recommendations.
Never guess a missing commercial or provider fact.

## Non-negotiable round boundary

The first round always has **zero external writes**. It never writes Miaoshou,
calls a paid image service, claims or creates a shop draft, or publishes.
Miaoshou synchronization belongs only to the second round in
`prepare-product-images`.

Kyle's explicit approval in the conversation is the only human approval entry.
Record it independently of page buttons and technical readiness. Product pages
are editing and observation surfaces, not approval authorities.

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
.venv\Scripts\python.exe skills\prepare-product-publication\scripts\prepare_product_publication.py --offer-id <OFFER_ID> --targets <COMMA_SEPARATED_TARGETS>
```

When image work is in scope, create one explicit
`first-review-image-plan/v1` JSON file and add `--image-plan <PATH>`. The plan
lists each chosen source position as KEEP, TRANSLATE, REMOVE, or REFERENCE,
exact target languages, and proposed net-new assets. Do not use OCR to select
images. Do not call a paid API in this round.

If no local workbench exists, the client may perform the existing upstream
read and a local workbench-state write once. These are not provider mutations.
If requested targets are missing, return `DECISION_REQUIRED`; never silently
restore defaults.

Legacy `--execute-miaoshou` or `--confirm-miaoshou-write` arguments must fail
with a clear second-round boundary error. `--skip-miaoshou` is a compatibility
no-op because Miaoshou is always deferred.

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
and metadata. Zero or multiple safe category candidates require user review.

Do not automatically choose translation positions or dual content groups.
Propose the image plan before first approval unless Kyle explicitly approves
the frozen scope earlier; then persist approval intent and reconcile the plan
later without asking again.

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
- explicit statement: first-round Miaoshou writes `0`, deferred to second round;
- next phrase: `第一轮通过，开始第二轮`.

Do not start paid generation, Miaoshou synchronization, or publication without
the corresponding user instruction. A new agent resumes from the durable
packet and current Product Center state, not conversation memory.

## Safety and evidence

- Keep first-round provider write count exactly zero.
- Never expose credentials, raw responses, provider URLs, or exception args.
- Keep confirmed write counts separate from attempted requests.
- Do not let stale technical state erase a recorded conversation approval.
- Update knowledge only when official facts, regression evidence, and readback
  agree; never promote a one-off hypothesis into a product-family rule.
