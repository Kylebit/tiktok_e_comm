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
