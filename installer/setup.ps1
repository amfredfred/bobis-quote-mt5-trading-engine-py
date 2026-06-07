# installer\setup.ps1 — Apex Quant Trader first-run setup wizard
#
# Called automatically by the Inno Setup installer after file copy.
# Can also be run standalone for reconfiguration:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Flags:
#   -Unattended    Skip interactive prompts; read answers from setup-answers.ps1
#   -SkipService   Skip Windows service install (useful for dev / Docker)
#   -InstallDir    Override install directory (default: parent of this script)

param(
    [switch]$Unattended,
    [switch]$SkipService,
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
if ($InstallDir -eq "") {
    # When run from Inno Setup Run section: {app} is the install root and
    # setup.ps1 is placed there directly.  When run from the repo for dev
    # the script is in installer\ so go up one level.
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (Test-Path (Join-Path $ScriptDir "apex-quant-trader-agent\apex-quant-trader-agent.exe")) {
        $InstallDir = $ScriptDir
    } elseif (Test-Path (Join-Path (Split-Path -Parent $ScriptDir) "dist\apex-quant-trader-agent\apex-quant-trader-agent.exe")) {
        $InstallDir = Split-Path -Parent $ScriptDir
    } else {
        $InstallDir = $ScriptDir
    }
}

$EngineExe      = Join-Path $InstallDir "apex-quant-trader-agent\apex-quant-trader-agent.exe"
$ConfigTemplate = Join-Path $InstallDir "apex-quant-trader-agent\config.example.yaml"
$ConfigOut      = Join-Path $InstallDir "config.yaml"
$DataDir        = Join-Path $InstallDir "data"
$LogsDir        = Join-Path $InstallDir "logs"
$ServiceScript  = Join-Path $InstallDir "install_service.ps1"

# Unattended answers file (optional)
$AnswersFile = Join-Path $InstallDir "setup-answers.ps1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Banner([string]$text) {
    Write-Host ""
    Write-Host ("=" * 50) -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ("=" * 50) -ForegroundColor Cyan
    Write-Host ""
}

function Write-Step([int]$n, [int]$total, [string]$title) {
    Write-Host ""
    Write-Host "  [$n/$total] $title" -ForegroundColor Yellow
    Write-Host ("  " + ("-" * 46)) -ForegroundColor DarkGray
}

function Ask([string]$Prompt, [string]$Default = "", [switch]$Secret) {
    if ($Unattended) { return $Default }
    $hint = if ($Default) { " [$Default]" } else { "" }
    $full = "  $Prompt$hint`: "
    if ($Secret) {
        $sec = Read-Host -Prompt $full -AsSecureString
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        return if ($plain) { $plain } else { $Default }
    }
    $answer = Read-Host -Prompt $full
    return if ($answer.Trim()) { $answer.Trim() } else { $Default }
}

function AskYN([string]$Prompt, [bool]$DefaultYes = $true) {
    if ($Unattended) { return $DefaultYes }
    $hint = if ($DefaultYes) { "Y/n" } else { "y/N" }
    $answer = Read-Host -Prompt "  $Prompt [$hint]"
    if (-not $answer.Trim()) { return $DefaultYes }
    return $answer.Trim() -match '^[Yy]'
}

# Simple YAML value setter — replaces "  key: oldvalue" lines in a string.
# Only handles top-level and single-indent keys.  The template is simple enough
# that a regex approach is fine; no full YAML parser needed.
function Set-YamlValue([string]$yaml, [string]$key, [string]$value, [int]$indent = 0) {
    $pad   = " " * $indent
    $esc   = [regex]::Escape($key)
    # Quote values that contain special YAML chars
    $safe = $value
    if ($value -match '[:{}[\]|>&*!,#]' -or $value -match '^\s' -or $value -match '\s$') {
        $safe = '"' + $value.Replace('"', '\"') + '"'
    }
    $pattern     = "(?m)^($pad$esc\s*:\s*).*$"
    $replacement = "`${1}$safe"
    if ([regex]::IsMatch($yaml, $pattern)) {
        return [regex]::Replace($yaml, $pattern, $replacement)
    }
    # Key not found — append it
    return $yaml + "`n$pad$key: $safe"
}

function Set-YamlList([string]$yaml, [string]$key, [string[]]$items, [int]$indent = 0) {
    $pad      = " " * $indent
    $esc      = [regex]::Escape($key)
    $listBody = ($items | ForEach-Object { "$pad  - $_" }) -join "`n"
    $newBlock = "$pad$key:`n$listBody"
    # Replace existing key + its list items (multiline)
    $pattern = "(?m)^$pad$esc\s*:(\s*\n(?:$pad  -[^\n]*\n?)*)"
    if ([regex]::IsMatch($yaml, $pattern)) {
        return [regex]::Replace($yaml, $pattern, $newBlock + "`n")
    }
    return $yaml + "`n$newBlock"
}

# Write an .env file entry (create or update existing line)
function Set-EnvValue([string]$envContent, [string]$key, [string]$value) {
    $esc  = [regex]::Escape($key)
    $line = "$key=$value"
    if ($envContent -match "(?m)^$esc=") {
        return [regex]::Replace($envContent, "(?m)^$esc=.*$", $line)
    }
    return $envContent.TrimEnd() + "`n$line`n"
}

# Restrict a file to the current user only (equivalent to chmod 600)
function Protect-File([string]$path) {
    try {
        $acl  = Get-Acl $path
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
            "FullControl",
            "Allow"
        )
        $acl.AddAccessRule($rule)
        Set-Acl $path $acl
    } catch {
        Write-Warning "Could not restrict .env file permissions: $_"
    }
}

# ---------------------------------------------------------------------------
# MT5 discovery (4.4)
# ---------------------------------------------------------------------------
function Find-MT5Path {
    # Known standard install locations
    $candidates = @(
        "C:\Program Files\MetaTrader 5\terminal64.exe",
        "C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
        "C:\Program Files\FBS MetaTrader 5\terminal64.exe",
        "C:\Program Files\Exness MetaTrader 5\terminal64.exe",
        "C:\Program Files\ICMarkets MetaTrader 5\terminal64.exe",
        "C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe",
        "C:\Program Files\FTMO MetaTrader 5\terminal64.exe",
        "C:\Program Files\ThinkMarkets MetaTrader 5\terminal64.exe",
        "C:\Program Files\XM Global MetaTrader 5\terminal64.exe",
        "C:\Program Files\Oanda MetaTrader 5\terminal64.exe",
        "C:\Program Files\FXCM MetaTrader 5\terminal64.exe",
        "C:\Program Files\HotForex MetaTrader 5\terminal64.exe"
    )

    # Scan MetaQuotes Terminal roaming folders (origin.txt contains the exe path)
    $mqBase = Join-Path $env:APPDATA "MetaQuotes\Terminal"
    if (Test-Path $mqBase) {
        Get-ChildItem $mqBase -Directory | ForEach-Object {
            $origin = Join-Path $_.FullName "origin.txt"
            if (Test-Path $origin) {
                $p = (Get-Content $origin -Raw).Trim()
                if ($p -and $p -ne "" -and (Test-Path $p)) {
                    $candidates += $p
                }
            }
        }
    }

    # Registry search
    $regPaths = @(
        "HKLM:\SOFTWARE\MetaQuotes Software Corp\MetaTrader 5",
        "HKCU:\SOFTWARE\MetaQuotes Software Corp\MetaTrader 5"
    )
    foreach ($rp in $regPaths) {
        try {
            $val = Get-ItemProperty -Path $rp -Name "Path" -ErrorAction SilentlyContinue
            if ($val -and $val.Path) {
                $exe = Join-Path $val.Path "terminal64.exe"
                if (Test-Path $exe) { $candidates += $exe }
            }
        } catch {}
    }

    # Return first existing path (deduplicated)
    $seen = @{}
    foreach ($c in $candidates) {
        $norm = $c.ToLower()
        if (-not $seen[$norm] -and (Test-Path $c)) {
            $seen[$norm] = $true
            return $c
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Load unattended answers if present
# ---------------------------------------------------------------------------
$ua = @{}
if ($Unattended -and (Test-Path $AnswersFile)) {
    Write-Host "  Loading unattended answers from: $AnswersFile" -ForegroundColor DarkGray
    . $AnswersFile   # dot-source; expected to populate $ua hashtable
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Banner "Apex Quant Trader — Setup Wizard"

$VersionFile = Join-Path $InstallDir "apex-quant-trader-agent\version.txt"
if (-not (Test-Path $VersionFile)) {
    $VersionFile = Join-Path $InstallDir "version.txt"
}
if (Test-Path $VersionFile) {
    $ver = (Get-Content $VersionFile -Raw).Trim()
    Write-Host "  Version: $ver" -ForegroundColor DarkGray
}
Write-Host "  Install: $InstallDir"
Write-Host ""
Write-Host "  This wizard configures the engine for first-time use."
Write-Host "  All settings can be changed later by re-running setup.ps1."
Write-Host ""

# ---------------------------------------------------------------------------
# STEP 1 — Activation key
# ---------------------------------------------------------------------------
Write-Step 1 9 "Activation key"
Write-Host "  Your activation key was emailed to you after purchase."
Write-Host "  Format: TR-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
Write-Host ""

$ActivationKey = ""
while ($true) {
    $ActivationKey = Ask "Activation key" ($ua["ActivationKey"] ?? "")
    if ($ActivationKey -match '^TR-[A-Za-z0-9_\-]{20,}$') { break }
    if ($Unattended) {
        Write-Warning "Unattended mode: invalid or missing activation key — continuing anyway"
        break
    }
    Write-Host "  Invalid format. Key must start with TR- and be at least 24 characters." -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# STEP 2 — MT5 discovery
# ---------------------------------------------------------------------------
Write-Step 2 9 "MetaTrader 5"

$Mt5Path   = ""
$Mt5Login  = ""
$Mt5Server = ""
$Mt5Magic  = "8858"

$discovered = Find-MT5Path
if ($discovered) {
    Write-Host "  Found MetaTrader 5 at: $discovered" -ForegroundColor Green
    $useit = AskYN "Use this path?" $true
    $Mt5Path = if ($useit) { $discovered } else { Ask "MT5 terminal64.exe path" "" }
} else {
    Write-Host "  MetaTrader 5 was not found in standard locations." -ForegroundColor Yellow
    $Mt5Path = Ask "MT5 terminal64.exe path (leave blank to configure later)"
}

$Mt5Login  = Ask "MT5 account number (login)"  ($ua["Mt5Login"]  ?? "")
$Mt5Server = Ask "MT5 server name"             ($ua["Mt5Server"] ?? "")
$Mt5Magic  = Ask "Magic number for this engine" ($ua["Mt5Magic"] ?? "8858")

Write-Host ""
Write-Host "  MT5 password will be stored in config.yaml (permissions restricted to current user)." -ForegroundColor DarkGray
$Mt5Password = Ask "MT5 account password" "" -Secret

# ---------------------------------------------------------------------------
# STEP 3 — Gateway URL
# ---------------------------------------------------------------------------
Write-Step 3 9 "Gateway connection"

$GatewayUrl = Ask "Gateway WebSocket URL" ($ua["GatewayUrl"] ?? "wss://gateway.apexquanttrader.io/engine")
$EngineId   = Ask "Engine ID (unique name for this machine)" ($ua["EngineId"] ?? "engine-$(hostname)-01")

# ---------------------------------------------------------------------------
# STEP 4 — Symbols
# ---------------------------------------------------------------------------
Write-Step 4 9 "Trading symbols"
Write-Host "  Enter the symbols you are licensed to trade."
Write-Host "  Comma-separated, e.g.: XAUUSD,EURUSD,GBPUSD"
Write-Host ""

$SymbolsRaw = Ask "Symbols" ($ua["Symbols"] ?? "XAUUSD")
$Symbols = ($SymbolsRaw -split "[,\s]+") |
    Where-Object { $_ -ne "" } |
    ForEach-Object { $_.ToUpper().Trim("/").Trim() } |
    Select-Object -Unique

Write-Host "  Configured: $($Symbols -join ', ')" -ForegroundColor Green

# ---------------------------------------------------------------------------
# STEP 5 — Mode selection
# ---------------------------------------------------------------------------
Write-Step 5 9 "Trading mode"
Write-Host ""
Write-Host "  [1] Paper mode  — Records trades without sending real orders (safe)"
Write-Host "  [2] Live mode   — Sends real orders to your MT5 account (real money)"
Write-Host ""

$ModeChoice = "1"
if ($Unattended) {
    $ModeChoice = ($ua["Mode"] ?? "1")
} else {
    while ($true) {
        $ModeChoice = Read-Host "  Select mode [1]"
        if (-not $ModeChoice.Trim()) { $ModeChoice = "1" }
        if ($ModeChoice -eq "1" -or $ModeChoice -eq "2") { break }
        Write-Host "  Enter 1 or 2." -ForegroundColor Red
    }
}

$LiveMode = $false
if ($ModeChoice -eq "2") {
    Write-Host ""
    Write-Host "  *** LIVE MODE — REAL MONEY WARNING ***" -ForegroundColor Red
    Write-Host "  Real orders will be placed on your MT5 account." -ForegroundColor Red
    Write-Host "  Ensure your risk settings are correct before proceeding." -ForegroundColor Red
    Write-Host ""
    if (-not $Unattended) {
        $confirm = Read-Host '  Type "LIVE" to confirm'
        if ($confirm.Trim() -eq "LIVE") {
            $LiveMode = $true
            Write-Host "  Live mode confirmed." -ForegroundColor Green
        } else {
            Write-Host "  Confirmation not received — defaulting to paper mode." -ForegroundColor Yellow
        }
    } else {
        $LiveMode = $true
    }
}

$ModeLabel = if ($LiveMode) { "live" } else { "paper" }
Write-Host "  Mode: $ModeLabel" -ForegroundColor $(if ($LiveMode) { "Red" } else { "Green" })

# ---------------------------------------------------------------------------
# STEP 6 — Risk configuration
# ---------------------------------------------------------------------------
Write-Step 6 9 "Risk configuration"
Write-Host "  Press Enter to accept defaults shown in [brackets]."
Write-Host ""

$MaxDailyLoss   = Ask "Max daily loss %"         ($ua["MaxDailyLoss"]   ?? "2.5")
$MaxLosingStreak = Ask "Max losing streak (trades)" ($ua["MaxLosingStreak"] ?? "3")
$MaxLotSize     = Ask "Max lot size"             ($ua["MaxLotSize"]    ?? "100.0")
$MinRR          = Ask "Min risk/reward ratio"    ($ua["MinRR"]         ?? "1.0")
$SlRatio        = Ask "SL spread ratio threshold" ($ua["SlRatio"]      ?? "0.35")

Write-Host ""
$advancedRisk = AskYN "Configure per-symbol SL ratio threshold?" $false
$SymbolSlRatios = @{}
if ($advancedRisk -and -not $Unattended) {
    foreach ($sym in $Symbols) {
        $ratio = Ask "  SL ratio for $sym" $SlRatio
        $SymbolSlRatios[$sym] = $ratio
    }
}

# ---------------------------------------------------------------------------
# STEP 7 — Write configuration files
# ---------------------------------------------------------------------------
Write-Step 7 9 "Writing configuration files"

# ── Load template ────────────────────────────────────────────────────────
if (-not (Test-Path $ConfigTemplate)) {
    # Fall back: find it relative to this script
    $ConfigTemplate = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\config.example.yaml"
    $ConfigTemplate = (Resolve-Path $ConfigTemplate -ErrorAction SilentlyContinue)?.Path
}
if (-not $ConfigTemplate -or -not (Test-Path $ConfigTemplate)) {
    Write-Warning "config.example.yaml not found — writing minimal config.yaml"
    $yaml = "# Apex Quant Trader config`n"
} else {
    $yaml = Get-Content $ConfigTemplate -Raw
}

# ── Gateway section ───────────────────────────────────────────────────────
$yaml = Set-YamlValue $yaml "ws_url"          $GatewayUrl      2
$yaml = Set-YamlValue $yaml "engine_id"       $EngineId        2
$yaml = Set-YamlValue $yaml "activation_key"  $ActivationKey   2
$yaml = Set-YamlList  $yaml "symbols"         $Symbols         2

# ── MT5 section ───────────────────────────────────────────────────────────
if ($Mt5Login)    { $yaml = Set-YamlValue $yaml "login"    $Mt5Login    2 }
if ($Mt5Server)   { $yaml = Set-YamlValue $yaml "server"   $Mt5Server   2 }
if ($Mt5Path)     { $yaml = Set-YamlValue $yaml "path"     $Mt5Path     2 }
if ($Mt5Magic)    { $yaml = Set-YamlValue $yaml "magic"    $Mt5Magic    2 }
if ($Mt5Password) { $yaml = Set-YamlValue $yaml "password" $Mt5Password 2 }

# ── Risk section ──────────────────────────────────────────────────────────
$yaml = Set-YamlValue $yaml "max_daily_loss_percent" $MaxDailyLoss    2
$yaml = Set-YamlValue $yaml "max_losing_streak"      $MaxLosingStreak 2
$yaml = Set-YamlValue $yaml "max_lot_size"           $MaxLotSize      2
$yaml = Set-YamlValue $yaml "min_rr_ratio"           $MinRR           2
$yaml = Set-YamlValue $yaml "sl_ratio_threshold"     $SlRatio         2

if ($SymbolSlRatios.Count -gt 0) {
    $symBlock = ""
    foreach ($k in $SymbolSlRatios.Keys) { $symBlock += "    $k`: $($SymbolSlRatios[$k])`n" }
    $esc     = [regex]::Escape("symbol_sl_ratio_threshold")
    $pattern = "(?ms)^  symbol_sl_ratio_threshold:.*?(?=^\S|\z)"
    if ([regex]::IsMatch($yaml, $pattern)) {
        $yaml = [regex]::Replace($yaml, $pattern, "  symbol_sl_ratio_threshold:`n$symBlock")
    }
}

# ── Write config.yaml and restrict permissions to current user ────────────
Set-Content -Path $ConfigOut -Value $yaml -Encoding utf8
Protect-File $ConfigOut
Write-Host "  config.yaml written + permissions restricted: $ConfigOut" -ForegroundColor Green

# ── Create data/ and logs/ directories ────────────────────────────────────
New-Item -ItemType Directory -Force -Path $DataDir  | Out-Null
New-Item -ItemType Directory -Force -Path $LogsDir  | Out-Null
Write-Host "  Directories: data\  logs\" -ForegroundColor Green

# ---------------------------------------------------------------------------
# STEP 8 — Windows service install
# ---------------------------------------------------------------------------
Write-Step 8 9 "Windows service"

$installSvc = $false
if (-not $SkipService) {
    $installSvc = AskYN "Install Apex Quant Trader as a Windows service?" $true
}

if ($installSvc) {
    if (-not (Test-Path $ServiceScript)) {
        $ServiceScript = Join-Path $InstallDir "install_service.ps1"
    }
    if (Test-Path $ServiceScript) {
        Write-Host "  Running install_service.ps1..."
        try {
            & powershell.exe -ExecutionPolicy Bypass -File $ServiceScript -Action install
            Write-Host "  Service installed successfully." -ForegroundColor Green
        } catch {
            Write-Warning "Service install encountered an error: $_"
            Write-Host "  You can install it manually:"
            Write-Host "    powershell -ExecutionPolicy Bypass -File install_service.ps1"
        }
    } else {
        Write-Warning "install_service.ps1 not found at $ServiceScript"
        Write-Host "  Install it manually after locating install_service.ps1."
    }
} else {
    Write-Host "  Skipping service install." -ForegroundColor DarkGray
    Write-Host "  To run manually:"
    Write-Host "    apex-quant-trader-agent\apex-quant-trader-agent.exe"
}

# ---------------------------------------------------------------------------
# STEP 9 — Post-activation
# ---------------------------------------------------------------------------
Write-Step 9 9 "Finishing up"

# Wait for service to report running (up to 10 s)
if ($installSvc) {
    Write-Host "  Waiting for service to start..." -ForegroundColor DarkGray
    $started = $false
    for ($i = 0; $i -lt 10; $i++) {
        $svc = Get-Service "apex-quant-trader-agent" -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq "Running") { $started = $true; break }
        Start-Sleep 1
    }
    if ($started) {
        Write-Host "  apex-quant-trader-agent service is RUNNING." -ForegroundColor Green
    } else {
        Write-Host "  Service status: $($(Get-Service 'apex-quant-trader-agent' -ErrorAction SilentlyContinue)?.Status ?? 'unknown')" -ForegroundColor Yellow
        Write-Host "  Check logs: Get-Content '$LogsDir\stderr.log' -Tail 50"
    }
}

# Open dashboard in browser
$dashboardUrl = "https://app.apexquanttrader.io"

Write-Host ""
Write-Host "  Opening dashboard: $dashboardUrl" -ForegroundColor Cyan
try {
    Start-Process $dashboardUrl
} catch {
    Write-Host "  Could not open browser automatically — visit: $dashboardUrl"
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host ("=" * 50) -ForegroundColor Cyan
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host ("=" * 50) -ForegroundColor Cyan
Write-Host ""
Write-Host "  Config : $ConfigOut"
Write-Host "  Data   : $DataDir"
Write-Host "  Logs   : $LogsDir"
Write-Host "  Mode   : $ModeLabel"
Write-Host ""
if ($installSvc) {
    Write-Host "  Service commands:"
    Write-Host "    Start   : Start-Service apex-quant-trader-agent"
    Write-Host "    Stop    : Stop-Service  apex-quant-trader-agent"
    Write-Host "    Logs    : Get-Content '$LogsDir\stderr.log' -Tail 50 -Wait"
} else {
    Write-Host "  To start engine:"
    Write-Host "    & '$EngineExe'"
}
Write-Host ""
Write-Host "  Re-run this wizard to update settings:"
Write-Host "    powershell -ExecutionPolicy Bypass -File '$($MyInvocation.MyCommand.Path)'"
Write-Host ""
