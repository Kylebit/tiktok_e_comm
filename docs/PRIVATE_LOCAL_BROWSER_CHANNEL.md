# Private local browser channel

This transport is explicitly installed by a private fixture/service object. The
normal product service responds `503 LOCAL_OPERATOR_FEATURE_DISABLED`; no env
variable, caller SID, approved_by, loopback address or Origin enables it.

`scripts/private_local_browser_bootstrap.py --private-root <synthetic root>
--port <private listener port> --reservation-id <verified COMMON reservation>
[--marketplace-plan-id <registered marketplace plan>]` obtains the actual effective Windows TokenUser
and verifies the existing owner-only ACL. It grants a same-user session and
opens the actual init page with an independent 60 second, single-use handoff.
The handoff binds owner, instance, session, exact listener origin and a review
path built from the same database's verified candidate Offer and plan. The init
script removes the fragment with replaceState before its first network request.
The reusable capability is an HttpOnly cookie; the separate CSRF cookie is
script readable. Cookie names bind instance and port, and POST requires exact
loopback Host and exact source Origin. This is consistent with allowing another
program under the same Windows user to obtain a controlled local capability.

The handoff is consumed in the same private release.db transaction and its raw
grant JSON is cleared. A caller cannot bootstrap with its own SID, choose a
different port, replay the handoff, or submit approved_by as authority. Request
bodies are limited to 4 KiB and a three-second read timeout. No reusable secret
is put in a URL, response body or application log.

Current prepare/decision methods still use the explicitly synthetic ledger.
The new domain adapter explicitly registers complete producer bytes in the
same database and rebuilds them on each prepare/decision; it rejects caller
FrozenReview and changed content. It does not turn synthetic COMMON evidence
into official evidence. It must not approve a real R3 marketplace candidate: COMMON and marketplace
have different plans and the production bridge owns the complete R1/R2,
ordered target, display, price, budget and official COMMON evidence. The
original review page integration requires a domain-produced descriptor and
rechecks its displayed digest before one decision; the legacy raw approve
route remains closed. Provider execution authority remains false.

A short final-review nonce is refreshed for the same displayed frozen digest
without a new human approval. Source drift changes the displayed digest and
blocks the original click until the updated candidate is displayed. Losing the
response or renewing a same-user session preserves the durable decision.

This package does not claim a second logged-in Windows user was tested, does not
create Windows accounts, and does not start formal services or use formal data.
