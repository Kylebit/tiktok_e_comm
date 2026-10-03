param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Output
)
$ErrorActionPreference = 'Stop'
if (-not [IO.Path]::IsPathRooted($Python) -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Select the explicit Python executable containing the locked desktop build dependencies.'
}
if (-not [IO.Path]::IsPathRooted($Output)) { throw 'Output must be an absolute new directory.' }
& $Python -B (Join-Path $PSScriptRoot 'build_orbit_desktop.py') --output $Output
exit $LASTEXITCODE
