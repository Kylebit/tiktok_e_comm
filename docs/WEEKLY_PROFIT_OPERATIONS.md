# Weekly profit operating loop

## Business outcome

Every Monday at 09:00, `OrbitWeeklyProfitPush` builds the previous complete
Monday-to-Sunday realized-profit digest and places one idempotent item in the
local Orbit inbox.

The weekly digest is separate from:

- the monthly settlement close planned for the 16th;
- `/sku-profit`, which remains an on-demand SKU probe;
- estimate rows, which are never mixed into realized settlement totals.

## Data flow

1. Read `data/shop.db` in SQLite read-only mode for platform-SKU mappings and
   versioned catalog costs.
2. Read supported local TikTok income CSV and Shopee weekly HTML snapshots.
3. Normalize numeric seller SKUs to the four-digit business key.
4. Apply source quantity to unit cost, deduplicate overlapping snapshots, and
   aggregate by calculation kind, channel, region, and seller SKU.
5. Persist the immutable report and one notification to
   `data/orbit_platform.db`.

No marketplace API, Feishu webhook, or production commerce table is written by
this loop.

## Commands

Dry-run the previous complete week:

```powershell
.\.venv\Scripts\python.exe -m shared_platform.weekly_profit_runner
```

Dry-run an explicit period:

```powershell
.\.venv\Scripts\python.exe -m shared_platform.weekly_profit_runner --start 2026-07-20 --end 2026-07-26
```

Persist one local Orbit report:

```powershell
.\.venv\Scripts\python.exe -m shared_platform.weekly_profit_runner --persist-local
```

## Current review gates

A report remains `needs_review` when any governed input is incomplete. The
current expected gates are:

- legacy Shopee snapshots do not prove order quantity;
- advertising-spend facts are not yet attached;
- a small number of catalog seller SKUs have conflicting positive costs;
- used settlement rows may have no matching cost.

The displayed profit is therefore explicitly preliminary until those gates are
closed. New Shopee snapshots retain quantity, unit cost, and total line cost;
legacy files remain auditable rather than being silently reinterpreted.
