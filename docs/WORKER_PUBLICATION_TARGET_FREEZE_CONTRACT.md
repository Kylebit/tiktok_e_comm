# Publication worker target scope before R1 category observation

Status: **BLOCKED for tasks whose `scope.shops` is empty**. This is a task authorization boundary, not a Product Center approval or permission to publish.

## Current source gap

- `WorkbenchEngine.create` accepts a publication Offer with empty `shops`; `bind_scope` can bind exact shops later, but stores only the scope. It does not store Product Center revision or source digest.
- Product Center `review.selected_sites` represents TikTok workbench sites. It is not the complete canonical publication target set.
- `build_release_dashboard(offer_id=...)` with no `publication_targets` uses `workbench_default`. Its projection includes derived or default targets such as TikTok MX/GB and Ozon RU. Those labels cannot be interpreted as the user's explicit target choice.
- R1 category request `requested_targets` comes from the caller. The domain validates product readiness and selection, but that request cannot establish the worker task's authority.

Therefore a worker must **not** fill an empty `scope.shops` from the dashboard default, the category request body, or inferred country/platform relationships. The existing `worker_category_admission.validate` rejection is the correct result until the binding below exists.

## Required binding contract

1. The Product Center owner durably records one explicit, canonical target selection for the exact Offer, with a state revision and an immutable selection reference. Its read-only projection must distinguish explicit user selection from defaults and return an ordered unique allowlisted target list. A derived target is not an explicit selection.
2. A server-owned worker path reads that record while holding the Product Center Offer state lock. It verifies the task's exact Offer, current revision, selection reference and canonical target list. Missing, stale, default or ambiguous evidence returns `BLOCKED` without changing the task.
3. The task ledger atomically binds `scope.shops` and a source record containing Offer, Product Center revision, selection reference, canonical target digest and exact release identity. `bind_scope` alone is insufficient for this provenance. An existing nonempty scope or conflicting binding is not silently replaced.
4. Immediately before private R1 options/capture dispatch, the server rechecks that Product Center revision and target digest still match the bound source, and that `requested_targets` is a subset of the bound shops. A changed source blocks a new request; an uncertain already-started domain request is reconciled by request ID and never replayed under a new target binding.
5. This binding authorizes only preapproval category observation. It never creates a Product Center approval receipt, frozen publication candidate or external publication authority.

Acceptance tests need two isolated stores and synthetic Product Center state: exact binding and restart, stale revision, changed selection, missing explicit source, concurrent bind, conflicting prebound task, out-of-scope R1, crash after task binding, and an unchanged human R1 route. No live provider or formal worker run is needed for those tests.
