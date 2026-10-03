# R1 initial category options backend

The initial request accepts empty selection intent. POST category action `options`
uses `round1-category-options-request/v1` with the existing explicit offer,
revision, ordered targets, source region, account digest and context digest.
The response contains an immutable options reference and digest, category paths,
attribute labels and value identities. It does not select a category or values.
An options record is not a complete observation or a freeze receipt.

POST `capture` with `round1-category-capture-request/v3` supplies that options
reference/digest and explicit category, attribute and value identities. The
server resolves identities only within the offered candidate. Existing required,
single, multi, text, unit and zero-value rules apply. It rechecks current input,
region/account, official category path and full attribute tree before producing
the complete observation used by the existing resolve/prepare/freeze chain.
Path or tree drift returns `RECHECK_REQUIRED`. There is no TTL authority shortcut.
Legacy and v2 capture contracts remain available.

Each request has immutable purpose and payload identity. Its ledger is persisted
before collection. Every official GET records STARTED before transport and
RECEIVED after transport returns. Attempted and returned counts describe those
boundaries, not provider success. Options storage and success linkage commit
atomically. Partial failure, lost response and restart remain readable through
capture-status; repeating the same request ID never recollects. Conflicting
payload/purpose is rejected. An interrupted request is UNKNOWN; callers must
reconcile it, not automatically create another ID. Legacy unmeasured counts are
null rather than invented zeroes.

Technical resource limits are 10 recommended candidates, JSON depth 32, 50,000
visited entries and 4 MiB of normalized UTF-8 JSON per official response. These
are local implementation bounds, not official business limits. Candidate overflow
fails without truncation. Response checks run on decoded transport output before
semantic use or storage; they do not impose a streaming wire/allocation limit on
the underlying HTTP client. Only recommendation, category and attribute GETs run.

Controlled validation uses synthetic credentials/GET responses, temporary SQLite,
the real Handler and a loopback HTTP server, with a guard installed before imports.
The accepted parent returns 404 for the new empty-intent action (valid red).
The final related suites pass 131 tests. A two-candidate chain performs 5 options
GETs and 2 capture GETs; repeat/status/prepare/freeze add zero. A partial third-GET
failure retains attempted=3 and returned=2, with zero replay GETs. Tests cover
candidate/attribute/context mismatches, path/tree drift, concurrent request IDs,
atomic rollback, resource limits and legacy behavior.

This package contains backend, dedicated tests and this document only. Frontend
integration, real account capture and marketplace publication are not validated.
