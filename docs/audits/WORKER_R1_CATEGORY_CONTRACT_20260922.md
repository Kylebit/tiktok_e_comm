# Worker R1 category options/capture boundary

The current worker does **not** receive an HTTP write route. `worker_category_admission.validate` is a server-side validation component only; `operations_launch.maintenance_handler` continues to reject both category POST routes. No approval, freeze, image decision or publication route is eligible.

| Operation | Existing domain request | Current protection | Missing for worker HTTP dispatch |
| --- | --- | --- | --- |
| `options` | `round1-category-options-request/v1`, offer/context/account/targets and `request_id` | Domain rechecks current Product Center and official account context; release store deduplicates exact request ID and records unknown results | Authenticated out-of-band Orbit task ID, exact release, worker identity and live lease; trusted route must validate before domain code is called |
| `capture` | `round1-category-capture-request/v3` plus exact options reference/digest and selected category/attributes | Domain resolves selection against recorded options, rechecks context, deduplicates request ID | Same task/lease binding, plus a record that options and capture belong to the same task and frozen facts attempt |

`worker_category_admission.request_id(task_id, action, body)` derives a stable `wkr-` key from the task and the exact request body, excluding `request_id`. `validate` checks the current task ledger's running lease, worker ID, exact release, `publication` template, `facts` step, offer match, supported schema and deterministic request ID. These checks use a temporary SQLite ledger in tests. The body cannot grant approval: extra fields such as `approval_receipt` fail the exact schema check. R1 options/capture are preapproval evidence collection, so no human approval is required to read official category facts.

Before enabling a worker route, the server must provide task ID, worker ID and lease token from a trusted worker channel, never from the browser JSON body; atomically bind the domain request ID/attempt to the task before making any provider call; verify the options receipt belongs to that same task before capture; keep the domain's `UNKNOWN`/status reconciliation and never mint a changed request ID merely to retry; and test the entire handler with a lowest-layer fake official provider. The present component does not make the validation-to-dispatch window atomic, so it is intentionally not wired into HTTP. The original human review page retains sole approval authority.

Code references: `shared_platform/operations_service.py` sets `ControlledAgentBridge.service_url`; `shared_platform/operations_runtime.py` directs the facts agent to category context/options/capture; `modules/products/server.py` implements `_round1_category_initial_request` and the HTTP routes; `shared_platform/round1_category_observations.py` defines the request schemas. `tests/test_worker_category_admission.py` covers the task-side gate. Existing domain idempotency and source recheck cases are in `tests/test_round1_initial_options.py`. The project `.venv` Python 3.12.8 successfully ran the combined domain and intent set (71 passed); an unrelated Python 3.9 `pytest` command cannot collect this module's 3.10+ type syntax.

## Offline two-ledger prototype

`shared_platform/worker_category_intents.py` now persists one immutable request per task/action in the task DB. Only insertion returns `dispatch_once=True`; a repeat, restart, or an absent domain row cannot grant a second dispatch. A read-only domain reconciliation checks the exact request payload digest, purpose, offer, options result context, and capture observation reference. Capture reservation requires this task's successfully reconciled options reference/digest and unchanged context. An `UNKNOWN` result may become `SUCCEEDED` after later domain readback, without another provider call. `tests/test_worker_category_intents.py` exercises crash-before-domain, crash-after-domain, duplicate, nonterminal/unknown, cross-task and context-conflict cases with temporary SQLite and a fake domain.

This remains **offline only**. `ReleaseStore.begin_category_capture(..., worker_origin=...)` now atomically writes a server-private `WorkerCategoryOrigin` sidecar with a new domain request, and `category_worker_origin` reads it without creating tables. An existing identical request with no sidecar cannot be taken over by a worker; a different task, release, purpose or body digest cannot rebind a worker row. The task-ledger reconciler requires this exact origin before accepting domain success. The ordinary human path still omits `worker_origin` and keeps its previous behavior. `tests/test_worker_category_origin.py` covers restart, duplicate, old row, UNKNOWN, forced sidecar insertion failure and concurrent first begin in temporary SQLite.

The offline bridge below constructs the origin from a validated live task/lease and retains the task-side one-shot dispatch result. Before activation, connect it to a trusted worker-only transport and test that transport across process kill, timeout and concurrent requests with a fake official provider. Keep maintenance POST denial until those end-to-end proofs pass. If task intent commits but the process dies before the domain call, it intentionally strands the intent as `UNKNOWN`; recovery requires an explicit operator decision or a new task, never silent replay.

## Offline trusted bridge acceptance

`shared_platform/worker_category_bridge.py` now composes the live task lease gate, one-shot task intent, private `WorkerCategoryOrigin`, existing R1 domain function and readback. The human HTTP handler calls the R1 function without this private parameter. `modules/products/server.py` also checks origin in its early existing-request return branch, so a legacy human row with the same ID cannot yield a worker success. No route invokes this bridge and the formal web-only maintenance handler still denies category POST.

`tests/test_worker_category_bridge.py` uses the real domain function and isolated R1 fixtures with a fake official GET provider. It verifies options→capture, exact digest equality, duplicate and concurrent calls, provider timeout, process exit after official reads, old human row conflict, JSON origin injection rejection, and the actual loopback maintenance HTTP denial. A concurrent reconciliation race was found and fixed: an older `UNKNOWN` read can no longer overwrite a newer task-ledger `SUCCEEDED`/`FAILED` result. The remaining activation gate is a trusted worker-only transport and a full process-level test of that transport; neither a browser-supplied task ID nor the current web-only listener is authority to invoke this bridge.

## Private process transport prototype

`shared_platform/worker_category_pipe.py` now binds a `WorkerCategoryBridge` to server-held task/worker/lease identity and accepts only bounded JSON-byte frames through an inherited `multiprocessing.Pipe` handle. Worker messages may request `options`, `capture` or read-only `status`; they cannot supply a task ID, lease, approval or review action. This module creates no listener or worker process by itself. The formal `scripts/operations_worker_entry.py --run` remains disabled, and the web-only maintenance handler remains unchanged.

`tests/test_worker_category_pipe.py` starts separate synthetic worker/service processes with isolated task and release SQLite files. It covers startup, duplicate and concurrent child requests, expired lease, fake official timeout, and `os._exit` of the service after a persisted domain begin and fake official read. Restart returns `UNKNOWN` without a second official read. This proves the pipe protocol and offline recovery behavior; it does **not** activate a production worker, establish deployment ownership of the pipe handle, or replace the remaining full worker runtime acceptance gate.

`shared_platform/worker_category_pipe_lifecycle.py` supplies a disabled-by-default offline supervisor for one child process. It requires a marked isolation root containing both fixture databases, keeps task/worker/lease identity only in the parent, closes the parent's copy of the child pipe immediately after spawn, and never restarts a child automatically. Stop terminates the fixture child and waits for the service loop; if an in-flight service call does not finish within the timeout, it reports `stop_pending` instead of claiming shutdown. Its receipt counts only responses actually sent, never infers a domain outcome from the child's exit code. `tests/test_worker_category_pipe_lifecycle.py` verifies child crash/EOF, explicit stop, restart through a new supervisor, identity omission and concurrent children against temporary stores. The formal `--run` entry remains rejected.

## Facts agent evidence handoff

`shared_platform/worker_category_facts_transport.py` verifies a completed same-task capture against the live lease, pinned release, immutable domain origin and observation readback. Candidate `ControlledAgentBridge.execute_facts` accepts only this parent-held read-only evidence; a missing/UNKNOWN capture blocks before the Codex child starts. The parent writes one evidence file with a SHA-256 and checks it again after the child returns. `operations_service.py` no longer suggests its `server.server_port` as a category write channel. There is still no parent-owned first stage to collect the agent's eventual category choice and execute options/capture, and `get_runtime` intentionally does not inject this transport. This candidate cannot activate a worker.

Target admission now requires a nonempty `task.scope.shops` and constrains `requested_targets` to that exact scope before any domain/provider call. An empty scope remains a legal new-product task state but is blocked for private category reads until a separate server-owned Product Center target source is frozen. The original domain review confirms Product Center selection, not task authorization. No default target expansion is permitted.

## 2026-09-23 isolated agent-bridge continuation

`ControlledAgentBridge.execute_facts_offline` now accepts a dependency-injected
`ParentR1CategoryFlow`, the exact live lease, and Product Center context supplied
by its trusted parent caller. It runs options through the existing one-shot
intent/domain bridge, gives only a verified options projection to a read-only
Codex child for one structured selection, validates that selection in the
parent, executes capture through the same bridge, and hands the verified
capture to the existing facts agent. Neither child receives the lease,
domain handle, private pipe, or a web server URL. The task database now reserves
separate immutable `choice` and `facts` agent attempts under task, facts step,
release, worker, lease digest and exact source reference before either child
starts. A changed output directory or new lease cannot grant another launch;
an unresolved attempt remains `UNKNOWN` until independently reconciled. The
offline entry places local artifacts under the verified task ID even when two
tasks receive the same output root; the local choice marker is retained only
as an artifact, not as replay authority.
The entry also checks the actual Git HEAD and runtime manifest before options,
again after the choice child, and before facts handoff. A mismatch blocks.
There is no silent attempt reset or new session when output is ambiguous;
options/capture requests retain their deterministic IDs and never replay an
ambiguous official GET.

The method is an **offline component**, not a registered publication adapter
or worker entry. `operations_service.get_runtime` does not inject the parent
flow or resolve Product Center context for it. The normal facts adapter still
fails closed without a preverified capture. The worker entry's `--run` still
returns `WORKER_ACTIVATION_NOT_AVAILABLE`; the web-only
maintenance handler still denies category POST.

`tests/test_worker_category_offline_agent_bridge.py` uses separate synthetic
task and domain SQLite stores, a fake official GET provider and a fake Codex
child. It covers full options/choice/capture/facts handoff, repeated calls,
human-origin request-ID conflict, official timeout/UNKNOWN, forged and expired
lease, release/source-manifest drift, out-of-scope shop, task-scoped attempt
isolation, forced attempt-insert failure, and separate service processes killed
after options/capture GETs or either agent stage. Restart across a different
output directory performs no additional official GET or child launch. The fake
choice child selects the first synthetic option solely to exercise transport;
category membership does not establish a correct business choice. Current
Product Center facts must justify the selected category and attributes before
any real workflow can use the observation.

Activation still requires a server-owned current Product Center context
resolver bound to the exact task and release, a worker-only lifecycle and
trusted process ownership, durable reconciliation of an interrupted choice
agent session, complete R1 review admission, and separate R2/COMMON/final
approval/target readback gates. None is supplied by this offline method.
