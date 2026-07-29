# Workbench regression gate

Recent failures showed that a green unit test is not sufficient for a stateful
workflow. A fix is complete only after the affected user journey passes all
applicable layers:

1. **Persistence contract** — the submitted value, including negative values
   such as `remove`, `none`, `false`, and an empty selection, survives a
   server-side save and readback.
2. **State-transition contract** — the saved value produces the intended
   approval fingerprint and invalidates only the approvals that actually bind
   that fact.
3. **HTTP/control-plane contract** — a stale or blocked action returns the
   latest redacted dashboard, exact blocker, zero external writes, and zero
   unintended durable mutation.
4. **Browser contract** — after reload, the visible control has the persisted
   value; disabled controls expose a human-readable reason; the next permitted
   action is discoverable.

Every workflow bug must add regression coverage in at least two adjacent
layers. Bugs that strand the user between steps must include layers 3 and 4.
Do not close a bug from a static-string assertion alone.

Run the fast mandatory gate:

```powershell
.\scripts\run_workbench_regression_gate.ps1
```

Before integration, also run the real Chromium journey:

```powershell
.\scripts\run_workbench_regression_gate.ps1 -Browser
```

Before a release candidate, run all repository tests as well:

```powershell
.\scripts\run_workbench_regression_gate.ps1 -Browser -Full
```

The focused gate currently binds the video `keep → remove` journey across
request persistence, product-approval fingerprint transition, ReleasePlan
rejection/readback, disabled-control explanation, and UI copy. New recent
regressions should be appended to this gate rather than creating an isolated
one-off command.
