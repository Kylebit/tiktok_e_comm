param(
  [int]$Port = 8766
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
python ".\scripts\start_new_product_server.py" $Port
