# Codex migration runbook

Status date: 2026-07-25

## Default operating mode

`tiktok_e_comm` is the only business source repository. Codex is the default
task and integration surface. A2A, EigenFlux, Cursor/WorkBuddy bridges, and
Feishu dispatch code remain available for later experiments, but none is a
required runtime dependency and none should auto-start.

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

The following login-start items were moved, not deleted:

`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\_Disabled_OrbitHive_2026-07-25`

- `FeishuWorkBuddyBridge.vbs`
- `OrbitHive Stage3 Autostart.bat`
- `OrbitHive-Cursor-FeishuWS.lnk`

The `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value
`EigenFluxStreamListener` was removed. Its preserved command is:

`C:\Users\Windows11\Desktop\Agent_PR\start_stream_listener.bat`

The disabled scheduled task `EigenFlux-Cursor-MsgFetch` remains disabled. The
business task `OrbitWeeklyProfitPush` remains enabled.

To restore a single startup bridge, move only its file from the disabled folder
back to the parent `Startup` folder. To restore the EigenFlux listener, recreate
the `EigenFluxStreamListener` string value with the preserved command above.
Do not restore all bridges together.

Stage3 can still be started manually from the repository:

```powershell
python agent_comms/stage3/start_stage3.py
```

Do not restart `agent_comms/serve_deliverables.py` until it is changed to serve
an explicit output allowlist instead of the repository tree.

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

Until these steps are complete, only one Codex thread may write the repository
at a time. Other domain threads may analyze and plan read-only.

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

