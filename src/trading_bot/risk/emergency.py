"""Idempotent emergency cancel-and-flatten orchestration."""

from collections.abc import Callable, Iterable
from decimal import Decimal
from time import time_ns
from typing import Protocol

from trading_bot.domain import OrderSide
from trading_bot.execution import EmergencyCloseRequest, ExchangeOrder, PositionSnapshot
from trading_bot.risk.breaker import RiskCircuitBreaker
from trading_bot.risk.errors import (
    EmergencyActionAlreadyRecordedError,
    EmergencyExitError,
)
from trading_bot.risk.models import (
    EmergencyActionStatus,
    EmergencyExitResult,
    RiskAuditEvent,
    RiskEventType,
    RiskReason,
)
from trading_bot.risk.service import RiskAuditLedger


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


class EmergencyExecutionGateway(Protocol):
    """Exchange mutations and reads needed by emergency exit."""

    async def cancel_all_open_orders(self, symbol: str) -> None:
        """Cancel all current standard orders for one symbol."""

    async def fetch_positions(self, symbol: str) -> tuple[PositionSnapshot, ...]:
        """Fetch the position again after cancellation."""

    async def submit_reduce_only_market(
        self,
        request: EmergencyCloseRequest,
    ) -> ExchangeOrder:
        """Submit one deterministic one-way reduce-only MARKET order."""


class EmergencyAuditLedger(RiskAuditLedger, Protocol):
    """Durable emergency action state plus generic risk audit events."""

    async def begin_emergency_action(
        self,
        action_id: str,
        symbol: str,
        created_at_ms: int,
    ) -> bool:
        """Persist an action before the first exchange mutation."""

    async def complete_emergency_action(
        self,
        action_id: str,
        status: EmergencyActionStatus,
        *,
        client_order_id: str | None,
        exchange_order_id: int | None,
        updated_at_ms: int,
    ) -> None:
        """Persist the latest terminal or unknown action outcome."""


class EmergencyExitCoordinator:
    """Latch, cancel all orders, refetch position, then reduce-only flatten."""

    def __init__(
        self,
        gateway: EmergencyExecutionGateway,
        ledger: EmergencyAuditLedger,
        breaker: RiskCircuitBreaker,
        *,
        clock_ms: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        self._gateway = gateway
        self._ledger = ledger
        self._breaker = breaker
        self._clock_ms = clock_ms

    async def execute(
        self,
        action_id: str,
        symbol: str,
        reason: RiskReason,
    ) -> EmergencyExitResult:
        self._breaker.trip(reason)
        started_at_ms = self._clock_ms()
        inserted = await self._ledger.begin_emergency_action(action_id, symbol, started_at_ms)
        if not inserted:
            raise EmergencyActionAlreadyRecordedError(
                f"emergency action {action_id} is already recorded; reconcile it before retry"
            )
        await self._record_event(action_id, symbol, "STARTED", reason)

        request: EmergencyCloseRequest | None = None
        try:
            await self._gateway.cancel_all_open_orders(symbol)
            position_quantity = self._one_way_position_quantity(
                symbol,
                await self._gateway.fetch_positions(symbol),
            )
            order: ExchangeOrder | None = None
            if position_quantity != 0:
                request = EmergencyCloseRequest(
                    action_id=action_id,
                    symbol=symbol,
                    side=OrderSide.SELL if position_quantity > 0 else OrderSide.BUY,
                    quantity=abs(position_quantity),
                )
                order = await self._gateway.submit_reduce_only_market(request)
                self._validate_close_response(request, order)
        except BaseException:
            await self._ledger.complete_emergency_action(
                action_id,
                EmergencyActionStatus.UNKNOWN,
                client_order_id=None if request is None else request.client_order_id,
                exchange_order_id=None,
                updated_at_ms=self._clock_ms(),
            )
            await self._record_event(action_id, symbol, "UNKNOWN", reason)
            raise

        status = (
            EmergencyActionStatus.NO_POSITION if order is None else EmergencyActionStatus.SUBMITTED
        )
        await self._complete(action_id, status, order)
        await self._record_event(action_id, symbol, status.value, reason)
        return EmergencyExitResult(
            action_id=action_id,
            symbol=symbol,
            position_quantity_before_close=position_quantity,
            close_order=order,
            status=status,
        )

    async def _complete(
        self,
        action_id: str,
        status: EmergencyActionStatus,
        order: ExchangeOrder | None,
    ) -> None:
        await self._ledger.complete_emergency_action(
            action_id,
            status,
            client_order_id=None if order is None else order.client_order_id,
            exchange_order_id=None if order is None else order.exchange_order_id,
            updated_at_ms=self._clock_ms(),
        )

    async def _record_event(
        self,
        action_id: str,
        symbol: str,
        outcome: str,
        reason: RiskReason,
    ) -> None:
        await self._ledger.record_risk_event(
            RiskAuditEvent(
                event_type=RiskEventType.EMERGENCY,
                symbol=symbol,
                outcome=outcome,
                event_time_ms=self._clock_ms(),
                client_order_id=action_id,
                reason_codes=(reason.value,),
            )
        )

    @staticmethod
    def _one_way_position_quantity(
        symbol: str,
        positions: Iterable[PositionSnapshot],
    ) -> Decimal:
        matching = [
            position for position in positions if position.symbol == symbol and position.quantity
        ]
        if not matching:
            return Decimal("0")
        if len(matching) != 1 or matching[0].position_side != "BOTH":
            raise EmergencyExitError("emergency flatten supports exactly one one-way BOTH position")
        return matching[0].quantity

    @staticmethod
    def _validate_close_response(
        request: EmergencyCloseRequest,
        order: ExchangeOrder,
    ) -> None:
        if (
            order.client_order_id != request.client_order_id
            or order.symbol != request.symbol
            or order.side is not request.side
            or order.original_quantity != request.quantity
            or not order.reduce_only
        ):
            raise EmergencyExitError(
                "exchange emergency-close response violates request invariants"
            )
