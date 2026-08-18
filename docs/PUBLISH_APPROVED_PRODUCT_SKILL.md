# Publish Approved Product Skill governance

`skills/publish-approved-product/` is the Git-owned single source of truth for
the installed `publish-approved-product` Skill. Runtime copies under
`~/.codex/skills/` are installations, not an independent place to edit.

## Safe installation and parity

Check the current installation without writing anything:

```powershell
python scripts/sync_publish_approved_product_skill.py --check
```

Install or refresh the runtime copy from the repository explicitly:

```powershell
python scripts/sync_publish_approved_product_skill.py --install
```

The script hashes every canonical file (normalizing only UTF-8 text line
endings so Git checkout policy cannot create false drift), ignores only
Python/pytest runtime caches, writes managed files atomically, and refuses to
declare parity while unmanaged or divergent source files remain. It does not
launch the Skill or contact any commerce platform.

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
