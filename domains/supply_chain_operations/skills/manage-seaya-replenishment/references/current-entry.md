# Supply entry, safe collection and adoption

Source roles were verified on 2026-10-05. Recheck the work order and anchors before
using these paths; path existence does not prove a running consumer.

| Role | Binding |
| --- | --- |
| Installed personal Skill | `C:/Users/Windows11/.codex/skills/manage-seaya-replenishment`, a junction to the D04 domain Skill below |
| Personal D04 source | `D:/Users/Windows11/Desktop/Agent_PR/_codex_worktrees/04-supply-chain-ops`, branch `codex/supply-chain-ops`, historical HEAD `44ab16a6074186daa76cc8bc671da61c8552c160` |
| Maintenance source | `E:/OrbitHive/wt-publish-current-instructions-20261005`, HEAD `9334001d42fa05b03f54fa851a8011a68c556aaa` |
| Formal service source | `E:/OrbitHive/wt-ui-availability-wording-20261005`, HEAD `594615493b1bba3aad489d39f73939bf33374b08`, port 49289 |
| Formal supply data | `E:/OrbitEvidence/supply-d04-preflight-20261001/final4ca6-private-runtime`, admitted **2026-09-30 historical** `AUDITED_MANUAL_SNAPSHOT`; no execution authority |
| Exact settings | `D:/Users/Windows11/Desktop/Agent_PR/tiktok_e_comm/config/settings.json` |
| Python | `D:/Users/Windows11/Desktop/Agent_PR/tiktok_e_comm/.venv/Scripts/python.exe` |
| TikTok existing token file | `D:/Users/Windows11/Desktop/Agent_PR/tiktok_e_comm/tiktok_tokens.json` |
| Shopee D04 token file | `D:/Users/Windows11/Desktop/Agent_PR/_codex_worktrees/04-supply-chain-ops/shopee_tokens.json` |

Residual `C:/Users/Windows11/Desktop/Agent_PR/tiktok_e_comm` is not an execution
source. Do not use its config, imports or default output because cwd lookup failed.
The isolated repair source is explicitly selected by its profile, not by drive
letter inference. Neither an installed junction nor an E formal historical
capture automatically follows a new candidate or a D04 refresh. Preserve D04 WIP,
token backup/lock/recovery files and frozen E evidence. Do not clear leases,
replay queued operations, initialize the missing workbench DB or restart a server
to make source inspection succeed.

## Explicit profile and no-refresh reads

`scripts/read_orders_explicit.py` accepts a profile with exactly these fields:

```json
{
  "schema": "supply-read-profile/v1",
  "source_root": "ABSOLUTE_EXACT_EXECUTING_CHECKOUT",
  "settings_path": "ABSOLUTE_EXACT_SETTINGS_JSON",
  "token_paths": {"tiktok": "ABSOLUTE_EXISTING_FILE", "shopee": "ABSOLUTE_EXISTING_FILE"},
  "output_root": "ABSOLUTE_OWNED_RUNTIME_OUTSIDE_CODE",
  "targets": {
    "MY": {"warehouse":"MY8803", "shopee_shop_id":1561117812, "tiktok_shop_id":null, "tiktok_cipher":null},
    "TH": {"warehouse":"TH8806", "shopee_shop_id":1561124013, "tiktok_shop_id":null, "tiktok_cipher":null},
    "VN": {"warehouse":"VN8805", "shopee_shop_id":1723948773, "tiktok_shop_id":null, "tiktok_cipher":null},
    "PH": {"warehouse":"PH8807", "shopee_shop_id":1527371343, "tiktok_shop_id":null, "tiktok_cipher":null}
  }
}
```

Null TikTok fields mean pending identity, never guessed targets. `--read-identity`
uses the existing valid token on `/authorization/202309/shops`, requires exactly
one observed shop per country, compares any already pinned identity and reports
only the four non-secret identities. Pin the returned exact IDs/ciphers in the
same local profile before any order collection. Shopee IDs above are this user's
reviewed D04 targets; do not reuse them for another account/profile.

Run in a fresh process, with no conflicting inherited `ORBIT_HIVE_SETTINGS`:

```text
python read_orders_explicit.py --profile ABSOLUTE_PROFILE --preflight
python read_orders_explicit.py --profile ABSOLUTE_PROFILE --inspect-sessions
python read_orders_explicit.py --profile ABSOLUTE_PROFILE --read-identity
python read_orders_explicit.py --profile ABSOLUTE_PROFILE --read-orders
```

Preflight binds paths and exact country scope without HTTP/auth/output writes.
Session inspection reads existing credentials in memory and reports only expiry,
readiness and exact file blockers. Unknown/unreadable recovery journals are not
ignored. It does not call legacy `access_token`, `ensure_shop_token`, `sync_shop_ids`
or their credential refresh/save paths. The legacy standalone
`pull_order_demand.py` CLI still has auth side effects and is not this read-only
entry. Its explicit injected `capture_orders` API is reused instead.

Each actual request has an exact endpoint allowlist, existing selected token,
single-attempt transport, no automatic credential recovery and no hidden curl
fallback. Settings and session hashes are checked before each wire call and
after capture. A changed/unreadable source aborts. TLS verification remains
enabled. Provider rejection, rate limit or expiry is a concrete read blocker,
not permission to expand scope or retry an auth mutation.

Full collection retains complete terminal-page receipts and Shopee detail-ID
reconciliation, redacts buyer/credential fields and writes a uniquely named local
source only after success. No DB, business lease, procurement, inventory or
dashboard application is performed. Keep raw order IDs in private runtime
evidence; only SKU aggregates belong in dashboard data.

When only Shopee is blocked, `--read-tiktok-orders` continues the independent
four-store TikTok read. Its `supply-partial-order-read/v1` explicitly declares
`TIKTOK_ONLY_NOT_COMPLETE` and missing Shopee. Preserve unresolved SKU counts;
those lines must be resolved through exact catalog relations before complete
quantity admission. Partial evidence cannot enter the governed captured refresh
or replace missing demand with zero. A new complete read is needed after exact
authorization/reconciliation is restored; never replay an uncertain refresh.

When TikTok has already been captured, use `--read-shopee-orders
--continue-from-tiktok ABSOLUTE_RETAINED_TIKTOK_SOURCE` after the original owner
has resolved the exact Shopee authorization dependency. This reads only Shopee
sessions, validates all retained TikTok page receipts/targets, preserves their
31-day event cutoff, verifies each official Shopee shop ID/region and pulls only
the missing four Shopee scopes. It records independent collection/materialization
clocks and a retained-source hash; no TikTok identity or order request is repeated.
The output remains `SHOPEE_ONLY_NOT_COMPLETE`, kept separately from original
TikTok evidence. Unresolved facts remain visible and are never silently removed
to assert full admission.

`get_shop_info` uses the exact signed request shop ID and the observed official
region. The existing client returns raw JSON; identity fields can be top-level
or nested under `response`. A returned shop ID, when present, must match exactly;
do not invent a returned ID when the provider omits it. Preserve failed read
receipts before correcting a parser, and never repeat a credential refresh to
repair an identity-shape error.

The original `modules/shopee/auth.py` journal has `schema_version:
shopee-refresh-state/v1` and per-entity `shops`/`merchants`, not one top-level
status. `COMMITTED`/`COMMITTED_RECOVERED` are terminal; `IN_FLIGHT`/`UNKNOWN`
require original-owner reconciliation, including aliases sharing the same
refresh credential. An unreadable or unsupported journal blocks the dependent
read. The no-refresh reader never clears it, changes ACLs, recovers status or
rotates credentials. Owner recovery is a separate explicitly authorized action.

For this D04 journal, the original guarded entry is
`modules.shopee.auth.refresh_token(shop_id)` with the exact token path explicitly
pinned by `ORBIT_SHOPEE_TOKEN_PATH`, and exact settings selected before import.
The original owner must inspect the existing journal first; `IN_FLIGHT`/`UNKNOWN`
with the same old refresh digest blocks replay. A changed, durably stored
credential can be reconciled by that original guard, without a new provider
attempt. If a refresh is explicitly authorized and safe to dispatch, it can
rotate the selected credential and updates the same token/backup/lock/journal;
the reviewed source makes a single physical POST, preserves other entries and
does not weaken ACLs. Serialize with other cooperating token writers and stop
on uncertain results. Do not run old D daily's unsafe transport, switch to the
new adapter's separate `.recovery-state-*` journal to skip this original state,
delete a journal, copy credentials into a more readable path or change ACLs.
Owner-only state files may require both recovery and the following Shopee-only
order read to stay under that authorized owner identity. Four shop IDs above
remain the full intended scope; no new store or permission is requested.

## Seaya and refresh continuation

Use an independent logged-in official browser tab or a separately reviewed
read-only adapter. Do not claim the local `seaya_capture.py` contract is a live
Seaya HTTP transport. Capture the tenant identity, exact four warehouses,
unfiltered status scope, total and every page, terminal-page witness, observation
clocks and complete raw identities. Zero activity requires pending-review,
transporting, waiting-inbound and inbound-processing all to be observed as zero,
plus every stock row's inbound quantity to reconcile. Preserve completed/voided
batch identities; their stock is not future supply.

Latest browser evidence alone does not fulfill an incomplete tenant/protocol
capture contract. Keep it as current witnessed read evidence. Convert only exact,
complete evidence through `seaya_capture.capture`; do not manufacture provider
receipts, page cursors, tenant IDs or new dates from old aggregates.

Once inventory/inbound and all eight store order scopes are complete, follow
[captured-refresh.md](captured-refresh.md): freeze the clean source/profile,
raw source hashes, all three independent clocks, destination seed and local
images; stage the existing consumers, inspect the per-SKU preview, then apply
only the named stage digest to the explicit runtime output. Matching dates alone
do not establish live readiness. Preserve last-good data if collection fails.
The final editable table follows [decision-contract.md](decision-contract.md).

The existing Orbit `/supply-chain/` Handler and original dashboard remain the
display contract. An exported HTML is only an attachment and cannot stand in
for data admission or running-service adoption. Verify the original route in an
isolated original Handler fixture; never replace it with a new frontend. A
formal-page audit that must remain read-only should read only its static HTML
and reviewed resources without executing the shared operations shell: stable
`/api/orbit/operations-runtime` calls `engine.dashboard()`, which expires leases.

Candidate adoption is separate from runtime activation: replay focused changes
on the coordinator's isolated combined maintenance candidate, resolve only those
paths, refresh/check dashboard-Skill hashes and run affected code/browser tests.
Personal junction adoption requires the exact selected source under the current
work order; do not repoint another worktree automatically. Formal activation
requires a separately reviewed data admission and deployment against fresh
source/config/OS identity. Never directly overwrite the frozen 49289 package,
deploy, push, migrate a shared DB or claim business acceptance from code/tests.
