# watchdog.ps1 — Health-checks the AQ Agent scheduled task and force-restarts
# it if the process has died or gone silent, independent of Task Scheduler's
# own -RestartCount cap (which only fires on a *failure* exit code, and gives
# up permanently after 10 tries within an hour).
#
# "Healthy" = the agent process is alive AND its local UI-bridge WebSocket
# (monitoring_port) is accepting connections. A process that's alive but hung
# (e.g. stuck on a dead MT5 COM call) will fail the port check and get
# force-restarted just the same as a crashed one.
#
# Registered as its own scheduled task by install.ps1, repeating every 5
# minutes indefinitely — not tied to the agent task's own restart budget.
#
# -Broker: empty = today's single-instance behavior, unchanged. Set (via
# install.ps1 -Broker, forwarded through watchdog-launcher.vbs) to only
# check/restart that broker's own task — never a sibling's, even though
# they share this same checkout and $EngineDir.

param(
    [string]$Broker = ""
)

$BrokerSuffix = if ($Broker) { " ($Broker)" } else { "" }
$TaskName   = "AQ Agent$BrokerSuffix"
$TaskFolder = "\Apex Quantel\"
$EngineDir  = Split-Path -Parent $MyInvocation.MyCommand.Path

# Same fixed known-broker ports as config/settings.py's
# _default_monitoring_port — kept in sync manually since one lives in
# Python, the other in PowerShell. Falls back to the historical single-
# instance default (8080) for no broker or an unlisted one; an unlisted
# broker's actual port (deterministically hashed in Python) isn't
# reproducible here, so set engine.monitoring_port explicitly in that
# broker's config for the watchdog to check the right port.
$KnownBrokerPorts = @{ fbs = 8091; exness = 8092; fundednext = 8093; deriv = 8094 }
$HealthPort = if ($Broker -and $KnownBrokerPorts.ContainsKey($Broker.ToLower())) {
    $KnownBrokerPorts[$Broker.ToLower()]
} else {
    8080
}

$LogFile = if ($Broker) { Join-Path $EngineDir "logs\$Broker\watchdog.log" } else { Join-Path $EngineDir "logs\watchdog.log" }

New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
if ((Test-Path -LiteralPath $LogFile) -and (Get-Item $LogFile).Length -gt 5MB) {
    Remove-Item -LiteralPath $LogFile -Force
}

function Log([string]$msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Add-Content -LiteralPath $LogFile
}

$escapedDir = [regex]::Escape($EngineDir)
$escapedBrokerFlag = if ($Broker) { [regex]::Escape("--broker=$Broker") } else { $null }
$proc = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $escapedDir -and
    ($_.Name -like "apex-quant*" -or $_.Name -like "aq-agent*" -or $_.Name -like "pythonw*" -or $_.Name -like "python*") -and
    (-not $escapedBrokerFlag -or $_.CommandLine -match $escapedBrokerFlag)
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
