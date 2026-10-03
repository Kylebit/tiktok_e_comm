param(
    [switch]$Browser,
    [switch]$Full,
    [string]$ScratchRoot = $env:ORBIT_REGRESSION_SCRATCH_ROOT,
    [string]$Python = $env:ORBIT_PYTHON_BIN
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = $Python
if (-not $python) { $python = Join-Path $repoRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $python)) {
    throw "Repository virtualenv Python was not found: $python"
}

$node = $env:ORBIT_NODE_BIN
if (-not $node) {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCommand) { $node = $nodeCommand.Source }
}
if (-not $node) {
    $bundledNode = Join-Path $env:USERPROFILE (
        ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    )
    if (Test-Path -LiteralPath $bundledNode) { $node = $bundledNode }
}
if (-not $node) {
    throw "Node.js was not found. Set ORBIT_NODE_BIN to the bundled node.exe."
}

$head = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -notmatch '^[0-9a-f]{40}$') {
    throw "Could not resolve an exact repository HEAD"
}

$environmentNames = @(
    "ORBIT_OPERATIONS_DATA_ROOT",
    "ORBIT_TEST_OPERATIONS_DATA_ROOT",
    "ORBIT_TEST_RUN_ID",
    "ORBIT_TEST_PHASE",
    "ORBIT_OPERATIONS_PROFILE",
    "ORBIT_REQUIRE_BROWSER_TESTS",
    "ORBIT_HIVE_SETTINGS",
    "ORBIT_NODE_BIN",
    "ORBIT_BROWSER_EXECUTABLE",
    "NODE_PATH",
    "PYTHONUTF8"
)
$priorEnvironment = @{}
foreach ($name in $environmentNames) {
    $item = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    $priorEnvironment[$name] = @{
        Exists = ($null -ne $item)
        Value = if ($null -ne $item) { $item.Value } else { $null }
    }
}

$settingsArgs = @(
    (Join-Path $PSScriptRoot "regression_run_envelope.py"),
    "settings", "--repo-root", $repoRoot
)
if ($priorEnvironment["ORBIT_HIVE_SETTINGS"].Exists) {
    $settingsArgs += @("--configured", [string]$env:ORBIT_HIVE_SETTINGS)
}
$settingsJson = & $python @settingsArgs
if ($LASTEXITCODE -ne 0) { throw "Could not resolve regression gate settings" }
$settings = $settingsJson | ConvertFrom-Json

$allocateArgs = @(
    (Join-Path $PSScriptRoot "regression_run_envelope.py"),
    "allocate", "--repo-root", $repoRoot, "--head", $head, "--owner-pid", $PID
)
if ($ScratchRoot) { $allocateArgs += @("--scratch-root", $ScratchRoot) }
$envelopeJson = & $python @allocateArgs
if ($LASTEXITCODE -ne 0) { throw "Could not allocate a regression run envelope" }
$envelope = $envelopeJson | ConvertFrom-Json
$runRoot = $envelope.path
$terminalStatus = "failed"

function Invoke-RegressionPhase {
    param(
        [string]$Name,
        [string[]]$PytestArguments,
        [switch]$RequireBrowser
    )
    $phase = $envelope.phases.$Name
    if ($null -eq $phase) { throw "Missing envelope phase: $Name" }
    $env:ORBIT_TEST_OPERATIONS_DATA_ROOT = $phase.operations_root
    $env:ORBIT_TEST_RUN_ID = $envelope.run_id
    $env:ORBIT_TEST_PHASE = $Name
    if ($RequireBrowser) { $env:ORBIT_REQUIRE_BROWSER_TESTS = "1" }
    else { Remove-Item -LiteralPath Env:ORBIT_REQUIRE_BROWSER_TESTS -ErrorAction SilentlyContinue }
    Write-Host "Orbit regression phase=$Name basetemp=$($phase.basetemp) operations=$($phase.operations_root)"
    & $python -m pytest @PytestArguments -q -p no:cacheprovider --basetemp $phase.basetemp
    if ($LASTEXITCODE -ne 0) { throw "$Name workbench regression gate failed" }
}

Write-Host "Orbit regression run_id=$($envelope.run_id) root=$runRoot head=$head"
$locationPushed = $false
try {
    if (-not $settings.inherited) { $env:ORBIT_HIVE_SETTINGS = $settings.path }
    $env:PYTHONUTF8 = "1"
    $env:ORBIT_NODE_BIN = $node
    if (-not $priorEnvironment["ORBIT_BROWSER_EXECUTABLE"].Exists) {
        $bundledBrowser = Join-Path $env:LOCALAPPDATA (
            "ms-playwright\chromium-1228\chrome-win64\chrome.exe"
        )
        if (Test-Path -LiteralPath $bundledBrowser) {
            $env:ORBIT_BROWSER_EXECUTABLE = $bundledBrowser
        }
    }
    if (-not $priorEnvironment["NODE_PATH"].Exists) {
        $bundledModules = Join-Path $env:USERPROFILE (
            ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
        )
        if (Test-Path -LiteralPath $bundledModules) { $env:NODE_PATH = $bundledModules }
    }
    Push-Location $repoRoot
    $locationPushed = $true
    & $node --check "web/static/ai_image_studio.js"
    if ($LASTEXITCODE -ne 0) { throw "AI studio JavaScript syntax failed" }
    & $node --check "web/static/product_workspace.js"
    if ($LASTEXITCODE -ne 0) { throw "Product workspace JavaScript syntax failed" }
    & $node --check "web/static/disabled_control_hints.js"
    if ($LASTEXITCODE -ne 0) { throw "Disabled-control hints syntax failed" }

    $focused = @(
        "tests/test_content_experience_recipe.py::test_source_decisions_and_identity_references_save_atomically_with_revision",
        "tests/test_product_approval_lock.py::test_negative_video_decision_is_fingerprint_bound_and_requires_reapproval",
        "tests/test_product_release_v1.py::test_release_plan_rejection_returns_fresh_dashboard_and_exact_blocker",
        "tests/test_ai_image_studio_recipe.py::test_legacy_source_video_review_is_preserved_in_ai_studio",
        "tests/test_release_ux_contract.py::test_disabled_release_checkboxes_expose_visible_reasons",
        "tests/test_release_ux_contract.py::test_release_plan_failure_refreshes_the_current_gate_and_explains_reapproval",
        "tests/test_miaoshou_client.py::MiaoshouClientTests::test_open_business_rejection_is_distinct_from_transport_unknown",
        "tests/test_miaoshou_variant_contract.py",
        "tests/test_regression_run_envelope.py"
    )
    Invoke-RegressionPhase -Name "focused" -PytestArguments $focused
    if ($Browser) {
        Invoke-RegressionPhase -Name "browser" -RequireBrowser -PytestArguments @(
            "tests/test_release_ux_contract.py::test_release_pages_in_real_chromium"
        )
    }
    if ($Full) { Invoke-RegressionPhase -Name "full" -PytestArguments @("tests") }
    $terminalStatus = "passed"
}
catch [System.Management.Automation.PipelineStoppedException] {
    $terminalStatus = "interrupted"
    throw
}
finally {
    try {
        if ($locationPushed) { Pop-Location }
    }
    finally {
        try {
            & $python "$PSScriptRoot\regression_run_envelope.py" finalize `
                --run-root $runRoot --status $terminalStatus --head $head | Out-Null
        }
        finally {
            foreach ($name in $environmentNames) {
                if ($priorEnvironment[$name].Exists) {
                    Set-Item -LiteralPath "Env:$name" -Value $priorEnvironment[$name].Value
                }
                else {
                    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
                }
            }
        }
    }
    Write-Host "Orbit regression evidence retained at $runRoot status=$terminalStatus"
}
