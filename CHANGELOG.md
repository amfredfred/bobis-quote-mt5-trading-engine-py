# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Streak-based dynamic position sizing** — per-trade risk is now derived from the system's worst recorded losing streak rather than a manually configured percentage or fixed amount
  - New config field `MAX_LOSING_STREAK` (int, min 1): set to the worst consecutive losing streak observed in backtesting or live history
  - `daily_budget = start_of_day_equity × (MAX_DAILY_LOSS_PERCENT / 100)`
  - `risk_per_trade = daily_budget / (MAX_LOSING_STREAK + 1)`
  - `max_open_trades = MAX_LOSING_STREAK + 1` — derived, never configured separately
  - Budget coherence is now a mathematical guarantee: maximum simultaneous exposure equals exactly the daily budget
- **`LossTracker.daily_risk_amount(max_losing_streak)`** — single source of truth for per-trade risk amount; called by `TradePlanner` on every signal
- **`LossTracker` tracks `start_of_day_equity`** — latched from the broker on the first poll cycle of each calendar day and held fixed for the session; ensures lot sizes are stable throughout the day regardless of intraday P&L movement
- **`Mt5Positions.get_daily_pnl_info(magic)`** — replaces `get_daily_loss_pct()`; returns `(loss_pct, start_of_day_equity)` in a single broker call, surfacing the start equity that was previously computed internally and discarded
- **Startup validation for `MAX_LOSING_STREAK`** — raises `ValueError` with a descriptive message if the value is `< 1`; fails before any broker connection is attempted
- **`_validate_symbol_info()` and `_resolve_fill_price()` helpers in `rules.py`** — shared validation extracted into private helpers; rules remain independently testable
- **`_UNKNOWN_SIGNAL_ID` constant in `rules.py`** — replaces magic string `"unknown"` in `duplicate_signal_rule`

### Changed
- **`min_rr_rule` rewritten to use live fill price** — previously used `ctx.signal.risk_reward_ratio` (computed at signal generation time from a potentially stale `entry_price`); now computes R:R from `si.ask`/`si.bid` at execution time, the same way `spread_quality_rule` does; rejection reason surfaces both the actual and signal R:R for log comparison
- **`spread_quality_rule` anchored to live fill price** — SL distance is measured from `si.ask` (long) or `si.bid` (short) rather than the stale `signal.entry_price`; all direction checks now use `SignalDirection.LONG` consistent with the rest of the codebase
- **`max_open_trades_rule` derives limit from `config.max_losing_streak + 1`** — no longer reads a configured `max_open_trades` field
- **`daily_loss_limit_rule` Layer 2 uses streak formula** — per-trade risk percentage is computed as `MAX_DAILY_LOSS_PERCENT / (MAX_LOSING_STREAK + 1)`, eliminating the `RiskMode.PERCENTAGE` branch; Layer 2 now always runs regardless of mode
- **`TradePlanner` depends on `LossTracker`** — injected at construction; calls `loss_tracker.daily_risk_amount()` for lot sizing instead of branching on `RiskMode`
- **`calculate_lot_size` signature simplified** — removed `account_balance`, `risk_mode`, `risk_percent`, `risk_fixed`; accepts `risk_amount: float` directly; single responsibility: sizing math only
- **`RiskConfig` fields removed**: `risk_mode`, `risk_percent_per_trade`, `risk_fixed_amount`, `max_open_trades`; replaced by `max_losing_streak: int`
- **`ExecutionEngine.update_daily_loss(loss_pct, start_equity)`** — signature gains `start_equity` parameter; forwards to `LossTracker`
- **`PositionManager._poll()`** — calls `get_daily_pnl_info()` and unpacks both return values
- **`monitoring.py`** — `max_open_trades` derived as `max_losing_streak + 1`; `risk_pct` displayed as `daily_budget / (streak + 1)`; removed `risk_mode` display field
- **`bootstrap.py` and `__main__.py`** — startup logs updated to reflect new config fields; daily loss priming updated to use `get_daily_pnl_info()`
- **`ALL_RULES` ordering made explicit** — comments label each rule as memory-only or broker I/O; `min_rr_rule` moved to sit beside `spread_quality_rule` since both now require a live tick
- **`RiskMode` enum removed entirely** from `lot_calculator.py` and all imports across the codebase

### Removed
- `RISK_MODE` environment variable and config field
- `RISK_PERCENT_PER_TRADE` environment variable and config field
- `RISK_FIXED_AMOUNT` environment variable and config field
- `MAX_OPEN_TRADES` environment variable and config field (now derived)
- `RiskMode` enum (`lot_calculator.py`)
- `_parse_risk_mode()` function (`settings.py`)
- `get_daily_loss_pct()` method on `Mt5Positions` (replaced by `get_daily_pnl_info()`)
- Local `from src.utils.lot_calculator import RiskMode` import inside `daily_loss_limit_rule` (was hidden inside a function body)

### Fixed
- `spread_quality_rule` used raw strings/ints for direction (`"long"`, `1`, `"buy"`) instead of `SignalDirection` — now consistent with `no_hedging_rule` and the rest of the codebase
- `min_rr_rule` checked a stale signal-time R:R rather than the actual R:R at fill — a signal generated at one price could arrive at execution with a materially worse R:R, and the old rule would pass it
- `daily_loss_limit_rule` had a local import (`from src.utils.lot_calculator import RiskMode as _RiskMode`) masking a circular import rather than fixing it; import moved to module level

### Documentation
- `risk_rules.md` — fully rewritten to reflect streak-based config model; removed `RiskMode` section and old config examples; added budget coherence proof; documented live fill price behaviour of `min_rr_rule` and `spread_quality_rule`; updated `RuleContext` field reference; updated tuning guidance and best practices
- `architecture.md` — updated component descriptions for `loss_tracker.py`, `planner.py`, `positions.py`, `lot_calculator.py`, and `monitoring.py`; added dedicated "Daily Loss / Sizing Data Flow" diagram; updated configuration section with derived values
- `deployment.md` — added `MAX_LOSING_STREAK` validation error to troubleshooting; updated health endpoint description; added note on `LossTracker` midnight reset in daily restart section
- `quickstart-windows.md` — Step 3 now documents `MAX_LOSING_STREAK` and key risk fields with inline explanations; Step 4 covers the new validation error; troubleshooting updated

---

## [Unreleased] — prior

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
