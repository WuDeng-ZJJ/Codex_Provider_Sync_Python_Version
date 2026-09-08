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

function Get-TrackedCodexProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.Generic.HashSet[int]]$ProcessIds
    )

    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    # Include descendants that are started while the desktop process is closing.
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $snapshot) {
            if ($null -ne $process.ParentProcessId -and $ProcessIds.Contains([int]$process.ParentProcessId)) {
                if ($ProcessIds.Add([int]$process.ProcessId)) {
                    $changed = $true
                }
            }
        }
    }

    @(
        $snapshot | Where-Object {
            $ProcessIds.Contains([int]$_.ProcessId)
        }
    )
}

$snapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
$roots = @(
    $snapshot | Where-Object {
        $path = $_.ExecutablePath
        $path -and $path.StartsWith($installRoot, [System.StringComparison]::OrdinalIgnoreCase)
    }
)

if ($roots.Count -eq 0) {
    Write-Host "[INFO] Codex Desktop is not running."
    exit 0
}

$trackedIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($root in $roots) {
    [void]$trackedIds.Add([int]$root.ProcessId)
}

# Track app-server and other descendants before the main process exits.
$changed = $true
while ($changed) {
    $changed = $false
    foreach ($process in $snapshot) {
        if ($null -ne $process.ParentProcessId -and $trackedIds.Contains([int]$process.ParentProcessId)) {
            if ($trackedIds.Add([int]$process.ProcessId)) {
                $changed = $true
            }
        }
    }
}

$processes = @(Get-TrackedCodexProcesses -ProcessIds $trackedIds)
Write-Host "[INFO] Found $($processes.Count) Codex Desktop process(es), including descendants."
foreach ($process in $processes) {
    Write-Host "  PID $($process.ProcessId): $($process.Name)"
}

if ($DryRun) {
    Write-Host "[DRY RUN] No process was closed."
    exit 0
}

# Ask the main window to close first so the app can flush its local state.
foreach ($process in $roots) {
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
    $remaining = @(Get-TrackedCodexProcesses -ProcessIds $trackedIds)
} while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $gracefulDeadline)

if ($remaining.Count -gt 0) {
    Write-Host "[INFO] Forcing $($remaining.Count) remaining Codex Desktop process(es) to stop."
    foreach ($process in $remaining) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    $forceDeadline = [DateTime]::UtcNow.AddSeconds(2)
    do {
        Start-Sleep -Milliseconds 200
        $remaining = @(Get-TrackedCodexProcesses -ProcessIds $trackedIds)
    } while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $forceDeadline)
}

if ($remaining.Count -gt 0) {
    Write-Error "Codex Desktop could not be closed completely."
    exit 1
}

Write-Host "[OK] Codex Desktop is closed."
exit 0
