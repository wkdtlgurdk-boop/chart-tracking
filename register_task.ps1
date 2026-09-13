# Trade Signal - register Windows Task Scheduler jobs.
# Run from PowerShell in this folder:  powershell -ExecutionPolicy Bypass -File .\register_task.ps1
# (English only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 files as ANSI.)
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $dir 'trade_signal.py'
$python = (Get-Command python.exe -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$weekdays = 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'

# 1) Scan: weekdays 15:40, backup 16:10 (skips itself if already sent today)
$scanAction = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $dir
$scanTriggers = @(
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At '15:40'),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At '16:10')
)
Register-ScheduledTask -TaskName 'TradeSignal-Scan' -Action $scanAction -Trigger $scanTriggers `
    -Settings $settings -Description 'Watchlist 20d new high / 20d, 60d pullback -> Telegram' -Force | Out-Null

# 2) Telegram commands (/add /del /list /scan): every 10 minutes
$cmdAction = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`" --commands-only" -WorkingDirectory $dir
$cmdTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'TradeSignal-Commands' -Action $cmdAction -Trigger $cmdTrigger `
    -Settings $settings -Description 'Trade Signal Telegram command polling' -Force | Out-Null

Get-ScheduledTask -TaskName 'TradeSignal-*' | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
