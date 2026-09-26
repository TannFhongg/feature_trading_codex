from decimal import Decimal

from trading_bot.application.strategy_runtime import NeutralGridRuntime
from trading_bot.domain import GridConfig, OrderSide, SymbolRules
from trading_bot.execution import (
    ExchangeOrder,
    ExecutionType,
    OrderRecord,
    OrderStatus,
    OrderTradeUpdate,
)
from trading_bot.strategy import generate_arithmetic_grid


def runtime() -> NeutralGridRuntime:
    config = GridConfig(
        symbol="BTCUSDT",
        lower_price=Decimal("50000"),
        upper_price=Decimal("70000"),
        reference_price=Decimal("60000"),
        grid_count=4,
        quantity_per_order=Decimal("0.002"),
    )
    rules = SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    return NeutralGridRuntime("grid-a", generate_arithmetic_grid(config, rules), rules)


def fill_event(
    request: object,
    *,
    status: OrderStatus = OrderStatus.FILLED,
    quantity: Decimal = Decimal("0.002"),
    trade_id: int = 1,
) -> OrderTradeUpdate:
    from trading_bot.execution import OrderRequest

    assert isinstance(request, OrderRequest)
    order = ExchangeOrder(
        symbol=request.symbol,
        client_order_id=request.client_order_id,
        exchange_order_id=10,
        side=request.side,
        status=status,
        price=request.price,
        original_quantity=request.quantity,
        executed_quantity=quantity,
        average_price=request.price,
        reduce_only=False,
        update_time_ms=100,
    )
    return OrderTradeUpdate(
        event_time_ms=100,
        transaction_time_ms=100,
        execution_type=ExecutionType.TRADE,
        order=order,
        last_filled_quantity=quantity,
        last_filled_price=request.price,
        trade_id=trade_id,
        commission=Decimal("0"),
        commission_asset="USDT",
        realized_pnl=Decimal("0"),
        maker=True,
    )


def test_initial_grid_and_confirmed_fill_emit_deterministic_adjacent_replacement() -> None:
    strategy = runtime()
    initial = strategy.initial_intents()

    assert len(initial) == 4
    buy = next(order for order in initial if order.level_index == 1)
    replacement = strategy.on_user_event(fill_event(buy))

    assert len(replacement) == 1
    assert replacement[0].level_index == 2
    assert replacement[0].side is OrderSide.SELL
    assert replacement[0].quantity == Decimal("0.002")
    assert strategy.on_user_event(fill_event(buy)) == ()


def test_partial_fill_emits_filter_valid_quantity_and_blocks_duplicate_logical_order() -> None:
    strategy = runtime()
    buy = next(order for order in strategy.initial_intents() if order.level_index == 1)

    first = strategy.on_user_event(
        fill_event(
            buy,
            status=OrderStatus.PARTIALLY_FILLED,
            quantity=Decimal("0.001"),
        )
    )
    second = strategy.on_user_event(
        fill_event(
            buy,
            status=OrderStatus.FILLED,
            quantity=Decimal("0.001"),
            trade_id=2,
        )
    )

    assert first[0].quantity == Decimal("0.001")
    assert second == ()


def test_restore_advances_cycle_and_never_reopens_an_active_logical_slot() -> None:
    strategy = runtime()
    first = strategy.initial_intents()[0]
    strategy.restore(
        (
            OrderRecord(
                client_order_id=first.client_order_id,
                strategy_id=first.strategy_id,
                level_index=first.level_index,
                cycle=first.cycle,
                exchange_order_id=1,
                symbol=first.symbol,
                side=first.side,
                status=OrderStatus.CANCELED,
                price=first.price,
                original_quantity=first.quantity,
                executed_quantity=Decimal("0"),
                average_price=Decimal("0"),
                reduce_only=False,
                last_event_time_ms=1,
            ),
        )
    )

    restored = strategy.initial_intents()
    reopened = next(
        order
        for order in restored
        if order.level_index == first.level_index and order.side is first.side
    )
    assert reopened.cycle == first.cycle + 1
    assert reopened.client_order_id != first.client_order_id
