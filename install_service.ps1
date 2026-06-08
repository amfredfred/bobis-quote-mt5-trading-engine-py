# install_service.ps1 - Run as Administrator
#
# Installs, removes, or updates the Apex Quant Trader Windows service via NSSM.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install_service.ps1
#   powershell -ExecutionPolicy Bypass -File install_service.ps1 uninstall
#   powershell -ExecutionPolicy Bypass -File install_service.ps1 update
#   powershell -ExecutionPolicy Bypass -File install_service.ps1 -VenvName .venv
#
# Resolution order for the engine executable:
#   1. dist\apex-quant-trader-agent\apex-quant-trader-agent.exe  (packaged build - preferred)
#   2. <VenvName>\Scripts\execution-engine.exe        (dev venv install - fallback)

param(
    [ValidateSet("install", "uninstall", "update")]
    [string]$Action = "install",

    [string]$VenvName = "venv"
)

$ErrorActionPreference = "Stop"

$ServiceName   = "apex-quant-trader-agent"
$DisplayName   = "Apex Quant Trader"
$Description   = "Event-driven trade execution engine for MetaTrader 5 (Apex Quant Trader)"
$EngineDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$NssmExe       = Join-Path $EngineDir "nssm\nssm-2.24\win64\nssm.exe"
$LogDir        = Join-Path $EngineDir "logs"

# ---------------------------------------------------------------------------
# Resolve executable path
# ---------------------------------------------------------------------------
$PackagedExe = Join-Path $EngineDir "dist\apex-quant-trader-agent\apex-quant-trader-agent.exe"
$VenvExe     = Join-Path $EngineDir "$VenvName\Scripts\execution-engine.exe"

$AppExe = $null
if (Test-Path -LiteralPath $PackagedExe) {
    $AppExe = $PackagedExe
    Write-Host "  Mode: packaged build ($PackagedExe)" -ForegroundColor DarkGray
} elseif (Test-Path -LiteralPath $VenvExe) {
    $AppExe = $VenvExe
    Write-Host "  Mode: venv install ($VenvExe) [dev fallback]" -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------------------
# NSSM - auto-download if missing
# ---------------------------------------------------------------------------
function Ensure-Nssm {
    if (Test-Path -LiteralPath $NssmExe) { return }

    $zip = Join-Path $EngineDir "nssm.zip"
    if (-not (Test-Path -LiteralPath $zip)) {
        Write-Host "Downloading NSSM 2.24..."
        Invoke-WebRequest `
            -Uri "https://nssm.cc/release/nssm-2.24.zip" `
            -OutFile $zip `
            -UseBasicParsing
    }

    Expand-Archive $zip -DestinationPath (Join-Path $EngineDir "nssm") -Force

    if (-not (Test-Path -LiteralPath $NssmExe)) {
        Write-Error "NSSM executable not found after extraction: $NssmExe"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Service lifecycle helpers
# ---------------------------------------------------------------------------
function Stop-ServiceSafe {
    $svc = Get-Service $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Write-Host "  Stopping $ServiceName..."
        & $NssmExe stop $ServiceName confirm | Out-Null
    }
    for ($i = 0; $i -lt 20; $i++) {
        $svc = Get-Service $ServiceName -ErrorAction SilentlyContinue
        if (-not $svc -or $svc.Status -eq "Stopped") { return }
        Start-Sleep 1
    }
    Write-Warning "Service did not stop within 20 s - continuing anyway"
}

function Remove-ServiceSafe {
    Stop-ServiceSafe
    if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
        Write-Host "  Removing $ServiceName..."
        & $NssmExe remove $ServiceName confirm 2>$null | Out-Null
        sc.exe delete $ServiceName 2>$null | Out-Null
        Start-Sleep 2
    }
}

function Cleanup-Orphans {
    $escapedDir = [regex]::Escape($EngineDir)
    Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine -match $escapedDir -and
        ($_.Name -like "Apex Quant Trader*" -or $_.Name -like "execution-engine*" -or $_.Name -like "python*")
    } | ForEach-Object {
        Write-Host "  Stopping orphan PID $($_.ProcessId): $($_.Name)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Validate-Exe {
    if (-not $AppExe) {
        Write-Host ""
        Write-Host "ERROR: No engine executable found." -ForegroundColor Red
        Write-Host "  Expected (packaged): $PackagedExe" -ForegroundColor Red
        Write-Host "  Expected (dev venv): $VenvExe" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Build the packaged exe first:" -ForegroundColor Yellow
        Write-Host "    powershell -ExecutionPolicy Bypass -File installer\build.ps1" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Or install the dev venv:" -ForegroundColor Yellow
        Write-Host "    $VenvName\Scripts\pip install -e ." -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
}

function _install {
    Validate-Exe
    Remove-ServiceSafe
    Cleanup-Orphans

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    Write-Host ""
    Write-Host "  Installing service..."
    Write-Host "    Name    : $ServiceName"
    Write-Host "    Exe     : $AppExe"
    Write-Host "    CWD     : $EngineDir"

    & $NssmExe install $ServiceName $AppExe

    & $NssmExe set $ServiceName AppDirectory      $EngineDir
    & $NssmExe set $ServiceName AppParameters     "--headless"

    # Environment - prevent user-site packages from leaking into service env
    & $NssmExe set $ServiceName AppEnvironmentExtra `
        "PYTHONNOUSERSITE=1" `
        "PYTHONPATH=$EngineDir"

    # Logging
    & $NssmExe set $ServiceName AppStdout         (Join-Path $LogDir "stdout.log")
    & $NssmExe set $ServiceName AppStderr         (Join-Path $LogDir "stderr.log")
    & $NssmExe set $ServiceName AppRotateFiles    1
    & $NssmExe set $ServiceName AppRotateBytes    10485760   # 10 MB per file
    & $NssmExe set $ServiceName AppRotateOnline   1

    # Graceful stop (give engine time to flush trades + close MT5)
    & $NssmExe set $ServiceName AppStopMethodConsole  15000
    & $NssmExe set $ServiceName AppStopMethodWindow   15000
    & $NssmExe set $ServiceName AppStopMethodThreads  15000

    # Restart policy - 5 s backoff, 5-minute reset window
    & $NssmExe set $ServiceName AppThrottle       5000
    & $NssmExe set $ServiceName AppExit           Default Restart

    # Service metadata
    & $NssmExe set $ServiceName Start             SERVICE_AUTO_START
    & $NssmExe set $ServiceName DisplayName       $DisplayName
    & $NssmExe set $ServiceName Description       $Description

    # SC failure policy: restart after 5 s, 15 s, then stop retrying
    sc.exe failure $ServiceName reset= 300 actions= restart/5000/restart/15000/""/0 | Out-Null

    Write-Host ""
    Write-Host "  Starting service..."
    & $NssmExe start $ServiceName

    Start-Sleep 3
    & $NssmExe status $ServiceName

    Write-Host ""
    Write-Host "  Logs:"
    Write-Host "    Get-Content '$LogDir\stderr.log' -Tail 50 -Wait"
    Write-Host "    Get-Content '$LogDir\stdout.log' -Tail 50 -Wait"
}

function _update {
    Validate-Exe

    Write-Host "  Updating $ServiceName executable..."
    Stop-ServiceSafe

    # NSSM already points at the correct path; just restart
    Write-Host "  Restarting $ServiceName..."
    & $NssmExe start $ServiceName

    Start-Sleep 3
    & $NssmExe status $ServiceName
    Write-Host ""
    Write-Host "  Update complete."
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
Ensure-Nssm

if ($Action -eq "uninstall") {
    Remove-ServiceSafe
    Cleanup-Orphans
    Write-Host "Uninstall complete."
} elseif ($Action -eq "update") {
    _update
} else {
    _install
}
