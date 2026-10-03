# Codex migration runbook

Status date: 2026-07-25

> Historical migration record, not current startup or instruction policy. Paths,
> ports, ACL observations, scheduled-task states and the five-thread plan below
> describe that date only. Do not rename Git directories, restore startup items,
> authenticate or start services merely to take over a task. Start with current
> [AGENTS](../AGENTS.md), [ownership/authority](THREAD_OPERATING_MODEL.md) and
> [read-only hand-off scenarios](AGENT_HANDOFF.md); the current work order and
> verified Git identity supersede these historical locations and assignments.

## Default operating mode

`tiktok_e_comm` was the business source repository observed at that time.
The A2A, EigenFlux, Cursor/WorkBuddy bridge and external-message dispatch
experiments described below are retired and their executable source is absent
from the current tree. This record does not authorize restoring or starting
them.

The canonical local business services are:

| Port | Service |
| --- | --- |
| 8765 | Main operations console |
| 8766 | New-product/Treasury console |
| 8767 | Russia/Ozon console |

Duplicate consoles on 8799 and 8866 were stopped during migration. The legacy
8790 repository-wide static server was also stopped because it exposed more of
the checkout than a deliverables service should.

## Frozen legacy runtime

The following login-start items were observed as disabled during the historical
migration. They are not current platform components and must not be restored
from this document:

`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\_Disabled_OrbitHive_2026-07-25`

- `FeishuWorkBuddyBridge.vbs`
- `OrbitHive Stage3 Autostart.bat`
- `OrbitHive-Cursor-FeishuWS.lnk`

The `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value
`EigenFluxStreamListener` was removed. Its preserved command is:

`C:\Users\Windows11\Desktop\Agent_PR\start_stream_listener.bat`

The disabled scheduled task `EigenFlux-Cursor-MsgFetch` remains disabled. The
business task `OrbitWeeklyProfitPush` remains enabled.

`OrbitWeeklyProfitPush` runs every Monday at 09:00 and now calls
`scripts/weekly_profit_push.bat`. The launcher builds the previous complete
Monday-to-Sunday profit digest and writes it only to the local Orbit report
store/inbox. It does not call Feishu, pull marketplace APIs, or write an
`outputs` report. A run with missing quantities, costs, advertising spend, or
conflicting catalog facts is stored as `needs_review`, never as a successful
confirmed-profit report.

Safe manual rehearsal:

```powershell
.\.venv\Scripts\python.exe -m shared_platform.weekly_profit_runner
```

The command above is dry-run. Add `--persist-local` only when the result should
become an Orbit inbox item.

No restart or restoration procedure is retained: the old bridge and repository
wide deliverables server were removed from the current source tree.

## Git and Codex project safety

The valid repository is:

`C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm`

The parent `Agent_PR` directory still contains a separate Git directory with no
commit. It must never be used for business commits or worktrees. Its ACL cannot
be changed by the current Codex process.

Before enabling five simultaneous code-writing threads:

1. Close tools using `Agent_PR`.
2. As the Windows owner, rename `Agent_PR\.git` to
   `Agent_PR\.git.disabled-root-20260725`. Keep it until the migration is
   verified; do not delete it immediately.
3. Save `Agent_PR\tiktok_e_comm` itself as the Codex project.
4. Confirm `git rev-parse --show-toplevel` returns the `tiktok_e_comm` path.
5. Use a separate Git worktree for every code-writing domain thread.

The parent Git directory could not be renamed from the active Codex project
because Windows reported it as in use. This does not block native worktrees
created directly from the valid `tiktok_e_comm` repository. As an interim
mode, every domain thread must receive one explicit worktree path, verify that
`git rev-parse --show-toplevel` returns that exact path, and refuse to edit the
parent checkout or another domain worktree.

When a thread cannot be pinned to an isolated worktree, only one Codex thread
may write the repository at a time. Other domain threads may analyze and plan
read-only.

## Five-thread delivery rule

Each domain thread owns only its `domains/<domain>` package, its documented
legacy adapters, and its tests. Changes to `shared_platform`, database
migrations, `main.py`, or `modules/products/server.py` require CEO/integrator
review. Real channel writes always require an explicit user approval.

Every hand-off must include:

- outcome and affected domain;
- changed files and migration/contract impact;
- tests run and results;
- real external writes performed, if any;
- commit hash and remaining risks.
