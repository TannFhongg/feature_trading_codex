"""Durable order lifecycle orchestration around an execution adapter."""

from typing import Protocol

from trading_bot.execution.errors import (
    AmbiguousExecutionError,
    ExecutionAdapterError,
    ExecutionIntentAlreadyRecordedError,
)
from trading_bot.execution.models import (
    AccountUpdate,
    ExchangeOrder,
    ListenKeyExpired,
    OrderRequest,
    OrderStatus,
    OrderTradeUpdate,
    UserDataEvent,
    UserStreamNotice,
)


class OrderExecutionGateway(Protocol):
    """Mutating order operations used by the durable executor."""

    async def submit_order(self, request: OrderRequest) -> ExchangeOrder:
        """Submit one deterministic order."""

    async def cancel_order(self, symbol: str, client_order_id: str) -> ExchangeOrder | None:
        """Cancel one order by deterministic ID."""


class ExecutionLedger(Protocol):
    """Persistence operations needed by live order lifecycle orchestration."""

    async def record_order_intent(self, request: OrderRequest) -> bool:
        """Persist an order intent before network I/O."""

    async def mark_order_status(self, client_order_id: str, status: OrderStatus) -> bool:
        """Persist a local terminal or unknown outcome."""

    async def apply_order_snapshot(self, order: ExchangeOrder) -> bool:
        """Apply a REST order snapshot."""

    async def apply_order_event(self, event: OrderTradeUpdate) -> object:
        """Apply a real-time order event."""

    async def apply_account_update(self, event: AccountUpdate) -> bool:
        """Apply a real-time account update."""


class PersistentOrderExecutor:
    """Ensure intent durability and unknown-state recording around adapter calls."""

    def __init__(self, gateway: OrderExecutionGateway, ledger: ExecutionLedger) -> None:
        self._gateway = gateway
        self._ledger = ledger

    async def submit(self, request: OrderRequest) -> ExchangeOrder:
        inserted = await self._ledger.record_order_intent(request)
        if not inserted:
            raise ExecutionIntentAlreadyRecordedError(
                f"order intent {request.client_order_id} is already recorded; query or reconcile it"
            )
        try:
            order = await self._gateway.submit_order(request)
        except AmbiguousExecutionError:
            await self._ledger.mark_order_status(request.client_order_id, OrderStatus.UNKNOWN)
            raise
        except ExecutionAdapterError:
            await self._ledger.mark_order_status(
                request.client_order_id, OrderStatus.SUBMISSION_REJECTED
            )
            raise
        await self._ledger.apply_order_snapshot(order)
        return order

    async def cancel(self, symbol: str, client_order_id: str) -> ExchangeOrder | None:
        try:
            order = await self._gateway.cancel_order(symbol, client_order_id)
        except AmbiguousExecutionError:
            await self._ledger.mark_order_status(client_order_id, OrderStatus.UNKNOWN)
            raise
        if order is not None:
            await self._ledger.apply_order_snapshot(order)
        return order

    async def ingest_user_event(self, event: UserDataEvent) -> bool:
        """Persist ledger-relevant user events and explicitly ignore typed notices."""

        if isinstance(event, OrderTradeUpdate):
            await self._ledger.apply_order_event(event)
            return True
        if isinstance(event, AccountUpdate):
            return await self._ledger.apply_account_update(event)
        if isinstance(event, (ListenKeyExpired, UserStreamNotice)):
            return False
        raise TypeError(f"unsupported user event {type(event).__name__}")
