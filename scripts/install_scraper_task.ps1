# Registers the Windows Task Scheduler task "PHOENIX scraper" (S37).
#
# The task starts scripts/run_scraper_forever.py (the watchdog) at logon and every morning,
# and restarts it on failure. Running this script twice is safe: an existing task is replaced.
#
# Install (operator, once; from the repo root, in PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\install_scraper_task.ps1
# Uninstall:
#   powershell -ExecutionPolicy Bypass -File scripts\install_scraper_task.ps1 -Uninstall
#   (or directly: Unregister-ScheduledTask -TaskName "PHOENIX scraper" -Confirm:$false)

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$DailyAt = "06:00"
)

$ErrorActionPreference = "Stop"
$TaskName = "PHOENIX scraper"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Watchdog = Join-Path $Root "scripts\run_scraper_forever.py"
$UninstallCommand = 'Unregister-ScheduledTask -TaskName "PHOENIX scraper" -Confirm:$false'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "Scheduled task '$TaskName' is not installed; nothing to remove."
    }
    exit 0
}

if (-not (Test-Path $Watchdog)) {
    throw "Watchdog script not found: $Watchdog"
}

$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
    throw "python was not found on PATH. Activate the project environment first."
}
$Python = $PythonCmd.Source
# Prefer pythonw.exe so no console window stays open next to the browser.
$PythonW = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
if (Test-Path $PythonW) { $Python = $PythonW }

$Action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + $Watchdog + '"') -WorkingDirectory $Root

$Triggers = @(
    New-ScheduledTaskTrigger -AtLogOn
    New-ScheduledTaskTrigger -Daily -At $DailyAt
)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Runs the PHOENIX LotusBook recorder watchdog (scripts/run_scraper_forever.py)." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName'."
Write-Host "  Action:   $Python `"$Watchdog`""
Write-Host "  Triggers: at logon, daily at $DailyAt"
Write-Host "  Check:    python scripts\run_scraper_forever.py --status"
Write-Host "  Uninstall: $UninstallCommand"
