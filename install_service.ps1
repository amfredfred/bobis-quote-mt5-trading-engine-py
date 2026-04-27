#!/usr/bin/env powershell
<#
.SYNOPSIS
    Install/uninstall Execution Engine as a Windows service using NSSM

.DESCRIPTION
    Registers the Execution Engine as a Windows service that starts automatically
    and restarts on failure. Requires administrator privileges.

.PARAMETER Action
    'install' (default) or 'uninstall' the service

.EXAMPLE
    # Install as administrator
    powershell -ExecutionPolicy Bypass -File install_service.ps1

    # Uninstall
    powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action uninstall

.NOTES
    Must be run as Administrator
    NSSM (Non-Sucking Service Manager) is automatically downloaded if missing
#>

param(
    [string]$Action = "install"
)

$ServiceName = "ExecutionEngine"
$EngineDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe   = Join-Path $EngineDir "venv\Scripts\python.exe"
$EngineModule = "src"
$NssmExe     = Join-Path $EngineDir "nssm\nssm-2.24\win64\nssm.exe"
$LogDir      = Join-Path $EngineDir "logs"

function Remove-ExistingService {
    $status = & $NssmExe status $ServiceName 2>$null
    if ($status) {
        Write-Host "Stopping and removing $ServiceName..."
        & $NssmExe stop   $ServiceName confirm 2>$null | Out-Null
        & $NssmExe remove $ServiceName confirm 2>$null | Out-Null
        Start-Sleep -Seconds 2
        Write-Host "$ServiceName removed."
    } else {
        Write-Host "$ServiceName is not installed -- nothing to remove."
    }
}

if ($Action -eq "uninstall") {
    Remove-ExistingService
    exit 0
}

if ($Action -ne "install") {
    Write-Error "Unknown action '$Action'. Use 'install' or 'uninstall'."
    exit 1
}

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python not found: $PythonExe`nRun: python -m venv venv (from $EngineDir)"
    exit 1
}

if (-not (Test-Path "$EngineDir\.env")) {
    Write-Error ".env file not found: $EngineDir\.env`nCopy from .env.example and fill in MT5 credentials"
    exit 1
}

if (-not (Test-Path $NssmExe)) {
    Write-Host "Downloading NSSM..."
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile "$EngineDir\nssm.zip"
    Expand-Archive "$EngineDir\nssm.zip" -DestinationPath "$EngineDir\nssm" -Force
    Remove-Item "$EngineDir\nssm.zip"
}

Remove-ExistingService

Write-Host "Installing $ServiceName..."
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

& $NssmExe install $ServiceName $PythonExe "-m" "$EngineModule"
& $NssmExe set $ServiceName AppDirectory   $EngineDir
& $NssmExe set $ServiceName AppStdout      "$LogDir\service_stdout.log"
& $NssmExe set $ServiceName AppStderr      "$LogDir\service_stderr.log"
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateBytes 10485760
& $NssmExe set $ServiceName Start          SERVICE_AUTO_START
& $NssmExe set $ServiceName DisplayName    "Execution Engine"
& $NssmExe set $ServiceName Description    "Event-driven trade execution engine for MetaTrader 5"

sc.exe failure $ServiceName reset= 60 actions= restart/5000/restart/10000/restart/30000 | Out-Null

Write-Host "Starting $ServiceName..."
& $NssmExe start $ServiceName
Start-Sleep -Seconds 4
& $NssmExe status $ServiceName

Write-Host ""
Write-Host "✓ Service installed and started!"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  View logs:    Get-Content '$LogDir\service_stderr.log' -Tail 50 -Wait"
Write-Host "  Check status: & nssm status $ServiceName"
Write-Host "  Stop service: & nssm stop $ServiceName"
Write-Host "  Start service: & nssm start $ServiceName"
Write-Host "  Uninstall:    powershell -File install_service.ps1 -Action uninstall"
Write-Host ""
Write-Host "Monitor in Event Viewer under: Windows Logs > Application"