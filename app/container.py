"""
Dependency injection container.

Builds and wires all components from configuration.
Returns a plain dataclass — no framework required.
"""

from __future__ import annotations

from dataclasses import dataclass

from brokers.mt5.mt5_client import Mt5Client
from brokers.mt5.mt5_orders import Mt5Orders
from brokers.mt5.mt5_positions import Mt5Positions
from config.config import AppConfig
from core.event_bus import EventBus
from execution.execution_engine import ExecutionEngine
from execution.order_manager import OrderManager
from execution.trade_planner import TradePlanner
from positions.position_manager import PositionManager
from positions.position_store import PositionStore
from risk.risk_engine import RiskEngine
from signals.signal_consumer import SignalConsumer
from signals.signal_validator import SignalValidator
from storage.trade_repository import TradeRepository
from strategies.signal_adapter import PassthroughAdapter
from strategies.strategy_router import StrategyRouter


@dataclass
class AppContainer:
    event_bus: EventBus
    signal_consumer: SignalConsumer
    execution_engine: ExecutionEngine
    position_manager: PositionManager
    mt5_client: Mt5Client
    mt5_positions: Mt5Positions
    position_store: PositionStore
    trade_repo: TradeRepository
    strategy_router: StrategyRouter


def build_container(config: AppConfig) -> AppContainer:
    # ── Core ──────────────────────────────────────────────────────────────
    event_bus = EventBus()

    # ── Broker ────────────────────────────────────────────────────────────
    mt5_client = Mt5Client(config.mt5)
    mt5_orders = Mt5Orders(mt5_client)
    mt5_positions = Mt5Positions(mt5_client)

    # ── Storage ───────────────────────────────────────────────────────────
    trade_repo = TradeRepository(config.storage_path)
    position_store = PositionStore()

    # ── Risk + execution ──────────────────────────────────────────────────
    risk_engine = RiskEngine(config.risk)
    trade_planner = TradePlanner(config.risk, config.execution)
    order_manager = OrderManager(mt5_orders, mt5_positions, config.execution)

    execution_engine = ExecutionEngine(
        risk_engine=risk_engine,
        trade_planner=trade_planner,
        order_manager=order_manager,
        mt5_positions=mt5_positions,
        position_store=position_store,
        trade_repo=trade_repo,
        event_bus=event_bus,
        exec_config=config.execution,
    )

    # ── Position management ───────────────────────────────────────────────
    position_manager = PositionManager(
        store=position_store,
        mt5_pos=mt5_positions,
        mt5_orders=mt5_orders,
        repository=trade_repo,
        execution_engine=execution_engine,
        event_bus=event_bus,
        exec_config=config.execution,
        poll_interval=config.position_poll_interval,
    )

    # ── Signal ingestion ──────────────────────────────────────────────────
    validator = SignalValidator()
    signal_consumer = SignalConsumer(
        event_bus=event_bus,
        validator=validator,
        ws_url=config.signal.ws_url,
        symbols=config.signal.symbols,
    )

    # ── Strategies ────────────────────────────────────────────────────────
    strategy_router = StrategyRouter()
    strategy_router.register("default", PassthroughAdapter())

    return AppContainer(
        event_bus=event_bus,
        signal_consumer=signal_consumer,
        execution_engine=execution_engine,
        position_manager=position_manager,
        mt5_client=mt5_client,
        mt5_positions=mt5_positions,
        trade_repo=trade_repo,
        position_store=position_store,
        strategy_router=strategy_router,
    )
