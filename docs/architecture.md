# Architecture

This document describes the system architecture of the Execution Engine, following Clean Architecture and Domain-Driven Design principles.

## Overview

The Execution Engine is an event-driven system for automated trade execution with MetaTrader 5. It processes trading signals, applies risk management rules, and executes orders while maintaining real-time monitoring capabilities.

## System Components

### 1. Domain Layer (`src/domain/`)

Pure business logic with no external dependencies:

- **`signal.py`**: Signal types and validation
  - `InboundSignal`: External trading signal
  - `SignalDirection`: BUY/SELL enums
  - Signal validation rules

- **`trade.py`**: Trade entities and business rules
  - `Trade`: Complete trade lifecycle
  - `TradePlan`: Execution planning
  - `OrderSide`: Order direction enums

- **`position.py`**: Position management
  - `Position`: Current positions
  - `AccountInfo`: Account state
  - `SymbolInfo`: Symbol specifications

### 2. Core Layer (`src/core/`)

Cross-cutting infrastructure:

- **`event_bus.py`**: Async event bus implementation
  - Event publishing/subscription
  - Async event handlers
  - Event filtering and routing

- **`event_types.py`**: Event definitions
  - `TradeEvent`: Trade lifecycle events
  - `SignalEvent`: Signal processing events
  - `RiskEvent`: Risk rule violations

- **`events.py`**: Event data structures
  - Typed event payloads
  - Event metadata

### 3. Execution Layer (`src/execution/`)

Trade execution pipeline:

- **`engine.py`**: Main execution engine
  - Signal processing orchestration
  - Risk validation integration
  - Order execution coordination

- **`planner.py`**: Trade planning
  - Lot size calculation
  - Slippage estimation
  - Spread surcharge handling

- **`order_manager.py`**: Order lifecycle management
  - Order placement and tracking
  - Fill confirmation
  - Error handling and retry logic

### 4. Risk Management (`src/risk/`)

Risk control system:

- **`engine.py`**: Risk rule evaluation
  - Rule application pipeline
  - Violation handling
  - Risk state tracking

- **`rules.py`**: Risk rule definitions
  - `ALL_RULES`: Registry of active rules
  - `RuleContext`: Rule evaluation context
  - Individual rule implementations

- **`loss_tracker.py`**: Loss monitoring
  - Daily/weekly loss tracking
  - Drawdown calculations
  - Risk limit enforcement

### 5. Infrastructure Layer (`src/infra/`)

External system adapters:

- **`database.py`**: SQLite persistence
  - Trade storage and retrieval
  - Signal history
  - Metrics persistence

- **`monitoring.py`**: HTTP dashboard server
  - Real-time metrics endpoint
  - WebSocket monitoring
  - Health checks

- **`websocket.py`**: WebSocket client
  - Signal ingestion
  - External system integration

- **`logger.py`**: Structured logging
  - Configurable log levels
  - Log aggregation
  - Error tracking

- **`metrics.py`**: Metrics collection
  - Performance counters
  - Trade statistics
  - System health metrics

### 6. Broker Adapters (`src/brokers/mt5/`)

MT5 integration:

- **`client.py`**: MT5 connection management
  - Terminal connection
  - Authentication
  - Session management

- **`orders.py`**: Order operations
  - Market/limit order placement
  - Order status tracking
  - Cancellation handling

- **`positions.py`**: Position synchronization
  - Live position monitoring
  - Position reconciliation
  - PnL calculations

- **`types.py`**: MT5-specific types
  - MT5 API type mappings
  - Error code handling

### 7. Signal Processing (`src/signals/`)

Signal ingestion pipeline:

- **`consumer.py`**: Signal consumption
  - WebSocket signal intake
  - Signal parsing and validation
  - Signal queuing

- **`queue.py`**: Signal buffering
  - Async signal queue
  - Priority handling
  - Backpressure management

- **`validator.py`**: Signal validation
  - Business rule validation
  - Data integrity checks
  - Duplicate detection

- **`types.py`**: Signal type definitions
  - Signal event enums
  - Signal data structures

### 8. Strategy Routing (`src/strategies/`)

Signal-to-strategy mapping:

- **`adapter.py`**: Strategy adapters
  - BaseAdapter interface
  - PassthroughAdapter implementation
  - Custom strategy logic

- **`router.py`**: Strategy routing
  - Signal routing rules
  - Strategy selection
  - Fallback handling

### 9. Utility Functions (`src/utils/`)

Shared utilities:

- **`price.py`**: Price calculations
  - Pip size calculations
  - Lot size normalization
  - Price formatting

- **`symbol.py`**: Symbol handling
  - Symbol normalization
  - Symbol validation
  - Market data utilities

- **`time.py`**: Time utilities
  - Timestamp generation
  - Timezone handling
  - Duration calculations

- **`lot_calculator.py`**: Position sizing
  - Risk-based lot calculation
  - Account balance consideration
  - Leverage adjustments

## Data Flow

```
External Signal Source
        ↓
    WebSocket Server
        ↓
    Signal Consumer
        ↓
    Signal Validator
        ↓
    Signal Queue
        ↓
    Event Bus
        ↓
    Strategy Router
        ↓
    Risk Engine
        ↓
    Execution Engine
        ↓
    Trade Planner
        ↓
    Order Manager
        ↓
    MT5 Client
        ↓
    MetaTrader 5 Terminal
```

## Event Flow

1. **Signal Ingestion**: External signals received via WebSocket
2. **Validation**: Signals validated against business rules
3. **Enrichment**: Signals enriched with market data
4. **Risk Check**: Risk rules evaluated for trade safety
5. **Planning**: Trade parameters calculated (lots, slippage)
6. **Execution**: Orders placed with MT5
7. **Confirmation**: Fill confirmations processed
8. **Persistence**: Trade data stored in database
9. **Monitoring**: Metrics updated and broadcast

## Database Schema

### trades
- id (PRIMARY KEY)
- symbol
- direction
- volume
- open_price
- close_price
- open_time
- close_time
- profit
- commission
- status

### signals
- id (PRIMARY KEY)
- timestamp
- symbol
- direction
- volume
- price
- source
- processed

### metrics_counters
- name (PRIMARY KEY)
- value
- updated_at

### metrics_gauges
- name (PRIMARY KEY)
- value
- updated_at

## Configuration

The system uses a hierarchical configuration:

1. **Environment Variables**: Runtime secrets and overrides
2. **Settings Dataclass**: Typed configuration with defaults
3. **Validation**: Configuration validated on startup

## Error Handling

- **Circuit Breakers**: Automatic shutdown on critical errors
- **Retry Logic**: Exponential backoff for transient failures
- **Logging**: Comprehensive error logging with context
- **Recovery**: Graceful recovery from connection losses

## Performance Considerations

- **Async Processing**: Non-blocking I/O operations
- **Connection Pooling**: Reused database connections
- **Event Buffering**: High-throughput event processing
- **Memory Management**: Bounded queues and cleanup
- **Monitoring**: Real-time performance metrics

## Security

- **API Authentication**: WebSocket secret validation
- **Input Validation**: Strict signal and configuration validation
- **Error Masking**: Sensitive data not exposed in logs
- **Access Control**: MT5 credentials encrypted in environment

## Extensibility

The modular architecture allows for:

- **New Brokers**: Additional broker adapters
- **Custom Strategies**: Strategy-specific signal processing
- **Additional Risk Rules**: Domain-specific risk controls
- **Monitoring Integrations**: External monitoring systems
- **Signal Sources**: Multiple signal ingestion methods