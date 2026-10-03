# First-review decision contract

Use schema `publication-preparation-decision/v1`.

## Required sections

- `offer_id` and `product_center_revision`
- `status`: `DECISION_REQUIRED`, `READY_FOR_MIAOSHOU_SYNC`, or
  `FIRST_REVIEW_READY`
- `target_selection`: requested, selected and missing targets
- `product_facts`: title, SKU, cost, weight, package dimensions and source
  image count
- `targets`: one row per requested target with category, price and copy status
- `image_decisions`: positions follow explicit task scope or a deterministic
  evidence-backed plan, never OCR
- `image_execution_plan`: validated `first-review-image-plan/v1` with status,
  source actions, generated assets and a bounded summary
- `content_groups`: groups follow exact target/brand scope or an explicit task
  choice; unresolved ambiguity is a blocker
- `publication_stock_policy`: complete `publication-default-stock/v1` governed
  decision (200 per selected SKU), visible in the candidate and checked
  unchanged before final candidate compilation
- `blockers`
- `miaoshou_sync`
- `external_write_count`, `request_attempted`, and `readback_verified`

## Evidence levels

Use one of these sources on each material target decision:

1. `frozen_or_reviewed_product_fact`
2. `confirmed_product_family_rule`
3. `official_provider_read`
4. `explicit_task_decision`

Anything else is a candidate, not an approved fact.

## Candidate audit rules

- Show shared facts once and target differences per row.
- Group identical unresolved blockers instead of turning each store into a
  separate approval prompt.
- Explain why the top category is exact and list alternatives only when the
  exact choice is unavailable or ambiguous.
- Do not show raw provider payloads, tokens, URLs, item identities or exception
  text.
- A successful local calculation is not a successful Miaoshou sync.
- `source_actions` must use visible 1-based source positions. TRANSLATE requires
  exact target languages and an output count equal to the language count.
- `generated_assets` contains only genuinely new image concepts. Keep it empty
  when the plan only translates selected source images.
- Never persist generation prompts, provider URLs, raw OCR output or paid API
  payloads in the first-review packet.
