Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

python -m pyinstaller --clean pyinstaller.spec

Write-Host "Built dist\desktop-agent.exe"
