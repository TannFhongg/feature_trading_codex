"""Pure Neutral Arithmetic Grid runtime that emits intents but performs no I/O."""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from trading_bot.domain import GridPlan, OrderSide, SymbolRules
from trading_bot.execution import (
    ExchangeOrder,
    ExecutionType,
    OrderRecord,
    OrderRequest,
    OrderTradeUpdate,
    UserDataEvent,
)
from trading_bot.strategy import floor_to_increment

LogicalOrderKey = tuple[int, OrderSide]


@dataclass(frozen=True, slots=True)
class OwnedOrder:
    """Logical ownership metadata retained independently from adapter payloads."""

    level_index: int
    side: OrderSide
    cycle: int


class NeutralGridRuntime:
    """Restore grid ownership and deterministically derive initial/replacement intents."""

    def __init__(
        self,
        strategy_id: str,
        plan: GridPlan,
        rules: SymbolRules,
        *,
        seen_event_capacity: int = 4_096,
    ) -> None:
        if not strategy_id or strategy_id != strategy_id.strip():
            raise ValueError("strategy_id must be non-empty and trimmed")
        if plan.symbol != rules.symbol:
            raise ValueError("grid plan and symbol rules must match")
        if seen_event_capacity <= 0:
            raise ValueError("seen_event_capacity must be greater than zero")
        self._strategy_id = strategy_id
        self._plan = plan
        self._rules = rules
        self._seen_event_capacity = seen_event_capacity
        self._seen_event_ids: set[str] = set()
        self._seen_event_order: deque[str] = deque()
        self._owned: dict[str, OwnedOrder] = {}
        self._active: set[LogicalOrderKey] = set()
        self._next_cycle: dict[LogicalOrderKey, int] = {}
        self._pending_replacement: dict[LogicalOrderKey, Decimal] = {}

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    @property
    def plan(self) -> GridPlan:
        return self._plan

    def restore(self, orders: tuple[OrderRecord, ...]) -> None:
        """Restore all prior cycles and active logical slots after reconciliation."""

        self._owned.clear()
        self._active.clear()
        self._next_cycle.clear()
        for order in orders:
            if order.strategy_id != self._strategy_id or order.symbol != self._plan.symbol:
                continue
            if order.level_index is None or order.cycle is None:
                continue
            key = (order.level_index, order.side)
            self._owned[order.client_order_id] = OwnedOrder(
                level_index=order.level_index,
                side=order.side,
                cycle=order.cycle,
            )
            self._next_cycle[key] = max(self._next_cycle.get(key, 0), order.cycle + 1)
            if order.status.is_active:
                self._active.add(key)

    def initial_intents(self) -> tuple[OrderRequest, ...]:
        """Emit at most one active logical order for every non-anchor grid level."""

        intents: list[OrderRequest] = []
        for level in self._plan.levels:
            if level.side is None:
                continue
            key = (level.index, level.side)
            if key in self._active:
                continue
            intents.append(self._new_intent(key, self._plan.quantity_per_order))
        return tuple(intents)

    def on_user_event(self, event: UserDataEvent) -> tuple[OrderRequest, ...]:
        """Consume one confirmed order event and emit valid adjacent replacements."""

        if not isinstance(event, OrderTradeUpdate):
            return ()
        if event.order.symbol != self._plan.symbol or not self._remember(event.event_id):
            return ()
        owned = self._owned.get(event.order.client_order_id)
        if owned is None or owned.side is not event.order.side:
            return ()

        source_key = (owned.level_index, owned.side)
        if event.order.status.is_active:
            self._active.add(source_key)
        else:
            self._active.discard(source_key)

        if event.execution_type is ExecutionType.TRADE:
            target_index = (
                owned.level_index + 1 if owned.side is OrderSide.BUY else owned.level_index - 1
            )
            if 0 <= target_index < len(self._plan.levels):
                target_side = OrderSide.SELL if owned.side is OrderSide.BUY else OrderSide.BUY
                target_key = (target_index, target_side)
                current = self._pending_replacement.get(target_key, Decimal("0"))
                self._pending_replacement[target_key] = current + event.last_filled_quantity

        return self._flush_ready_replacements()

    def observe_submission(self, request: OrderRequest, order: ExchangeOrder) -> None:
        """Reflect the immediate exchange response without performing any I/O."""

        key = (request.level_index, request.side)
        if order.status.is_active:
            self._active.add(key)
        else:
            self._active.discard(key)

    def release_intent(self, request: OrderRequest) -> None:
        """Release an in-memory slot after submission was rejected before becoming active."""

        self._active.discard((request.level_index, request.side))

    def _flush_ready_replacements(self) -> tuple[OrderRequest, ...]:
        intents: list[OrderRequest] = []
        for key in sorted(self._pending_replacement, key=lambda item: (item[0], item[1].value)):
            if key in self._active:
                continue
            level_index, _ = key
            available = floor_to_increment(
                self._pending_replacement[key],
                self._rules.step_size,
            )
            quantity = min(available, self._plan.quantity_per_order)
            price = self._plan.levels[level_index].price
            if quantity < self._rules.min_qty or price * quantity < self._rules.min_notional:
                continue
            intents.append(self._new_intent(key, quantity))
            remaining = self._pending_replacement[key] - quantity
            if remaining == 0:
                del self._pending_replacement[key]
            else:
                self._pending_replacement[key] = remaining
        return tuple(intents)

    def _new_intent(self, key: LogicalOrderKey, quantity: Decimal) -> OrderRequest:
        level_index, side = key
        cycle = self._next_cycle.get(key, 0)
        request = OrderRequest(
            strategy_id=self._strategy_id,
            level_index=level_index,
            cycle=cycle,
            symbol=self._plan.symbol,
            side=side,
            price=self._plan.levels[level_index].price,
            quantity=quantity,
        )
        self._next_cycle[key] = cycle + 1
        self._owned[request.client_order_id] = OwnedOrder(level_index, side, cycle)
        self._active.add(key)
        return request

    def _remember(self, event_id: str) -> bool:
        if event_id in self._seen_event_ids:
            return False
        self._seen_event_ids.add(event_id)
        self._seen_event_order.append(event_id)
        if len(self._seen_event_order) > self._seen_event_capacity:
            expired = self._seen_event_order.popleft()
            self._seen_event_ids.remove(expired)
        return True
