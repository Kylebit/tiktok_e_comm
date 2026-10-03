# Evidence-bound R3 closure service

`shared_platform.product_publication_closure.PublicationClosureService` is the
application boundary for closure preparation, persistence and current validation.
It requires explicit ReleaseStore, RunStore, ReportStore, authority and closure
roots. No HTTP route or UI consumer is installed in this work package.

Call `prepare(offer_id, plan_id, recorded_by, recorded_at, manual_handoffs=None)`
with keyword arguments. It loads the exact approved plan/snapshot and existing
persisted final candidate/approval, then consumes the R3 status projection across
every approved target. Source run identities, report digests, summary counts,
timestamps and scoped authority are checked by the existing projection/store.
The service checks source files and database paths for links/reparse points before
reading evidence. No provider readback or publication is performed.

The read-only result has `status`, complete `target_results`, `blockers`, `closure`,
`input_digest` and an empty `writes_performed` list. NOT_RUN, unfinished, unknown,
missing/corrupt/ambiguous evidence or a superseded plan yields BLOCKED. It contains
no closure document and does not assert business_complete. v1 is unchanged:
it requires real source runs and cannot represent an unexecuted target honestly.

READY_TO_RECORD includes the existing v1 document and an input digest covering
the document, entire projection and final approval digests. `record(prepared)`
re-prepares from durable evidence and compares both the exact document and input
digest before immutable persistence. New runs, missing reports, altered inputs
or supersession invalidate an earlier preparation. Repeating identical input is
idempotent. A preview is not a new business approval requirement.

All target source statuses and evidence codes are derived from verified reports.
PUBLISHED counts as official success only under the projection's existing checks.
PROCESSING remains OPEN_PROCESSING and FAILED remains FAILED. A manual handoff
must be explicitly supplied under its exact target label, with accepted_by,
accepted_at and note. These values are retained; timestamps must follow the
selected source report and not exceed recorded_at. No person/time/note defaults
are generated. Manual handoff never removes UNKNOWN or counts as official success.

`latest(offer_id=..., plan_id=...)` selects only the exact plan and revalidates
against current bound evidence. A later conflicting run makes an old closure
invalid as current completion; the immutable historical file remains untouched.
Corrupt, missing, linked or mislocated closure files raise explicit errors rather
than falling back to an older completion. Because a malformed file cannot safely
be assigned to a plan, it conservatively blocks history loading for that offer.
Times are compared as timezone-aware instants; ties are explicit conflicts.

The pure `build_publication_closure` / `validate_publication_closure` helpers
remain schema-only compatibility functions. The legacy `store_publication_closure`
now uses the established immutable file helper, but alone still does not verify
source truth. Likewise the legacy latest helper validates storage/schema only.
New application consumers must use the service. There is no parallel ledger or
new permission model. Report digest strings in v1 retain the sha256: prefix; they
bind the complete verified report representation, including stored metadata.

Future HTTP/UI integration should instantiate this service using the same stores
and trusted startup roots as publication-stages; expose prepare as read-only and
record as an explicit local closure action consuming its exact prepared result.
Show blockers per target and keep manual handoff distinct from official success.
Do not let clients submit source statuses, source report paths, or success counts
as authority. Handle stale previews by displaying current evidence, without
repeating publication. Map validation errors explicitly; do not substitute null
or an older closure. No such route, approval UX, or provider outcome is claimed here.

Validation is offline with real Store/compiler/final approval/runner/report paths
and synthetic executor outputs. Cross-store updates are not one distributed
transaction: record rechecks evidence immediately before the immutable save, and
latest rechecks it again when consumed. A stored closure is historical evidence,
not a lock preventing future runs or an ongoing declaration of provider success.
