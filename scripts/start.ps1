#requires -Version 7.0
[CmdletBinding()]
param([switch] $Monitoring)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $composeArguments = @('compose', '--project-name', 'ledgerflow', '--file', (Join-Path $projectRoot 'compose.yaml'))
    if ($Monitoring) { $composeArguments += @('--profile', 'monitoring') }
    $composeArguments += @('up', '--build', '--detach', '--wait', '--wait-timeout', '180')
    & docker @composeArguments
    if ($LASTEXITCODE -ne 0) { throw 'LedgerFlow did not start successfully. Inspect docker compose ps and docker compose logs.' }
    Write-Host 'LedgerFlow UI: http://localhost:5173'
    Write-Host 'LedgerFlow API: http://localhost:8000/docs'
    if ($Monitoring) { Write-Host 'Grafana: http://localhost:3000 (sandbox-admin / sandbox-observability-password)' }
} finally {
    Pop-Location
}
