$ErrorActionPreference = "Stop"

# Run this from the Codex desktop session that owns notify_on_output. Do not
# redirect stdout: AGENT_A2A_TICK_codex must be visible to that session.
$stage3Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDir = Split-Path -Parent (Split-Path -Parent $stage3Dir)
$python = Join-Path $repoDir "agent_comms\a2a_poc\venv\Scripts\python.exe"
$adapter = Join-Path $stage3Dir "codex_adapter.py"

Set-Location $repoDir
& $python -u $adapter --stream
exit $LASTEXITCODE
