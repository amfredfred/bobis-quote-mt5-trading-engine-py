; TradeRelay.iss — Inno Setup script for the TradeRelay Engine installer
;
; Prerequisites:
;   - PyInstaller build complete: dist\TradeRelay-Engine\ must exist
;   - NSSM zip present or dist\nssm\ directory available
;
; Build command (from execution-engine\ dir):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\TradeRelay.iss
;   — or via build pipeline:
;   powershell -ExecutionPolicy Bypass -File installer\build.ps1
;
; Output: installer\Output\TradeRelaySetup.exe

#define MyAppName      "TradeRelay Engine"
#define MyAppPublisher "TradeRelay"
#define MyAppURL       "https://traderelay.io"
#define MyAppExeName   "TradeRelay-Engine\TradeRelay-Engine.exe"
#define MyServiceName  "TradeRelayEngine"

; Read version from version.txt at compile time
#define MyAppVersion   FileRead(AddBackslash(SourcePath) + "..\version.txt")
; Trim any trailing whitespace/newline
#define MyAppVersion   Trim(MyAppVersion)

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/support
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\TradeRelay
DefaultGroupName=TradeRelay
AllowNoIcons=yes
LicenseFile=..\LICENSE
; OutputDir is relative to the .iss file location
OutputDir=Output
OutputBaseFilename=TradeRelaySetup
SetupIconFile=assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
; Require admin so we can install the Windows service
PrivilegesRequired=admin
; Version info embedded in setup.exe
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoProductName={#MyAppName}
; Restart if files are locked (service was running)
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";    Description: "Create a &desktop shortcut for the setup wizard"; GroupDescription: "Additional icons:"
Name: "startupservice"; Description: "Install and start as a &Windows service (recommended)"; GroupDescription: "Service:"; Flags: checkedonce

[Dirs]
Name: "{app}\data";  Permissions: authusers-modify
Name: "{app}\logs";  Permissions: authusers-modify

[Files]
; ── Packaged engine (onedir) ────────────────────────────────────────────────
Source: "..\dist\TradeRelay-Engine\*"; \
    DestDir: "{app}\TradeRelay-Engine"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; ── Config template + version ──────────────────────────────────────────────
; These are also inside dist\ but placing explicit copies at {app} root
; makes setup.ps1 path resolution simpler.
Source: "..\config.example.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\version.txt";         DestDir: "{app}"; Flags: ignoreversion

; ── Service installer script ──────────────────────────────────────────────
Source: "..\install_service.ps1"; DestDir: "{app}"; Flags: ignoreversion

; ── Setup wizard ─────────────────────────────────────────────────────────
Source: "setup.ps1"; DestDir: "{app}"; Flags: ignoreversion

; ── Utility scripts ──────────────────────────────────────────────────────
Source: "..\scripts\update.ps1";         DestDir: "{app}\scripts"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\scripts\support-bundle.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion skipifsourcedoesntexist

; ── NSSM ─────────────────────────────────────────────────────────────────
Source: "..\nssm\nssm-2.24\win64\nssm.exe"; \
    DestDir: "{app}\nssm\nssm-2.24\win64"; \
    Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\Setup Wizard";       Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\setup.ps1"""; WorkingDir: "{app}"; IconFilename: "{app}\TradeRelay-Engine\TradeRelay-Engine.exe"
Name: "{group}\View Logs";          Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-ExecutionPolicy Bypass -Command ""Get-Content '{app}\logs\stderr.log' -Tail 50 -Wait"""
Name: "{group}\Support Bundle";     Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\support-bundle.ps1"""
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\TradeRelay Setup"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\setup.ps1"""; Tasks: desktopicon

[Run]
; Run setup wizard after installation (non-silent mode only)
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -File ""{app}\setup.ps1"""; \
    WorkingDir: "{app}"; \
    Description: "Run the TradeRelay setup wizard"; \
    Flags: postinstall nowait runascurrentuser; \
    Check: not WizardSilent

[UninstallRun]
; Stop and remove the service before uninstall
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -File ""{app}\install_service.ps1"" uninstall"; \
    RunOnceId: "RemoveService"; \
    Flags: runhidden

[UninstallDelete]
; Remove generated files not tracked by Inno Setup
Type: files;     Name: "{app}\config.yaml"
Type: files;     Name: "{app}\.env"
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"
