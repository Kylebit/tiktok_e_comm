# Codex thread operating model

## Default communication path

Kyle talks to the CEO/integrator thread about outcomes, priorities, acceptance
criteria, and business decisions. The CEO turns that input into a bounded task
contract and sends execution to the owning domain thread.

Kyle may talk directly to a domain thread for visual taste exploration or
specialist discovery, but step-by-step implementation debugging is not the
default operating mode.

## Thread roles

| Thread | Responsibility |
| --- | --- |
| CEO/integrator | Portfolio priority, task contracts, shared contracts, review, integration, release decision |
| Product operations | Product/SKU master data, intake, approval package |
| Content operations | Copy, image/video assets, review evidence, content package |
| Channel operations | Marketplace adapters and listing lifecycle |
| Supply-chain operations | Suppliers, warehouse inventory, receiving, replenishment |
| Data operations | Costs, settlements, profit, ads, analytics |
| Temporary QA | Read-only independent review of a release candidate; reports findings but does not fix them |

## Task contract

Every delegated task states:

1. business outcome;
2. inputs and owned files;
3. required outputs;
4. hard constraints and prohibited actions;
5. acceptance criteria and tests;
6. external-write authority;
7. iteration/time budget;
8. hand-off report format.

An executor asks Kyle only when a missing answer changes the business outcome.
Technical implementation questions go to the CEO/integrator.

## Human review gates

For subjective content work, use at most two normal human gates:

1. direction gate: Kyle selects among a small batch of materially different
   candidates;
2. final gate: Kyle approves the evidence-backed final result.

Technical failures are returned to the domain thread without consuming a Kyle
review cycle. Real marketplace, warehouse, or financial mutations always use a
separate explicit approval gate.

## Testing and review

The domain thread owns focused tests and self-review. The CEO/integrator owns
the complete regression suite and integration review. A temporary QA thread is
created only for a large release, cross-domain change, high regression risk, or
independent visual evaluation.

The QA thread is read-only. Findings go back to the original domain thread so
testing does not become a second implementation path.

## Git isolation

Each code-writing domain thread uses its own worktree and branch. Before any
edit, it must verify its exact Git top-level path and clean/expected status.
Threads never edit `main.py`, `modules/products/server.py`, `shared_platform`,
or database migrations without an explicit CEO/integrator task contract.

Every completed hand-off includes:

- outcome and affected domain;
- changed files and contract/migration impact;
- tests run and results;
- external writes performed, if any;
- commit hash;
- remaining risks and decisions needed.
