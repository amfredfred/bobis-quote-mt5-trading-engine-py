; Apex Quant Trader.iss — Inno Setup script (GUI wizard edition)
;
; All first-run configuration is collected inside the Inno Setup wizard itself:
;   Activation key → MT5 path (auto-discovered + Browse button) → Account details
;   → Gateway URL + Engine ID → Symbols → Paper/Live mode → Risk settings
;
; After file copy, config.yaml and .env are written from the wizard answers.
; No console window, no separate PowerShell wizard needed for initial install.
; setup.ps1 is still bundled for reconfiguration via the Start Menu shortcut.
;
; Build (from execution-engine\ dir):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\ApexQuantTrader.iss
;   — or via the build pipeline:
;   powershell -ExecutionPolicy Bypass -File installer\build.ps1

#define MyAppName      "Apex Quant Trader"
#define MyAppPublisher "Apex Quant Trader"
#define MyAppURL       "https://apexquanttrader.io"
#define MyAppExeName   "apex-quant-trader-agent\apex-quant-trader-agent.exe"
#define MyServiceName  "apex-quant-trader-agent"
#define MyAppVersion   Trim(FileRead(AddBackslash(SourcePath) + "..\version.txt"))

; ============================================================================
[Setup]
; ============================================================================
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/support
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\Apex Quant Trader
DefaultGroupName=Apex Quant Trader
AllowNoIcons=yes
LicenseFile=..\LICENSE
OutputDir=Output
OutputBaseFilename=ApexQuantTraderSetup
SetupIconFile=assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
PrivilegesRequired=admin
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} {#MyAppVersion} Installer
VersionInfoProductName={#MyAppName}
CloseApplications=yes
RestartApplications=no

; ============================================================================
[Languages]
; ============================================================================
Name: "english"; MessagesFile: "compiler:Default.isl"

; ============================================================================
[Tasks]
; ============================================================================
Name: "installservice"; \
    Description: "Install and start as a &Windows service (recommended)"; \
    GroupDescription: "Service:"; \
    Flags: checkedonce
Name: "desktopicon"; \
    Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional icons:"

; ============================================================================
[Dirs]
; ============================================================================
Name: "{app}\data";  Permissions: authusers-modify
Name: "{app}\logs";  Permissions: authusers-modify

; ============================================================================
[Files]
; ============================================================================
; Packaged engine (onedir from PyInstaller)
Source: "..\dist\apex-quant-trader-agent\*"; \
    DestDir: "{app}\apex-quant-trader-agent"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; Config template + version (also inside dist\ but explicit copy simplifies paths)
Source: "..\config.example.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\version.txt";         DestDir: "{app}"; Flags: ignoreversion

; Service installer + reconfiguration wizard
Source: "..\install_service.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "setup.ps1";              DestDir: "{app}"; Flags: ignoreversion

; Utility scripts
Source: "..\scripts\update.ps1";          DestDir: "{app}\scripts"; \
    Flags: ignoreversion skipifsourcedoesntexist
Source: "..\scripts\support-bundle.ps1";  DestDir: "{app}\scripts"; \
    Flags: ignoreversion skipifsourcedoesntexist

; NSSM service manager (bundled so no download needed at install time)
Source: "..\nssm\nssm-2.24\win64\nssm.exe"; \
    DestDir: "{app}\nssm\nssm-2.24\win64"; \
    Flags: ignoreversion skipifsourcedoesntexist

; ============================================================================
[Icons]
; ============================================================================
Name: "{group}\Reconfigure Apex Quant Trader"; \
    Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -File ""{app}\setup.ps1"""; \
    WorkingDir: "{app}"
Name: "{group}\View Logs"; \
    Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -Command ""Get-Content '{app}\logs\stderr.log' -Tail 80 -Wait"""
Name: "{group}\Support Bundle"; \
    Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\support-bundle.ps1"""
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\Apex Quant Trader"; \
    Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -File ""{app}\setup.ps1"""; \
    Tasks: desktopicon

; ============================================================================
[Run]
; ============================================================================
; Open the Apex Quant Trader dashboard after successful install (Finish page checkbox)
Filename: "https://app.apexquanttrader.io"; \
    Description: "Open Apex Quant Trader dashboard in browser"; \
    Flags: postinstall shellexec nowait skipifsilent

; ============================================================================
[UninstallRun]
; ============================================================================
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -File ""{app}\install_service.ps1"" uninstall"; \
    RunOnceId: "RemoveService"; \
    Flags: runhidden

; ============================================================================
[UninstallDelete]
; ============================================================================
Type: files;          Name: "{app}\config.yaml"
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"

; ============================================================================
[Code]
; ============================================================================

var
  // ── Custom wizard page references ───────────────────────────────────────
  ActivationKeyPage : TInputQueryWizardPage;  // Step 1 — activation key
  MT5Page           : TInputQueryWizardPage;  // Step 2 — MT5 path + login + server
  MT5PassPage       : TInputQueryWizardPage;  // Step 3 — MT5 password (masked)
  ConnPage          : TInputQueryWizardPage;  // Step 4 — gateway URL + engine ID
  SymbolsPage       : TInputQueryWizardPage;  // Step 5 — symbol list
  ModePage          : TInputOptionWizardPage; // Step 6 — paper / live radio
  RiskPage          : TInputQueryWizardPage;  // Step 7 — risk parameters

  // ── MT5 Browse button (overlaid on MT5Page surface) ─────────────────────
  MT5BrowseBtn      : TButton;

  // ── Internal state ───────────────────────────────────────────────────────
  LiveModeConfirmed : Boolean;


// ---------------------------------------------------------------------------
// Scan well-known locations for MetaTrader 5 terminal64.exe
// ---------------------------------------------------------------------------
function FindMT5Path: String;
var
  Paths: array[0..13] of String;
  Mt5Dir: String;
  I: Integer;
begin
  Result := '';

  Paths[0]  := 'C:\Program Files\MetaTrader 5\terminal64.exe';
  Paths[1]  := 'C:\Program Files (x86)\MetaTrader 5\terminal64.exe';
  Paths[2]  := 'C:\Program Files\FBS MetaTrader 5\terminal64.exe';
  Paths[3]  := 'C:\Program Files\Exness MetaTrader 5\terminal64.exe';
  Paths[4]  := 'C:\Program Files\ICMarkets MetaTrader 5\terminal64.exe';
  Paths[5]  := 'C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe';
  Paths[6]  := 'C:\Program Files\FTMO MetaTrader 5\terminal64.exe';
  Paths[7]  := 'C:\Program Files\XM Global MetaTrader 5\terminal64.exe';
  Paths[8]  := 'C:\Program Files\HotForex MetaTrader 5\terminal64.exe';
  Paths[9]  := 'C:\Program Files\Oanda MetaTrader 5\terminal64.exe';
  Paths[10] := 'C:\Program Files\ThinkMarkets MetaTrader 5\terminal64.exe';
  Paths[11] := 'C:\Program Files\Tickmill MetaTrader 5\terminal64.exe';
  Paths[12] := 'C:\Program Files\Axiory MetaTrader 5\terminal64.exe';
  Paths[13] := 'C:\Program Files\Roboforex MetaTrader 5\terminal64.exe';

  for I := 0 to 13 do begin
    if FileExists(Paths[I]) then begin
      Result := Paths[I];
      Exit;
    end;
  end;

  // Registry — HKLM
  if RegQueryStringValue(HKLM,
      'SOFTWARE\MetaQuotes Software Corp\MetaTrader 5', 'Path', Mt5Dir) then begin
    if FileExists(Mt5Dir + '\terminal64.exe') then begin
      Result := Mt5Dir + '\terminal64.exe';
      Exit;
    end;
  end;

  // Registry — HKCU (per-user installs)
  if RegQueryStringValue(HKCU,
      'SOFTWARE\MetaQuotes Software Corp\MetaTrader 5', 'Path', Mt5Dir) then begin
    if FileExists(Mt5Dir + '\terminal64.exe') then begin
      Result := Mt5Dir + '\terminal64.exe';
      Exit;
    end;
  end;
end;


// ---------------------------------------------------------------------------
// MT5 Browse button — open folder picker, auto-append terminal64.exe
// ---------------------------------------------------------------------------
procedure MT5BrowseBtnClick(Sender: TObject);
var
  Dir: String;
begin
  // Start from the current value's directory (or Program Files as default)
  if Trim(MT5Page.Values[0]) <> '' then
    Dir := ExtractFileDir(Trim(MT5Page.Values[0]))
  else
    Dir := 'C:\Program Files';

  if BrowseForFolder('Select your MetaTrader 5 installation folder', Dir, False) then begin
    if FileExists(Dir + '\terminal64.exe') then
      MT5Page.Values[0] := Dir + '\terminal64.exe'
    else
      MsgBox(
        'terminal64.exe was not found in that folder.' + #13#10 +
        'Please select the folder that contains terminal64.exe' + #13#10 +
        '(e.g. C:\Program Files\MetaTrader 5).',
        mbError, MB_OK
      );
  end;
end;


// ---------------------------------------------------------------------------
// Trim a string (Inno Setup Pascal Script doesn't have a built-in Trim)
// ---------------------------------------------------------------------------
function TrimStr(const S: String): String;
var
  I, L: Integer;
begin
  L := Length(S);
  I := 1;
  while (I <= L) and (S[I] <= ' ') do Inc(I);
  while (L >= I) and (S[L] <= ' ') do Dec(L);
  Result := Copy(S, I, L - I + 1);
end;


// ---------------------------------------------------------------------------
// Split a delimited string into a TStringList
// ---------------------------------------------------------------------------
function SplitStr(const S, Delim: String): TStringList;
var
  Remaining, Part: String;
  P: Integer;
begin
  Result := TStringList.Create;
  Remaining := S;
  while Remaining <> '' do begin
    P := Pos(Delim, Remaining);
    if P > 0 then begin
      Part := Copy(Remaining, 1, P - 1);
      Remaining := Copy(Remaining, P + Length(Delim), MaxInt);
    end else begin
      Part := Remaining;
      Remaining := '';
    end;
    Part := TrimStr(Part);
    if Part <> '' then
      Result.Add(Uppercase(Part));
  end;
end;


// ---------------------------------------------------------------------------
// Build config.yaml from wizard answers
// ---------------------------------------------------------------------------
function BuildConfigYaml: String;
var
  Lines: TStringList;
  Symbols: TStringList;
  I: Integer;
  LiveMode: Boolean;
begin
  LiveMode := ModePage.Values[1];   // index 1 = Live mode radio
  Symbols  := SplitStr(TrimStr(SymbolsPage.Values[0]), ',');

  Lines := TStringList.Create;
  Lines.Add('# Apex Quant Trader — configuration');
  Lines.Add('# Generated by installer on ' +
    GetDateTimeString('yyyy/mm/dd hh:nn:ss', '/', ':'));
  Lines.Add('# Re-run the "Reconfigure Apex Quant Trader" shortcut to change any setting.');
  Lines.Add('');

  Lines.Add('gateway:');
  Lines.Add('  ws_url: ' + TrimStr(ConnPage.Values[0]));
  Lines.Add('  engine_id: ' + TrimStr(ConnPage.Values[1]));
  Lines.Add('  engine_version: 0.1.0');
  Lines.Add('  room_ttl_seconds: 3600');
  Lines.Add('  activation_key: "' + TrimStr(ActivationKeyPage.Values[0]) + '"');
  Lines.Add('  signal_hmac_secret: ""');
  Lines.Add('  symbols:');
  if Symbols.Count = 0 then
    Lines.Add('    - XAUUSD')
  else
    for I := 0 to Symbols.Count - 1 do
      Lines.Add('    - ' + Symbols[I]);
  Lines.Add('');

  Lines.Add('mt5:');
  Lines.Add('  login: ' + TrimStr(MT5Page.Values[1]));
  Lines.Add('  password: "' + MT5PassPage.Values[0] + '"');
  Lines.Add('  server: ' + TrimStr(MT5Page.Values[2]));
  if TrimStr(MT5Page.Values[0]) <> '' then
    Lines.Add('  path: "' +
      StringReplace(TrimStr(MT5Page.Values[0]), '\', '\\', [rfReplaceAll]) + '"')
  else
    Lines.Add('  path: ""');
  Lines.Add('  magic: 8858');
  Lines.Add('  slippage: 10');
  Lines.Add('  comment: apexquanttrader');
  Lines.Add('');

  Lines.Add('risk:');
  Lines.Add('  max_daily_loss_percent: ' + TrimStr(RiskPage.Values[0]));
  Lines.Add('  max_losing_streak: '       + TrimStr(RiskPage.Values[1]));
  Lines.Add('  max_lot_size: '            + TrimStr(RiskPage.Values[2]));
  Lines.Add('  min_rr_ratio: '            + TrimStr(RiskPage.Values[3]));
  Lines.Add('  sl_ratio_threshold: '      + TrimStr(RiskPage.Values[4]));
  Lines.Add('  max_exposure_per_symbol: 2');
  Lines.Add('  min_lot_size: 0.01');
  Lines.Add('  no_hedging: true');
  Lines.Add('  max_equity_drawdown_percent: 2.0');
  Lines.Add('  rolling_window_size: 2');
  Lines.Add('  rolling_drawdown_pct: 2.0');
  Lines.Add('  symbol_sl_ratio_threshold: {}');
  Lines.Add('');

  Lines.Add('execution:');
  Lines.Add('  tp1_trigger_pct: 50.0');
  Lines.Add('  tp1_percentage: 0.0');
  Lines.Add('  move_sl_to_be_on_tp1: true');
  Lines.Add('  breakeven_spread_multiplier: 1.5');
  Lines.Add('  breakeven_max_buffer_pct_of_risk: 10.0');
  Lines.Add('  order_retry_count: 2');
  Lines.Add('  order_retry_delay_sec: 0.5');
  Lines.Add('  max_entry_slippage_pct_of_stop: 0.20');
  Lines.Add('  max_signal_age_ms: 120000');
  Lines.Add('  close_on_slippage_exceed: false');
  Lines.Add('  adjust_levels_on_slippage: false');
  Lines.Add('  spread_risk_multiplier: 1.0');
  Lines.Add('  tf_overrides: {}');
  Lines.Add('');

  Lines.Add('engine:');
  Lines.Add('  timezone: UTC');
  Lines.Add('  log_level: INFO');
  Lines.Add('  storage_path: ./data');
  Lines.Add('  monitoring_port: 8080');
  Lines.Add('  position_poll_interval: 0.6');
  if LiveMode then
    Lines.Add('  # mode: live  (set at install time)')
  else
    Lines.Add('  # mode: paper  (set at install time)');

  Result := Lines.Text;
  Lines.Free;
  Symbols.Free;
end;


// ---------------------------------------------------------------------------
// Restrict a file to the current user (icacls, equivalent to chmod 600)
// ---------------------------------------------------------------------------
procedure ProtectFile(const FilePath: String);
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\icacls.exe'),
       '"' + FilePath + '" /inheritance:r /grant:r "' +
       GetUserNameString + ':F"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;


// ---------------------------------------------------------------------------
// Run install_service.ps1 install (hidden window, wait for exit)
// ---------------------------------------------------------------------------
procedure RunServiceInstall;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-ExecutionPolicy Bypass -NonInteractive -File "' +
       ExpandConstant('{app}\install_service.ps1') + '" install',
       ExpandConstant('{app}'),
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;


// ---------------------------------------------------------------------------
// InitializeWizard — create all custom pages and insert them into the flow
// ---------------------------------------------------------------------------
procedure InitializeWizard;
var
  DiscoveredMT5: String;
  EditW: Integer;
begin
  LiveModeConfirmed := False;

  // ══ Page 1: Activation Key ══════════════════════════════════════════════
  ActivationKeyPage := CreateInputQueryPage(
    wpSelectTasks,
    'Activation Key',
    'Enter the activation key from your purchase confirmation email.',
    ''
  );
  ActivationKeyPage.Add('Activation key (format: TR-...):', False);

  // ══ Page 2: MT5 path + account ══════════════════════════════════════════
  MT5Page := CreateInputQueryPage(
    ActivationKeyPage.ID,
    'MetaTrader 5 — Location & Account',
    'Apex Quant Trader found the MT5 path below automatically. Correct it if needed, then fill in your account details.',
    ''
  );
  MT5Page.Add('Path to terminal64.exe:', False);
  MT5Page.Add('Account number (login):', False);
  MT5Page.Add('Server name:', False);

  // Pre-fill auto-discovered path
  DiscoveredMT5 := FindMT5Path;
  if DiscoveredMT5 <> '' then
    MT5Page.Values[0] := DiscoveredMT5;

  // Shrink the path edit to make room for the Browse button on its right
  EditW := MT5Page.Edits[0].Width;
  MT5Page.Edits[0].Width := EditW - 90;

  MT5BrowseBtn          := TButton.Create(WizardForm);
  MT5BrowseBtn.Parent   := MT5Page.Surface;
  MT5BrowseBtn.Caption  := 'Browse...';
  MT5BrowseBtn.Width    := 82;
  MT5BrowseBtn.Height   := MT5Page.Edits[0].Height;
  MT5BrowseBtn.Left     := MT5Page.Edits[0].Left + MT5Page.Edits[0].Width + 8;
  MT5BrowseBtn.Top      := MT5Page.Edits[0].Top;
  MT5BrowseBtn.OnClick  := @MT5BrowseBtnClick;

  // ══ Page 3: MT5 password (masked) ═══════════════════════════════════════
  MT5PassPage := CreateInputQueryPage(
    MT5Page.ID,
    'MetaTrader 5 — Password',
    'Your password is stored in .env on this machine only, protected by Windows file permissions. It is never sent to Apex Quant Trader servers.',
    ''
  );
  MT5PassPage.Add('MT5 account password:', True);   // True = masked input

  // ══ Page 4: Gateway connection ══════════════════════════════════════════
  ConnPage := CreateInputQueryPage(
    MT5PassPage.ID,
    'Gateway Connection',
    'Configure how this engine connects to the Apex Quant Trader cloud gateway.',
    ''
  );
  ConnPage.Add('Gateway WebSocket URL:', False);
  ConnPage.Add('Engine ID (unique name for this machine):', False);
  ConnPage.Values[0] := 'wss://gateway.apexquanttrader.io/engine';
  ConnPage.Values[1] := 'engine-' + GetEnv('COMPUTERNAME') + '-01';

  // ══ Page 5: Symbols ══════════════════════════════════════════════════════
  SymbolsPage := CreateInputQueryPage(
    ConnPage.ID,
    'Trading Symbols',
    'Enter the symbols you are licensed to trade. Separate multiple symbols with commas.',
    ''
  );
  SymbolsPage.Add('Symbols (e.g. XAUUSD,EURUSD,GBPUSD):', False);
  SymbolsPage.Values[0] := 'XAUUSD';

  // ══ Page 6: Trading mode (radio buttons) ════════════════════════════════
  ModePage := CreateInputOptionPage(
    SymbolsPage.ID,
    'Trading Mode',
    'Choose how this engine handles trade signals.',
    '',
    True,    // Exclusive = radio button group
    False    // Not a listbox (larger radio-button style)
  );
  ModePage.Add(
    'Paper mode  —  signals are processed and recorded but no real orders are placed  (recommended for first run)');
  ModePage.Add(
    'Live mode  —  real orders are placed on your MT5 account  (real money at risk)');
  ModePage.Values[0] := True;   // Paper selected by default

  // ══ Page 7: Risk settings ════════════════════════════════════════════════
  RiskPage := CreateInputQueryPage(
    ModePage.ID,
    'Risk Settings',
    'Set your trade risk limits. Press Next to accept the defaults shown.',
    ''
  );
  RiskPage.Add('Max daily loss %  (engine pauses after this loss):', False);
  RiskPage.Add('Max losing streak  (consecutive losing trades before pause):', False);
  RiskPage.Add('Max lot size  (hard cap per order):', False);
  RiskPage.Add('Min risk/reward ratio  (signals below this R:R are skipped):', False);
  RiskPage.Add('Spread-to-stop ratio threshold  (0.35 = reject if spread > 35% of SL):', False);
  RiskPage.Values[0] := '2.5';
  RiskPage.Values[1] := '3';
  RiskPage.Values[2] := '100.0';
  RiskPage.Values[3] := '1.0';
  RiskPage.Values[4] := '0.35';
end;


// ---------------------------------------------------------------------------
// Validate each page before allowing the user to proceed
// ---------------------------------------------------------------------------
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  // ── Activation key: must start TR- and be ≥ 24 chars ───────────────────
  if CurPageID = ActivationKeyPage.ID then begin
    if (Pos('TR-', TrimStr(ActivationKeyPage.Values[0])) <> 1) or
       (Length(TrimStr(ActivationKeyPage.Values[0])) < 24) then begin
      MsgBox(
        'Please enter a valid activation key.' + #13#10 +
        'Keys start with "TR-" and are at least 24 characters.' + #13#10 + #13#10 +
        'Your key was included in your purchase confirmation email.',
        mbError, MB_OK
      );
      Result := False;
      Exit;
    end;
  end;

  // ── MT5 path: warn if file does not exist (non-blocking) ───────────────
  if CurPageID = MT5Page.ID then begin
    if (TrimStr(MT5Page.Values[0]) <> '') and
       not FileExists(TrimStr(MT5Page.Values[0])) then begin
      if MsgBox(
        'The path entered does not exist:' + #13#10 +
        TrimStr(MT5Page.Values[0]) + #13#10 + #13#10 +
        'Continue anyway? You can fix this in config.yaml later.',
        mbConfirmation, MB_YESNO
      ) = IDNO then begin
        Result := False;
        Exit;
      end;
    end;
  end;

  // ── Live mode: require explicit confirmation ────────────────────────────
  if CurPageID = ModePage.ID then begin
    if ModePage.Values[1] and not LiveModeConfirmed then begin
      if MsgBox(
        'LIVE MODE — REAL MONEY WARNING' + #13#10 + #13#10 +
        'In Live mode this engine places real orders on your MT5 account.' + #13#10 +
        'You can lose real money. Ensure:' + #13#10 + #13#10 +
        '  ' + #183 + ' Your MT5 account has sufficient margin' + #13#10 +
        '  ' + #183 + ' Your risk settings on the next page are correct' + #13#10 +
        '  ' + #183 + ' You understand the risks of automated trading' + #13#10 + #13#10 +
        'Continue in Live mode?',
        mbConfirmation, MB_YESNO
      ) = IDNO then begin
        ModePage.Values[0] := True;   // Revert to paper
        Result := False;
        Exit;
      end;
      LiveModeConfirmed := True;
    end;
  end;
end;


// ---------------------------------------------------------------------------
// After all files are installed: write config + .env + optional service
// ---------------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: String;
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then Exit;

  ConfigFile := ExpandConstant('{app}\config.yaml');

  // Write config.yaml (contains all settings including secrets)
  if not SaveStringToFile(ConfigFile, BuildConfigYaml, False) then
    MsgBox(
      'Warning: could not write config.yaml.' + #13#10 +
      'Use the "Reconfigure Apex Quant Trader" shortcut after install.',
      mbError, MB_OK
    )
  else
    // Restrict to current user so other Windows accounts cannot read credentials
    ProtectFile(ConfigFile);

  // Install Windows service if selected
  if IsTaskSelected('installservice') then
    RunServiceInstall;
end;


// ---------------------------------------------------------------------------
// Customise the Finish page message
// ---------------------------------------------------------------------------
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption :=
      'Apex Quant Trader has been installed successfully.' + #13#10 + #13#10 +
      'Click Finish to open the Apex Quant Trader dashboard in your browser.' + #13#10 + #13#10 +
      'Tip: use the "Reconfigure Apex Quant Trader" Start Menu shortcut' + #13#10 +
      'at any time to update your settings.';
end;
