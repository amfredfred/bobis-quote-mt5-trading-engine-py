# Quick Start Guide - Windows

Get the Execution Engine running as a background task in 5 minutes. This is
a headless service - there is no GUI, and it runs invisibly in the
background via Windows Task Scheduler (not a Windows Service or NSSM - MT5
needs an interactive desktop session, which Session-0 services don't have).

## Prerequisites

- Windows 10 or later
- Python 3.12+
- MetaTrader 5 terminal (installed, and running before the engine starts)

## Step 1: Clone Repository

```powershell
git clone https://github.com/amfredfred/bobis-quote-mt5-trading-engine-py.git
cd execution-engine
```

## Step 2: Setup Python Environment

```powershell
# Create virtual environment
python -m venv venv

# Activate it
venv\Scripts\activate

# Install dependencies (registers the venv\Scripts\execution-engine.exe entry point)
pip install -e .
```

## Step 3: Configure

```powershell
# Copy example configuration
Copy-Item config.example.yaml config.yaml

# Edit it
notepad config.yaml
```

**Required sections**: `mt5` (login/password/server/path), `risk`, `execution`,
`signal_engine` (ws_url + symbols), and `engine` (storage_path/log_level/
position_poll_interval/timezone). See `config.example.yaml` for the full,
commented reference. `mt5.password` can also come from the `MT5_PASSWORD`
environment variable instead of the file.

**Key risk settings** (set these before going live):
```yaml
risk:
  max_losing_streak: 4          # Your system's worst recorded consecutive losing streak
  max_daily_loss_percent: 5.0   # Daily loss budget as % of account equity
  sl_ratio_threshold: 0.34      # Max spread/SL ratio — lower = stricter
  min_rr_ratio: 1.0             # Minimum risk:reward ratio
```

## Step 4: Install as a Background Task

```powershell
cd execution-engine
powershell -ExecutionPolicy Bypass -File install.ps1
```

The script will:
- Register `\Apex Quantel\AQ Agent` as a Task Scheduler task
- Set it to start ~30 s after you log in (giving MT5 time to start)
- Set it to auto-restart up to 10x on failure
- Start it immediately

✓ Done!

## Verify Installation

```powershell
make service-status
# or directly:
powershell -Command "Get-ScheduledTask -TaskName 'AQ Agent' -TaskPath '\Apex Quantel\'"
```

Should show `State: Running`.

## Common Operations

```powershell
make service-status    # Check task state
make service-logs      # Tail the most recent log file
make service-restart   # Stop then start the task
make service-stop      # Stop the task
make service-remove    # Unregister the task
```

Or run `install.ps1` directly:
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 uninstall
powershell -ExecutionPolicy Bypass -File install.ps1 update    # re-registers with the current exe path
```

## View Logs

Logs go to `%ProgramData%\Apex Quantel\logs\` for a packaged/installed build,
or next to whichever `config.yaml` was actually loaded for a dev/venv run
(usually `execution-engine\logs\`).

```powershell
make service-logs

# Or manually:
Get-Content "$env:PROGRAMDATA\Apex Quantel\logs\*.log" -Tail 50 -Wait
```

### Windows Event Viewer

Task Scheduler task history (not application logs) can be inspected via:
1. Open Task Scheduler (`taskschd.msc`)
2. Navigate to Task Scheduler Library `\Apex Quantel\AQ Agent`
3. Click the "History" tab

## Troubleshooting

### Task won't stay running

Check the log file (see above) for the actual error. Common causes:
- `config.yaml` missing or invalid (run the exe manually once to see the error)
- MT5 terminal not running, or wrong `mt5.path`/`login`/`server`
- `risk.max_losing_streak` missing or set to `0` (must be >= 1)
- Python environment not activated before `pip install -e .`

**Solution**:
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 update
```

### High CPU/Memory Usage

Check the live values via the dashboard's Performance tab (connects directly
to the engine's WebSocket bridge), or:
```powershell
Get-Process python,apex-quant-trader-agent -ErrorAction SilentlyContinue |
  Select-Object Name, Id, CPU, WorkingSet
```

### Can't run PowerShell scripts

```powershell
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope CurrentUser
```

### Need to update config.yaml

```powershell
make service-stop
notepad config.yaml
make service-restart
```

## Update/Upgrade

```powershell
cd execution-engine
git pull
venv\Scripts\activate
pip install -e . --upgrade
make service-restart
```

## Get Help

- [Full Documentation](../docs/)
- [Report Issues](https://github.com/amfredfred/execution-engine/issues)
- [Deployment Guide](../docs/deployment.md)

## Next Steps

1. **Monitor**: Point the `customer-dashboard` app's `NEXT_PUBLIC_EXECUTION_ENGINE_WS_URL`
   at `ws://localhost:8080` (or the host running this engine) - it connects
   directly, read-only, no login required.
2. **Review Risk Settings**: Tune `max_losing_streak`, `max_daily_loss_percent`,
   and `sl_ratio_threshold` in `config.yaml`.
3. **Review Logs**: Check logs regularly for rule rejections and sizing info.
4. **Test Signals**: Start with a demo account to verify integration before live trading.

## Security Reminders

- Keep `config.yaml` private (it contains your MT5 password) - it's git-ignored
- Don't share your MT5 credentials
- Rotate passwords regularly
- Use a firewall to restrict access to the engine's WebSocket port (8080) if
  the dashboard connects from another machine

---

**Ready to trade?** Your Execution Engine is now running in the background!
