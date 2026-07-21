# Orbit Codex SSE Adapter Runbook

## Purpose

The Codex adapter receives A2A tasks over SSE, writes the local inbox, ACKs the
Orchestrator, and prints `AGENT_A2A_TICK_codex`. It is not an executor.

The wake line must be visible to the live Codex desktop session. Starting the
adapter as a detached child with stdout redirected to a log cannot wake that
session.

## Required desktop-session setup

Configure the Codex session output hook to match:

```text
^AGENT_A2A_TICK_codex
```

In the terminal whose output the hook watches, run:

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\agent_comms\stage3\run_codex_adapter.ps1
```

Do not redirect that command's stdout. When an A2A task arrives, the session
reads the JSON `path`, performs the requested work, reports milestones using
`codex_adapter.py --report`, and finishes with `--complete`.

## Ownership and recovery

- `start_stage3.py` starts shared Stage3 services only. It must not own the
  Codex adapter because it redirects child output to log files.
- `codex_adapter.py --stream` holds a Windows file lock. A second stream process
  exits immediately, so accidental duplicate launchers do not compete for SSE.
- Windows may show a venv launcher parent and a Python child with the same command
  line. That is one adapter launch, not two independent subscribers.
- The fallback heartbeat remains active: health check, then
  `--scan-local-inbox`; use `--poll-once` only if recovery requires a one-time
  server drain.

## Acceptance test

1. Start the adapter in the notify-enabled terminal.
2. Have Orchestrator dispatch a harmless test task to `Orbit Codex`.
3. Confirm the session wakes on `AGENT_A2A_TICK_codex`, ACK is visible in the
   Orchestrator, and the local inbox contains the task.
4. Complete the test only after the execution session has reported its real
   result.
