param(
    [string]$CodexRoot = ""
)

$ErrorActionPreference = "Stop"
$skillRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $CodexRoot) {
    if ($env:CODEX_HOME) {
        $CodexRoot = $env:CODEX_HOME
    } else {
        $CodexRoot = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex"
    }
}

$skillsRoot = Join-Path $CodexRoot "skills"
$destination = Join-Path $skillsRoot "manage-seaya-replenishment"
New-Item -ItemType Directory -Path $skillsRoot -Force | Out-Null

if (Test-Path -LiteralPath $destination) {
    $item = Get-Item -LiteralPath $destination -Force
    $targets = @($item.Target | ForEach-Object { [string]$_ })
    if ($item.LinkType -eq "Junction" -and $targets -contains $skillRoot) {
        Write-Output "Skill link is current: $destination"
        exit 0
    }
    throw "Refusing to replace existing skill path: $destination"
}

New-Item -ItemType Junction -Path $destination -Target $skillRoot | Out-Null
Write-Output "Installed linked skill: $destination"
