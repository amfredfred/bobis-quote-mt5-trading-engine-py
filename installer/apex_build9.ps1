Set-StrictMode -Off
$ErrorActionPreference = "Stop"
$EngineDir = "C:\Workspace\Bots\trade-relay\execution-engine"

Write-Host "-- Stopping service..." -ForegroundColor Yellow
& sc.exe stop "apex-quant-trader-agent" 2>$null
Start-Sleep -Seconds 2

Write-Host "-- Killing any lingering process..." -ForegroundColor Yellow
Stop-Process -Name "apex-quant-trader-agent" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

Write-Host "-- Running build pipeline..." -ForegroundColor Cyan
& powershell.exe -ExecutionPolicy Bypass -File "$EngineDir\installer\build.ps1" -Clean

Write-Host "-- Restarting service..." -ForegroundColor Yellow
& sc.exe start "apex-quant-trader-agent" 2>$null

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Read-Host "Press Enter to close"
