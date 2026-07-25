# Codex 30-day convergence plan

The goal is not to add five more half-finished systems. Each domain must close
one daily business loop, while the CEO/integrator keeps contracts and releases
coherent.

## Current checkpoint (2026-07-25)

The five domain worktrees have completed and the CEO/integrator has merged
their first read-only hand-offs:

- product rows can become `ProductRecord` and explicitly approved
  `ApprovedProductPackage` objects;
- reviewed image-suite facts can become an auditable `ContentPackage`;
- approved product/content packages can become deterministic, approval-gated
  channel dry-run plans;
- local Seaya/Yacang exports can become inventory snapshots, daily deltas, and
  low-stock signals;
- cost and settlement rows can become decimal-safe `FinancialFact` objects
  with data-quality issues.

No marketplace, warehouse, finance database, or other external system was
written. The full repository test suite passes. The parent no-commit Git
directory remains locked by the active desktop project, but each domain uses a
native worktree from the canonical repository, so it is contained rather than
blocking delivery.

These are verified seams, not yet five closed operating loops. The next
milestone is one real local product-readiness rehearsal using existing
workbench data, ending in a channel dry-run report. Do not start a real publish
adapter until that rehearsal is reproducible and its blockers are useful to
Kyle.

## Immediate delivery order

1. Wire one existing new-product workbench record into the product and content
   adapters through read-only domain-owned loaders.
2. Produce one integrated readiness report: product approval, selected content
   completeness, channel-specific missing conditions, and source lineage.
3. Expose that report in the existing local Codex-controlled console without
   changing current routes or performing a channel write.
4. Validate the supply-chain parser against one redacted real export after
   credentials are rotated; keep the synthetic fixture as the regression
   baseline.
5. Build the first SKU/channel margin view from real local cost and settlement
   tables, preserving missing-cost/currency/time flags.
6. Only then prepare one explicitly approved controlled channel write.

## Week 1: governance and stable seams

- Keep one canonical repository and remove the parent no-commit Git ambiguity.
- Preserve the full test baseline and make domain ownership visible in code.
- Freeze old multi-agent auto-start paths while retaining manual recovery.
- Record one measurable outcome and one backlog for each domain.

Exit criterion: isolated Codex worktrees can be created from the canonical
repository and the complete test suite passes.

## Week 2: product and content closure

- Product operations adapts one existing SKU/product flow into
  `ProductRecord` and `ApprovedProductPackage`.
- Content operations turns the current storyboard/image review into a traceable
  `ContentPackage` with prompt/version/asset lineage.
- Product approval must not publish to a channel; it only hands approved
  packages to channel operations.

Exit criterion: one product can move from intake to approved product and
content packages without a real marketplace write.

## Week 3: channel and supply-chain closure

- Channel operations consumes approved packages through a dry-run publication
  adapter, then performs one explicitly approved controlled publish.
- Supply-chain operations creates a read-only Seaya/Yacang inventory snapshot,
  daily diff, and low-stock signal. Credentials must be rotated before work
  resumes because earlier credentials appeared in conversation history.
- No warehouse or marketplace mutation is part of the first supply-chain loop.

Exit criterion: one approved package has a reproducible channel dry run, and
one daily inventory snapshot can be compared with the previous snapshot.

## Week 4: data operations and integrated routine

- Convert SKU cost and settlement inputs into `FinancialFact` records using
  decimal-safe amounts and explicit currency/time.
- Produce a SKU/channel margin view with data-quality flags for missing costs,
  weights, settlement, and ad spend.
- Run an end-to-end rehearsal and retire or queue every bypass around approval
  and audit boundaries.

Exit criterion: Kyle can use the five Codex threads for a weekly operating
cycle and the CEO thread can reproduce the business state from committed code
plus local governed data.

## Definition of done by domain

| Domain | First closed loop | Success measure |
| --- | --- | --- |
| Product | intake to approved product package | one package is reproducible and auditable |
| Content | brief to approved content package | assets, prompt version, and review decision are traceable |
| Channel | approved package to listing lifecycle | dry run is deterministic; real write is explicitly approved |
| Supply chain | warehouse snapshot to replenishment signal | daily diff and low-stock list are generated read-only |
| Data | source facts to margin view | missing inputs are visible and monetary math is decimal-safe |

## Work-in-progress limits

- One active business outcome per domain.
- One shared-platform contract change in integration at a time.
- No domain thread edits another domain's tables or adapters directly.
- CEO integration happens only from tested commits; unfinished experiments stay
  on their domain worktree.
