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

## External execution regression layers

Channel fixes must additionally cover all four execution boundaries:

1. **Pre-dispatch** - every immutable field is canonical, complete, and
   exactly mappable before the first external write. Malformed or unmapped
   values must produce zero write calls.
2. **Dispatch accounting** - business rejection, transport ambiguity,
   accepted write, and accepted-but-unverified readback are distinct durable
   outcomes with truthful write counts and classes.
3. **Exact readback** - a successful response is insufficient. Stable
   identities and the complete canonical field manifest must match; "looks
   valid" checks do not count as verification.
4. **Replay isolation** - any accepted or ambiguous write terminalizes
   automatic execution. Reload, resume, and repeated clicks must perform zero
   additional dispatches.

The focused gate includes a captured Miaoshou variant contract with
deterministic source-label canonicalization, malformed/drift fault injection,
exact post-save readback, and business-rejection versus transport-unknown
accounting. Every new channel execution bug must add a live-shaped fixture and
both a pre-write and a post-dispatch fault case.
