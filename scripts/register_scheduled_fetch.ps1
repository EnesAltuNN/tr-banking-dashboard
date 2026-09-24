<#
Registers (or updates) a weekly Windows scheduled task that runs scripts\scheduled_fetch.ps1.
The CBRT publishes weekly money and banking statistics on Thursdays at 14:30 (Istanbul time).

Usage:   powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_fetch.ps1
Run now: Start-ScheduledTask -TaskName "tr-banking weekly fetch"
Remove:  Unregister-ScheduledTask -TaskName "tr-banking weekly fetch" -Confirm:$false
#>
param(
    [string]$TaskName = "tr-banking weekly fetch",
    [System.DayOfWeek]$Day = [System.DayOfWeek]::Thursday,
    [string]$At = "15:00"
)
$ErrorActionPreference = "Stop"
$fetchScript = Join-Path $PSScriptRoot "scheduled_fetch.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass " +
    "-File `"$fetchScript`""
)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $At
# StartWhenAvailable: if the PC was off at the scheduled time, run as soon as it is back.
# A missed week is harmless anyway: every fetch re-reads the last 8 weeks.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

# No -User: runs as the current user while logged on, so no admin rights or stored password.
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Fetch weekly EVDS loan data into tr-banking-dashboard" `
    -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime, LastRunTime, LastTaskResult
