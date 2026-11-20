' MedScribe HK - start tray icon hidden (no console window)
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
script = root & "\tray\MedScribeTray.ps1"
cmd = Chr(34) & "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" & Chr(34) & " -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & script & Chr(34)
CreateObject("WScript.Shell").Run cmd, 0, False
