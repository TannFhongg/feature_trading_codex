"""In-memory typed runtime state used to build fail-closed risk snapshots."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from time import monotonic_ns

from trading_bot.binance import MarketEvent, MarkPrice
from trading_bot.domain import OrderSide, StrategyState
from trading_bot.execution import (
    AccountUpdate,
    ExchangeOrder,
    OrderRecord,
    OrderStatus,
    OrderTradeUpdate,
    PositionSnapshot,
    UserDataEvent,
)
from trading_bot.risk import CircuitBreakerState, RiskLimits, RuntimeRiskSnapshot


def _monotonic_ms() -> int:
    return monotonic_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class RuntimeStatusSnapshot:
    """Sanitized control/health view with no account or credential payload."""

    state: StrategyState
    ready: bool
    reconciliation_safe: bool
    breaker_state: CircuitBreakerState
    market_data_age_ms: int
    user_data_age_ms: int
    open_order_count: int
    task_count: int
    market_queue_depth: int
    user_queue_depth: int
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class _TrackedOrder:
    price: Decimal
    original_quantity: Decimal
    executed_quantity: Decimal
    status: OrderStatus


class RuntimeStateStore:
    """Maintain the minimum observable state required by P5 risk checks."""

    def __init__(
        self,
        limits: RiskLimits,
        reference_price: Decimal,
        *,
        quote_asset: str = "USDT",
        leverage: int = 1,
        monotonic_ms: Callable[[], int] = _monotonic_ms,
    ) -> None:
        self._limits = limits
        self._reference_price = reference_price
        self._quote_asset = quote_asset
        self._leverage = leverage
        self._monotonic_ms = monotonic_ms
        self.state = StrategyState.DRAFT
        self.reconciliation_safe = False
        self.last_error_code: str | None = None
        self._mark_price = reference_price
        self._funding_rate = Decimal("0")
        self._last_market_ms: int | None = None
        self._last_user_ms: int | None = None
        self._positions: tuple[PositionSnapshot, ...] = ()
        self._orders: dict[str, _TrackedOrder] = {}
        self._wallet_balance = Decimal("0")
        self._realized_pnl_today = Decimal("0")
        self._peak_equity = Decimal("0")

    def transition(self, state: StrategyState) -> None:
        self.state = state
        if state is not StrategyState.ERROR:
            self.last_error_code = None

    def fail(self, error_code: str) -> None:
        self.state = StrategyState.ERROR
        self.reconciliation_safe = False
        self.last_error_code = error_code

    def restore(
        self,
        orders: Iterable[OrderRecord],
        positions: Iterable[PositionSnapshot],
    ) -> None:
        self._orders = {
            order.client_order_id: _TrackedOrder(
                price=order.price,
                original_quantity=order.original_quantity,
                executed_quantity=order.executed_quantity,
                status=order.status,
            )
            for order in orders
            if order.status.is_active
        }
        self._positions = tuple(positions)

    def touch_market(self) -> None:
        self._last_market_ms = self._monotonic_ms()

    def apply_market_event(self, event: MarketEvent) -> None:
        self.touch_market()
        if isinstance(event, MarkPrice):
            self._mark_price = event.mark_price
            self._funding_rate = event.funding_rate

    def touch_user(self) -> None:
        self._last_user_ms = self._monotonic_ms()

    def apply_user_event(self, event: UserDataEvent) -> None:
        if isinstance(event, AccountUpdate):
            self._positions = event.positions
            quote = next(
                (balance for balance in event.balances if balance.asset == self._quote_asset),
                None,
            )
            if quote is not None:
                self._wallet_balance = quote.wallet_balance
        elif isinstance(event, OrderTradeUpdate):
            self.observe_order(event.order)
            fill = event.fill
            if fill is not None:
                self._realized_pnl_today += fill.realized_pnl
                delta = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
                self._positions = tuple(
                    replace(
                        position,
                        quantity=position.quantity + delta,
                        notional=abs(position.quantity + delta) * self._mark_price,
                        update_time_ms=event.transaction_time_ms,
                    )
                    if position.symbol == fill.symbol and position.position_side == "BOTH"
                    else position
                    for position in self._positions
                )
        self._update_peak_equity()

    def observe_order(self, order: ExchangeOrder) -> None:
        if order.status.is_active:
            self._orders[order.client_order_id] = _TrackedOrder(
                price=order.price,
                original_quantity=order.original_quantity,
                executed_quantity=order.executed_quantity,
                status=order.status,
            )
        else:
            self._orders.pop(order.client_order_id, None)

    def remove_order(self, client_order_id: str) -> None:
        self._orders.pop(client_order_id, None)

    def risk_snapshot(self) -> RuntimeRiskSnapshot:
        now_ms = self._monotonic_ms()
        position_quantity = sum(
            (position.quantity for position in self._positions),
            start=Decimal("0"),
        )
        active_positions = tuple(position for position in self._positions if position.quantity != 0)
        liquidation_price = (
            active_positions[0].liquidation_price if len(active_positions) == 1 else Decimal("0")
        )
        unrealized = sum(
            (position.unrealized_pnl for position in self._positions),
            start=Decimal("0"),
        )
        maintenance = sum(
            (position.maintenance_margin for position in self._positions),
            start=Decimal("0"),
        )
        equity = max(self._wallet_balance + unrealized, Decimal("0"))
        peak = max(self._peak_equity, equity)
        open_orders = tuple(order for order in self._orders.values() if order.status.is_active)
        open_notional = sum(
            (
                order.price * (order.original_quantity - order.executed_quantity)
                for order in open_orders
            ),
            start=Decimal("0"),
        )
        return RuntimeRiskSnapshot(
            symbol=self._limits.symbol,
            strategy_state=self.state,
            position_quantity=position_quantity,
            mark_price=self._mark_price,
            liquidation_price=liquidation_price,
            open_order_count=len(open_orders),
            open_order_notional=open_notional,
            realized_pnl_today=self._realized_pnl_today,
            equity=equity,
            peak_equity=peak,
            funding_rate=self._funding_rate,
            maintenance_margin=maintenance,
            margin_balance=equity,
            market_data_age_ms=self._age(now_ms, self._last_market_ms),
            user_data_age_ms=self._age(now_ms, self._last_user_ms),
            reconciliation_safe=self.reconciliation_safe,
            one_way_mode=all(position.position_side == "BOTH" for position in self._positions),
            isolated_margin=all(
                position.quantity == 0 or position.margin_type.lower() == "isolated"
                for position in self._positions
            ),
            leverage=self._leverage,
        )

    def status(
        self,
        breaker_state: CircuitBreakerState,
        *,
        task_count: int,
        market_queue_depth: int,
        user_queue_depth: int,
    ) -> RuntimeStatusSnapshot:
        snapshot = self.risk_snapshot()
        ready = (
            self.state in {StrategyState.PAUSED, StrategyState.RUNNING}
            and snapshot.reconciliation_safe
            and snapshot.market_data_age_ms <= self._limits.max_market_data_age_ms
            and snapshot.user_data_age_ms <= self._limits.max_user_data_age_ms
            and snapshot.one_way_mode
            and snapshot.isolated_margin
            and snapshot.leverage <= self._limits.max_leverage
            and breaker_state is CircuitBreakerState.ARMED
        )
        return RuntimeStatusSnapshot(
            state=self.state,
            ready=ready,
            reconciliation_safe=snapshot.reconciliation_safe,
            breaker_state=breaker_state,
            market_data_age_ms=snapshot.market_data_age_ms,
            user_data_age_ms=snapshot.user_data_age_ms,
            open_order_count=snapshot.open_order_count,
            task_count=task_count,
            market_queue_depth=market_queue_depth,
            user_queue_depth=user_queue_depth,
            last_error_code=self.last_error_code,
        )

    def _update_peak_equity(self) -> None:
        unrealized = sum(
            (position.unrealized_pnl for position in self._positions),
            start=Decimal("0"),
        )
        self._peak_equity = max(self._peak_equity, self._wallet_balance + unrealized)

    @staticmethod
    def _age(now_ms: int, observed_ms: int | None) -> int:
        if observed_ms is None:
            return 2**63 - 1
        return max(now_ms - observed_ms, 0)
