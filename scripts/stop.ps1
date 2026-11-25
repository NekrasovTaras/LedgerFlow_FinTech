#requires -Version 7.0
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    # Preserve all data volumes. Never remove unrelated Docker resources.
    & docker compose --project-name ledgerflow --file (Join-Path $projectRoot 'compose.yaml') --profile monitoring down
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the LedgerFlow Compose project.' }
} finally {
    Pop-Location
}
