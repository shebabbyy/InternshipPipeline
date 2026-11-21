# Puts "Start Control Center" on the desktop. Safe to run again.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$desktop = [Environment]::GetFolderPath("Desktop")
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$desktop\Start Control Center.lnk")
$lnk.TargetPath = "$env:WINDIR\System32\wscript.exe"
$lnk.Arguments = '"' + "$root\Start Control Center.vbs" + '"'
$lnk.WorkingDirectory = $root
$lnk.IconLocation = "$env:WINDIR\System32\shell32.dll,44"
$lnk.Description = "Internship Control Center"
$lnk.Save()
Write-Host "Desktop shortcut ready: Start Control Center"
