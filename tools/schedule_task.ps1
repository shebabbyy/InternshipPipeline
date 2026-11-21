# Creates the scheduled task that runs the pipeline once a day at 6:40 AM (waking the laptop from sleep if it's plugged in),
# plus at sign-in and unlock as a backup. It runs hidden at low priority and only works when a scan is due
# (scan_every_hours in config.yaml, 20 h), so it's one scan a day.
$dir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # the InternshipPipeline folder (this file lives in tools)
# Hidden: wscript runs the check with no window, so nothing pops up while you game.
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$dir\tools\run_hidden.vbs`"" -WorkingDirectory $dir
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$class = Get-CimClass -Namespace Root/Microsoft/Windows/TaskScheduler -ClassName MSFT_TaskSessionStateChangeTrigger
$unlock = New-CimInstance -CimClass $class -ClientOnly
$unlock.StateChange = 8   # 8 = session unlock
$unlock.UserId = "$env:USERDOMAIN\$env:USERNAME"
$unlock.Delay = "PT1M"
$morning = New-ScheduledTaskTrigger -Daily -At 6:40am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Shehab Internship Pipeline" -Action $action -Trigger @($morning, $logon, $unlock) -Settings $settings -Force | Out-Null
# Remove the hourly task from an earlier version, if it exists (daily is enough).
Unregister-ScheduledTask -TaskName "Shehab Internship Pipeline (hourly)" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Scheduled: once a day, hidden and low priority (6:40 AM, or your first sign-in/unlock after that)."
Write-Host "Tip: Windows only wakes for tasks if 'Allow wake timers' is on (Power Options > Sleep). Plugged in works best."
