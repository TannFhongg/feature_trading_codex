"""Deterministic local adapters used by the executable P6 dry-run mode."""

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from time import time_ns

from trading_bot.binance import MarketEvent, MarkPrice
from trading_bot.domain import OrderSide
from trading_bot.execution import (
    AccountSnapshot,
    AccountUpdate,
    BalanceSnapshot,
    EmergencyCloseRequest,
    ExchangeFill,
    ExchangeOrder,
    ExecutionType,
    IncomeRecord,
    OrderRequest,
    OrderStatus,
    OrderTradeUpdate,
    PositionSnapshot,
    UserDataEvent,
)


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


class DryRunExchangeGateway:
    """In-memory exchange boundary; it never opens a socket or uses credentials."""

    def __init__(self, symbol: str, reference_price: Decimal) -> None:
        self._symbol = symbol
        self._reference_price = reference_price
        self._orders: dict[str, ExchangeOrder] = {}
        self._next_exchange_id = 1
        self._next_trade_id = 1
        self._position = self._position_snapshot(Decimal("0"))
        self._user_events: asyncio.Queue[UserDataEvent] = asyncio.Queue(256)

    async def submit_order(self, request: OrderRequest) -> ExchangeOrder:
        existing = self._orders.get(request.client_order_id)
        if existing is not None:
            return existing
        order = ExchangeOrder(
            symbol=request.symbol,
            client_order_id=request.client_order_id,
            exchange_order_id=self._allocate_exchange_id(),
            side=request.side,
            status=OrderStatus.NEW,
            price=request.price,
            original_quantity=request.quantity,
            executed_quantity=Decimal("0"),
            average_price=Decimal("0"),
            reduce_only=request.reduce_only,
            update_time_ms=_wall_clock_ms(),
        )
        self._orders[order.client_order_id] = order
        return order

    async def cancel_order(
        self,
        symbol: str,
        client_order_id: str,
    ) -> ExchangeOrder | None:
        order = self._orders.get(client_order_id)
        if order is None or order.symbol != symbol:
            return None
        if not order.status.is_active:
            return order
        canceled = ExchangeOrder(
            symbol=order.symbol,
            client_order_id=order.client_order_id,
            exchange_order_id=order.exchange_order_id,
            side=order.side,
            status=OrderStatus.CANCELED,
            price=order.price,
            original_quantity=order.original_quantity,
            executed_quantity=order.executed_quantity,
            average_price=order.average_price,
            reduce_only=order.reduce_only,
            update_time_ms=_wall_clock_ms(),
        )
        self._orders[client_order_id] = canceled
        return canceled

    async def cancel_all_open_orders(self, symbol: str) -> None:
        for client_order_id, order in tuple(self._orders.items()):
            if order.symbol == symbol and order.status.is_active:
                await self.cancel_order(symbol, client_order_id)

    async def submit_reduce_only_market(
        self,
        request: EmergencyCloseRequest,
    ) -> ExchangeOrder:
        quantity = self._position.quantity
        expected_side = OrderSide.SELL if quantity > 0 else OrderSide.BUY
        if quantity == 0 or request.side is not expected_side or request.quantity != abs(quantity):
            raise ValueError("dry-run emergency request does not reduce the complete position")
        order = ExchangeOrder(
            symbol=request.symbol,
            client_order_id=request.client_order_id,
            exchange_order_id=self._allocate_exchange_id(),
            side=request.side,
            status=OrderStatus.FILLED,
            price=Decimal("0"),
            original_quantity=request.quantity,
            executed_quantity=request.quantity,
            average_price=self._reference_price,
            reduce_only=True,
            update_time_ms=_wall_clock_ms(),
        )
        self._orders[order.client_order_id] = order
        self._position = self._position_snapshot(Decimal("0"))
        return order

    async def query_order(
        self,
        symbol: str,
        client_order_id: str,
    ) -> ExchangeOrder | None:
        order = self._orders.get(client_order_id)
        return order if order is not None and order.symbol == symbol else None

    async def list_open_orders(self, symbol: str) -> tuple[ExchangeOrder, ...]:
        return tuple(
            order
            for order in self._orders.values()
            if order.symbol == symbol and order.status.is_active
        )

    async def list_account_trades(
        self,
        symbol: str,
        *,
        from_id: int | None = None,
        limit: int = 1_000,
    ) -> tuple[ExchangeFill, ...]:
        del symbol, from_id, limit
        return ()

    async def fetch_positions(self, symbol: str) -> tuple[PositionSnapshot, ...]:
        return (self._position,) if symbol == self._symbol else ()

    async def fetch_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            total_wallet_balance=Decimal("10000"),
            total_unrealized_profit=self._position.unrealized_pnl,
            total_margin_balance=Decimal("10000") + self._position.unrealized_pnl,
            available_balance=Decimal("10000"),
            update_time_ms=_wall_clock_ms(),
            balances=(self._balance(),),
            total_initial_margin=self._position.initial_margin,
            total_maintenance_margin=self._position.maintenance_margin,
        )

    async def list_income(
        self,
        symbol: str,
        *,
        income_type: str = "FUNDING_FEE",
        limit: int = 1_000,
    ) -> tuple[IncomeRecord, ...]:
        del symbol, income_type, limit
        return ()

    def account_update(self) -> AccountUpdate:
        now_ms = _wall_clock_ms()
        return AccountUpdate(
            event_time_ms=now_ms,
            transaction_time_ms=now_ms,
            reason="DRY_RUN_HEARTBEAT",
            balances=(self._balance(),),
            positions=(self._position,),
        )

    async def simulate_fill(
        self,
        client_order_id: str,
        quantity: Decimal,
    ) -> OrderTradeUpdate:
        """Confirm one deterministic dry-run partial/full fill through the user stream."""

        order = self._orders.get(client_order_id)
        if order is None or not order.status.is_active:
            raise ValueError("dry-run fill requires an active known order")
        remaining = order.original_quantity - order.executed_quantity
        if quantity <= 0 or quantity > remaining:
            raise ValueError("dry-run fill quantity must be positive and not exceed remaining")
        executed = order.executed_quantity + quantity
        now_ms = _wall_clock_ms()
        updated = ExchangeOrder(
            symbol=order.symbol,
            client_order_id=order.client_order_id,
            exchange_order_id=order.exchange_order_id,
            side=order.side,
            status=(
                OrderStatus.FILLED
                if executed == order.original_quantity
                else OrderStatus.PARTIALLY_FILLED
            ),
            price=order.price,
            original_quantity=order.original_quantity,
            executed_quantity=executed,
            average_price=order.price,
            reduce_only=order.reduce_only,
            update_time_ms=now_ms,
        )
        self._orders[client_order_id] = updated
        signed_quantity = quantity if order.side is OrderSide.BUY else -quantity
        self._position = self._position_snapshot(self._position.quantity + signed_quantity)
        event = OrderTradeUpdate(
            event_time_ms=now_ms,
            transaction_time_ms=now_ms,
            execution_type=ExecutionType.TRADE,
            order=updated,
            last_filled_quantity=quantity,
            last_filled_price=order.price,
            trade_id=self._next_trade_id,
            commission=Decimal("0"),
            commission_asset="USDT",
            realized_pnl=Decimal("0"),
            maker=True,
        )
        self._next_trade_id += 1
        self._user_events.put_nowait(event)
        self._user_events.put_nowait(self.account_update())
        return event

    async def next_user_event(self) -> UserDataEvent:
        return await self._user_events.get()

    def _allocate_exchange_id(self) -> int:
        value = self._next_exchange_id
        self._next_exchange_id += 1
        return value

    def _position_snapshot(self, quantity: Decimal) -> PositionSnapshot:
        return PositionSnapshot(
            symbol=self._symbol,
            position_side="BOTH",
            quantity=quantity,
            entry_price=Decimal("0") if quantity == 0 else self._reference_price,
            break_even_price=Decimal("0") if quantity == 0 else self._reference_price,
            unrealized_pnl=Decimal("0"),
            margin_type="isolated",
            isolated_wallet=Decimal("0"),
            update_time_ms=_wall_clock_ms(),
            mark_price=self._reference_price,
            liquidation_price=Decimal("0"),
            notional=abs(quantity) * self._reference_price,
            initial_margin=abs(quantity) * self._reference_price,
            maintenance_margin=Decimal("0"),
        )

    @staticmethod
    def _balance() -> BalanceSnapshot:
        return BalanceSnapshot(
            asset="USDT",
            wallet_balance=Decimal("10000"),
            cross_wallet_balance=Decimal("0"),
            balance_change=Decimal("0"),
        )


class DryRunMarketSource:
    def __init__(self, symbol: str, reference_price: Decimal) -> None:
        self._symbol = symbol
        self._reference_price = reference_price

    async def events(self) -> AsyncIterator[MarketEvent]:
        while True:
            now_ms = _wall_clock_ms()
            yield MarkPrice(
                event_time_ms=now_ms,
                symbol=self._symbol,
                mark_price=self._reference_price,
                index_price=self._reference_price,
                estimated_settle_price=self._reference_price,
                funding_rate=Decimal("0"),
                next_funding_time_ms=now_ms + 8 * 60 * 60 * 1_000,
            )
            await asyncio.sleep(0.25)


class DryRunUserSource:
    def __init__(self, gateway: DryRunExchangeGateway) -> None:
        self._gateway = gateway

    async def events(self) -> AsyncIterator[UserDataEvent]:
        while True:
            try:
                yield await asyncio.wait_for(self._gateway.next_user_event(), timeout=0.25)
            except TimeoutError:
                yield self._gateway.account_update()
