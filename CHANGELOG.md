# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release of Execution Engine
- Event-driven architecture with async event bus
- MetaTrader 5 integration with order and position management
- Comprehensive risk management system with configurable rules
- WebSocket API for real-time monitoring and signal ingestion
- SQLite-based trade persistence
- HTTP dashboard server with live metrics
- Type-safe configuration with Pydantic/dataclasses
- Full test suite with unit and integration tests
- Ruff linting and formatting
- MyPy type checking
- Pre-commit hooks for code quality
- Docker support (planned)

### Features
- Real-time signal processing from external sources
- Automated trade execution with slippage control
- Position synchronization with MT5 terminal
- Risk rule validation (position limits, loss thresholds, circuit breakers)
- Live metrics collection and reporting
- WebSocket-based monitoring dashboard
- Configurable logging and error handling

### Technical
- Python 3.12+ with modern async/await patterns
- Clean Architecture with domain-driven design
- Dependency injection container
- Event sourcing for trade lifecycle
- Thread-safe database operations with WAL mode
- Comprehensive error handling and recovery

### Documentation
- Architecture overview and data flow diagrams
- Risk rules reference and tuning guide
- API documentation for WebSocket endpoints
- Configuration guide
- Development setup instructions

## [0.1.0] - 2024-04-27

### Added
- Project scaffolding with modern Python packaging
- Basic event bus implementation
- MT5 client wrapper
- Risk engine skeleton
- Database schema for trades and signals
- WebSocket server for monitoring
- Configuration management with environment variables
- Logging setup with structured logging
- Initial test framework setup

### Changed
- Migrated from flat structure to src/ layout
- Updated to Python 3.12+ type hints
- Replaced flake8/black/isort with Ruff
- Single pyproject.toml configuration

### Technical
- Hatch build system
- Ruff for linting and formatting
- MyPy for type checking
- Pytest with async support
- Pre-commit hooks