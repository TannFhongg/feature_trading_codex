"""Backtest configuration, funding events, and immutable report records."""

from dataclasses import dataclass, field
from decimal import Decimal

from trading_bot.domain import DomainValidationError
from trading_bot.simulator.models import (
    MarketTrade,
    SimulatedFill,
    SimulationConfig,
    SimulationResult,
    _require_decimal,
    _require_non_negative_int,
)


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Fee, capital, and execution assumptions for a single backtest run."""

    initial_balance: Decimal
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    simulation: SimulationConfig = field(default_factory=SimulationConfig)

    def __post_init__(self) -> None:
        _require_decimal("initial_balance", self.initial_balance, positive=True)
        maker_fee_rate = _require_decimal("maker_fee_rate", self.maker_fee_rate, non_negative=True)
        taker_fee_rate = _require_decimal("taker_fee_rate", self.taker_fee_rate, non_negative=True)
        if maker_fee_rate >= 1 or taker_fee_rate >= 1:
            raise DomainValidationError("fee rates must be less than one")
        if not isinstance(self.simulation, SimulationConfig):
            raise TypeError("simulation must be SimulationConfig")


@dataclass(frozen=True, slots=True)
class FundingEvent:
    """A funding timestamp with the applicable rate and mark price."""

    event_time_ms: int
    rate: Decimal
    mark_price: Decimal

    def __post_init__(self) -> None:
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        _require_decimal("rate", self.rate)
        _require_decimal("mark_price", self.mark_price, positive=True)


BacktestEvent = MarketTrade | FundingEvent


@dataclass(frozen=True, slots=True)
class FillAccounting:
    """Fee and position impact associated with one simulated fill."""

    fill: SimulatedFill
    fee: Decimal
    realized_pnl: Decimal
    position_after: Decimal
    average_entry_price_after: Decimal


@dataclass(frozen=True, slots=True)
class FundingPayment:
    """Funding cash flow; a positive amount is received by the strategy."""

    event_time_ms: int
    rate: Decimal
    mark_price: Decimal
    position_quantity: Decimal
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CompletedGridTrade:
    """A paired entry/exit slice with allocated opening and closing fees."""

    opening_fill_id: str
    closing_fill_id: str
    quantity: Decimal
    gross_profit: Decimal
    opening_fee: Decimal
    closing_fee: Decimal
    net_profit: Decimal


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Auditable performance and risk summary for one deterministic run."""

    simulation: SimulationResult
    fill_accounting: tuple[FillAccounting, ...]
    funding_payments: tuple[FundingPayment, ...]
    completed_grid_trades: tuple[CompletedGridTrade, ...]
    initial_balance: Decimal
    final_mark_price: Decimal
    final_position: Decimal
    average_entry_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    maker_fees: Decimal
    taker_fees: Decimal
    total_fees: Decimal
    funding_pnl: Decimal
    final_balance: Decimal
    final_equity: Decimal
    net_pnl: Decimal
    gross_grid_profit: Decimal
    net_grid_profit: Decimal
    completed_grid_count: int
    profit_per_completed_grid: Decimal
    max_drawdown: Decimal
    max_drawdown_ratio: Decimal
    max_abs_position: Decimal
    max_notional: Decimal
    fill_ratio: Decimal
    funding_pnl_ratio: Decimal | None
