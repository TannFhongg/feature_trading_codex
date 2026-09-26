import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest

from trading_bot.binance import BinanceAmbiguousOrderError, BinanceApiError
from trading_bot.domain import OrderSide
from trading_bot.execution import (
    AccountUpdate,
    AmbiguousExecutionError,
    BalanceSnapshot,
    ExchangeOrder,
    ExecutionIntentAlreadyRecordedError,
    ExecutionType,
    IncomeRecord,
    OrderRequest,
    OrderStatus,
    OrderTradeUpdate,
    PersistentOrderExecutor,
    PositionSnapshot,
)
from trading_bot.persistence import LedgerConflictError, LedgerError, SqliteExecutionLedger


def request(
    *,
    strategy_id: str = "neutral-btc",
    level_index: int = 1,
    cycle: int = 1,
) -> OrderRequest:
    return OrderRequest(
        strategy_id=strategy_id,
        level_index=level_index,
        cycle=cycle,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("65000.10"),
        quantity=Decimal("0.002"),
    )


def exchange_order(
    order_request: OrderRequest,
    *,
    status: OrderStatus = OrderStatus.NEW,
    exchange_order_id: int = 42,
    executed_quantity: Decimal = Decimal("0"),
    average_price: Decimal = Decimal("0"),
    update_time_ms: int = 100,
) -> ExchangeOrder:
    return ExchangeOrder(
        symbol=order_request.symbol,
        client_order_id=order_request.client_order_id,
        exchange_order_id=exchange_order_id,
        side=order_request.side,
        status=status,
        price=order_request.price,
        original_quantity=order_request.quantity,
        executed_quantity=executed_quantity,
        average_price=average_price,
        reduce_only=order_request.reduce_only,
        update_time_ms=update_time_ms,
    )


def order_event(
    order: ExchangeOrder,
    *,
    execution_type: ExecutionType,
    event_time_ms: int,
    trade_id: int | None = None,
    last_quantity: Decimal = Decimal("0"),
) -> OrderTradeUpdate:
    is_trade = execution_type is ExecutionType.TRADE
    return OrderTradeUpdate(
        event_time_ms=event_time_ms,
        transaction_time_ms=event_time_ms,
        execution_type=execution_type,
        order=order,
        last_filled_quantity=last_quantity,
        last_filled_price=order.price if is_trade else Decimal("0"),
        trade_id=trade_id,
        commission=Decimal("0.026") if is_trade else Decimal("0"),
        commission_asset="USDT" if is_trade else None,
        realized_pnl=Decimal("0.01") if is_trade else Decimal("0"),
        maker=True,
    )


def test_intent_is_idempotent_and_active_logical_order_is_unique() -> None:
    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            first = request()
            assert await ledger.record_order_intent(first, created_at_ms=1)
            assert not await ledger.record_order_intent(first, created_at_ms=2)

            with pytest.raises(LedgerConflictError, match="active logical order"):
                await ledger.record_order_intent(request(cycle=2), created_at_ms=3)

            await ledger.apply_order_snapshot(
                exchange_order(
                    first,
                    status=OrderStatus.CANCELED,
                    update_time_ms=4,
                )
            )
            assert await ledger.record_order_intent(request(cycle=2), created_at_ms=5)

    asyncio.run(scenario())


def test_exchange_timestamp_replaces_local_pending_timestamp() -> None:
    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            order_request = request()
            await ledger.record_order_intent(order_request, created_at_ms=10_000)
            await ledger.apply_order_snapshot(
                exchange_order(order_request, status=OrderStatus.NEW, update_time_ms=100)
            )
            await ledger.apply_order_snapshot(
                exchange_order(order_request, status=OrderStatus.CANCELED, update_time_ms=101)
            )

            stored = await ledger.get_order(order_request.client_order_id)
            assert stored is not None
            assert stored.status is OrderStatus.CANCELED
            assert stored.last_event_time_ms == 101

    asyncio.run(scenario())


def test_exchange_snapshot_cannot_rebind_an_owned_order_intent() -> None:
    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            order_request = request()
            await ledger.record_order_intent(order_request, created_at_ms=1)

            with pytest.raises(LedgerConflictError, match="persisted order intent"):
                await ledger.apply_order_snapshot(
                    replace(exchange_order(order_request), price=Decimal("65001.10"))
                )

            stored = await ledger.get_order(order_request.client_order_id)
            assert stored is not None
            assert stored.status is OrderStatus.PENDING_SUBMIT
            assert stored.price == order_request.price

    asyncio.run(scenario())


def test_duplicate_trade_event_inserts_exactly_one_fill_and_event() -> None:
    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            order_request = request()
            await ledger.record_order_intent(order_request, created_at_ms=1)
            filled = exchange_order(
                order_request,
                status=OrderStatus.PARTIALLY_FILLED,
                executed_quantity=Decimal("0.001"),
                average_price=Decimal("65000.10"),
                update_time_ms=10,
            )
            event = order_event(
                filled,
                execution_type=ExecutionType.TRADE,
                event_time_ms=10,
                trade_id=777,
                last_quantity=Decimal("0.001"),
            )

            first = await ledger.apply_order_event(event)
            duplicate = await ledger.apply_order_event(event)

            assert first.fill_inserted
            assert not first.duplicate_event
            assert duplicate.duplicate_event
            assert not duplicate.fill_inserted
            assert len(await ledger.list_fills("BTCUSDT")) == 1
            assert await ledger.count_exchange_events() == 1

    asyncio.run(scenario())


def test_duplicate_financial_business_keys_must_match_original_records() -> None:
    fill = order_event(
        exchange_order(
            request(),
            status=OrderStatus.PARTIALLY_FILLED,
            executed_quantity=Decimal("0.001"),
            average_price=Decimal("65000.10"),
            update_time_ms=10,
        ),
        execution_type=ExecutionType.TRADE,
        event_time_ms=10,
        trade_id=780,
        last_quantity=Decimal("0.001"),
    ).fill
    assert fill is not None
    income = IncomeRecord(
        symbol="BTCUSDT",
        income_type="FUNDING_FEE",
        transaction_id=900,
        asset="USDT",
        amount=Decimal("-0.05"),
        event_time_ms=20,
    )

    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            assert await ledger.record_fill(fill)
            assert not await ledger.record_fill(fill)
            with pytest.raises(LedgerConflictError, match="different fill"):
                await ledger.record_fill(replace(fill, commission=Decimal("0.027")))

            assert await ledger.record_income_records((income,)) == 1
            assert await ledger.record_income_records((income,)) == 0
            with pytest.raises(LedgerConflictError, match="different record"):
                await ledger.record_income_records((replace(income, amount=Decimal("-0.06")),))

    asyncio.run(scenario())


def test_late_cancel_event_cannot_regress_a_completed_fill() -> None:
    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            order_request = request()
            await ledger.record_order_intent(order_request, created_at_ms=1)
            filled = exchange_order(
                order_request,
                status=OrderStatus.FILLED,
                executed_quantity=Decimal("0.002"),
                average_price=Decimal("65000.10"),
                update_time_ms=200,
            )
            await ledger.apply_order_event(
                order_event(
                    filled,
                    execution_type=ExecutionType.TRADE,
                    event_time_ms=200,
                    trade_id=778,
                    last_quantity=Decimal("0.002"),
                )
            )
            stale_cancel = exchange_order(
                order_request,
                status=OrderStatus.CANCELED,
                executed_quantity=Decimal("0"),
                update_time_ms=150,
            )
            result = await ledger.apply_order_event(
                order_event(
                    stale_cancel,
                    execution_type=ExecutionType.CANCELED,
                    event_time_ms=150,
                )
            )

            stored = await ledger.get_order(order_request.client_order_id)
            assert not result.order_updated
            assert stored is not None
            assert stored.status is OrderStatus.FILLED
            assert stored.executed_quantity == Decimal("0.002")
            assert len(await ledger.list_fills("BTCUSDT")) == 1

    asyncio.run(scenario())


def test_late_fill_progress_cannot_reopen_a_canceled_order() -> None:
    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            order_request = request()
            await ledger.record_order_intent(order_request, created_at_ms=1)
            await ledger.apply_order_snapshot(
                exchange_order(
                    order_request,
                    status=OrderStatus.CANCELED,
                    update_time_ms=100,
                )
            )
            late_partial_fill = exchange_order(
                order_request,
                status=OrderStatus.PARTIALLY_FILLED,
                executed_quantity=Decimal("0.001"),
                average_price=Decimal("65000.10"),
                update_time_ms=101,
            )

            result = await ledger.apply_order_event(
                order_event(
                    late_partial_fill,
                    execution_type=ExecutionType.TRADE,
                    event_time_ms=101,
                    trade_id=779,
                    last_quantity=Decimal("0.001"),
                )
            )

            stored = await ledger.get_order(order_request.client_order_id)
            assert result.order_updated
            assert result.fill_inserted
            assert stored is not None
            assert stored.status is OrderStatus.CANCELED
            assert stored.executed_quantity == Decimal("0.001")

    asyncio.run(scenario())


def test_account_update_is_idempotent_and_rejects_duplicate_business_event() -> None:
    event = AccountUpdate(
        event_time_ms=100,
        transaction_time_ms=99,
        reason="ORDER",
        balances=(
            BalanceSnapshot(
                asset="USDT",
                wallet_balance=Decimal("1000"),
                cross_wallet_balance=Decimal("900"),
                balance_change=Decimal("0"),
            ),
        ),
        positions=(
            PositionSnapshot(
                symbol="BTCUSDT",
                position_side="BOTH",
                quantity=Decimal("0.001"),
                entry_price=Decimal("65000.10"),
                break_even_price=Decimal("65026.10"),
                unrealized_pnl=Decimal("1.5"),
                margin_type="isolated",
                isolated_wallet=Decimal("100"),
                update_time_ms=99,
            ),
        ),
    )

    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            assert await ledger.apply_account_update(event)
            assert not await ledger.apply_account_update(event)
            assert await ledger.count_exchange_events() == 1

    asyncio.run(scenario())


def test_file_ledger_survives_close_and_reopen(tmp_path: object) -> None:
    from pathlib import Path

    database = Path(str(tmp_path)) / "execution.sqlite3"
    order_request = request()

    async def scenario() -> None:
        ledger = await SqliteExecutionLedger.open(database)
        await ledger.record_order_intent(order_request, created_at_ms=1)
        await ledger.apply_order_snapshot(exchange_order(order_request, update_time_ms=2))
        await ledger.close()

        reopened = await SqliteExecutionLedger.open(database)
        stored = await reopened.get_order(order_request.client_order_id)
        assert stored is not None
        assert stored.exchange_order_id == 42
        assert stored.price == Decimal("65000.10")
        await reopened.close()
        with pytest.raises(LedgerError, match="closed"):
            await reopened.get_order(order_request.client_order_id)

    asyncio.run(scenario())


class ScriptedExecutionGateway:
    def __init__(self, response: ExchangeOrder | BaseException) -> None:
        self.response = response
        self.submit_calls = 0

    async def submit_order(self, order_request: OrderRequest) -> ExchangeOrder:
        del order_request
        self.submit_calls += 1
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def cancel_order(self, symbol: str, client_order_id: str) -> ExchangeOrder | None:
        del symbol, client_order_id
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_persistent_executor_records_success_unknown_and_definite_rejection() -> None:
    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            successful_request = request(level_index=1)
            successful = PersistentOrderExecutor(
                ScriptedExecutionGateway(exchange_order(successful_request)), ledger
            )
            await successful.submit(successful_request)
            stored = await ledger.get_order(successful_request.client_order_id)
            assert stored is not None and stored.status is OrderStatus.NEW

            ambiguous_request = request(level_index=2)
            ambiguous = PersistentOrderExecutor(
                ScriptedExecutionGateway(
                    BinanceAmbiguousOrderError(ambiguous_request.client_order_id, "submission")
                ),
                ledger,
            )
            with pytest.raises(AmbiguousExecutionError):
                await ambiguous.submit(ambiguous_request)
            stored = await ledger.get_order(ambiguous_request.client_order_id)
            assert stored is not None and stored.status is OrderStatus.UNKNOWN

            rejected_request = request(level_index=3)
            rejected = PersistentOrderExecutor(
                ScriptedExecutionGateway(BinanceApiError(400, -2010)), ledger
            )
            with pytest.raises(BinanceApiError):
                await rejected.submit(rejected_request)
            stored = await ledger.get_order(rejected_request.client_order_id)
            assert stored is not None and stored.status is OrderStatus.SUBMISSION_REJECTED

    asyncio.run(scenario())


def test_persistent_executor_never_resubmits_an_existing_intent() -> None:
    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            order_request = request()
            gateway = ScriptedExecutionGateway(exchange_order(order_request))
            executor = PersistentOrderExecutor(gateway, ledger)

            await executor.submit(order_request)
            with pytest.raises(ExecutionIntentAlreadyRecordedError, match="query or reconcile"):
                await executor.submit(order_request)

            assert gateway.submit_calls == 1
            stored = await ledger.get_order(order_request.client_order_id)
            assert stored is not None and stored.status is OrderStatus.NEW

    asyncio.run(scenario())
