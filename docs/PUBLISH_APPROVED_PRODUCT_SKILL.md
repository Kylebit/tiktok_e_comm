# Publish Approved Product Skill governance

`skills/publish-approved-product/` is the Git-owned single source of truth for
the installed `publish-approved-product` Skill. Runtime copies under
`~/.codex/skills/` are installations, not an independent place to edit.

## Safe installation and parity

Use the explicit runtime and destination in the [tool entry](tools/README.md)
and its path preflight. Do not run a default personal-directory check or install
merely to inspect this source. The old no-target CLI examples are compatibility
forms for a separately scoped personal installation task.

An installable Skill tree does not establish runtime readiness; check the
registered source and full workflow dependencies separately. The current
[closure entry](AGENT_HANDOFF.md) requires the complete workflow runtime and
its exact original evidence. The old personal HTTP closure reference is not
the source contract and its `doctor` / `--base-url` examples do not apply.
Parity uses the existing complete Skill manifest and LF-normalized text hashes;
retain extras and partial results, and report exact diagnostics. Installation
within an existing authorized scope does not require a second approval.

## WP1 report boundary

The Skill will eventually receive an
`approved-publication-snapshot/v4` envelope from Product Operations. Shared
Platform stores only its schema version and digest; product semantics remain
owned by domain 01.

Publication reports are indexed in additive SQLite table
`product_publication_reports` and written below the server-owned path:

`reports/product-publication/<offer>/<revision>/<run>/report.json`

The Product Center read-only API is:

- `GET /api/product-workspace/publication-report?offer_id=...&report_id=...`
- `GET /api/product-workspace/publication-reports?offer_id=...&revision=...`

Only four user-facing outcomes are accepted: `PUBLISHED`, `PROCESSING`,
`PARTIAL`, and `FAILED`. The public projection contains redacted counts,
evidence booleans, and digests; it excludes report paths, credentials, raw
provider responses, copy, URLs, and external IDs.

WP1 intentionally provides no POST endpoint, runner, process launch, channel
adapter call, or change to existing publication buttons.

## Approved snapshot persistence follow-up

`approved_publication_snapshots` is an additive table in the shared
ReleaseStore database. A ReleasePlan that declares
`approved-publication-snapshot/v4` is approved and frozen in one
`BEGIN IMMEDIATE` transaction: approval insert, plan status transition, typed
snapshot validation, and immutable snapshot insert either all commit or all
roll back. Reopening the database revalidates the canonical JSON against the
01 product-owned contract and the exact ReleasePlan digest.

The Product Center summary endpoint is read-only:

`GET /api/product-workspace/publication-snapshot?offer_id=...&plan_id=...`

It also accepts `snapshot_digest` instead of `plan_id`. The HTTP response
contains identity, digests and coverage counts only. The full self-contained
snapshot is available exclusively through the server-owned internal runner
seam.

Plans approved before this migration remain immutable and project
`SNAPSHOT_UNAVAILABLE`; they are never rebuilt from a current dashboard. The
strict projector can activate v4 automatically when server-owned upstream
inputs provide every required fact. As of this integration, the historical
production-shaped plan fixture is still missing:

- Product Operations: approved description, structured per-SKU specification,
  and per-SKU image binding;
- Content Operations: the approved description/variant-image hand-off;
- Channel Operations: target-exact official category ID/name/path and decision
  digest for every selected storefront (the product main category is never a
  provider fallback);
- Shared approval evidence: content, policy, category, pricing and SKU-lineage
  digests in the immutable plan payload.

Until those facts are present, the old plan remains readable as
`SNAPSHOT_UNAVAILABLE`; the platform neither guesses them nor retroactively
modifies the approval.
