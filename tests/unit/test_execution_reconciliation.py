import asyncio
from collections.abc import Iterator
from decimal import Decimal

from trading_bot.domain import OrderSide
from trading_bot.execution import (
    AccountSnapshot,
    BalanceSnapshot,
    ExchangeFill,
    ExchangeOrder,
    ExecutionReconciler,
    IncomeRecord,
    OrderRequest,
    OrderStatus,
    PositionSnapshot,
)
from trading_bot.persistence import SqliteExecutionLedger


def request(level_index: int) -> OrderRequest:
    return OrderRequest(
        strategy_id="neutral-btc",
        level_index=level_index,
        cycle=1,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("65000.10"),
        quantity=Decimal("0.002"),
    )


def order(
    order_request: OrderRequest,
    exchange_order_id: int,
    *,
    status: OrderStatus = OrderStatus.NEW,
    executed_quantity: Decimal = Decimal("0"),
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
        average_price=(Decimal("65000.10") if executed_quantity > 0 else Decimal("0")),
        reduce_only=False,
        update_time_ms=update_time_ms,
    )


class FakeReconciliationGateway:
    def __init__(
        self,
        *,
        open_orders: tuple[ExchangeOrder, ...],
        queried_orders: dict[str, ExchangeOrder | None],
        fills: tuple[ExchangeFill, ...] = (),
    ) -> None:
        self.open_orders = open_orders
        self.queried_orders = queried_orders
        self.fills = fills
        self.query_calls: list[str] = []

    async def list_open_orders(self, symbol: str) -> tuple[ExchangeOrder, ...]:
        assert symbol == "BTCUSDT"
        return self.open_orders

    async def query_order(self, symbol: str, client_order_id: str) -> ExchangeOrder | None:
        assert symbol == "BTCUSDT"
        self.query_calls.append(client_order_id)
        return self.queried_orders[client_order_id]

    async def list_account_trades(
        self,
        symbol: str,
        *,
        from_id: int | None = None,
        limit: int = 1_000,
    ) -> tuple[ExchangeFill, ...]:
        assert symbol == "BTCUSDT"
        assert from_id is None
        assert limit == 1_000
        return self.fills

    async def fetch_positions(self, symbol: str) -> tuple[PositionSnapshot, ...]:
        assert symbol == "BTCUSDT"
        return (
            PositionSnapshot(
                symbol="BTCUSDT",
                position_side="BOTH",
                quantity=Decimal("0.002"),
                entry_price=Decimal("65000.10"),
                break_even_price=Decimal("65026.10"),
                unrealized_pnl=Decimal("1.5"),
                margin_type="isolated",
                isolated_wallet=Decimal("100"),
                update_time_ms=500,
            ),
        )

    async def fetch_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            total_wallet_balance=Decimal("1000"),
            total_unrealized_profit=Decimal("1.5"),
            total_margin_balance=Decimal("1001.5"),
            available_balance=Decimal("900"),
            update_time_ms=500,
            balances=(
                BalanceSnapshot(
                    asset="USDT",
                    wallet_balance=Decimal("1000"),
                    cross_wallet_balance=Decimal("900"),
                    balance_change=Decimal("0"),
                ),
            ),
        )

    async def list_income(
        self,
        symbol: str,
        *,
        income_type: str = "FUNDING_FEE",
        limit: int = 1_000,
    ) -> tuple[IncomeRecord, ...]:
        assert symbol == "BTCUSDT"
        assert income_type == "FUNDING_FEE"
        assert limit == 1_000
        return (
            IncomeRecord(
                symbol="BTCUSDT",
                income_type="FUNDING_FEE",
                transaction_id=900,
                asset="USDT",
                amount=Decimal("-0.05"),
                event_time_ms=450,
            ),
        )


def test_reconciliation_repairs_terminal_order_fill_position_and_income_idempotently() -> None:
    local_request = request(1)
    orphan_request = request(2)
    local_new = order(local_request, 42)
    local_filled = order(
        local_request,
        42,
        status=OrderStatus.FILLED,
        executed_quantity=Decimal("0.002"),
        update_time_ms=200,
    )
    orphan_open = order(orphan_request, 43, update_time_ms=110)
    fill = ExchangeFill(
        symbol="BTCUSDT",
        trade_id=777,
        exchange_order_id=42,
        side=OrderSide.BUY,
        price=Decimal("65000.10"),
        quantity=Decimal("0.002"),
        commission=Decimal("0.026"),
        commission_asset="USDT",
        realized_pnl=Decimal("0"),
        event_time_ms=200,
        maker=True,
    )
    gateway = FakeReconciliationGateway(
        open_orders=(orphan_open,),
        queried_orders={local_request.client_order_id: local_filled},
        fills=(fill,),
    )
    clock_values: Iterator[int] = iter([1_000, 1_001, 1_002, 1_003, 1_004, 1_005])

    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            await ledger.record_order_intent(local_request, created_at_ms=1)
            await ledger.apply_order_snapshot(local_new)
            reconciler = ExecutionReconciler(
                gateway,
                ledger,
                clock_ms=lambda: next(clock_values),
            )

            first = await reconciler.reconcile("BTCUSDT")
            assert first.orphan_client_order_ids == (orphan_request.client_order_id,)
            assert first.unresolved_local_client_order_ids == ()
            assert first.inserted_fills == 1
            assert first.inserted_income_records == 1
            assert first.position_mismatch_keys == ("BTCUSDT:BOTH",)
            assert not first.safe_to_resume
            stored_local = await ledger.get_order(local_request.client_order_id)
            assert stored_local is not None and stored_local.status is OrderStatus.FILLED
            assert len(await ledger.list_fills("BTCUSDT")) == 1

            second = await reconciler.reconcile("BTCUSDT")
            assert second.orphan_client_order_ids == (orphan_request.client_order_id,)
            assert second.unresolved_local_client_order_ids == ()
            assert second.inserted_fills == 0
            assert second.inserted_income_records == 0
            assert second.position_mismatch_keys == ()
            assert not second.safe_to_resume

            gateway.open_orders = ()
            gateway.queried_orders[orphan_request.client_order_id] = order(
                orphan_request,
                43,
                status=OrderStatus.CANCELED,
                update_time_ms=300,
            )
            third = await reconciler.reconcile("BTCUSDT")
            assert third.orphan_client_order_ids == ()
            assert third.unresolved_local_client_order_ids == ()
            assert third.safe_to_resume

    asyncio.run(scenario())


def test_reconciliation_marks_local_order_unknown_when_exchange_cannot_find_it() -> None:
    local_request = request(3)
    gateway = FakeReconciliationGateway(
        open_orders=(),
        queried_orders={local_request.client_order_id: None},
    )
    clock_values: Iterator[int] = iter([2_000, 2_001])

    async def scenario() -> None:
        async with await SqliteExecutionLedger.open(":memory:") as ledger:
            await ledger.record_order_intent(local_request, created_at_ms=1)
            reconciler = ExecutionReconciler(
                gateway,
                ledger,
                clock_ms=lambda: next(clock_values),
            )

            report = await reconciler.reconcile("BTCUSDT")

            assert report.unresolved_local_client_order_ids == (local_request.client_order_id,)
            assert not report.safe_to_resume
            assert gateway.query_calls == [local_request.client_order_id]

    asyncio.run(scenario())
