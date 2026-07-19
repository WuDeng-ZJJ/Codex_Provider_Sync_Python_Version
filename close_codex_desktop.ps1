[CmdletBinding()]
param(
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$package = Get-AppxPackage -Name "OpenAI.Codex" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $package) {
    Write-Host "[INFO] OpenAI.Codex package is not installed for the current user."
    exit 0
}

$installRoot = [System.IO.Path]::GetFullPath($package.InstallLocation).TrimEnd("\") + "\"

function Get-CodexDesktopProcess {
    @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $path = $_.ExecutablePath
                $path -and $path.StartsWith($installRoot, [System.StringComparison]::OrdinalIgnoreCase)
            }
    )
}

$processes = @(Get-CodexDesktopProcess)
if ($processes.Count -eq 0) {
    Write-Host "[INFO] Codex Desktop is not running."
    exit 0
}

Write-Host "[INFO] Found $($processes.Count) Codex Desktop process(es)."
foreach ($process in $processes) {
    Write-Host "  PID $($process.ProcessId): $($process.Name)"
}

if ($DryRun) {
    Write-Host "[DRY RUN] No process was closed."
    exit 0
}

# Ask the main window to close first so the app can flush its local state.
foreach ($process in $processes) {
    try {
        $localProcess = Get-Process -Id $process.ProcessId -ErrorAction Stop
        if ($localProcess.MainWindowHandle -ne 0) {
            [void]$localProcess.CloseMainWindow()
        }
    } catch {
        # The process may have exited while the process list was being inspected.
    }
}

$gracefulDeadline = [DateTime]::UtcNow.AddSeconds(5)
do {
    Start-Sleep -Milliseconds 250
    $remaining = @(Get-CodexDesktopProcess)
} while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $gracefulDeadline)

if ($remaining.Count -gt 0) {
    Write-Host "[INFO] Forcing $($remaining.Count) remaining Codex Desktop process(es) to stop."
    foreach ($process in $remaining) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    $forceDeadline = [DateTime]::UtcNow.AddSeconds(2)
    do {
        Start-Sleep -Milliseconds 200
        $remaining = @(Get-CodexDesktopProcess)
    } while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $forceDeadline)
}

if ($remaining.Count -gt 0) {
    Write-Error "Codex Desktop could not be closed completely."
    exit 1
}

Write-Host "[OK] Codex Desktop is closed."
exit 0
