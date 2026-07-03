param(
  [int]$Port = 8767
)

$Root = Split-Path -Parent $PSScriptRoot
python "$PSScriptRoot\start_rus_server.py" $Port
