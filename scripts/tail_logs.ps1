# tail_logs.ps1 — Tail the AQ Agent's most recent log file.
#
# Mirrors src/__main__.py's log location logic: packaged/frozen builds log to
# %ProgramData%\Apex Quantel\logs\, dev/venv runs log next to config.yaml.

$ProgramDataLogs = Join-Path $env:PROGRAMDATA "Apex Quantel\logs"
$LocalLogs       = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent) "logs"

$LogDir = if (Test-Path -LiteralPath $ProgramDataLogs) { $ProgramDataLogs }
          elseif (Test-Path -LiteralPath $LocalLogs)   { $LocalLogs }
          else { $null }

if (-not $LogDir) {
    Write-Host "No log directory found in either:"
    Write-Host "  $ProgramDataLogs"
    Write-Host "  $LocalLogs"
    exit 1
}

$Latest = Get-ChildItem -Path $LogDir -Filter "*.log" -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $Latest) {
    Write-Host "No .log files found in $LogDir"
    exit 1
}

Write-Host "Tailing $($Latest.FullName)"
Get-Content -Path $Latest.FullName -Tail 50 -Wait
