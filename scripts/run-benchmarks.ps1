param([string]$ApiUrl = 'http://127.0.0.1:8000')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$address = [Uri]$ApiUrl
if ($address.Host -notin @('localhost', '127.0.0.1', '::1') -or $address.Scheme -ne 'http') {
    throw 'Use only a local LedgerFlow HTTP sandbox.'
}
$runName = Get-Date -Format 'yyyyMMdd-HHmmss'
$evidence = Join-Path $projectRoot "artifacts\benchmarks\$runName"
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$previousOutput = $env:LEDGERFLOW_BENCH_OUTPUT
$resourceJob = $null
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
$uvExecutable = if ($uvCommand) { $uvCommand.Source } else { Join-Path $projectRoot '.tools\uv\bin\uv.exe' }
if (-not (Test-Path -LiteralPath $uvExecutable -PathType Leaf)) {
    throw 'Install uv or restore the project-local .tools/uv/bin/uv.exe first.'
}
try {
    $env:LEDGERFLOW_BENCH_OUTPUT = $evidence
    $cpu = Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors
    $system = Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
    $os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, FreePhysicalMemory
    $environment = [ordered]@{
        recorded_at = (Get-Date).ToUniversalTime().ToString('o')
        git_commit = (git -C $projectRoot rev-parse HEAD)
        git_worktree_dirty = [bool](git -C $projectRoot status --porcelain)
        workload_sha256 = (Get-FileHash -LiteralPath (Join-Path $projectRoot 'benchmarks/locustfile.py') -Algorithm SHA256).Hash.ToLowerInvariant()
        cpu = $cpu
        system = $system
        os = $os
        duration_seconds = 30
        stages_users = @(1, 5, 10)
        spawn_per_second = 1
        host = $ApiUrl
    }
    $environment | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $evidence 'environment.json') -Encoding utf8
    docker compose --project-directory $projectRoot ps --format json | Set-Content -LiteralPath (Join-Path $evidence 'compose-before.json') -Encoding utf8
    $projectContainers = @(docker compose --project-directory $projectRoot ps --quiet)
    if ($projectContainers.Count -gt 0) {
        docker stats --no-stream --format '{{json .}}' @projectContainers | Set-Content -LiteralPath (Join-Path $evidence 'resources-before.jsonl') -Encoding utf8
        $resourceJob = Start-Job -ArgumentList $evidence, ($projectContainers -join ',') -ScriptBlock {
            param($outputDirectory, $containerList)
            $containerIds = $containerList -split ','
            while ($true) {
                $sampleTime = (Get-Date).ToUniversalTime().ToString('o')
                docker stats --no-stream --format '{{json .}}' @containerIds | ForEach-Object {
                    $sample = $_ | ConvertFrom-Json
                    $sample | Add-Member -NotePropertyName recorded_at -NotePropertyValue $sampleTime
                    $sample | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $outputDirectory 'resources-during.jsonl') -Encoding utf8
                }
                Start-Sleep -Seconds 2
            }
        }
    }
    Push-Location (Join-Path $projectRoot 'backend')
    try {
        & $uvExecutable run locust --version | Set-Content -LiteralPath (Join-Path $evidence 'locust-version.txt') -Encoding utf8
        & $uvExecutable run locust -f ../benchmarks/locustfile.py --host $ApiUrl --headless --stop-timeout 20 --csv (Join-Path $evidence 'locust') --html (Join-Path $evidence 'report.html') --logfile (Join-Path $evidence 'locust.log')
        $runExit = $LASTEXITCODE
    } finally { Pop-Location }
    if ($projectContainers.Count -gt 0) {
        docker stats --no-stream --format '{{json .}}' @projectContainers | Set-Content -LiteralPath (Join-Path $evidence 'resources-after.jsonl') -Encoding utf8
    }
    docker compose --project-directory $projectRoot ps --format json | Set-Content -LiteralPath (Join-Path $evidence 'compose-after.json') -Encoding utf8
    Write-Output "Benchmark evidence: $evidence"
    if ($runExit -ne 0) { throw "Locust or financial verification failed (exit $runExit)." }
} finally {
    if ($resourceJob) {
        Stop-Job -Job $resourceJob
        Receive-Job -Job $resourceJob -ErrorAction Continue | Out-Null
        Remove-Job -Job $resourceJob
    }
    $env:LEDGERFLOW_BENCH_OUTPUT = $previousOutput
}
