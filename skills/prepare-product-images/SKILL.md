---
name: prepare-product-images
description: Consume an immutable round-1 technical snapshot and prepare auditable dual-brand master and localized image candidates through Lingshi AI, with a shared product budget, durable recovery, rework and automated QA. Use for round-2 product image preparation; do not create an intermediate human approval gate, write Miaoshou or publish.
---

# Prepare Product Images

Before resuming an existing product, use the selected project's commit-bound
`scripts/publication_takeover.py` as described in
`docs/PUBLICATION_SOURCE_CONTRACT.md`. Preserve prior paid receipts and QA;
the read-only result never starts a new task or converts generated assets to keep.

Use `scripts/prepare_product_images.py` for R2 and `scripts/run_automated_image_qa.py` for visual QA. Keep the frozen R1 product, brand, target and source identities intact. Read [the local budget and recovery contract](references/paid-recovery.md) before handling unknown results, legacy records or technical drift.

## Authority and inputs

Require a valid `round1-approved-snapshot/v1` and matching current Product Center technical freeze. The schema's `approved` wording is historical: an autopilot snapshot with `human_approval=false` is the normal input and grants no marketplace execution authority. Fact or target changes rebuild the candidate lineage and invalidate any later final marketplace approval bound to the old candidate.

Paid execution requires the existing applicable policy loaded by `shared_platform.publication_autopilot`. Its attributed authority, allowed purposes and product cap must cover this action. Record the selected model, current price, purpose and QA plan in the candidate evidence. Continue without an approval prompt when existing authority covers the exact paid purpose and budget. A new paid purpose, provider, or scope outside that authority requires explicit authorization before the request.

The bundled `historical-autopilot-policy.example.json` is inactive source evidence. Its original names, date and ACTIVE state are not current authority. Do not activate it as default configuration. Offline fixtures and green tests never establish live generation authority.

## Execute one phase

1. Run with only `--offer-id` to inspect local status. Help/status do not call paid providers. Legacy UI consumers without the existing R1/budget bridge return `PAID_CONTEXT_REQUIRED` or `LEGACY_R2_BRIDGE_REQUIRED` before a provider call.
2. Validate the frozen brand roles and reuse plan. With applicable paid authority, run `--execute-brand-generation --paid-policy <existing-policy>`. Complete source bytes and frozen facts bind the technical plan and each checkpoint. Continue through automated master-image QA; a rendered review is an audit artifact, not a human gate.
3. Run `run_automated_image_qa.py --offer-id <id> --model <approved-model> --paid-policy <existing-policy>` for master QA. Retain raw replies, exact artifact digests and the QA receipt. Passed QA belongs only to those artifacts and that R1 snapshot.
4. Freeze the policy-selected numbered-image scope with `--approve-translation-images <numbers> --dimension-only-images <numbers-or-none>`. The CLI defaults to the governed technical actor `orbit-product-publication-default-v1`; an explicit `--approved-by Kyle` is blocked because this command has no independent conversation-receipt binding. Existing Kyle plans retain their original bytes and digest as historical evidence, but cannot authorize new R2 paid execution without independently verifiable provenance. This runtime currently has no trusted historical approval registry or automatic successor migration, so affected products remain blocked for new paid translation work. These plans do not grant final marketplace approval. An existing plan with different scope or authority is preserved and requires an explicit successor rather than an in-place rewrite. Route locales within frozen R1 targets; dimension-only images create no translation tasks.
5. With matching paid authority, run `--execute-paid --paid-policy <existing-policy>`. OCR, text translation and localized image generation share the product ledger with masters and QA. Empty OCR regions are reused without a paid model request.
6. Run localized QA using the exact passed master QA receipt and current localized artifacts. Preserve failures in factual alignment, OCR language or duplication as blockers or bounded rework evidence. Passed artifacts flow to R3 candidate compilation without an intermediate approval prompt.

For wallpaper and wall stickers consume the existing product-family rule packs under `prepare-product-publication/references`. Do not invent missing facts in prompts.

## Budget and recovery

`reports/product-preparation/<id>/paid-requests/events.jsonl` is the accounting authority. Brand, locale, QA, retries, new processes and changed approval digests share it. A durable reservation occupies a slot before POST. Attempted, unknown, failed, superseded and completed work remains counted. The ledger lock is released before provider waits.

For a new product, code inventories complete known R1/R2 report and localized review/pack metadata roots. Ordinary review HTML/Markdown and local plans are evidence, not paid calls. Positive old receipts are imported with source hashes and same-provider task duplicates count once. Missing metadata, redirected roots, ambiguous ownership and unknown prior work cannot become zero.

Reports separate planned requests for the current phase, occupied, attempted, unknown, confirmed and new requests in this invocation. Asset counts and old receipt generation counts are lifetime artifact facts. Raw usage/cost is separate from price estimates. Prior R1 title/copy calls count only when the applicable product policy includes that purpose and a bound actual request receipt exists.

For SUBMITTING, UNKNOWN or post-submit timeout, inspect local request/checkpoint state. Existing task IDs resume by querying, never by creating again. Raw chat replies are durable before parsing and can be parsed again locally. Timeout without raw reply requires upstream reconciliation; changing prompt or retry number is not authorization to resend.

Use the local recovery CLI in `references/paid-recovery.md`. It checks exact product/request/attempt/revision, source hashes, verifier, timestamp and evidence reference. It does not discover or prove external billing facts. Preserve bad and legacy bytes; never manufacture a no-charge conclusion.

## Rework and technical rebuild

A completed paid artifact rejected by exact QA or explicitly selected for redo may create a new attempt under existing rework policy and remaining product budget. Use `--prepare-brand-rework` for the bounded brand request, or `--retry-localized-review-number <n> --retry-locale <locale> --retry-failure-code <code> --retry-authorized-by <existing-actor>` for one localized artifact. Bind old task/artifact, QA or user intent, current input and next attempt. A known failed exact artifact may be retried within the existing policy without another prompt; a new paid purpose or expanded scope may not. Maximum three retries applies; old successful work remains counted and needs no false no-charge proof.

TECHNICAL_PLAN_DRIFT writes a digest-bound proposal and preserves the active plan. Once existing approval provides valid current inputs, explicitly activate that proposal through the local CLI. Activation archives the old plan and affected R2 projections, emits R3 invalidation evidence and retains all paid events. Unknown product requests block activation. Never delete ledger/checkpoint files to make a new plan run.

R2 does not write Miaoshou, ReleaseStore, ReleasePlan, marketplace drafts or publication. R3 consumes the technically adopted image evidence and `round2-technical-invalidation.json` through its own authority. Offline tests, image QA and compatibility fields named `approved` do not authorize publication. Only the later digest-bound `FINAL_MARKETPLACE_PUBLISH` receipt does.
