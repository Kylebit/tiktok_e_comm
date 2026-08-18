param(
    [switch]$Browser,
    [switch]$Full
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Repository virtualenv Python was not found: $python"
}

$node = $env:ORBIT_NODE_BIN
if (-not $node) {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCommand) {
        $node = $nodeCommand.Source
    }
}
if (-not $node) {
    $bundledNode = Join-Path $env:USERPROFILE (
        ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    )
    if (Test-Path -LiteralPath $bundledNode) {
        $node = $bundledNode
    }
}
if (-not $node) {
    throw "Node.js was not found. Set ORBIT_NODE_BIN to the bundled node.exe."
}

Push-Location $repoRoot
try {
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
        "tests/test_miaoshou_variant_contract.py"
    )
    & $python -m pytest @focused -q -p no:cacheprovider `
        --basetemp ".pytest-workbench-regression-focused"
    if ($LASTEXITCODE -ne 0) { throw "Focused workbench regression gate failed" }

    if ($Browser) {
        $priorBrowserRequirement = $env:ORBIT_REQUIRE_BROWSER_TESTS
        try {
            $env:ORBIT_REQUIRE_BROWSER_TESTS = "1"
            & $python -m pytest `
                "tests/test_release_ux_contract.py::test_release_pages_in_real_chromium" `
                -q -p no:cacheprovider `
                --basetemp ".pytest-workbench-regression-browser"
            if ($LASTEXITCODE -ne 0) {
                throw "Real-browser workbench regression gate failed"
            }
        }
        finally {
            $env:ORBIT_REQUIRE_BROWSER_TESTS = $priorBrowserRequirement
        }
    }

    if ($Full) {
        & $python -m pytest tests -q -p no:cacheprovider `
            --basetemp ".pytest-workbench-regression-full"
        if ($LASTEXITCODE -ne 0) { throw "Full test suite failed" }
    }
}
finally {
    Pop-Location
}
