' Runs the automatic pipeline check with no window (used by the scheduled task).
' Problems are not shown in a window: the Control Center shows them (Today and Health).
Dim sh, fso, dir
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Environment("PROCESS")("PIPELINE_HIDDEN") = "1"
sh.Run "cmd.exe /c """ & dir & "\RUN_PIPELINE_AUTO.bat""", 0, True
