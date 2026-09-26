from decimal import Decimal

import pytest

from trading_bot.domain import DomainValidationError, GridConfig, OrderSide, SymbolRules
from trading_bot.simulator import (
    BacktestConfig,
    FundingEvent,
    GridBacktester,
    Liquidity,
    MarketTrade,
    SimulationConfig,
)
from trading_bot.strategy import generate_arithmetic_grid


def backtester(
    *,
    maker_fee_rate: Decimal = Decimal("0.001"),
    taker_fee_rate: Decimal = Decimal("0.002"),
) -> GridBacktester:
    plan = generate_arithmetic_grid(
        GridConfig(
            symbol="BTCUSDT",
            lower_price=Decimal("100"),
            upper_price=Decimal("110"),
            reference_price=Decimal("105"),
            grid_count=5,
            quantity_per_order=Decimal("0.12"),
        ),
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.1"),
            step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"),
            min_notional=Decimal("5"),
        ),
    )
    return GridBacktester(
        plan,
        BacktestConfig(
            initial_balance=Decimal("1000"),
            maker_fee_rate=maker_fee_rate,
            taker_fee_rate=taker_fee_rate,
            simulation=SimulationConfig(queue_ahead_multiplier=Decimal("0")),
        ),
    )


def market_trade(
    event_time_ms: int,
    price: str,
    quantity: str,
    aggressor_side: OrderSide,
    *,
    liquidity: Liquidity = Liquidity.MAKER,
) -> MarketTrade:
    return MarketTrade(
        event_time_ms=event_time_ms,
        price=Decimal(price),
        quantity=Decimal(quantity),
        aggressor_side=aggressor_side,
        liquidity=liquidity,
    )


def test_round_trip_reports_fees_realized_pnl_and_grid_profit() -> None:
    report = backtester().run(
        [
            market_trade(1, "102", "0.12", OrderSide.SELL),
            market_trade(2, "104", "0.12", OrderSide.BUY),
        ]
    )

    assert report.final_position == Decimal("0")
    assert report.realized_pnl == Decimal("0.24")
    assert report.unrealized_pnl == Decimal("0")
    assert report.maker_fees == Decimal("0.02472")
    assert report.taker_fees == Decimal("0")
    assert report.net_pnl == Decimal("0.21528")
    assert report.final_equity == Decimal("1000.21528")
    assert report.gross_grid_profit == Decimal("0.24")
    assert report.net_grid_profit == Decimal("0.21528")
    assert report.completed_grid_count == 1
    assert report.profit_per_completed_grid == Decimal("0.21528")


def test_partial_exit_allocates_opening_fee_proportionally() -> None:
    report = backtester().run(
        [
            market_trade(1, "102", "0.12", OrderSide.SELL),
            market_trade(2, "104", "0.05", OrderSide.BUY),
        ],
        final_mark_price=Decimal("104"),
    )

    grid_trade = report.completed_grid_trades[0]
    assert grid_trade.quantity == Decimal("0.05")
    assert grid_trade.opening_fee == Decimal("0.00510")
    assert grid_trade.closing_fee == Decimal("0.00520")
    assert grid_trade.net_profit == Decimal("0.08970")
    assert report.final_position == Decimal("0.07")


def test_taker_fill_uses_taker_fee_rate() -> None:
    report = backtester().run(
        [
            market_trade(
                1,
                "102",
                "0.12",
                OrderSide.SELL,
                liquidity=Liquidity.TAKER,
            )
        ],
        final_mark_price=Decimal("102"),
    )

    assert report.maker_fees == Decimal("0")
    assert report.taker_fees == Decimal("0.02448")
    assert report.net_pnl == Decimal("-0.02448")


def test_positive_funding_rate_charges_long_position() -> None:
    report = backtester().run(
        [
            market_trade(1, "102", "0.12", OrderSide.SELL),
            FundingEvent(
                event_time_ms=2,
                rate=Decimal("0.001"),
                mark_price=Decimal("103"),
            ),
        ]
    )

    assert report.funding_pnl == Decimal("-0.01236")
    assert report.funding_payments[0].position_quantity == Decimal("0.12")
    assert report.unrealized_pnl == Decimal("0.12")
    assert report.net_pnl == Decimal("0.09540")
    assert report.funding_pnl_ratio == Decimal("0.01236") / Decimal("0.09540")


def test_positive_funding_rate_pays_short_position() -> None:
    report = backtester().run(
        [
            market_trade(1, "106", "0.12", OrderSide.BUY),
            FundingEvent(
                event_time_ms=2,
                rate=Decimal("0.001"),
                mark_price=Decimal("105"),
            ),
        ]
    )

    assert report.final_position == Decimal("-0.12")
    assert report.average_entry_price == Decimal("106.0")
    assert report.funding_pnl == Decimal("0.01260")
    assert report.unrealized_pnl == Decimal("0.120")


def test_report_tracks_drawdown_inventory_notional_and_fill_ratio() -> None:
    report = backtester(maker_fee_rate=Decimal("0")).run(
        [
            market_trade(1, "102", "0.12", OrderSide.SELL),
            market_trade(2, "100", "0.01", OrderSide.BUY),
        ],
        final_mark_price=Decimal("100"),
    )

    assert report.max_drawdown == Decimal("0.24")
    assert report.max_drawdown_ratio == Decimal("0.00024")
    assert report.max_abs_position == Decimal("0.12")
    assert report.max_notional == Decimal("12.24")
    assert report.fill_ratio == Decimal("0.12") / Decimal("0.72")


def test_empty_run_has_zero_pnl_and_uses_anchor_as_mark() -> None:
    report = backtester().run([])

    assert report.final_mark_price == Decimal("104.0")
    assert report.final_equity == Decimal("1000")
    assert report.net_pnl == Decimal("0")
    assert report.completed_grid_count == 0
    assert report.profit_per_completed_grid == Decimal("0")
    assert report.funding_pnl_ratio is None


def test_backtest_rejects_events_out_of_chronological_order() -> None:
    events = [
        FundingEvent(2, Decimal("0.001"), Decimal("103")),
        market_trade(1, "102", "0.01", OrderSide.SELL),
    ]

    with pytest.raises(DomainValidationError, match="chronological"):
        backtester().run(events)


def test_backtest_rejects_unknown_event_type() -> None:
    with pytest.raises(TypeError, match="unsupported backtest event"):
        backtester().run([object()])  # type: ignore[list-item]


def test_backtest_config_rejects_float_and_invalid_fee_rate() -> None:
    with pytest.raises(TypeError, match="initial_balance must be Decimal"):
        BacktestConfig(  # type: ignore[arg-type]
            initial_balance=1000.0,
            maker_fee_rate=Decimal("0.001"),
            taker_fee_rate=Decimal("0.002"),
        )

    with pytest.raises(DomainValidationError, match="less than one"):
        BacktestConfig(
            initial_balance=Decimal("1000"),
            maker_fee_rate=Decimal("1"),
            taker_fee_rate=Decimal("0.002"),
        )


def test_final_mark_price_must_be_positive_decimal() -> None:
    with pytest.raises(TypeError, match="final_mark_price must be Decimal"):
        backtester().run([], final_mark_price=100.0)  # type: ignore[arg-type]

    with pytest.raises(DomainValidationError, match="greater than zero"):
        backtester().run([], final_mark_price=Decimal("0"))
