<#
.SYNOPSIS
    Run the deterministic four-scenario rehearsal with complete cleanup.

.DESCRIPTION
    Starts the local OpenAI-compatible fixture, supervises the gateway/observer/console,
    runs the strict rehearsal, and tears down both the Interlock services and the full
    fixture process tree even when the rehearsal fails.
#>
[CmdletBinding()]
param(
    [int]$FixturePort = 8099,
    [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$fixture = $null
$exitCode = 1

try {
    $fixture = Start-Process -FilePath 'uv' `
        -ArgumentList 'run', 'python', 'scripts/replay_console.py', '--port', $FixturePort `
        -WindowStyle Hidden -PassThru
    $env:INTERLOCK_OLLAMA_BASE_URL = "http://127.0.0.1:$FixturePort/v1"
    $env:INTERLOCK_DB_PATH = 'data/rehearsal.db'
    .\scripts\up.ps1 -RiskEngine stub -MockObserver -TimeoutSeconds $TimeoutSeconds
    if ($LASTEXITCODE -ne 0) { throw "Interlock supervisor failed with exit code $LASTEXITCODE" }
    uv run python scripts/rehearse_gateway.py --strict-actions
    $exitCode = $LASTEXITCODE
}
finally {
    .\scripts\down.ps1
    if ($null -ne $fixture) {
        & taskkill.exe /PID $fixture.Id /T /F 2>$null | Out-Null
    }
    Remove-Item Env:\INTERLOCK_OLLAMA_BASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:\INTERLOCK_DB_PATH -ErrorAction SilentlyContinue
}

exit $exitCode
