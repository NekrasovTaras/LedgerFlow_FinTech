#requires -Version 7.0
[CmdletBinding()]
param(
    [string] $ReportPath = 'artifacts/preflight.json'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$expectedRepository = 'NekrasovTaras/LedgerFlow_FinTech'
$checks = [System.Collections.Generic.List[object]]::new()

function Add-Check {
    param([string] $Name, [bool] $Passed, [bool] $Required, [string] $Detail)
    $checks.Add([pscustomobject]@{ name = $Name; passed = $Passed; required = $Required; detail = $Detail })
    $label = if ($Passed) { 'PASS' } elseif ($Required) { 'FAIL' } else { 'WARN' }
    Write-Host ('[{0}] {1}: {2}' -f $label, $Name, $Detail)
}

function Find-Tool {
    param([string[]] $Names, [string] $Fallback = '')
    foreach ($toolName in $Names) {
        $command = Get-Command $toolName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return $command.Source }
    }
    if ($Fallback -and (Test-Path -LiteralPath $Fallback -PathType Leaf)) { return $Fallback }
    return $null
}

function Invoke-Probe {
    param([string] $Executable, [string[]] $Arguments = @(), [int] $TimeoutSeconds = 30)
    # cmd wrappers require PowerShell's native invocation; command arguments are fixed by this script.
    if ([System.IO.Path]::GetExtension($Executable) -eq '.cmd') {
        Push-Location -LiteralPath $projectRoot
        try {
            $captured = & $Executable @Arguments 2>&1
            return [pscustomobject]@{ code = $LASTEXITCODE; text = ($captured | Out-String).Trim() }
        } finally {
            Pop-Location
        }
    }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $startInfo.ArgumentList.Add($argument) }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        [void] $process.Start()
        $outputTask = $process.StandardOutput.ReadToEndAsync()
        $errorTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill($true)
            $process.WaitForExit()
            return [pscustomobject]@{ code = 124; text = 'Probe timed out.' }
        }
        $outputText = $outputTask.GetAwaiter().GetResult()
        $errorText = $errorTask.GetAwaiter().GetResult()
        return [pscustomobject]@{ code = $process.ExitCode; text = ($outputText + $errorText).Trim() }
    } catch {
        return [pscustomobject]@{ code = 1; text = 'Tool could not be started.' }
    } finally {
        $process.Dispose()
    }
}

function Test-Version {
    param([string] $Name, [string] $Executable, [string[]] $Arguments, [bool] $Required = $true)
    if (-not $Executable) {
        Add-Check $Name $false $Required 'Not found.'
        return
    }
    $probe = Invoke-Probe $Executable $Arguments
    # Only version output is surfaced; authentication and arbitrary diagnostics are never printed.
    $version = if ($probe.code -eq 0) { ($probe.text -split '\r?\n')[0] } else { 'Version probe failed.' }
    Add-Check $Name ($probe.code -eq 0) $Required $version
}

$gitTool = Find-Tool @('git.exe', 'git')
$nodeTool = Find-Tool @('node.exe', 'node')
$npmTool = Find-Tool @('npm.cmd', 'npm')
$pythonTool = Find-Tool @('python.exe', 'python3', 'python')
$dockerTool = Find-Tool @('docker.exe', 'docker')
$codexTool = Find-Tool @('codex.cmd', 'codex')
$ghTool = Find-Tool @('gh.exe', 'gh') 'C:\Program Files\GitHub CLI\gh.exe'

Write-Host 'LedgerFlow preflight (read-only probes; local evidence report only)'
Test-Version 'Git' $gitTool @('--version')
Test-Version 'Node.js' $nodeTool @('--version')
Test-Version 'npm' $npmTool @('--version')
Test-Version 'Python (product stack pending)' $pythonTool @('--version') $false
Test-Version 'Codex CLI' $codexTool @('--version')
Test-Version 'GitHub CLI' $ghTool @('--version')

if ($codexTool) {
    $codexConfig = Invoke-Probe $codexTool @('--strict-config', 'app-server', '--print-startup-config')
    Add-Check 'Codex configuration schema' ($codexConfig.code -eq 0) $true 'Installed CLI strict configuration validation; raw configuration suppressed.'
}

if ($nodeTool) {
    $nodeVersion = Invoke-Probe $nodeTool @('-p', 'process.versions.node.split(".")[0]')
    Add-Check 'Node.js 24' ($nodeVersion.code -eq 0 -and [int] $nodeVersion.text -eq 24) $true 'Bootstrap tooling requires Node.js major version 24.'
}

if ($gitTool) {
    $gitRoot = Invoke-Probe $gitTool @('rev-parse', '--show-toplevel')
    $isProjectRoot = $gitRoot.code -eq 0 -and [System.IO.Path]::GetFullPath($gitRoot.text) -eq $projectRoot
    Add-Check 'Repository root' $isProjectRoot $true 'Commands target the LedgerFlow checkout.'
    $origin = Invoke-Probe $gitTool @('remote', 'get-url', 'origin')
    $correctOrigin = $origin.code -eq 0 -and $origin.text -match '^(https://github\.com/|git@github\.com:)NekrasovTaras/LedgerFlow_FinTech(?:\.git)?/?$'
    Add-Check 'Origin identity' $correctOrigin $true $expectedRepository
    $branch = Invoke-Probe $gitTool @('symbolic-ref', '--short', 'HEAD')
    Add-Check 'Working branch' ($branch.code -eq 0 -and $branch.text -eq 'feat/ledgerflow-build') $true $branch.text
    $head = Invoke-Probe $gitTool @('rev-parse', '--verify', 'HEAD')
    Add-Check 'Local history' $true $true $(if ($head.code -eq 0) { $head.text } else { 'Unborn branch; no local commits yet.' })
    $status = Invoke-Probe $gitTool @('status', '--porcelain=v1')
    $changeCount = if ($status.text) { @($status.text -split '\r?\n').Count } else { 0 }
    Add-Check 'Working tree inspection' ($status.code -eq 0) $true ('{0} changed or untracked entries; review before commit.' -f $changeCount)
}

if ($ghTool) {
    $authentication = Invoke-Probe $ghTool @('auth', 'status', '--hostname', 'github.com')
    $authDetail = if ($authentication.code -eq 0) { 'Credential output suppressed.' } else { 'Authentication could not be verified (probe exit {0}); raw output suppressed.' -f $authentication.code }
    Add-Check 'GitHub authentication' ($authentication.code -eq 0) $true $authDetail
    if ($authentication.code -eq 0) {
        $remote = Invoke-Probe $ghTool @('repo', 'view', $expectedRepository, '--json', 'nameWithOwner,isEmpty,defaultBranchRef')
        if ($remote.code -eq 0) {
            try {
                $repository = $remote.text | ConvertFrom-Json
                $repositoryMatches = $repository.nameWithOwner -eq $expectedRepository
                $remoteDetail = if ($repository.isEmpty) { 'Remote repository is empty; no existing history.' } else { 'Remote repository has history; inspect refs before integration.' }
                Add-Check 'GitHub repository' $repositoryMatches $true $remoteDetail
            } catch {
                Add-Check 'GitHub repository' $false $true 'Unexpected metadata response; raw output suppressed.'
            }
        } else {
            Add-Check 'GitHub repository' $false $true 'Repository metadata request failed; raw output suppressed.'
        }
    }
}

if ($dockerTool) {
    $dockerVersion = Invoke-Probe $dockerTool @('version', '--format', '{{.Client.Version}} / {{.Server.Version}}')
    Add-Check 'Docker client / daemon' ($dockerVersion.code -eq 0) $true $(if ($dockerVersion.code -eq 0) { $dockerVersion.text } else { 'Docker daemon is unavailable.' })
    Test-Version 'Docker Compose (product stack pending)' $dockerTool @('compose', 'version', '--short') $false
} else {
    Add-Check 'Docker client / daemon' $false $true 'Docker not found.'
}

$requiredFailures = @($checks | Where-Object { $_.required -and -not $_.passed })
$report = [ordered]@{
    generatedAtUtc = [DateTime]::UtcNow.ToString('o')
    project = $expectedRepository
    scope = 'Bootstrap environment; no financial product acceptance checks.'
    passed = $requiredFailures.Count -eq 0
    checks = @($checks.ToArray())
}

if ($ReportPath) {
    $resolvedReport = [System.IO.Path]::GetFullPath($ReportPath, $projectRoot)
    $projectPrefix = $projectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedReport.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The evidence report must stay inside the LedgerFlow workspace.'
    }
    [void] [System.IO.Directory]::CreateDirectory((Split-Path -Parent $resolvedReport))
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resolvedReport -Encoding utf8
    Write-Host ('Evidence: {0}' -f $resolvedReport)
}

if ($requiredFailures.Count -gt 0) {
    Write-Host ('Preflight failed: {0} required checks failed.' -f $requiredFailures.Count)
    exit 1
}
Write-Host 'Preflight passed.'
exit 0
