# S01-C OAuth TLS and credential refresh replay

Work order `WO-20260905-ORBIT-S01-C`; base
`3dbbb5cd80fd83ad8233015f10d35a927c714956`.

The standalone `tiktok_auth.py` OAuth exchange now keeps the certificate and
hostname verification supplied by `ssl.create_default_context()`. Its existing
single urllib request and OAuth behavior remain; no second transport stack,
proxy override, browser launch, or live exchange was introduced.

`core/auth.py` now explicitly passes `attempts=1` and
`allow_curl_fallback=False` for token refresh. This GET changes credentials, so
method-based read retries are inappropriate. An uncertain timeout/reset/SSL
outcome raises its original exception for the owning credential recovery layer;
this caller neither repeats the exchange nor saves a response after failure.

Minimal validation rejects missing/empty access credentials and a missing,
invalid, Boolean, zero or negative access expiry before saving. An explicitly
provided refresh token must be a nonblank string; an omitted refresh token
continues to use the old token. Existing optional fields and refresh expiry
fallback are retained. A successful synthetic response is saved once.

The existing `_expiry_ts` converts to an integer, `access_expires_at` uses
`datetime.fromtimestamp`, and expiry comparisons use `time.time()`. Therefore
this patch validates the existing absolute-timestamp interpretation. It does
not add the current time, reinterpret the field as a relative duration, or
change the raw stored value; parseable string timestamps remain compatible.
This local contract is not a claim about unobserved current provider responses.
Existing unknown/absent expiry treatment and full credential state recovery
remain with Q00; this patch does not certify them as permanently valid.

## Offline evidence

The exact-base red phase produced four failures: insecure OAuth context, four
underlying GET refresh sends after a timeout, and two incomplete responses
being accepted. Three more red cases captured explicitly damaged refresh token
values (integer, null and blank). All 21 final paired tests pass, without skips.
They verify secure exchange context, single sends, original failure identity,
zero saves after failure, one successful save, known-valid token reuse without
refresh/settings access, invalid refresh credentials, and optional-field
fallback. The seven regressions are included in the 21, not added again.

Tests load the actual candidate auth/OAuth/shared transport modules with a
synthetic `core.config`, fake in-memory token load/save, and patched urllib.
Socket connect/bind, DNS, browser, subprocess and SQLite access are blocked;
opening settings.json, tiktok_tokens.json or database files is prohibited.
No business configuration initialization, real credential read/write, token
refresh, OAuth browser/callback, business DB, provider or paid call occurs.
No real TLS handshake is claimed for this package. S01-A's existing TLS evidence
is separate; its 95 tests were not unnecessarily rerun.

## Unchanged upper-level client boundaries

`core/api_client.py:84–99` handles HTTP 401 by refreshing credentials then
re-entering `request` once with `_retry_on_401=False`; lines 103–115 do the
same for provider code 105002. Its `request` loop at lines 130–155 may replay
after HTTP 429 or code 36009037, with default five retries configured at
lines 21–23. These loops accept the original method/body and are independent
of transport-level attempts. They were only read, not changed or executed.

A later owner needs official per-endpoint facts about whether an authentication
or throttling response guarantees non-commit, authorization/token rotation
semantics, idempotency keys and retention, retry limits/Retry-After, and a
lookup/reconciliation route for unknown write outcomes. No provider facts were
guessed or fetched in this zero-network package.

Commands, raw red/green logs, JUnit and exact receipt are retained under
`outputs/orbithive-audit-20260905/security/auth-validation` and
`security/S01_C_RECEIPT.md/json`. No client-loop edit, dependency installation,
old-source write, running-service change or push. Rollback is omitting/reverting
this focused commit; that restores the known OAuth bypass and refresh replay.
