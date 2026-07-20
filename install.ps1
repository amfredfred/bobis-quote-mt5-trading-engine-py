# install.ps1
#
# Registers AQ Agent as a Windows Task Scheduler task that runs under your
# user account at logon.
#
# WHY TASK SCHEDULER INSTEAD OF A SERVICE:
#   Windows services run in Session 0 (no desktop). The MT5 Python API
#   cannot attach to a terminal running in the user's session from Session 0,
#   so the service would launch an invisible MT5 that can never log in.
#   A scheduled task runs as the logged-in user in their own session —
#   MT5 is fully visible and the agent connects normally.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 uninstall
#   powershell -ExecutionPolicy Bypass -File install.ps1 update
#   powershell -ExecutionPolicy Bypass -File install.ps1 -VenvName .venv

param(
    [ValidateSet("install", "uninstall", "update")]
    [string]$Action = "install",

    [string]$VenvName = "venv"
)

$ErrorActionPreference = "Stop"

$TaskName    = "AQ Agent"
$TaskFolder  = "\Apex Quantel\"
$Description = "Apex Quantel AQ Agent - automated trade execution engine for MetaTrader 5"
$EngineDir   = Split-Path -Parent $MyInvocation.MyCommand.Path

$WatchdogTaskName    = "AQ Agent Watchdog"
$WatchdogDescription = "Restarts AQ Agent if it dies or hangs. Runs independently of the agent task's own -RestartCount cap."
$WatchdogScript      = Join-Path $EngineDir "watchdog.ps1"
$WatchdogLauncher    = Join-Path $EngineDir "watchdog-launcher.vbs"

# ── Resolve executable ────────────────────────────────────────────────────────
# Installed via Inno Setup: files land directly under the install dir (no dist\ prefix)
$InstalledExe = Join-Path $EngineDir "apex-quant-trader-agent\apex-quant-trader-agent.exe"
# Dev / manual build: PyInstaller output is under dist\
$PackagedExe  = Join-Path $EngineDir "dist\apex-quant-trader-agent\apex-quant-trader-agent.exe"
# Venv dev mode: launched via pythonw.exe (windowless), not the aq-agent.exe
# console-script shim. That shim is a console-subsystem binary, so Task
# Scheduler running it under an interactive logon pops a visible cmd window -
# and closing that window kills the process. pythonw.exe never allocates a
# console, so there is no window to close in the first place, and stdout
# logging (which auto-colours only when attached to a real terminal) stays
# plain since there is no terminal at all. `-m src` is the exact equivalent
# of the `aq-agent = "src.__main__:main"` entry point in pyproject.toml.
$VenvPythonw = Join-Path $EngineDir "$VenvName\Scripts\pythonw.exe"

$AppExe  = $null
$AppArgs = $null
if (Test-Path -LiteralPath $InstalledExe) {
    $AppExe = $InstalledExe
    Write-Host "  Mode: installed (Program Files)" -ForegroundColor DarkGray
} elseif (Test-Path -LiteralPath $PackagedExe) {
    $AppExe = $PackagedExe
    Write-Host "  Mode: packaged build (dev)" -ForegroundColor DarkGray
} elseif (Test-Path -LiteralPath $VenvPythonw) {
    $AppExe  = $VenvPythonw
    $AppArgs = "-m src"
    Write-Host "  Mode: venv install (dev, windowless)" -ForegroundColor DarkYellow
}

# ── Helpers ───────────────────────────────────────────────────────────────────
function Stop-Task {
    $t = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder -ErrorAction SilentlyContinue
    if ($t -and $t.State -eq "Running") {
        Write-Host "  Stopping running task..."
        Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder -ErrorAction SilentlyContinue
        Start-Sleep 3
    }
}

function Remove-Task {
    Stop-Task
    if (Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder -ErrorAction SilentlyContinue) {
        Write-Host "  Removing existing task..."
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder -Confirm:$false
    }
}

function Remove-Watchdog {
    if (Get-ScheduledTask -TaskName $WatchdogTaskName -TaskPath $TaskFolder -ErrorAction SilentlyContinue) {
        Write-Host "  Removing existing watchdog task..."
        Unregister-ScheduledTask -TaskName $WatchdogTaskName -TaskPath $TaskFolder -Confirm:$false
    }
}

function Register-Watchdog {
    # Trigger: at logon (90s after this user logs in, so the agent task's own
    # 30s-delayed first start has had time to happen), then repeat every 5
    # minutes for as long as the session is up. This is deliberately separate
    # from the agent task's own -RestartCount: that cap only fires on a
    # *failure* exit code and permanently gives up after 10 tries in an hour,
    # whereas the watchdog re-checks forever and also catches a process that
    # exits cleanly (code 0) without actually doing anything.
    $wTrigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $wTrigger.Delay = "PT90S"
    # RepetitionDuration must be a valid XML duration - [TimeSpan]::MaxValue
    # overflows Task Scheduler's schema (P99999999DT23H59M59S, rejected).
    # 10 years covers "runs for years" without hitting that ceiling.
    $wTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

    # Launched via wscript.exe + a VBS shim, not powershell.exe directly:
    # powershell.exe's own -WindowStyle Hidden still briefly flashes a
    # console window on many Windows builds (the console host allocates the
    # window before PowerShell applies the style), which is very visible on
    # a task that fires every 5 minutes. WScript.Shell.Run with window
    # style 0 never allocates a visible window at all.
    $wAction = New-ScheduledTaskAction `
        -Execute          "wscript.exe" `
        -Argument         "//B //Nologo `"$WatchdogLauncher`"" `
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
        -TaskName    $WatchdogTaskName `
        -TaskPath    $TaskFolder `
        -Action      $wAction `
        -Trigger     $wTrigger `
        -Settings    $wSettings `
        -Principal   $wPrincipal `
        -Description $WatchdogDescription `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $WatchdogTaskName -TaskPath $TaskFolder
    Write-Host "  Watchdog registered: checks every 5 min, force-restarts on failure" -ForegroundColor DarkGray
}

function Cleanup-Orphans {
    $escapedDir = [regex]::Escape($EngineDir)
    Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine -match $escapedDir -and
        ($_.Name -like "apex-quant*" -or $_.Name -like "aq-agent*" -or $_.Name -like "pythonw*" -or $_.Name -like "python*")
    } | ForEach-Object {
        Write-Host "  Stopping orphan PID $($_.ProcessId): $($_.Name)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Validate-Exe {
    if (-not $AppExe) {
        Write-Host ""
        Write-Host "ERROR: No executable found." -ForegroundColor Red
        Write-Host "  Expected (packaged): $PackagedExe" -ForegroundColor Red
        Write-Host "  Expected (dev venv): $VenvPythonw" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Build the packaged exe:" -ForegroundColor Yellow
        Write-Host "    powershell -ExecutionPolicy Bypass -File installer\build.ps1" -ForegroundColor Yellow
        Write-Host "  Or install the dev venv:" -ForegroundColor Yellow
        Write-Host "    $VenvName\Scripts\pip install -e ." -ForegroundColor Yellow
        exit 1
    }
}

function Validate-Config {
    # Mirrors src/config/paths.py's locate_config_path() priority order:
    # %ProgramData%\Apex Quantel\config.yaml wins even over one sitting next
    # to the exe, so check that first to avoid a misleading "found it" here
    # while the app actually loads a different (possibly stale) file.
    $ProgramDataCfg = Join-Path $env:PROGRAMDATA "Apex Quantel\config.yaml"
    if (Test-Path -LiteralPath $ProgramDataCfg) {
        Write-Host "  Config: $ProgramDataCfg (takes priority over any local copy)" -ForegroundColor DarkGray
        return
    }
    $LocalCfg = Join-Path $EngineDir "config.yaml"
    if (Test-Path -LiteralPath $LocalCfg) {
        Write-Host "  Config: $LocalCfg" -ForegroundColor DarkGray
        return
    }
    Write-Host ""
    Write-Host "WARNING: config.yaml not found in ProgramData or $EngineDir" -ForegroundColor Yellow
    Write-Host "  The agent will fail to start until one is created." -ForegroundColor Yellow
}

# ── Install ───────────────────────────────────────────────────────────────────
function _install {
    Validate-Exe
    Validate-Config
    Remove-Task
    Cleanup-Orphans

    Write-Host ""
    Write-Host "  Registering scheduled task..."
    Write-Host "    Task : $TaskFolder$TaskName"
    Write-Host "    Exe  : $AppExe $AppArgs"
    Write-Host "    CWD  : $EngineDir"
    Write-Host "    User : $env:USERDOMAIN\$env:USERNAME"

    # Action: run the agent from the engine directory. src/__main__.py is
    # headless-only (no GUI code path exists in it) regardless of args, so
    # no --headless flag is needed here.
    $action = New-ScheduledTaskAction `
        -Execute         $AppExe `
        -Argument        $AppArgs `
        -WorkingDirectory $EngineDir

    # Trigger: at logon for this user, 30-second delay so MT5 can start first
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $trigger.Delay = "PT30S"

    # Settings: no execution time limit, restart up to 10x on failure
    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances      IgnoreNew `
        -ExecutionTimeLimit     ([TimeSpan]::Zero) `
        -RestartCount           10 `
        -RestartInterval        (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    # Principal: the logged-in user, highest available privilege
    $principal = New-ScheduledTaskPrincipal `
        -UserId    "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel  Highest

    Register-ScheduledTask `
        -TaskName    $TaskName `
        -TaskPath    $TaskFolder `
        -Action      $action `
        -Trigger     $trigger `
        -Settings    $settings `
        -Principal   $principal `
        -Description $Description `
        -Force | Out-Null

    Write-Host ""
    Write-Host "  Starting task now..."
    Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder

    Start-Sleep 3
    $state = (Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder).State
    Write-Host "  Task state: $state" -ForegroundColor $(if ($state -eq "Running") { "Green" } else { "Yellow" })

    Write-Host ""
    Write-Host "  AQ Agent will start automatically 30 s after each login."
    Write-Host "  Logs: logs\ next to whichever config.yaml is loaded (same folder as the engine)"

    Remove-Watchdog
    Register-Watchdog
}

# ── Update ────────────────────────────────────────────────────────────────────
function _update {
    Validate-Exe
    # Full re-register so the exe path is always current
    _install
}

# ── Entry point ───────────────────────────────────────────────────────────────
switch ($Action) {
    "uninstall" {
        Remove-Task
        Remove-Watchdog
        Cleanup-Orphans
        Write-Host "Uninstall complete."
    }
    "update"  { _update }
    default   { _install }
}
