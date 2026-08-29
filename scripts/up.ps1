<#
.SYNOPSIS
    Start Interlock locally: gateway + observer + console.

.DESCRIPTION
    Replaces `docker compose up` (deviation D-001: Docker was dropped from this build).
    Starts each service as a background process, then polls its /health until ready --
    the same semantics a compose healthcheck would give, without the container runtime.

    No API key is required: the default upstream is a local Ollama.

.EXAMPLE
    .\scripts\up.ps1
    .\scripts\up.ps1 -TimeoutSeconds 120
#>
[CmdletBinding()]
param(
    [int]$GatewayPort = 8080,
    [int]$ObserverPort = 8081,
    [int]$ConsolePort = 5173,
    [int]$TimeoutSeconds = 90,
    [ValidateSet('real', 'stub')]
    [string]$RiskEngine = $(if ($env:INTERLOCK_RISK_ENGINE) { $env:INTERLOCK_RISK_ENGINE } else { 'real' }),
    [switch]$MockObserver
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$logDir = Join-Path $repo 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $repo 'data') | Out-Null

$consoleIndex = Join-Path $repo 'console\dist\index.html'
if (-not (Test-Path $consoleIndex)) {
    Write-Host 'Building the React console...' -ForegroundColor Cyan
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw 'Node.js/npm is required to build console/dist before the console service starts.'
    }
    if (-not (Test-Path (Join-Path $repo 'console\node_modules'))) {
        & npm --prefix console ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE" }
    }
    & npm --prefix console run build
    if ($LASTEXITCODE -ne 0) { throw "console build failed with exit code $LASTEXITCODE" }
}

# The mock remains an explicit rehearsal/chaos option. Production uses the real
# observer service, which owns the trained probe and optional claim verifier.
$observerApp = if ($MockObserver) {
    'interlock.observer.mock_server:app'
} else {
    'interlock.observer.server:app'
}

$services = @(
    @{ Name = 'observer'; App = $observerApp;                  Port = $ObserverPort },
    @{ Name = 'gateway';  App = 'interlock.gateway.app:app';   Port = $GatewayPort  },
    @{ Name = 'console';  App = 'interlock.console.app:app';   Port = $ConsolePort  }
)

function Test-Health {
    param([int]$Port)
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        return [bool]$response.ok
    } catch {
        return $false
    }
}

Write-Host "Starting Interlock (repo: $repo)" -ForegroundColor Cyan
Write-Host ("  risk engine: {0}" -f $RiskEngine) -ForegroundColor DarkGray

$previousRiskEngine = $env:INTERLOCK_RISK_ENGINE
$previousObserverUrl = $env:INTERLOCK_OBSERVER_URL
$env:INTERLOCK_RISK_ENGINE = $RiskEngine
$env:INTERLOCK_OBSERVER_URL = "http://127.0.0.1:$ObserverPort"

$started = @()
try {
    foreach ($service in $services) {
        if (Test-Health -Port $service.Port) {
            Write-Host ("  {0,-9} already running on :{1}" -f $service.Name, $service.Port) -ForegroundColor DarkGray
            continue
        }
        $log = Join-Path $logDir "$($service.Name).log"
        $process = Start-Process -FilePath 'uv' `
            -ArgumentList 'run', 'uvicorn', $service.App, '--host', '127.0.0.1', '--port', $service.Port, '--log-level', 'info' `
            -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
            -WindowStyle Hidden -PassThru
        $started += [pscustomobject]@{ Name = $service.Name; Port = $service.Port; Pid = $process.Id }
        Write-Host ("  {0,-9} starting on :{1} (pid {2}, log {3})" -f $service.Name, $service.Port, $process.Id, $log)
    }
}
finally {
    if ($null -eq $previousRiskEngine) { Remove-Item Env:\INTERLOCK_RISK_ENGINE -ErrorAction SilentlyContinue } else { $env:INTERLOCK_RISK_ENGINE = $previousRiskEngine }
    if ($null -eq $previousObserverUrl) { Remove-Item Env:\INTERLOCK_OBSERVER_URL -ErrorAction SilentlyContinue } else { $env:INTERLOCK_OBSERVER_URL = $previousObserverUrl }
}

# Poll until healthy. A cold start must reach healthy inside the timeout, which is the
# number the plan holds us to for "a judge runs it in one command".
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$pending = $services | ForEach-Object { $_ }
while ((Get-Date) -lt $deadline) {
    $pending = @($pending | Where-Object { -not (Test-Health -Port $_.Port) })
    if ($pending.Count -eq 0) { break }
    Start-Sleep -Milliseconds 700
}

Write-Host ''
$allHealthy = $true
foreach ($service in $services) {
    $healthy = Test-Health -Port $service.Port
    $allHealthy = $allHealthy -and $healthy
    $mark  = if ($healthy) { 'HEALTHY' } else { 'FAILED ' }
    $color = if ($healthy) { 'Green' }   else { 'Red' }
    Write-Host ("  {0}  {1,-9} http://127.0.0.1:{2}" -f $mark, $service.Name, $service.Port) -ForegroundColor $color
}

if (-not $allHealthy) {
    Write-Host ''
    Write-Host 'Not all services came up. Check logs\*.log and logs\*.log.err' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host 'Point any OpenAI-compatible client at the gateway:' -ForegroundColor Cyan
Write-Host '    client = OpenAI(base_url="http://localhost:8080/v1", api_key="local")'
Write-Host ''
Write-Host ("Open the console at: http://127.0.0.1:{0}" -f $ConsolePort) -ForegroundColor Cyan
Write-Host ''
Write-Host 'Stop with: .\scripts\down.ps1'
exit 0
