# Risk Rules Reference

This document provides a comprehensive reference for the risk management rules implemented in the Execution Engine, along with configuration guidance and tuning recommendations.

## Overview

The risk management system uses a rule-based approach where each trading signal is validated against a series of risk checks before execution. Rules are evaluated in order, and any rule failure prevents the trade from being executed.

## Rule Categories

### Guard Rules
Rules that run first and can short-circuit all other checks:

- **Loss Guard Rule**: Circuit breaker based on daily loss percentage
- **No Hedging Rule**: Prevents opposing positions on the same symbol

### Validation Rules
Core risk validation rules:

- **Minimum Risk-Reward Ratio**: Ensures adequate profit potential
- **Maximum Open Trades**: Limits concurrent positions
- **Maximum Symbol Exposure**: Limits positions per symbol
- **Duplicate Signal**: Prevents duplicate signal processing
- **Daily Loss Limit**: Monetary loss threshold with safety buffers
- **Spread Quality**: Validates spread vs stop loss ratio

## Rule Details

### Loss Guard Rule

**Purpose**: Emergency circuit breaker for excessive daily losses.

**Configuration**:
```python
# In RiskConfig
max_daily_loss_percent: float  # Daily loss threshold (percentage)
```

**Behavior**:
- Monitors realized losses against account equity
- Automatically pauses trading when threshold reached
- Resumes at midnight (account reset)
- Provides buffer zone to prevent overshoot

**Tuning**:
- Conservative: 1-2% for high-frequency strategies
- Aggressive: 3-5% for swing trading
- Consider account size and risk tolerance

### No Hedging Rule

**Purpose**: Prevents conflicting positions on the same symbol.

**Configuration**:
```python
# In RiskConfig
no_hedging: bool = True  # Enable/disable hedging prevention
```

**Behavior**:
- Blocks BUY signals when SELL position exists (and vice versa)
- Checks all open and planned trades
- Allows multiple positions in same direction

**Tuning**:
- Enable for directional strategies
- Disable for arbitrage or hedging strategies

### Minimum Risk-Reward Ratio Rule

**Purpose**: Ensures trades have adequate profit potential.

**Configuration**:
```python
# In RiskConfig
min_rr_ratio: float  # Minimum R:R ratio (e.g., 1.5)
```

**Behavior**:
- Compares signal's risk-reward ratio
- Requires signal to provide R:R calculation
- Blocks trades below threshold

**Tuning**:
- Conservative: 2.0+ (2:1 reward-to-risk)
- Balanced: 1.5-2.0
- Aggressive: 1.0-1.5

### Maximum Open Trades Rule

**Purpose**: Limits portfolio exposure through position count.

**Configuration**:
```python
# In RiskConfig
max_open_trades: int  # Maximum concurrent positions
```

**Behavior**:
- Counts all open and planned trades
- Blocks new trades when limit reached
- Works with daily loss limits for budget control

**Tuning**:
- Scalping: 5-10 positions
- Day trading: 3-5 positions
- Swing trading: 1-3 positions

### Maximum Symbol Exposure Rule

**Purpose**: Prevents over-concentration in single symbols.

**Configuration**:
```python
# In RiskConfig
max_exposure_per_symbol: int  # Max positions per symbol
```

**Behavior**:
- Counts positions per symbol
- Allows multiple entries in same direction
- Independent of hedging rules

**Tuning**:
- Major pairs: 2-3 positions
- Exotic pairs: 1 position
- Crypto: 1 position (high volatility)

### Duplicate Signal Rule

**Purpose**: Prevents processing the same signal multiple times.

**Configuration**: Automatic (no configuration needed)

**Behavior**:
- Checks signal ID against open trades
- Requires unique signal identifiers
- Handles "unknown" IDs gracefully

**Tuning**: Ensure signal sources provide unique IDs

### Daily Loss Limit Rule

**Purpose**: Monetary loss control with safety buffers.

**Configuration**:
```python
# In RiskConfig
max_daily_loss_percent: float  # Daily loss limit
risk_percent_per_trade: float  # Risk per trade (for budget projection)
```

**Behavior**:
- Two-layer protection:
  1. Hard stop at 95% of limit
  2. Pre-trade budget projection
- Prevents account from reaching full limit
- Works with position limits for natural throttling

**Tuning**:
- Daily limit: 1-5% of account
- Per-trade risk: 0.5-2% of account
- Balance with max_open_trades

### Spread Quality Rule

**Purpose**: Validates market conditions before trading.

**Configuration**:
```python
# In RiskConfig
sl_ratio_threshold: float  # Spread/SL ratio limit (default: 0.5)
```

**Behavior**:
- Compares current spread to stop loss distance
- Blocks trades when spread is too wide
- Requires live market data

**Tuning**:
- Conservative: 0.3 (spread ≤ 30% of SL)
- Balanced: 0.5 (spread ≤ 50% of SL)
- Aggressive: 1.0 (spread ≤ SL distance)

## Configuration Examples

### Conservative Configuration
```python
RiskConfig(
    risk_mode=RiskMode.PERCENTAGE,
    risk_percent_per_trade=0.5,  # 0.5% per trade
    max_daily_loss_percent=1.0,  # 1% daily limit
    max_open_trades=3,
    max_exposure_per_symbol=1,
    min_rr_ratio=2.0,
    sl_ratio_threshold=0.3,
    no_hedging=True,
)
```

### Aggressive Configuration
```python
RiskConfig(
    risk_mode=RiskMode.PERCENTAGE,
    risk_percent_per_trade=2.0,  # 2% per trade
    max_daily_loss_percent=5.0,  # 5% daily limit
    max_open_trades=10,
    max_exposure_per_symbol=3,
    min_rr_ratio=1.0,
    sl_ratio_threshold=1.0,
    no_hedging=False,
)
```

### Fixed Amount Configuration
```python
RiskConfig(
    risk_mode=RiskMode.FIXED,
    risk_fixed_amount=10.0,  # $10 per trade
    max_daily_loss_percent=2.0,  # 2% daily limit
    max_open_trades=5,
    max_exposure_per_symbol=2,
    min_rr_ratio=1.5,
    sl_ratio_threshold=0.5,
    no_hedging=True,
)
```

## Risk Mode Selection

### Percentage Mode
- Risk per trade as percentage of account equity
- Scales with account size
- Recommended for most strategies
- Enables budget projection in daily loss rule

### Fixed Amount Mode
- Fixed dollar amount per trade
- Consistent risk regardless of account size
- Simpler for small accounts
- No budget projection (use max_open_trades for control)

## Monitoring and Alerts

### Key Metrics to Monitor
- Rule rejection rates by type
- Daily loss percentage
- Open trades vs limits
- Symbol exposure distribution
- Spread quality trends

### Alert Thresholds
- Daily loss > 50% of limit
- Rule rejection rate > 20%
- Spread quality failures > 5%
- Circuit breaker activation

## Testing Risk Rules

### Unit Tests
```bash
pytest tests/unit/risk/test_rules.py -v
```

### Integration Tests
```bash
pytest tests/integration/ -k risk -v
```

### Manual Testing
Use the monitoring dashboard to observe rule behavior in real-time.

## Troubleshooting

### Common Issues

**All trades rejected**: Check loss guard status and daily limits
**Signal duplicates**: Verify signal source provides unique IDs
**Spread quality failures**: Review market conditions and SL distances
**Symbol exposure limits**: Monitor position concentration

### Debug Mode
Enable debug logging to see rule evaluation details:
```bash
LOG_LEVEL=DEBUG execution-engine
```

### Rule Bypass (Development Only)
For testing, rules can be temporarily disabled in `src/risk/rules.py`:
```python
ALL_RULES: List[RiskRule] = [
    # loss_guard_rule,  # Commented out for testing
    # no_hedging_rule,
]
```

## Performance Considerations

- Rules are evaluated synchronously before trade execution
- Guard rules run first to short-circuit expensive checks
- Market data calls are minimized when rules fail early
- Rule evaluation is typically < 10ms per signal

## Extending Risk Rules

### Adding Custom Rules
1. Define rule function in `src/risk/rules.py`:
```python
def custom_rule(ctx: RuleContext) -> RuleResult:
    # Your logic here
    return RuleResult(approved=True)
```

2. Add to `ALL_RULES` list:
```python
ALL_RULES: List[RiskRule] = [
    # ... existing rules
    custom_rule,
]
```

3. Add configuration in `RiskConfig` if needed
4. Write unit tests in `tests/unit/risk/test_rules.py`

### Rule Context
Rules receive a `RuleContext` with:
- `signal`: The inbound signal
- `open_trades`: Current open positions
- `config`: Risk configuration
- `daily_loss_pct`: Current daily loss percentage
- `effective_open`: Count of open trades
- `effective_symbol`: Positions for this symbol
- `symbol_info`: Live market data
- `loss_tracker`: Loss tracking state

## Best Practices

1. **Start Conservative**: Use tight limits when deploying new strategies
2. **Monitor Regularly**: Review rule rejection patterns weekly
3. **Test Thoroughly**: Validate rules with historical data
4. **Gradual Relaxation**: Increase limits gradually based on performance
5. **Multiple Timeframes**: Use different configs for different strategies
6. **Backup Guards**: Never rely on a single rule for critical protection
7. **Documentation**: Keep risk configurations versioned and documented