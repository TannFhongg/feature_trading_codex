"""Deterministic price-time simulation for neutral arithmetic grid orders."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from trading_bot.domain import DomainValidationError, GridPlan, OrderSide
from trading_bot.simulator.models import (
    IntentRole,
    MarketTrade,
    OpenOrder,
    OrderIntent,
    SimulatedFill,
    SimulationConfig,
    SimulationResult,
)


@dataclass(slots=True)
class _WorkingSlice:
    intent: OrderIntent
    remaining_quantity: Decimal
    queue_ahead: Decimal


class GridExecutionSimulator:
    """Simulate resting grid orders without network or exchange dependencies."""

    def __init__(self, plan: GridPlan, config: SimulationConfig | None = None) -> None:
        self._plan = plan
        self._config = config or SimulationConfig()
        self._orders: dict[tuple[int, OrderSide], list[_WorkingSlice]] = defaultdict(list)
        self._intents: list[OrderIntent] = []
        self._fills: list[SimulatedFill] = []
        self._intent_sequence = 0
        self._fill_sequence = 0
        self._last_event_time_ms = self._config.start_time_ms

        for level in self._plan.levels:
            if level.side is not None:
                self._add_intent(
                    created_at_ms=self._config.start_time_ms,
                    level_index=level.index,
                    side=level.side,
                    quantity=self._plan.quantity_per_order,
                    role=IntentRole.ENTRY,
                    opening_price=None,
                )

    @property
    def plan(self) -> GridPlan:
        return self._plan

    def process_trade(self, trade: MarketTrade) -> tuple[SimulatedFill, ...]:
        """Apply one aggregate trade and return only fills created by this event."""

        if trade.event_time_ms < self._last_event_time_ms:
            raise DomainValidationError("market trades must be processed in chronological order")

        start_index = len(self._fills)
        available_quantity = trade.quantity
        resting_side = OrderSide.BUY if trade.aggressor_side is OrderSide.SELL else OrderSide.SELL
        eligible_keys = [
            key
            for key, slices in self._orders.items()
            if slices
            and key[1] is resting_side
            and self._price_crosses(
                resting_side=resting_side,
                order_price=self._plan.levels[key[0]].price,
                trade_price=trade.price,
            )
        ]
        eligible_keys.sort(
            key=lambda key: self._plan.levels[key[0]].price,
            reverse=resting_side is OrderSide.BUY,
        )

        # Snapshot keys so replacement intents cannot fill on the trade that created them.
        for key in eligible_keys:
            if available_quantity <= 0:
                break
            slices = self._orders[key]
            slices_at_event_start = tuple(slices)
            for working_slice in slices_at_event_start:
                if available_quantity <= 0:
                    break
                if working_slice.intent.active_from_ms > trade.event_time_ms:
                    continue

                queue_consumed = min(available_quantity, working_slice.queue_ahead)
                working_slice.queue_ahead -= queue_consumed
                available_quantity -= queue_consumed
                if available_quantity <= 0:
                    break

                fill_quantity = min(available_quantity, working_slice.remaining_quantity)
                if fill_quantity <= 0:
                    continue
                available_quantity -= fill_quantity
                working_slice.remaining_quantity -= fill_quantity
                self._record_fill(trade, working_slice.intent, fill_quantity)

            self._orders[key] = [
                working_slice for working_slice in slices if working_slice.remaining_quantity > 0
            ]
            if not self._orders[key]:
                del self._orders[key]

        self._last_event_time_ms = trade.event_time_ms
        return tuple(self._fills[start_index:])

    def result(self) -> SimulationResult:
        """Return an immutable snapshot of all intents, fills, and open orders."""

        open_orders = tuple(
            OpenOrder(
                level_index=level_index,
                side=side,
                price=self._plan.levels[level_index].price,
                quantity=sum(
                    (working_slice.remaining_quantity for working_slice in slices),
                    start=Decimal("0"),
                ),
                intent_count=len(slices),
            )
            for (level_index, side), slices in sorted(
                self._orders.items(), key=lambda item: (item[0][0], item[0][1].value)
            )
            if slices
        )
        return SimulationResult(
            intents=tuple(self._intents),
            fills=tuple(self._fills),
            open_orders=open_orders,
        )

    @staticmethod
    def _price_crosses(
        *, resting_side: OrderSide, order_price: Decimal, trade_price: Decimal
    ) -> bool:
        if resting_side is OrderSide.BUY:
            return trade_price <= order_price
        return trade_price >= order_price

    def _add_intent(
        self,
        *,
        created_at_ms: int,
        level_index: int,
        side: OrderSide,
        quantity: Decimal,
        role: IntentRole,
        opening_price: Decimal | None,
    ) -> OrderIntent:
        self._intent_sequence += 1
        level = self._plan.levels[level_index]
        intent = OrderIntent(
            intent_id=f"sim-{level_index}-{side.value.lower()}-{self._intent_sequence:08d}",
            created_at_ms=created_at_ms,
            active_from_ms=created_at_ms + self._config.order_latency_ms,
            level_index=level_index,
            side=side,
            price=level.price,
            quantity=quantity,
            role=role,
            opening_price=opening_price,
        )
        self._intents.append(intent)
        self._orders[(level_index, side)].append(
            _WorkingSlice(
                intent=intent,
                remaining_quantity=quantity,
                queue_ahead=quantity * self._config.queue_ahead_multiplier,
            )
        )
        return intent

    def _record_fill(self, trade: MarketTrade, intent: OrderIntent, quantity: Decimal) -> None:
        self._fill_sequence += 1
        gross_grid_profit = self._grid_profit(intent, quantity)
        self._fills.append(
            SimulatedFill(
                fill_id=f"fill-{self._fill_sequence:08d}",
                intent_id=intent.intent_id,
                event_time_ms=trade.event_time_ms,
                level_index=intent.level_index,
                side=intent.side,
                price=intent.price,
                quantity=quantity,
                liquidity=trade.liquidity,
                role=intent.role,
                opening_price=intent.opening_price,
                gross_grid_profit=gross_grid_profit,
            )
        )

        if intent.side is OrderSide.BUY:
            target_index = intent.level_index + 1
            target_side = OrderSide.SELL
        else:
            target_index = intent.level_index - 1
            target_side = OrderSide.BUY
        if not 0 <= target_index < len(self._plan.levels):
            raise DomainValidationError("filled grid level has no valid replacement level")

        replacement_role = IntentRole.EXIT if intent.role is IntentRole.ENTRY else IntentRole.ENTRY
        self._add_intent(
            created_at_ms=trade.event_time_ms,
            level_index=target_index,
            side=target_side,
            quantity=quantity,
            role=replacement_role,
            opening_price=intent.price if replacement_role is IntentRole.EXIT else None,
        )

    @staticmethod
    def _grid_profit(intent: OrderIntent, quantity: Decimal) -> Decimal:
        if intent.role is IntentRole.ENTRY:
            return Decimal("0")
        if intent.opening_price is None:
            raise DomainValidationError("EXIT intent is missing opening_price")
        if intent.side is OrderSide.SELL:
            return (intent.price - intent.opening_price) * quantity
        return (intent.opening_price - intent.price) * quantity
