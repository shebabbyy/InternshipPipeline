' Starts the Internship Control Center with no console window.
' A second double-click just opens the page again. Quit it from the page.
Dim fso, sh, dir, py
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
py = dir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
  MsgBox "The pipeline is not installed yet. Double-click INSTALL.bat in the InternshipPipeline folder first.", 48, "Control Center"
  WScript.Quit 1
End If
sh.CurrentDirectory = dir
sh.Run """" & py & """ """ & dir & "\control_center.py""", 0, False
