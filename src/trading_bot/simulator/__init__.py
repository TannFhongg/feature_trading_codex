"""Deterministic exchange-free simulator for grid strategy validation."""

from trading_bot.simulator.backtest import GridBacktester
from trading_bot.simulator.execution import GridExecutionSimulator
from trading_bot.simulator.models import (
    IntentRole,
    Liquidity,
    MarketTrade,
    OpenOrder,
    OrderIntent,
    SimulatedFill,
    SimulationConfig,
    SimulationResult,
)
from trading_bot.simulator.reporting import (
    BacktestConfig,
    BacktestEvent,
    BacktestReport,
    CompletedGridTrade,
    FillAccounting,
    FundingEvent,
    FundingPayment,
)

__all__ = [
    "BacktestConfig",
    "BacktestEvent",
    "BacktestReport",
    "CompletedGridTrade",
    "FillAccounting",
    "FundingEvent",
    "FundingPayment",
    "GridBacktester",
    "GridExecutionSimulator",
    "IntentRole",
    "Liquidity",
    "MarketTrade",
    "OpenOrder",
    "OrderIntent",
    "SimulatedFill",
    "SimulationConfig",
    "SimulationResult",
]
