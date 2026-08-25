<#
.SYNOPSIS
    Stop the Interlock services started by up.ps1.

.DESCRIPTION
    Matches uvicorn processes by their command line rather than by a stored pid file,
    so it still works after a crash, a reboot of the shell, or a run started from a
    different terminal -- and so it cannot kill an unrelated Python process.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$patterns = @(
    '*uvicorn*interlock.gateway*',
    '*uvicorn*interlock.observer*'
)

$stopped = 0
foreach ($pattern in $patterns) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='uv.exe'" |
        Where-Object { $_.CommandLine -like $pattern } |
        ForEach-Object {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                Write-Host ("  stopped pid {0}" -f $_.ProcessId) -ForegroundColor DarkGray
                $stopped++
            } catch {
                Write-Host ("  could not stop pid {0}: {1}" -f $_.ProcessId, $_.Exception.Message) -ForegroundColor Yellow
            }
        }
}

if ($stopped -eq 0) {
    Write-Host 'Nothing to stop.' -ForegroundColor DarkGray
} else {
    Write-Host ("Stopped {0} process(es)." -f $stopped) -ForegroundColor Green
}
