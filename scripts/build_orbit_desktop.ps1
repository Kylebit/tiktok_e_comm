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
Write-Host "Build complete: $DistPath\OrbitDesktop\OrbitDesktop.exe" -ForegroundColor Green
