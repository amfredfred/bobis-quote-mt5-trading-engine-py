' watchdog-launcher.vbs — truly invisible launcher for watchdog.ps1.
'
' powershell.exe's own -WindowStyle Hidden still briefly flashes a console
' window on many Windows builds: the console host allocates the window
' before PowerShell applies the style, so a hidden-window PowerShell task
' still pops a visible flash every time Task Scheduler fires it (every 5
' minutes for this watchdog). WScript.Shell.Run with window style 0 never
' allocates a visible window in the first place - no flash, ever.
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
psCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & scriptDir & "\watchdog.ps1"""
CreateObject("WScript.Shell").Run psCmd, 0, False
