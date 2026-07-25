# Database governance

Status date: 2026-07-25

## Canonical runtime databases

- `data/shop.db` is the commerce catalog and operational SQLite database.
- `data/orbit_platform.db` stores immutable local report runs and Orbit inbox
  items.
- Browser-profile cache databases under `data/auth/` are third-party runtime
  caches and are not business sources of truth.

The main database currently uses WAL mode. A live copy of `shop.db` alone is
not a valid backup procedure because committed pages may still be in
`shop.db-wal`.

## Safe commands

Full read-only health check:

```powershell
.\.venv\Scripts\python.exe scripts\database_maintenance.py check --full
```

Read-only catalog quality report:

```powershell
.\.venv\Scripts\python.exe scripts\database_maintenance.py quality
```

Use `quality --fail-on-review` in a release or publication gate; it returns
exit code 2 while identity, cost, or derived-data blockers remain.

Create a non-overwriting, integrity-checked online backup:

```powershell
.\.venv\Scripts\python.exe scripts\database_maintenance.py backup
```

The backup uses SQLite's online backup API, so it includes committed WAL data
without stopping Orbit. It writes to `backups/database/`, verifies
`integrity_check=ok`, and returns a SHA-256 digest. It never overwrites an
existing backup.

## Current verified baseline

The 2026-07-25 production audit found:

- `quick_check=ok` and `integrity_check=ok`;
- 16 business tables, 1,091 TikTok product rows, 632 Shopee product rows, 847
  costs, and 725 logistics-weight rows;
- no current product-to-shop or cost-to-product orphans;
- `settlement_lines`, `ad_spend_daily`, and `affiliate_invites` are empty;
- the schema has no declared foreign keys or triggers and `user_version=0`;
- schema creation and incremental `ALTER TABLE` calls are still distributed
  across business modules.

The first verified online backup is recorded locally under
`backups/database/`. Backup files and databases are ignored by Git.

## Data-quality gates

P0:

- Five seller-SKU duplicate groups exist inside `UK_IMPORT_GB`: `0003`,
  `0153`, `0200`, `0619`, and `0926`. Reads or mutations that select one row
  with `LIMIT 1` are ambiguous until the UK identities are reviewed.

P1:

- 244 product rows have no direct platform-SKU cost. Canonical seller-SKU
  fallback resolves 146; 98 rows across 28 business keys remain unresolved.
- Three canonical keys have conflicting positive costs:
  `0018` (`5`/`5.5`), `0810` (`8`/`8.5`), and `0934` (`10`/`11`).
- 137 analytics rows no longer match the current active-product snapshot.
  They must be classified as history or stale derived data before deletion.
- 97 logistics rows do not match an exact regional seller SKU, but only four
  fail the approved numeric tail-four alignment. Do not bulk-delete all 97.
- One Shopee product row has a non-positive price.

P2:

- Add a central, versioned migration ledger before introducing foreign keys or
  CHECK constraints.
- Define whether analytics is a current snapshot or retained history.
- Add explicit reservation/uniqueness governance for new seller SKUs.
- Remove or quarantine packaged `dist` database copies so runtime state cannot
  drift from the canonical database.

The Orbit build script now strips a generated `_internal/data` directory only
after verifying that it is inside the current build bundle, then rejects any
remaining `shop.db`, `orbit_platform.db`, browser `Cookies`, or `Login Data`
file. Existing older bundles must still be quarantined or rebuilt once.

## Restore procedure

1. Stop the Orbit process that owns port 8765.
2. Run `check --full` against the backup file.
3. Preserve the current `shop.db`, `shop.db-wal`, and `shop.db-shm` as one
   recovery set; do not delete them immediately.
4. Copy the verified backup to `data/shop.db`.
5. Start Orbit and run `check --full` again before any synchronization or
   channel write.

Do not restore into a running process, and do not copy only a live WAL-mode
`shop.db`.
