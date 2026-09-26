from decimal import Decimal

import pytest

from trading_bot.domain import DomainValidationError, GridConfig, OrderSide, SymbolRules
from trading_bot.simulator import (
    GridExecutionSimulator,
    IntentRole,
    Liquidity,
    MarketTrade,
    SimulationConfig,
)
from trading_bot.strategy import generate_arithmetic_grid


def simulator(
    *, latency_ms: int = 0, queue_multiplier: Decimal = Decimal("0")
) -> GridExecutionSimulator:
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
    return GridExecutionSimulator(
        plan,
        SimulationConfig(
            order_latency_ms=latency_ms,
            queue_ahead_multiplier=queue_multiplier,
        ),
    )


def trade(
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


def test_initial_grid_creates_one_entry_intent_per_active_level() -> None:
    result = simulator().result()

    assert len(result.intents) == 5
    assert len(result.open_orders) == 5
    assert {intent.role for intent in result.intents} == {IntentRole.ENTRY}
    assert len({intent.intent_id for intent in result.intents}) == len(result.intents)
    assert [(order.level_index, order.side) for order in result.open_orders] == [
        (0, OrderSide.BUY),
        (1, OrderSide.BUY),
        (3, OrderSide.SELL),
        (4, OrderSide.SELL),
        (5, OrderSide.SELL),
    ]


def test_partial_fill_creates_replacement_for_filled_quantity_only() -> None:
    engine = simulator(latency_ms=50)

    fills = engine.process_trade(trade(100, "102", "0.05", OrderSide.SELL))
    result = engine.result()

    assert len(fills) == 1
    assert fills[0].level_index == 1
    assert fills[0].quantity == Decimal("0.05")
    replacement = result.intents[-1]
    assert replacement.level_index == 2
    assert replacement.side is OrderSide.SELL
    assert replacement.quantity == Decimal("0.05")
    assert replacement.role is IntentRole.EXIT
    assert replacement.opening_price == Decimal("102.0")
    assert replacement.active_from_ms == 150


def test_replacement_cannot_fill_before_latency_or_on_creating_trade() -> None:
    engine = simulator(latency_ms=50)
    engine.process_trade(trade(100, "102", "0.05", OrderSide.SELL))

    assert engine.process_trade(trade(100, "104", "1", OrderSide.BUY)) == ()
    assert engine.process_trade(trade(149, "104", "1", OrderSide.BUY)) == ()
    fills = engine.process_trade(trade(150, "104", "0.05", OrderSide.BUY))

    assert len(fills) == 1
    assert fills[0].side is OrderSide.SELL
    assert fills[0].role is IntentRole.EXIT


def test_queue_ahead_must_be_consumed_before_order_fills() -> None:
    engine = simulator(queue_multiplier=Decimal("1"))

    assert engine.process_trade(trade(1, "102", "0.12", OrderSide.SELL)) == ()
    fills = engine.process_trade(trade(2, "102", "0.05", OrderSide.SELL))

    assert len(fills) == 1
    assert fills[0].quantity == Decimal("0.05")


def test_trade_volume_is_allocated_in_price_priority_order() -> None:
    engine = simulator()

    fills = engine.process_trade(trade(1, "100", "0.15", OrderSide.SELL))

    assert [(fill.level_index, fill.quantity) for fill in fills] == [
        (1, Decimal("0.12")),
        (0, Decimal("0.03")),
    ]


def test_exit_fill_records_grid_profit_and_reopens_an_entry() -> None:
    engine = simulator()
    engine.process_trade(trade(1, "102", "0.05", OrderSide.SELL))

    fills = engine.process_trade(trade(2, "104", "0.05", OrderSide.BUY))
    result = engine.result()

    assert fills[0].gross_grid_profit == Decimal("0.10")
    assert result.intents[-1].level_index == 1
    assert result.intents[-1].side is OrderSide.BUY
    assert result.intents[-1].role is IntentRole.ENTRY
    assert result.intents[-1].opening_price is None


def test_multiple_partial_fills_share_one_active_logical_order_key() -> None:
    engine = simulator()
    engine.process_trade(trade(1, "102", "0.05", OrderSide.SELL))
    engine.process_trade(trade(2, "102", "0.04", OrderSide.SELL))

    matching_orders = [
        order
        for order in engine.result().open_orders
        if order.level_index == 2 and order.side is OrderSide.SELL
    ]

    assert len(matching_orders) == 1
    assert matching_orders[0].quantity == Decimal("0.09")
    assert matching_orders[0].intent_count == 2


def test_fill_preserves_simulated_liquidity_classification() -> None:
    engine = simulator()

    fills = engine.process_trade(trade(1, "102", "0.01", OrderSide.SELL, liquidity=Liquidity.TAKER))

    assert fills[0].liquidity is Liquidity.TAKER


def test_out_of_order_market_trade_is_rejected() -> None:
    engine = simulator()
    engine.process_trade(trade(2, "102", "0.01", OrderSide.SELL))

    with pytest.raises(DomainValidationError, match="chronological"):
        engine.process_trade(trade(1, "102", "0.01", OrderSide.SELL))


def test_simulator_configuration_rejects_binary_float() -> None:
    with pytest.raises(TypeError, match="queue_ahead_multiplier must be Decimal"):
        SimulationConfig(queue_ahead_multiplier=1.0)  # type: ignore[arg-type]
