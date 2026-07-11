# Deployment Guide

This guide covers deploying the Execution Engine for production use on Windows and Linux systems.

## Quick Start

Choose your deployment method:

- **Windows Native (Recommended)**: [Task Scheduler](#windows-task-scheduler)
- **Cross-Platform**: [Docker](#docker-deployment)
- **Linux/Mac**: [Systemd Service](#linux-systemd-service)

## Windows Task Scheduler

This is a **headless-only engine - there is no GUI**. It's installed as a
Task Scheduler task (not a Windows Service): Windows services run in
Session 0, which has no desktop, and the MT5 Python API cannot attach to a
terminal from there. A scheduled task runs in your own interactive session
at logon, so MT5 is fully visible and the engine connects normally.

Recommended for Windows because:
- Direct MetaTrader 5 terminal access (interactive session)
- Automatic restart on failure (up to 10x, 1-minute interval)
- No container overhead
- 24/7 operation optimized for trading

### Prerequisites

- Windows 10 or later
- MetaTrader 5 terminal installed and running
- Python 3.12+
- Virtual environment setup

### Installation

1. **Prepare environment**:
   ```powershell
   python -m venv venv
   venv\Scripts\activate
   pip install -e .[dev]
   ```

2. **Configure**: copy `config.example.yaml` to `config.yaml` and fill in
   `mt5`, `risk`, `execution`, `signal_engine`, and `engine` sections.

3. **Install the scheduled task**:
   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

   The script will:
   - Register `\Apex Quantel\AQ Agent` in Task Scheduler
   - Set it to start ~30 s after logon (so MT5 can start first)
   - Configure auto-restart on failure
   - Start it now

4. **Verify installation**:
   ```powershell
   make service-status
   # or: Get-ScheduledTask -TaskName "AQ Agent" -TaskPath "\Apex Quantel\"
   ```

### Task Management

```powershell
make service-status    # Check task state
make service-logs      # Tail the most recent log file
make service-restart   # Stop then start
make service-stop      # Stop
make service-remove    # Unregister (equivalent to install.ps1 uninstall)
```

### Monitoring

**Task Scheduler history**: open `taskschd.msc`, navigate to
`Task Scheduler Library\Apex Quantel\AQ Agent`, click the History tab.

**Logs**: `%ProgramData%\Apex Quantel\logs\` for packaged builds, or next to
whichever `config.yaml` was loaded for a dev/venv run.
```powershell
make service-logs
```

### Troubleshooting

**Task won't stay running**:
```powershell
make service-logs   # check the actual error
```

Common causes:
- `config.yaml` missing or invalid
- MT5 terminal not running, or wrong `mt5.path`/`login`/`server`
- `risk.max_losing_streak` missing or `0` (must be >= 1)

**Reinstall**:
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 update
```

**`risk.max_losing_streak` validation error on startup**:
```
ValueError: risk.max_losing_streak must be >= 1
```
Set it to your system's worst recorded consecutive losing streak (minimum
`1`) in `config.yaml` and restart the task.

---

## Docker Deployment

Docker is useful for:
- Cross-platform deployments (Windows, Linux, Mac)
- Cloud infrastructure (AWS, Azure, GCP)
- Container orchestration (Kubernetes)
- Containerized development/testing

### Prerequisites

- Docker Desktop installed
- Docker CLI available
- MetaTrader 5 terminal access (requires special setup)

### Build Image

```bash
# Build image
docker build -t execution-engine:latest .

# Tag for registry
docker tag execution-engine:latest amfredfred/execution-engine:latest
```

### Run Container

**Development**:
```bash
docker run -it \
  --env-file .env \
  -p 8080:8080 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  execution-engine:latest
```

**Production**:
```bash
docker run -d \
  --name execution-engine \
  --restart unless-stopped \
  --env-file .env \
  -p 8080:8080 \
  -v execution-engine-data:/app/data \
  -v execution-engine-logs:/app/logs \
  execution-engine:latest
```

### Docker Compose

```bash
# Start services
docker-compose up -d

# View logs
docker-compose logs -f execution-engine

# Stop services
docker-compose down

# Remove volumes
docker-compose down -v
```

### MT5 with Docker

**Note**: MT5 terminal typically runs on Windows host, not in container.

**Options**:
1. Run engine in Docker, connect to host MT5 (requires network setup)
2. Run everything on Windows with Task Scheduler (recommended for trading)
3. Use cloud MT5 broker with API access

**Host Network Access**:
```bash
# On Windows with Docker Desktop
docker run -it \
  --env-file .env \
  -e MT5_HOST=host.docker.internal \
  execution-engine:latest
```

---

## Linux Systemd Service

Deploy on Linux/Mac with systemd:

### Prerequisites

- Linux system with systemd
- Python 3.12+
- systemctl available

### Installation

1. **Prepare environment**:
   ```bash
   git clone https://github.com/amfredfred/bobis-quote-mt5-trading-engine-py.git
   cd execution-engine

   python3 -m venv venv
   source venv/bin/activate
   pip install -e .

   cp .env.example .env
   # Edit .env with your settings
   ```

2. **Create systemd service** (`/etc/systemd/system/execution-engine.service`):
   ```ini
   [Unit]
   Description=Execution Engine - Trade Execution Engine for MetaTrader 5
   After=network.target

   [Service]
   Type=simple
   User=trading
   WorkingDirectory=/home/trading/execution-engine
   Environment="PATH=/home/trading/execution-engine/venv/bin"
   EnvironmentFile=/home/trading/execution-engine/.env
   ExecStart=/home/trading/execution-engine/venv/bin/python -m src
   Restart=on-failure
   RestartSec=10
   StandardOutput=journal
   StandardError=journal

   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable and start**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable execution-engine
   sudo systemctl start execution-engine
   ```

4. **Monitor**:
   ```bash
   # Status
   systemctl status execution-engine

   # Logs
   journalctl -u execution-engine -f

   # Full logs
   journalctl -u execution-engine --since "1 hour ago"
   ```

---

## Dashboard Monitoring

The execution engine exposes a read-only WebSocket telemetry bridge
(UIBridge) on `engine.monitoring_port` (default 8080). The `customer-dashboard`
app connects directly to it - no login, no commands sent, live account/trade/
risk-guard state only.

```text
ws://localhost:8080
```

---

## Backup & Recovery

### Database Backups

```powershell
# Windows - backup every 6 hours
$schedule = New-Object -TypeName Microsoft.Win32.TaskScheduler.TaskDefinition
$task = Register-ScheduledTask `
  -TaskName "ExecutionEngine-Backup" `
  -Action (New-ScheduledTaskAction -Execute "powershell" -Argument "-File backup.ps1") `
  -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 6)) `
  -RunLevel Highest
```

### Manual Backup

```bash
# Backup database
cp data/engine.db data/engine.db.backup

# Backup configuration
cp .env .env.backup

# Backup logs
tar czf logs-$(date +%Y%m%d).tar.gz logs/
```

---

## Performance Tuning

### Memory Usage

Monitor memory in Task Manager or with:
```powershell
Get-Process python | Select-Object Name, WorkingSet
```

If memory grows unboundedly:
- Check for event bus memory leaks
- Verify database WAL cleanup
- Monitor queue sizes in logs

### CPU Usage

Monitor CPU in Task Manager or with:
```powershell
Get-Process python | Select-Object Name, CPU
```

If CPU is consistently high:
- Review signal processing rate
- Check for tight loops in rules
- Monitor database query performance

---

## Scaling

### Multiple Instances

For high-frequency strategies, consider:
1. Separate strategies per service instance
2. Load balancing signal routing
3. Shared database for state

### Resource Limits (Docker)

```yaml
services:
  execution-engine:
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
        reservations:
          cpus: '0.5'
          memory: 256M
```

---

## Security

### Service Isolation

- Run service as low-privilege user (not Administrator)
- Use firewall rules to restrict WebSocket access
- Enable Windows Defender/antivirus

### Configuration Security

- Use environment variables for secrets
- Rotate MT5 credentials regularly
- Enable Windows Credential Manager for passwords
- Use VPN for remote connections

### Log Security

- Rotate logs regularly
- Encrypt sensitive data in logs
- Restrict log file access permissions

---

## Disaster Recovery

### Automated Restarts

`install.ps1` registers the task with `-RestartCount 10 -RestartInterval 1min`,
so Task Scheduler automatically restarts it on failure. Verify:
```powershell
Get-ScheduledTask -TaskName "AQ Agent" -TaskPath "\Apex Quantel\" |
  Select-Object -ExpandProperty Settings |
  Select-Object RestartCount, RestartInterval
```

### Daily Restarts

Prevent memory leaks with a separate scheduled restart:

**Windows Task Scheduler**:
```powershell
$trigger = New-ScheduledTaskTrigger -Daily -At 02:00AM
Register-ScheduledTask `
  -TaskName "RestartAQAgent" `
  -TaskPath "\Apex Quantel\" `
  -Action (New-ScheduledTaskAction -Execute "powershell" -Argument "-Command `"Stop-ScheduledTask -TaskName 'AQ Agent' -TaskPath '\Apex Quantel\'; Start-Sleep 2; Start-ScheduledTask -TaskName 'AQ Agent' -TaskPath '\Apex Quantel\'`"") `
  -Trigger $trigger `
  -RunLevel Highest
```

**Linux cron**:
```bash
# Daily restart at 2 AM
0 2 * * * systemctl restart execution-engine
```

Note: `LossTracker` automatically resets its daily state at midnight via the internal `paused_until` rollover mechanism. A daily restart is optional but recommended to clear any in-memory accumulation.

---

## Choosing Your Deployment

| Requirement | Task Scheduler | Docker | Systemd |
|-------------|-----------------|--------|---------|
| **Windows native** | ✓ Best | Limited | ✗ |
| **MT5 integration** | ✓ Direct | Complex | Depends |
| **Cross-platform** | ✗ | ✓ Best | Linux only |
| **Cloud ready** | ✗ | ✓ Best | ✓ |
| **Kubernetes** | ✗ | ✓ | ✗ |
| **Simple setup** | ✓ | Moderate | ✓ |
| **24/7 trading** | ✓ Best | ✓ | ✓ |

**TL;DR**: Use **Task Scheduler** for Windows trading, **Docker** for cloud/scaling, **systemd** for Linux.
