# Post-COMMON task-home read-only links (isolated)

As of 2026-09-28 00:55 Asia/Shanghai. Delegated local work package based on
canonical `2d916fac` in `D:/OrbitHive/wt-post-common-task-links-20260928`.
No canonical, formal service, business database, provider, paid tool, or
publication write is included.

## Behavior

The task home now recognizes two distinct frozen publication action adapters
only when the task is at its original `release` step, waiting for the user,
has review mode `single-final-review/v1`, and its action and binding match the
task's Offer, SKU, ordered targets, scope, action ID, generation, and step.
The binding must have the exact adapter field set and nonempty source digests.

- `post-common-diagnostic/v1`: requires COMMON plan/payload/run/official
  readback digests plus source digest. The stored action URL must equal the
  exact original product-workspace diagnostic URL. Its link is labeled
  **“查看 COMMON 回读诊断（只读）”**.
- `single-final-review/v1`: requires the existing preview and COMMON token
  digests. The task home derives a fixed original product-workspace link from
  the frozen binding, labeled **“查看冻结候选（只读）”**.

An incomplete or changed binding hides the link in both the attention row and
task detail. Older `legacy` actions retain their prior same-origin route
handling. No link grants approval or execution authority. The browser does
not authenticate the source digest; the downstream GET must re-read the
original event, immutable task snapshot, and source documents before it shows
any candidate. This package adds no decision control.

## Source and integration boundary

The audited `9d6a971f`/`352f33ae` branch produces a
`post-common-diagnostic/v1` action with the exact URL and field set used here;
its GET still returns `EXECUTOR_PROJECTION_REQUIRED`,
`decision_available=false`, and `execution_authority=false`. The current
canonical `2d916fac` has neither that producer nor the task-bound GET or
post-COMMON original-page script. Consequently this UI contract is inert on
the current canonical task stream. The separate `41e6309f` fail-closed POST
bridge is also not included here. Manual combination and non-author review
must verify the exact producer/GET/page/action binding before any deployment.
The full executor request projection, R1 trusted images, COMMON technical
authority and budget, trusted final-review actor, and final decision receipt
remain upstream BLOCKED dependencies. There is no extra human review round.

## Red and green evidence

On unchanged `2d916fac`, synthetic real Chromium showed the task home
displaying a same-origin post-COMMON action URL changed to a different Offer;
the new contract expected no link and timed out after three seconds. On this
checkout, the same browser probe passes: exact diagnostic and frozen-candidate
links appear, URL/action/source/target/generation/review-mode drift hides
links, and no POST occurs. The existing `task_workspace.cjs` 16 synthetic
Chromium journeys also pass. Its screenshot/result files are isolated under
`D:/OrbitHive/pt-post-common-task-links-main-20260928`.

Commands from this checkout:

```cmd
set NODE_PATH=C:\Users\Windows11\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules&&set ORBIT_CHROMIUM_BIN=C:\Users\Windows11\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe&&C:\Users\Windows11\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe tests\browser\post_common_task_links.cjs
C:\Users\Windows11\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe tests\browser\task_workspace.cjs D:\OrbitHive\pt-post-common-task-links-main-20260928
```

These are local synthetic browser tests, not a release gate or proof of a
provider readback, live task, or completed final review.
