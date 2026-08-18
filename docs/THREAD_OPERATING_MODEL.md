# Codex thread operating model

This is the normative operating model for the CEO, shared platform, and the
five business-domain threads. A thread title or `active` indicator is not a
business status. Work Order messages, commits, test evidence, integration
receipts, and explicit acceptance are the sources of truth.

## Fixed execution lanes

| Lane | Responsibility |
| --- | --- |
| CEO/integrator | Priority, decomposition, Work Orders, review, integration, release, and escalation to Kyle |
| `00` Shared platform | Cross-domain contracts, registry, approvals/audit/jobs/health, shared shell, governance, and independent browser acceptance |
| `01` Product operations | Product/SKU master data, intake, approval, and `ApprovedProductPackage` |
| `02` Content operations | Copy, image/video assets, review evidence, and `ContentPackage` |
| `03` Channel operations | Marketplace adapters, listings, price/promotion/deactivation, and all authorized external-platform writes |
| `04` Supply-chain operations | Suppliers, warehouse/inventory facts, receiving, replenishment, and `InventorySnapshot` |
| `05` Data operations | Costs, settlement, profit, ads, analytics, and `FinancialFact` |

The CEO is a control and integration lane, not the default business
implementer. A task spanning two or more business domains must be split into
Work Orders for every owning fixed lane. The CEO may not keep the whole
implementation merely because it is convenient to edit one file.

Fixed `00`-`05` lanes take priority over temporary sub-agents. A temporary
agent may perform bounded research, test generation, or an isolated helper
task, but it does not become the domain owner, approve its own result, or
replace the required fixed-lane acknowledgement and delivery. The fixed lane
remains accountable for scope, commit, tests, and hand-off.

## Communication path

Kyle gives outcomes, priorities, acceptance criteria, and business decisions
to the CEO. Fixed lanes return technical questions, progress, blockers, and
delivery evidence to the CEO under the Work Order ID; the CEO decides what
requires Kyle's decision. Kyle may contact a specialist lane directly for
discovery or visual direction, but that does not bypass the CEO's integration
and release responsibility.

## Host approval is not business approval

Business authority and the host/tool approval mechanism are independent:

- Business authority answers whether the Work Order permits a repository,
  browser, database, or external-platform action.
- Host approval answers whether one particular tool invocation may leave its
  sandbox or use a protected capability. It does not grant business authority,
  and existing business authority does not guarantee that an escalated command
  will be accepted by the host.

Fixed `00`-`05` lanes default to `host_approval_policy=never` and
`no_escalation=true` for local engineering, local tests, and read-only browser
or API acceptance. They must not request host escalation for ordinary shell,
pytest, worktree, Git, or read-only validation. If a command would request
approval, the executor abandons or cancels that invocation and uses a
non-escalated, non-destructive equivalent. Examples include selecting another
writable `basetemp`, inspecting an exact path instead of recursively cleaning
it, and staging an explicit file list instead of cleaning a worktree.

A host `waitingOnApproval` signal is command state, not Work Order `BLOCKED`.
The executor records it only when useful for diagnosis, abandons that command,
and continues every safely achievable acceptance and delivery step. It must
not wait for Kyle merely to satisfy tool policy or cosmetic workspace
cleanliness.

Escalation to Kyle is reserved for external business authorization or a
high-risk action that is both necessary and has no safe, non-escalated
substitute. The report must name the exact action, target, risk, attempted safe
alternatives, and the decision Kyle must make. This exception does not weaken
the rule that external-platform writes belong only to `03`.

## Limited CEO coding exceptions

The CEO may write code only when the change is:

1. a mechanical integration resolution that does not choose or change domain
   business semantics;
2. release wiring or repository metadata with no owning domain implementation;
3. an urgent P0 restoration explicitly authorized by Kyle when the owning lane
   cannot respond.

The CEO records the exception and reason in the Work Order. If the change
touches domain behavior, the owning fixed lane must independently review it
before acceptance. Shared-platform feature work still belongs to `00`; a broad
server file is not automatically CEO-owned.

## Work Order lifecycle

The minimum fields and message format are defined in
[`pm/DISPATCH_CONVENTION.md`](pm/DISPATCH_CONVENTION.md). The observable states
are:

- `DRAFT`: CEO is shaping scope; no executor authority.
- `DISPATCHED`: the Work Order was sent to a named fixed lane.
- `ACKED`: that lane verified the Work Order, Git top-level, base, worktree,
  branch, and initial status.
- `RUNNING`: the executor has started; this is evidenced by a timestamped
  update, not inferred from a title.
- `BLOCKED`: a named business decision or dependency prevents safe progress
  after safe non-escalated alternatives are exhausted. A tool command in
  `waitingOnApproval` is not by itself a business blocker.
- `DELIVERED`: the fixed lane returned a commit and required evidence.
- `REVIEWED`: CEO accepted the review or returned specific changes.
- `INTEGRATED`: CEO recorded source-to-main commit mapping and integration
  tests.
- `ACCEPTED`: Kyle or the explicitly authorized release decision accepted the
  outcome.
- `CANCELLED`: CEO or Kyle ended the Work Order without integration.

`DELIVERED` is not `INTEGRATED`, and `INTEGRATED` is not `ACCEPTED`.
Cherry-picked work must record both the source and main commit because hashes
will differ.

## UI implementation and independent acceptance

The owning domain implements a formal UI change and its focused tests. `00`
then performs independent acceptance in a real browser; source-string or fake
DOM assertions do not satisfy this gate. At minimum, acceptance covers the
supported desktop and narrow viewports, computed visibility, horizontal
overflow, primary controls, asynchronous feedback, browser console/page
errors, and explicit `unknown`/`unavailable` states.

`00` reports findings to the owning lane and does not silently implement the
same domain change during the acceptance pass. The CEO integrates only after
the domain tests, independent browser evidence, and complete regression suite
pass. Browser acceptance must use isolated local fixtures and block external
platform traffic.

## External writes and single writer

All writes to an external marketplace, store, ERP, warehouse SaaS, or other
business platform are executed only by `03` as a single writer under CEO
supervision. Other lanes produce approved contracts or dry-run plans; they do
not call the external mutation. Temporary agents and the CEO never substitute
for `03` on an external write.

An external-write Work Order must name the target, exact mutation, approval
evidence, dry-run/preflight result, rollback or recovery plan, and audit
artifact. Missing authority means read-only. Repository commits and an
explicitly authorized Git push are governed separately and are not business
platform writes.

## Git isolation

Every code-writing lane uses an independent worktree and branch created from
the Work Order base. Before editing, it verifies and reports:

```text
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short --branch
```

There is one writer per worktree and branch. Two agents must not concurrently
edit the same checkout, branch, or overlapping files. A temporary helper is
read-only unless it receives another isolated worktree; the fixed lane still
owns the result. Never reset, clean, overwrite, or absorb pre-existing user
changes to make a worktree usable.

Tests that need temporary files use a Work Order-specific `basetemp` inside
the executor's independent worktree. Test artifacts are not delivery files
and do not need to be deleted to make a commit. Leave or ignore them, verify
the intended diff, and stage only the explicit delivery paths. Do not run a
recursive delete, `git clean`, or an escalated cleanup merely to obtain a
cosmetically clean status. A cleanup that is independently required must have
an exact verified target and remain within the worktree, but cleanup is never
a prerequisite for focused staging or delivery.

Durable code and governance changes require a focused commit before delivery.
Read-only audits and probes do not create empty commits. Push is forbidden
unless Kyle or the formal release process explicitly authorizes it.

## Review and hand-off

The executor sends progress, blockers, and delivery back to the CEO under the
same Work Order ID. A completed delivery includes:

- outcome and owning lane;
- changed files and contract or migration impact;
- test and browser evidence;
- external writes, stated explicitly even when zero;
- source branch and commit;
- remaining risks and decisions.

The CEO sends review findings back to the fixed lane, integrates only tested
commits, and records an Integration Receipt with source-to-main mapping,
integration method, regression results, external-write evidence, and final
owner. Optional notification systems may mirror these records but are not a
required dependency or source of truth.
