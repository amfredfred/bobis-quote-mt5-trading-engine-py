' watchdog-launcher.vbs — truly invisible launcher for watchdog.ps1.
'
' powershell.exe's own -WindowStyle Hidden still briefly flashes a console
' window on many Windows builds: the console host allocates the window
' before PowerShell applies the style, so a hidden-window PowerShell task
' still pops a visible flash every time Task Scheduler fires it (every 5
' minutes for this watchdog). WScript.Shell.Run with window style 0 never
' allocates a visible window in the first place - no flash, ever.
'
' Optional first argument (install.ps1 -Broker) is forwarded to
' watchdog.ps1 -Broker so a per-broker watchdog only ever checks/restarts
' its own instance, never a sibling's.
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
psCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & scriptDir & "\watchdog.ps1"""
If WScript.Arguments.Count > 0 Then
    psCmd = psCmd & " -Broker " & WScript.Arguments(0)
End If
CreateObject("WScript.Shell").Run psCmd, 0, False
