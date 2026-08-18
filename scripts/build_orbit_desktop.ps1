param(
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$EntryPoint = Join-Path $PSScriptRoot "start_orbit_desktop.py"
$DistPath = Join-Path $ProjectRoot "dist"
$WorkPath = Join-Path $ProjectRoot "build\pyinstaller"
$SpecPath = Join-Path $ProjectRoot "build"

if ($InstallDependencies) {
    python -m pip install --upgrade pyinstaller pywebview
}

python -c "import PyInstaller, webview" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Missing build dependencies. Run:" -ForegroundColor Yellow
    Write-Host "  .\scripts\build_orbit_desktop.ps1 -InstallDependencies"
    exit 2
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "OrbitDesktop" `
    --distpath $DistPath `
    --workpath $WorkPath `
    --specpath $SpecPath `
    --paths $ProjectRoot `
    --collect-all webview `
    --add-data "$ProjectRoot\web;web" `
    --add-data "$ProjectRoot\modules;modules" `
    --add-data "$ProjectRoot\scripts;scripts" `
    --add-data "$ProjectRoot\main.py;." `
    $EntryPoint

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Runtime databases and browser profiles are local state, never distributable
# application assets.  PyInstaller normally replaces the bundle, but older
# builds have retained `_internal\data`; remove it only after verifying that
# the resolved target is a child of this build's generated bundle.
$BundleInternal = Join-Path $DistPath "OrbitDesktop\_internal"
$PackagedData = Join-Path $BundleInternal "data"
if (Test-Path -LiteralPath $PackagedData) {
    $ResolvedInternal = (Resolve-Path -LiteralPath $BundleInternal).Path.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $ResolvedData = (Resolve-Path -LiteralPath $PackagedData).Path
    $ExpectedPrefix = $ResolvedInternal + [IO.Path]::DirectorySeparatorChar
    if (-not $ResolvedData.StartsWith(
        $ExpectedPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to remove packaged data outside bundle: $ResolvedData"
    }
    Remove-Item -LiteralPath $ResolvedData -Recurse -Force
    Write-Host "Removed runtime-only packaged data: $ResolvedData" -ForegroundColor Yellow
}

$ForbiddenRuntimeFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $DistPath "OrbitDesktop") -Recurse -File |
        Where-Object {
            $_.Name -in @("shop.db", "orbit_platform.db", "Cookies", "Login Data")
        }
)
if ($ForbiddenRuntimeFiles.Count -gt 0) {
    $Found = ($ForbiddenRuntimeFiles.FullName -join ", ")
    throw "Build contains runtime database or browser credentials: $Found"
}

Write-Host "Build complete: $DistPath\OrbitDesktop\OrbitDesktop.exe" -ForegroundColor Green
