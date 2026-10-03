# Local publication closure HTTP API

These endpoints consume PublicationClosureService directly. They do not publish,
reconcile with providers, or create a new approval step. No UI is included.

| Endpoint | Method | Input | Result |
| --- | --- | --- | --- |
| /api/product-workspace/publication-closure/prepare | POST | offer_id, plan_id, recorded_by, recorded_at; optional manual_handoffs | Read-only READY_TO_RECORD or BLOCKED preparation |
| /api/product-workspace/publication-closure/record | POST | Same explicit inputs plus the prepare input_digest | Rebuilt and revalidated immutable local record |
| /api/product-workspace/publication-closure/latest | GET | Exactly one offer_id and one plan_id query parameter | Exact-plan current validated closure or NOT_RECORDED |

The server instantiates the service from its existing ReleaseStore, RunStore and
ReportStore factories. The approved authority root is the same bridge.REPORTS_ROOT
used by publication-stages. Closure files use ReportStore.reports_root. No request
can override those roots, a candidate file, source reports, statuses or counts.
Unknown fields and duplicate JSON keys are rejected. record does not accept an
echoed closure document: it reconstructs from the explicit inputs and compares
the supplied digest before calling the service's own save-time revalidation.

Responses use publication-closure-http/v1 and external_writes_performed: [].
Prepare returns the service's complete target_results, blockers, closure,
input_digest and empty writes_performed. BLOCKED has no closure/business_complete.
If approved scope is known but authority loading fails, the response retains
every plan target with explicit evidence blockers rather than inventing results.

Record success returns RECORDED, the exact closure and input_digest, with
local_persistence: IMMUTABLE_RECORD_CONFIRMED. The same request is idempotent;
this field confirms persisted content, not that another file was written.
Latest returns RECORDED plus the validated document, or NOT_RECORDED plus null
only when no closure exists. Corrupt/stale history is an explicit error, never
null or an older successful closure. GET creates no local records or directories.

Manual handoff input is a mapping from exact approved target label to accepted_by,
accepted_at and note. All three must be explicit; no user consent or timestamp is
generated. Provider failure/processing remains in source_status. Manual handoff
does not count as official success or clear UNKNOWN. recorded_by is a supplied
audit attribution; this local API adds no identity authentication mechanism.

| HTTP status | Meaning |
| --- | --- |
| 200 | Valid read/prepare or confirmed local record |
| 400 | Missing/unknown inputs, ambiguous query, duplicate keys, malformed JSON/framing |
| 403 | Invalid loopback Host or cross-origin POST |
| 404 | Unknown action or missing plan |
| 405 | GET on prepare/record or POST on latest |
| 409 | Blocked/stale/conflicting/unreadable evidence or invalid disposition |
| 413 / 415 | Existing product-workflow body limit / JSON content-type requirement |

Host must be exactly localhost:port, 127.0.0.1:port or [::1]:port for the server's
bound port. Suffixes, duplicate/multiple values and wrong ports are rejected.
POST reuses the existing Origin contract: absent Origin is accepted for local
clients; present Origin must be http://localhost:port or http://127.0.0.1:port.
An IPv6 Host is accepted, but IPv6 Origin is not added to that existing allowlist.
Other unsupported methods retain BaseHTTPRequestHandler behavior. POST query
overrides, duplicate Content-Length/Origin and Transfer-Encoding are rejected.
These guards apply only to the new closure paths; legacy routing is unchanged.

Closure and source reports share a root. If a closure-prefixed directory has no
closure-report.json, it is recognized as a source run only after the same
ReportStore verifies report.json against its durable index, digests and exact
offer/revision/run identity. A present closure is always validated as closure.
An unindexed copy, damaged source, or damaged/missing closure cannot be hidden
merely by a directory name or file existence. The schema-only history helper
without ReportStore remains conservative.

Future UI can reuse these responses directly, retain the exact preparation inputs
and digest for record, display target blockers, and refresh on stale input. It
must not recreate closure selection logic or turn an HTTP 200 into provider
publication success. Tests use the actual in-memory HTTP Handler and real local
Store/compiler/runner paths with synthetic executors, not a live server or browser.
