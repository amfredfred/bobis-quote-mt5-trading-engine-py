# install.ps1
#
# One script for everything that needs registering as a Windows Task
# Scheduler task: broker instances (execution engines) and the dashboard
# hub they relay through.
#
# WHY TASK SCHEDULER INSTEAD OF A SERVICE (broker instances):
#   Windows services run in Session 0 (no desktop). The MT5 Python API
#   cannot attach to a terminal running in the user's session from Session 0,
#   so the service would launch an invisible MT5 that can never log in.
#   A scheduled task runs as the logged-in user in their own session -
#   MT5 is fully visible and the agent connects normally. The hub has no
#   such requirement (it never touches MT5) but uses Task Scheduler too,
#   for consistency with the rest of this deployment.
#
# Usage:
#   Single-instance (today's original default, no multi-broker involved):
#     powershell -ExecutionPolicy Bypass -File install.ps1
#     powershell -ExecutionPolicy Bypass -File install.ps1 uninstall
#     powershell -ExecutionPolicy Bypass -File install.ps1 update
#     powershell -ExecutionPolicy Bypass -File install.ps1 -VenvName .venv
#
#   One broker instance (run once per broker; each gets its own task name,
#   watchdog, monitoring port and storage_path via config.yaml's {profile}
#   template - see settings.py's _default_monitoring_port):
#     powershell -ExecutionPolicy Bypass -File install.ps1 -Broker fbs
#     powershell -ExecutionPolicy Bypass -File install.ps1 -Broker fbs uninstall
#
#   The dashboard hub every broker instance relays through (run once,
#   regardless of how many broker instances you run):
#     powershell -ExecutionPolicy Bypass -File install.ps1 -Hub
#     powershell -ExecutionPolicy Bypass -File install.ps1 -Hub uninstall
#
#   Everything at once (hub + fbs + exness + fundednext):
#     powershell -ExecutionPolicy Bypass -File install.ps1 -All
#     powershell -ExecutionPolicy Bypass -File install.ps1 -All uninstall

param(
    [ValidateSet("install", "uninstall", "update")]
    [string]$Action = "install",

    [string]$VenvName = "venv",

    # Empty = today's single-instance behavior, unchanged. Set to run
    # multiple per-broker instances from one checkout (see usage above).
    [string]$Broker = "",

    # Install/uninstall the dashboard hub instead of a broker instance.
    [switch]$Hub,

    # Install/uninstall the hub plus every known broker in one call.
    [switch]$All
)

$ErrorActionPreference = "Stop"
$EngineDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskFolder   = "\Apex Quantel\"
$KnownBrokers = @("fbs", "exness", "fundednext")

# ── Runs install/uninstall/update for exactly one identity: either a
#    broker instance ($broker non-empty, or "" for the legacy single-
#    instance mode) or the hub ($isHub). All the actual Task Scheduler
#    work lives in here so -All can just call this once per identity. ──
function Invoke-ForIdentity([string]$broker, [bool]$isHub, [string]$action) {
    $suffix = if ($broker) { " ($broker)" } else { "" }

    if ($isHub) {
        $taskName     = "Execution Hub"
        $description  = "Apex Quantel Execution Hub - relays every broker instance's dashboard telemetry/commands to one public endpoint"
        $hasWatchdog  = $false
    } else {
        $taskName     = "AQ Agent$suffix"
        $description  = "Apex Quantel AQ Agent - automated trade execution engine for MetaTrader 5"
        $hasWatchdog  = $true
        $watchdogName = "AQ Agent Watchdog$suffix"
        $watchdogDesc = "Restarts AQ Agent$suffix if it dies or hangs. Runs independently of the agent task's own -RestartCount cap."
        $watchdogScript   = Join-Path $EngineDir "watchdog.ps1"
        $watchdogLauncher = Join-Path $EngineDir "watchdog-launcher.vbs"
    }

    # ── Resolve executable ──────────────────────────────────────────────
    $venvPythonw = Join-Path $EngineDir "$VenvName\Scripts\pythonw.exe"
    $appExe = $null
    $appArgs = $null

    if ($isHub) {
        # The hub only ever ships as a venv/dev module target - it's not
        # part of the Inno Setup / PyInstaller packaged agent build.
        if (Test-Path -LiteralPath $venvPythonw) {
            $appExe = $venvPythonw
            $appArgs = "-m src.hub"
        }
    } else {
        # Installed via Inno Setup: files land directly under the install dir (no dist\ prefix)
        $installedExe = Join-Path $EngineDir "apex-quant-trader-agent\apex-quant-trader-agent.exe"
        # Dev / manual build: PyInstaller output is under dist\
        $packagedExe = Join-Path $EngineDir "dist\apex-quant-trader-agent\apex-quant-trader-agent.exe"

        if (Test-Path -LiteralPath $installedExe) {
            $appExe = $installedExe
            Write-Host "  Mode: installed (Program Files)" -ForegroundColor DarkGray
        } elseif (Test-Path -LiteralPath $packagedExe) {
            $appExe = $packagedExe
            Write-Host "  Mode: packaged build (dev)" -ForegroundColor DarkGray
        } elseif (Test-Path -LiteralPath $venvPythonw) {
            # Venv dev mode: launched via pythonw.exe (windowless), not the
            # aq-agent.exe console-script shim - that shim is a console-
            # subsystem binary, so Task Scheduler running it under an
            # interactive logon pops a visible cmd window, and closing that
            # window kills the process. pythonw.exe never allocates a
            # console in the first place.
            $appExe = $venvPythonw
            $appArgs = "-m src"
            Write-Host "  Mode: venv install (dev, windowless)" -ForegroundColor DarkYellow
        }

        if ($broker -and $appArgs) {
            $appArgs = "$appArgs --broker=$broker"
        } elseif ($broker) {
            $appArgs = "--broker=$broker"
        }
    }

    # ── Shared task helpers (closed over $taskName/$TaskFolder) ─────────
    function Stop-TaskNow([string]$name) {
        $t = Get-ScheduledTask -TaskName $name -TaskPath $TaskFolder -ErrorAction SilentlyContinue
        if ($t -and $t.State -eq "Running") {
            Write-Host "  Stopping running task..."
            Stop-ScheduledTask -TaskName $name -TaskPath $TaskFolder -ErrorAction SilentlyContinue
            Start-Sleep 3
        }
    }
    function Remove-TaskNow([string]$name) {
        Stop-TaskNow $name
        if (Get-ScheduledTask -TaskName $name -TaskPath $TaskFolder -ErrorAction SilentlyContinue) {
            Write-Host "  Removing existing task: $name"
            Unregister-ScheduledTask -TaskName $name -TaskPath $TaskFolder -Confirm:$false
        }
    }

    function Cleanup-Orphans {
        $escapedDir = [regex]::Escape($EngineDir)
        if ($isHub) {
            $matchExtra = "src\.hub"
        } else {
            # Without -Broker: today's exact behavior, matches anything
            # rooted in $EngineDir. With -Broker: also require
            # --broker=<name>, so this never touches a sibling broker's
            # still-running instance sharing the same checkout.
            $matchExtra = if ($broker) { [regex]::Escape("--broker=$broker") } else { $null }
        }
        Get-CimInstance Win32_Process | Where-Object {
            $_.ProcessId -ne $PID -and
            $_.CommandLine -and
            $_.CommandLine -match $escapedDir -and
            ($_.Name -like "apex-quant*" -or $_.Name -like "aq-agent*" -or $_.Name -like "pythonw*" -or $_.Name -like "python*") -and
            (-not $matchExtra -or $_.CommandLine -match $matchExtra)
        } | ForEach-Object {
            Write-Host "  Stopping orphan PID $($_.ProcessId): $($_.Name)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }

    function Register-Watchdog {
        # Trigger: at logon (90s after this user logs in, so the agent
        # task's own 30s-delayed first start has had time to happen), then
        # repeat every 5 minutes for as long as the session is up. This is
        # deliberately separate from the agent task's own -RestartCount:
        # that cap only fires on a *failure* exit code and permanently
        # gives up after 10 tries in an hour, whereas the watchdog
        # re-checks forever and also catches a process that exits cleanly
        # (code 0) without actually doing anything.
        $wTrigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        $wTrigger.Delay = "PT90S"
        # RepetitionDuration must be a valid XML duration -
        # [TimeSpan]::MaxValue overflows Task Scheduler's schema
        # (P99999999DT23H59M59S, rejected). 10 years covers "runs for
        # years" without hitting that ceiling.
        $wTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
            -RepetitionInterval (New-TimeSpan -Minutes 5) `
            -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

        # Launched via wscript.exe + a VBS shim, not powershell.exe
        # directly: powershell.exe's own -WindowStyle Hidden still briefly
        # flashes a console window on many Windows builds (the console
        # host allocates the window before PowerShell applies the style),
        # which is very visible on a task that fires every 5 minutes.
        # WScript.Shell.Run with window style 0 never allocates a visible
        # window at all. $broker is passed through as the launcher's own
        # WScript argument, forwarded to watchdog.ps1 as -Broker, so this
        # watchdog only ever checks/restarts its own broker's task.
        $wLauncherArgs = if ($broker) { "//B //Nologo `"$watchdogLauncher`" $broker" } else { "//B //Nologo `"$watchdogLauncher`"" }
        $wAction = New-ScheduledTaskAction `
            -Execute          "wscript.exe" `
            -Argument         $wLauncherArgs `
            -WorkingDirectory $EngineDir

        $wSettings = New-ScheduledTaskSettingsSet `
            -MultipleInstances  IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable

        $wPrincipal = New-ScheduledTaskPrincipal `
            -UserId    "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive `
            -RunLevel  Highest

        Register-ScheduledTask `
            -TaskName    $watchdogName `
            -TaskPath    $TaskFolder `
            -Action      $wAction `
            -Trigger     $wTrigger `
            -Settings    $wSettings `
            -Principal   $wPrincipal `
            -Description $watchdogDesc `
            -Force | Out-Null

        Start-ScheduledTask -TaskName $watchdogName -TaskPath $TaskFolder
        Write-Host "  Watchdog registered: checks every 5 min, force-restarts on failure" -ForegroundColor DarkGray
    }

    function Validate-Config {
        if ($isHub) { return }
        # Mirrors src/config/paths.py's locate_config_path() priority
        # order: %ProgramData%\Apex Quantel\config.yaml wins even over one
        # sitting next to the exe, so check that first to avoid a
        # misleading "found it" here while the app actually loads a
        # different (possibly stale) file.
        $programDataCfg = Join-Path $env:PROGRAMDATA "Apex Quantel\config.yaml"
        if (Test-Path -LiteralPath $programDataCfg) {
            Write-Host "  Config: $programDataCfg (takes priority over any local copy)" -ForegroundColor DarkGray
            return
        }
        $localCfg = Join-Path $EngineDir "config.yaml"
        if (Test-Path -LiteralPath $localCfg) {
            Write-Host "  Config: $localCfg" -ForegroundColor DarkGray
            return
        }
        Write-Host ""
        Write-Host "WARNING: config.yaml not found in ProgramData or $EngineDir" -ForegroundColor Yellow
        Write-Host "  The agent will fail to start until one is created." -ForegroundColor Yellow
    }

    function Do-Install {
        if (-not $appExe) {
            Write-Host ""
            Write-Host "ERROR: No executable found for $taskName." -ForegroundColor Red
            Write-Host "  Expected (dev venv): $venvPythonw" -ForegroundColor Red
            if (-not $isHub) {
                Write-Host "  Build the packaged exe:" -ForegroundColor Yellow
                Write-Host "    powershell -ExecutionPolicy Bypass -File installer\build.ps1" -ForegroundColor Yellow
            }
            Write-Host "  Or install the dev venv:" -ForegroundColor Yellow
            Write-Host "    $VenvName\Scripts\pip install -e ." -ForegroundColor Yellow
            exit 1
        }
        Validate-Config
        Remove-TaskNow $taskName
        Cleanup-Orphans

        Write-Host ""
        Write-Host "  Registering scheduled task..."
        Write-Host "    Task : $TaskFolder$taskName"
        Write-Host "    Exe  : $appExe $appArgs"
        Write-Host "    CWD  : $EngineDir"
        Write-Host "    User : $env:USERDOMAIN\$env:USERNAME"

        $taskAction = New-ScheduledTaskAction `
            -Execute          $appExe `
            -Argument         $appArgs `
            -WorkingDirectory $EngineDir

        $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        if (-not $isHub) {
            # 30s delay so MT5 can start first. The hub has nothing to
            # wait on - no MT5, and every instance's HubForwarder retries
            # with backoff regardless of start order.
            $trigger.Delay = "PT30S"
        }

        $settings = New-ScheduledTaskSettingsSet `
            -MultipleInstances      IgnoreNew `
            -ExecutionTimeLimit     ([TimeSpan]::Zero) `
            -RestartCount           10 `
            -RestartInterval        (New-TimeSpan -Minutes 1) `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable

        $principal = New-ScheduledTaskPrincipal `
            -UserId    "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive `
            -RunLevel  Highest

        Register-ScheduledTask `
            -TaskName    $taskName `
            -TaskPath    $TaskFolder `
            -Action      $taskAction `
            -Trigger     $trigger `
            -Settings    $settings `
            -Principal   $principal `
            -Description $description `
            -Force | Out-Null

        Write-Host ""
        Write-Host "  Starting task now..."
        Start-ScheduledTask -TaskName $taskName -TaskPath $TaskFolder

        Start-Sleep 3
        $state = (Get-ScheduledTask -TaskName $taskName -TaskPath $TaskFolder).State
        Write-Host "  Task state: $state" -ForegroundColor $(if ($state -eq "Running") { "Green" } else { "Yellow" })

        Write-Host ""
        if ($isHub) {
            Write-Host "  Execution Hub will start automatically at each login."
            Write-Host "  Internal (instance forwarders dial in): ws://127.0.0.1:8095  (HUB_INTERNAL_PORT to override)"
            Write-Host "  Public (dashboard connects):            ws://0.0.0.0:8080  (HUB_PUBLIC_PORT to override)"
        } else {
            Write-Host "  AQ Agent$suffix will start automatically 30 s after each login."
            if ($broker) {
                Write-Host "  Logs: logs\$broker\  Data: data\$broker\  (see config.yaml's {profile} template)"
            } else {
                Write-Host "  Logs: logs\ next to whichever config.yaml is loaded (same folder as the engine)"
            }
            Remove-TaskNow $watchdogName
            Register-Watchdog
        }
    }

    switch ($action) {
        "uninstall" {
            Remove-TaskNow $taskName
            if ($hasWatchdog) { Remove-TaskNow $watchdogName }
            Cleanup-Orphans
            Write-Host "Uninstalled: $taskName"
        }
        "update" { Do-Install }
        default  { Do-Install }
    }
}

# ── Entry point ───────────────────────────────────────────────────────────────
if ($All) {
    Write-Host ""
    Write-Host "=== $Action hub ===" -ForegroundColor Cyan
    Invoke-ForIdentity -broker "" -isHub $true -action $Action
    foreach ($b in $KnownBrokers) {
        Write-Host ""
        Write-Host "=== $Action broker: $b ===" -ForegroundColor Cyan
        Invoke-ForIdentity -broker $b -isHub $false -action $Action
    }
} elseif ($Hub) {
    Invoke-ForIdentity -broker "" -isHub $true -action $Action
} else {
    Invoke-ForIdentity -broker $Broker -isHub $false -action $Action
}
