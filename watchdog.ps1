# watchdog.ps1 — Health-checks the AQ Agent scheduled task and force-restarts
# it if the process has died or gone silent, independent of Task Scheduler's
# own -RestartCount cap (which only fires on a *failure* exit code, and gives
# up permanently after 10 tries within an hour).
#
# "Healthy" = the agent process is alive AND its local UI-bridge WebSocket
# (port 8080) is accepting connections. A process that's alive but hung
# (e.g. stuck on a dead MT5 COM call) will fail the port check and get
# force-restarted just the same as a crashed one.
#
# Registered as its own scheduled task by install.ps1, repeating every 5
# minutes indefinitely — not tied to the agent task's own restart budget.

$TaskName   = "AQ Agent"
$TaskFolder = "\Apex Quantel\"
$EngineDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$HealthPort = 8080
$LogFile    = Join-Path $EngineDir "logs\watchdog.log"

New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
if ((Test-Path -LiteralPath $LogFile) -and (Get-Item $LogFile).Length -gt 5MB) {
    Remove-Item -LiteralPath $LogFile -Force
}

function Log([string]$msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Add-Content -LiteralPath $LogFile
}

$escapedDir = [regex]::Escape($EngineDir)
$proc = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $escapedDir -and
    ($_.Name -like "apex-quant*" -or $_.Name -like "aq-agent*" -or $_.Name -like "pythonw*" -or $_.Name -like "python*")
}

$portOpen = [bool](Get-NetTCPConnection -LocalPort $HealthPort -State Listen -ErrorAction SilentlyContinue)

if ($proc -and $portOpen) {
    exit 0
}

Log "UNHEALTHY: process_running=$([bool]$proc) port_${HealthPort}_listening=$portOpen -- restarting task"

if ($proc) {
    $proc | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

try {
    Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder -ErrorAction Stop
    Log "Restart triggered via Start-ScheduledTask"
} catch {
    Log "ERROR: Start-ScheduledTask failed: $_"
}
