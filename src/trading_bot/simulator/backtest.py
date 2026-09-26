"""Backtest orchestration and performance accounting for simulated grid fills."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from trading_bot.domain import DomainValidationError, GridPlan
from trading_bot.simulator.accounting import PositionLedger
from trading_bot.simulator.execution import GridExecutionSimulator
from trading_bot.simulator.models import Liquidity, MarketTrade, SimulatedFill, _require_decimal
from trading_bot.simulator.reporting import (
    BacktestConfig,
    BacktestEvent,
    BacktestReport,
    CompletedGridTrade,
    FillAccounting,
    FundingEvent,
    FundingPayment,
)


@dataclass(slots=True)
class _PerformanceTracker:
    peak_equity: Decimal
    max_drawdown: Decimal = Decimal("0")
    max_drawdown_ratio: Decimal = Decimal("0")
    max_abs_position: Decimal = Decimal("0")
    max_notional: Decimal = Decimal("0")

    def observe(self, *, equity: Decimal, position: Decimal, mark_price: Decimal) -> None:
        self.peak_equity = max(self.peak_equity, equity)
        drawdown = self.peak_equity - equity
        self.max_drawdown = max(self.max_drawdown, drawdown)
        if self.peak_equity > 0:
            self.max_drawdown_ratio = max(self.max_drawdown_ratio, drawdown / self.peak_equity)
        self.max_abs_position = max(self.max_abs_position, abs(position))
        self.max_notional = max(self.max_notional, abs(position * mark_price))


class GridBacktester:
    """Run chronologically ordered trade and funding events through a grid plan."""

    def __init__(self, plan: GridPlan, config: BacktestConfig) -> None:
        self._plan = plan
        self._config = config

    def run(
        self,
        events: Iterable[BacktestEvent],
        *,
        final_mark_price: Decimal | None = None,
    ) -> BacktestReport:
        """Execute a fresh deterministic run and return its immutable report."""

        execution = GridExecutionSimulator(self._plan, self._config.simulation)
        position = PositionLedger()
        fill_records: list[FillAccounting] = []
        fill_records_by_id: dict[str, FillAccounting] = {}
        completed_grid_trades: list[CompletedGridTrade] = []
        funding_payments: list[FundingPayment] = []
        maker_fees = Decimal("0")
        taker_fees = Decimal("0")
        funding_pnl = Decimal("0")
        mark_price = self._plan.levels[self._plan.anchor_index].price
        last_event_time_ms = self._config.simulation.start_time_ms
        tracker = _PerformanceTracker(peak_equity=self._config.initial_balance)
        tracker.observe(
            equity=self._config.initial_balance,
            position=position.quantity,
            mark_price=mark_price,
        )

        for event in events:
            if not isinstance(event, (MarketTrade, FundingEvent)):
                raise TypeError(f"unsupported backtest event: {type(event).__name__}")
            if event.event_time_ms < last_event_time_ms:
                raise DomainValidationError("backtest events must be in chronological order")
            last_event_time_ms = event.event_time_ms
            if isinstance(event, MarketTrade):
                mark_price = event.price
                self._observe(
                    tracker,
                    position,
                    mark_price,
                    maker_fees + taker_fees,
                    funding_pnl,
                )
                for fill in execution.process_trade(event):
                    fee = self._fill_fee(fill)
                    if fill.liquidity is Liquidity.MAKER:
                        maker_fees += fee
                    else:
                        taker_fees += fee
                    realized = position.apply_fill(fill)
                    fill_record = FillAccounting(
                        fill=fill,
                        fee=fee,
                        realized_pnl=realized,
                        position_after=position.quantity,
                        average_entry_price_after=position.average_entry_price,
                    )
                    fill_records.append(fill_record)
                    fill_records_by_id[fill.fill_id] = fill_record
                    if fill.opening_fill_id is not None:
                        completed_grid_trades.append(
                            self._complete_grid_trade(
                                fill,
                                closing_fee=fee,
                                fill_records_by_id=fill_records_by_id,
                            )
                        )
                    self._observe(
                        tracker,
                        position,
                        mark_price,
                        maker_fees + taker_fees,
                        funding_pnl,
                    )
            elif isinstance(event, FundingEvent):
                mark_price = event.mark_price
                amount = -(position.quantity * event.mark_price * event.rate)
                funding_pnl += amount
                funding_payments.append(
                    FundingPayment(
                        event_time_ms=event.event_time_ms,
                        rate=event.rate,
                        mark_price=event.mark_price,
                        position_quantity=position.quantity,
                        amount=amount,
                    )
                )
                self._observe(
                    tracker,
                    position,
                    mark_price,
                    maker_fees + taker_fees,
                    funding_pnl,
                )
        if final_mark_price is not None:
            mark_price = _require_decimal("final_mark_price", final_mark_price, positive=True)
        total_fees = maker_fees + taker_fees
        self._observe(tracker, position, mark_price, total_fees, funding_pnl)
        unrealized_pnl = position.unrealized_pnl(mark_price)
        final_balance = (
            self._config.initial_balance + position.realized_pnl - total_fees + funding_pnl
        )
        final_equity = final_balance + unrealized_pnl
        net_pnl = final_equity - self._config.initial_balance
        simulation = execution.result()
        gross_grid_profit = sum(
            (trade.gross_profit for trade in completed_grid_trades), start=Decimal("0")
        )
        net_grid_profit = sum(
            (trade.net_profit for trade in completed_grid_trades), start=Decimal("0")
        )
        completed_grid_count = len(completed_grid_trades)
        profit_per_completed_grid = (
            net_grid_profit / completed_grid_count if completed_grid_count else Decimal("0")
        )
        fill_ratio = (
            simulation.filled_quantity / simulation.submitted_quantity
            if simulation.submitted_quantity > 0
            else Decimal("0")
        )
        funding_pnl_ratio = abs(funding_pnl) / abs(net_pnl) if net_pnl != 0 else None
        return BacktestReport(
            simulation=simulation,
            fill_accounting=tuple(fill_records),
            funding_payments=tuple(funding_payments),
            completed_grid_trades=tuple(completed_grid_trades),
            initial_balance=self._config.initial_balance,
            final_mark_price=mark_price,
            final_position=position.quantity,
            average_entry_price=position.average_entry_price,
            realized_pnl=position.realized_pnl,
            unrealized_pnl=unrealized_pnl,
            maker_fees=maker_fees,
            taker_fees=taker_fees,
            total_fees=total_fees,
            funding_pnl=funding_pnl,
            final_balance=final_balance,
            final_equity=final_equity,
            net_pnl=net_pnl,
            gross_grid_profit=gross_grid_profit,
            net_grid_profit=net_grid_profit,
            completed_grid_count=completed_grid_count,
            profit_per_completed_grid=profit_per_completed_grid,
            max_drawdown=tracker.max_drawdown,
            max_drawdown_ratio=tracker.max_drawdown_ratio,
            max_abs_position=tracker.max_abs_position,
            max_notional=tracker.max_notional,
            fill_ratio=fill_ratio,
            funding_pnl_ratio=funding_pnl_ratio,
        )

    def _fill_fee(self, fill: SimulatedFill) -> Decimal:
        rate = (
            self._config.maker_fee_rate
            if fill.liquidity is Liquidity.MAKER
            else self._config.taker_fee_rate
        )
        return fill.price * fill.quantity * rate

    @staticmethod
    def _complete_grid_trade(
        fill: SimulatedFill,
        *,
        closing_fee: Decimal,
        fill_records_by_id: dict[str, FillAccounting],
    ) -> CompletedGridTrade:
        if fill.opening_fill_id is None:
            raise DomainValidationError("completed grid fill is missing opening_fill_id")
        try:
            opening_record = fill_records_by_id[fill.opening_fill_id]
        except KeyError as error:
            raise DomainValidationError(
                "completed grid fill references an unknown opening fill"
            ) from error
        opening_fee = opening_record.fee / opening_record.fill.quantity * fill.quantity
        return CompletedGridTrade(
            opening_fill_id=fill.opening_fill_id,
            closing_fill_id=fill.fill_id,
            quantity=fill.quantity,
            gross_profit=fill.gross_grid_profit,
            opening_fee=opening_fee,
            closing_fee=closing_fee,
            net_profit=fill.gross_grid_profit - opening_fee - closing_fee,
        )

    def _observe(
        self,
        tracker: _PerformanceTracker,
        position: PositionLedger,
        mark_price: Decimal,
        total_fees: Decimal,
        funding_pnl: Decimal,
    ) -> None:
        equity = (
            self._config.initial_balance
            + position.realized_pnl
            + position.unrealized_pnl(mark_price)
            - total_fees
            + funding_pnl
        )
        tracker.observe(equity=equity, position=position.quantity, mark_price=mark_price)
