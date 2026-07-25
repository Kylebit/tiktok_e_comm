# Orbit Release Candidate V1

## Purpose

`/release` is the local acceptance console for the first formal Orbit
candidate. It joins the two current operating priorities without merging their
business facts:

- product listing: product facts → approved content/images → Seller SKU
  approval rehearsal → channel draft rehearsal;
- profit operations: latest realized weekly digest → read-only weekly
  recomputation → Seller SKU or exact platform-SKU estimate.

The page is a shared-platform view. Treasury remains the owner of the product
workbench and paid image generation; Data Operations remains the owner of
weekly realized facts and SKU estimates.

## Safety contract

The release console:

- reads the workbench state, image-review evidence, commerce catalog and local
  Orbit report store;
- creates only an in-memory product approval for contract rehearsal;
- builds channel listings with `dry_run=True` and
  `approval_required=True`;
- recomputes weekly profit without persisting a `ReportRun` or sending a
  notification;
- contains no product-publish or marketplace-write action.

Treasury business writes now require a literal JSON boolean confirmation at
the backend. Missing values, `false`, the string `"true"` and numeric `1` are
rejected before a network call or worker starts.

| Action | Required confirmation |
| --- | --- |
| Miaoshou pre-collection | `confirm_miaoshou_precollect: true` |
| AI storyboard planning | `confirm_ai_planning: true` |
| Miaoshou image/draft/SKU write | `confirm_miaoshou_write: true` |
| TikTok collection-box claim | `confirm_tiktok_claim: true` |
| Site draft creation | `confirm_site_draft_write: true` |
| Paid image generation | `confirm_paid_generation: true` |

Normal Treasury refreshes use `precollect:false`; refreshing a page cannot
create a collect-box item.

## Current real release gate for offer 3828811808

The approved content contract contains the five Kyle-approved images in the
saved order. Candidate Seller SKU `0946` is currently unique in the local
catalog and passes the approval rehearsal. The real release remains blocked
until all of the following are deliberately completed:

1. persist the product approval fact for offer `3828811808` and Seller SKU
   `0946` with a revision-checked transaction;
2. lock the workbench commercial fields;
3. explicitly synchronize the current five-image set, replacing the stale
   historical 11-image write;
4. approve the owning channel adapter action separately.

## Profit semantics

- Weekly digest rows are realized settlement facts. A `needs_review` digest is
  never presented as final accounting or as a usable profit headline.
- The current local weekly report is preliminary because governed advertising
  spend is not attached and legacy Shopee snapshots contain missing quantity
  evidence. Release Lab therefore shows settlement evidence but hides the
  profit conclusion until the data-quality gate is `ready`.
- A weekly recomputation must be one complete Monday-through-Sunday period.
- The SKU probe is an estimate. It accepts a Seller SKU or an exact long
  platform SKU ID.
- Unknown long platform IDs never fall back to their last four digits.
- Browser/API callers use explicit `ad_rate_percent` (`0..100`); internal
  callers use `ad_rate` as a fraction (`0..1`). The two fields are mutually
  exclusive. Lookback is limited to `1..365`, and manual sale/cost overrides
  must be finite positive numbers.

## Local acceptance

Start the services:

```powershell
.\.venv\Scripts\python.exe main.py serve --port 8765 --page release --no-browser
.\.venv\Scripts\python.exe scripts\start_new_product_server.py 8766
```

Open `http://127.0.0.1:8765/release`.

Required acceptance scenarios:

1. offer `3828811808`, Seller SKU `0946`: four rehearsal stages pass while
   the real release gate remains blocked;
2. Seller SKU `0021`: uniqueness rehearsal blocks it;
3. weekly period `2026-07-13` through `2026-07-19`: read-only recomputation
   returns `needs_review` and does not add a local report or inbox record;
4. SKU `0021`, both platforms: TikTok and Shopee estimates render;
5. TikTok SKU `1732993420424480699`, both platforms: TikTok succeeds and
   Shopee is a transparent partial failure;
6. invalid platform, ad rate or lookback: API returns HTTP 400;
7. viewport widths 1440 and 390: no horizontal overflow and all primary
   controls remain usable.
