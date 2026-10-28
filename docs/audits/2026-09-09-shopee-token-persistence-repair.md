# Shopee token persistence repair

This narrow repair protects the shop-token refresh required before a later authorized Shopee
settlement read. It does not execute a refresh and does not broaden marketplace write authority.

## Previous failure

`modules/shopee/auth.py` previously loaded the shared JSON token store, sent the refresh request,
then truncated and rewrote the file. Two different shops could refresh concurrently and lose one
update. A transport-unknown response had no durable reconciliation marker and a retry could consume
or invalidate a rotated provider token.

The pre-fix synthetic regression showed four failures, including provider maximum concurrency `2`
instead of `1`, no reconciliation exception/state, and no atomic replacement seam.

## Implemented boundary

- one thread plus cross-process file lock for the complete token store;
- lock-internal reload before selecting the shop refresh token;
- a redacted per-shop state file with `IN_FLIGHT`, `FAILED`, `UNKNOWN`, `COMMITTED`, and
  `COMMITTED_RECOVERED` states;
- token digests only in state metadata, never access/refresh token values or raw responses;
- exactly one provider POST (`attempts=1`, no curl fallback);
- validation of access token, refresh token and positive expiry before persistence;
- same-directory mode-0600 temporary files, flush/fsync, atomic `os.replace`, canonical re-read and
  digest verification;
- a protected `.bak` prior-state copy for evidence only, never automatic rollback;
- an unknown transport, incomplete response, or unverified replacement blocks automatic retry;
- if canonical replacement succeeded but the final audit-state write failed, the next call detects
  the changed refresh-token digest and records `COMMITTED_RECOVERED` without another provider call.
- public whole-store saves are metadata merges: existing shop/merchant credentials cannot be
  replaced by a stale caller, while guarded exchange/refresh paths update credentials explicitly;
- shop-region metadata is merged into a lock-internal fresh store rather than saving its earlier
  credential snapshot;
- HTTP 5xx, malformed/non-object responses, and unclassified provider errors are `UNKNOWN`, use
  local fixed error classifications only, and block a second provider attempt;
- merchant refresh now uses the same lock, single-attempt, redacted reconciliation boundary;
- `IN_FLIGHT`/`UNKNOWN` blocks reuse of the same refresh-token digest through any shop or merchant
  alias, while an independently bound credential remains usable;
- merchant refresh requires its own explicit credential binding and never selects an arbitrary
  shop refresh token as a fallback;
- Windows token replacements and `.bak` creation preserve the existing DACL; a new secret file is
  created with a protected owner-only DACL.

The unavoidable provider-rotation/local-commit crash window remains fail-closed. If the canonical
credential still has the old digest, software cannot prove whether the provider rotated it; manual
re-authorization or provider-supported reconciliation is required.

## Tests and external boundary

All tests use synthetic token files and a fake provider. They cover concurrent refreshes for two
shops, an actual two-process lock, stale shared-store writers, unknown timeout/HTTP 500/malformed
response/provider-error replay blocking, redaction, guarded merchant refresh, token-file replacement
failure, audit-log failure after a verified replacement, refusal to call the provider when the
pre-request state cannot be persisted, incomplete-success-response replay blocking, and Windows
DACL preservation for the canonical file and backup, cross-entity shared-credential blocking, and
an independent-merchant-credential positive control.

Focused and related result: `53 passed` across
`test_shopee_token_persistence.py`, `test_shopee_client_transport.py`, and
`test_shopee_skill_regions.py`. JUnit evidence is
`artifacts/test_logs/pytest-shopee-token-reviewfix-20260909.xml`.

No real token file was read. Provider calls, database writes, settlement reads and marketplace
writes were all zero.
