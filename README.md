# Execution Engine

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

An event-driven trade execution engine for MetaTrader 5, designed for high-frequency algorithmic trading with built-in risk management.

## Features

- **Event-Driven Architecture**: Asynchronous event bus for real-time signal processing
- **Risk Management**: Comprehensive risk rules engine with position limits, loss tracking, and circuit breakers
- **MetaTrader 5 Integration**: Native MT5 broker adapter with order management and position synchronization
- **WebSocket API**: Real-time monitoring dashboard via WebSocket
- **Modular Design**: Clean architecture with domain-driven design principles
- **Type Safety**: Full type hints with mypy validation
- **Modern Python**: Built for Python 3.12+ with async/await

## Architecture

The system follows Clean Architecture principles:

- **Domain Layer**: Pure business logic (signals, trades, positions, risk rules)
- **Core Layer**: Cross-cutting infrastructure (event bus, events)
- **Execution Layer**: Trade execution pipeline (planner, order manager, engine)
- **Infrastructure Layer**: External adapters (MT5 client, database, monitoring, WebSocket)
- **Interface Adapters**: Signal ingestion and strategy routing

See [Architecture Documentation](docs/architecture.md) for detailed diagrams and data flow.

## Installation

### Requirements

- Python 3.12+
- MetaTrader 5 terminal installed and running
- Active MT5 trading account

### Install from Source

```bash
git clone https://github.com/amfredfred/bobis-quote-mt5-trading-engine-py.git
cd execution-engine
pip install -e .[dev]
```

### Configuration

1. Copy `.env.example` to `.env`
2. Fill in your MT5 credentials:
   ```bash
   MT5_LOGIN=your_login
   MT5_PASSWORD=your_password
   MT5_SERVER=your_server
   ```
3. Validate configuration:
   ```bash
   python scripts/check_env.py
   ```

## Usage

### Windows (NSSM Service - Recommended)

For 24/7 automated trading on Windows:

```powershell
# Install as Windows service (run as Administrator)
powershell -ExecutionPolicy Bypass -File install_service.ps1

# Check status
powershell -File scripts/service.ps1 status

# View logs
powershell -File scripts/service.ps1 logs

# Restart if needed
powershell -File scripts/service.ps1 restart
```

**→ See [Windows Quick Start Guide](docs/quickstart-windows.md) for step-by-step instructions**

### Manual Usage

```bash
# Start the engine
execution-engine

# Or directly with Python
python -m src
```

### Development

```bash
# Install with development dependencies
pip install -e .[dev]

# Run tests
make test

# Lint and format
make lint
make format

# Type check
make type-check

# Run pre-commit hooks
make pre-commit

# Validate environment
make check-env

# Generate WebSocket secret
make gen-secret

# Run the application
make run

# Run in development mode
make dev
```

## Production Deployment

The Execution Engine supports multiple deployment methods optimized for different scenarios:

### Windows (Recommended for Trading)

Use **NSSM (Non-Sucking Service Manager)** for native Windows service management:

```powershell
# Run as Administrator
powershell -ExecutionPolicy Bypass -File install_service.ps1
```

Benefits:
- ✓ Native Windows service integration
- ✓ Direct MetaTrader 5 terminal access  
- ✓ Automatic restart on failure
- ✓ Windows Event Viewer logging
- ✓ No container overhead

See [Deployment Guide](docs/deployment.md#windows-nssm-service) for detailed instructions.

### Docker (Cross-Platform & Cloud)

For containerized deployment or cloud infrastructure:

```bash
# Build image
docker build -t execution-engine .

# Run container
docker run --env-file .env -p 8080:8080 execution-engine

# Or with Docker Compose
docker-compose up -d
```

Benefits:
- ✓ Cross-platform (Windows, Linux, Mac)
- ✓ Cloud-ready (AWS, Azure, GCP)
- ✓ Container orchestration (Kubernetes)
- ✓ Consistent environments

See [Deployment Guide](docs/deployment.md#docker-deployment) for details.

### Linux (Systemd)

For Linux deployments with systemd:

```bash
sudo systemctl start execution-engine
sudo systemctl status execution-engine
journalctl -u execution-engine -f
```

See [Deployment Guide](docs/deployment.md#linux-systemd-service) for setup.

**→ See [Complete Deployment Guide](docs/deployment.md) for all options, monitoring, backups, and troubleshooting.**

## API

### WebSocket Monitoring

Connect to `ws://localhost:8080/ws` for real-time metrics:

```javascript
const ws = new WebSocket('ws://localhost:8080/ws');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Metrics:', data);
};
```

### Signal Ingestion

Send trading signals via WebSocket:

```python
import websockets
import json

async def send_signal():
    uri = "ws://localhost:8080/ws"
    async with websockets.connect(uri) as websocket:
        signal = {
            "type": "signal",
            "data": {
                "symbol": "EURUSD",
                "direction": "BUY",
                "volume": 0.01,
                "price": 1.0850
            }
        }
        await websocket.send(json.dumps(signal))
```

## Risk Management

The engine includes comprehensive risk controls:

- **Position Limits**: Maximum exposure per symbol/account
- **Loss Limits**: Daily/weekly loss thresholds with automatic shutdown
- **Circuit Breakers**: Halt trading on extreme volatility
- **Lot Size Validation**: Minimum/maximum position sizes
- **Slippage Protection**: Maximum allowed slippage per trade

See [Risk Rules Documentation](docs/risk_rules.md) for configuration and tuning.

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `MT5_LOGIN` | MT5 account login | Yes |
| `MT5_PASSWORD` | MT5 account password | Yes |
| `MT5_SERVER` | MT5 server address | Yes |
| `WS_SECRET` | WebSocket authentication secret | No |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | No |

### Risk Configuration

Configure risk parameters in `src/config/settings.py`:

```python
@dataclass
class RiskConfig:
    max_daily_loss: float = 100.0  # USD
    max_position_size: float = 0.1  # lots
    max_slippage: float = 0.0001  # pips
    circuit_breaker_threshold: float = 0.01  # 1%
```

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test categories
pytest -m unit
pytest -m integration
```

### Mock MT5 for Testing

The test suite includes mocks for MT5 terminal, allowing full testing without a live connection.

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes with tests
4. Run the full test suite: `make test`
5. Format and lint: `make lint && make format`
6. Type check: `make type-check`
7. Commit with conventional commits
8. Push and create a PR

### Development Setup

```bash
# Clone and setup
git clone https://github.com/amfredfred/bobis-quote-mt5-trading-engine-py.git
cd execution-engine

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install with dev dependencies
make install

# Install pre-commit hooks
pre-commit install

# Run initial checks
make pre-commit
```

## Code of Conduct

This project follows a code of conduct to ensure a welcoming environment for all contributors. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for details.

## Code of Conduct

This project follows a code of conduct to ensure a welcoming environment for all contributors. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for details.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This software is for educational and research purposes only. Trading cryptocurrencies and forex involves substantial risk of loss and is not suitable for every investor. Past performance does not guarantee future results. Use at your own risk.

## Security

See [SECURITY.md](SECURITY.md) for our security policy and responsible disclosure guidelines.

## Support

- 📖 [Full Documentation](docs/)
- 🚀 [Windows Quick Start](docs/quickstart-windows.md)
- 📋 [Deployment Guide](docs/deployment.md)
- 🏗️ [Architecture Guide](docs/architecture.md)
- ⚠️ [Risk Rules Reference](docs/risk_rules.md)
- 🐛 [Issues](https://github.com/amfredfred/execution-engine/issues)
- 💬 [Discussions](https://github.com/amfredfred/execution-engine/discussions)