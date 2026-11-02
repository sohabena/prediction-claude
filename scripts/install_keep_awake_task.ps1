<#
Registers a Windows scheduled task "PHOENIX keep awake" that starts
scripts/keep_awake_while_recording.py at logon. While the phoenix-scraper container runs,
Windows will not idle-sleep; when it stops, normal sleep resumes.

Run once, from the repository root, in a normal (non-admin) PowerShell:
    powershell -ExecutionPolicy Bypass -File scripts\install_keep_awake_task.ps1
Remove:
    powershell -ExecutionPolicy Bypass -File scripts\install_keep_awake_task.ps1 -Uninstall

This does not change power settings. A closed lid or a manual Sleep still sleeps the PC.
#>
param([switch]$Uninstall)

$TaskName = "PHOENIX keep awake"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task '$TaskName'."
    exit 0
}

$Python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $Python) { $Python = (Get-Command python.exe).Source }

$Action = New-ScheduledTaskAction -Execute $Python `
    -Argument "`"$Repo\scripts\keep_awake_while_recording.py`"" -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "Registered and started '$TaskName' (runs at logon)."
Write-Output "Uninstall: powershell -ExecutionPolicy Bypass -File scripts\install_keep_awake_task.ps1 -Uninstall"
