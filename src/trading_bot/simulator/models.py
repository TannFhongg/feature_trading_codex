"""Immutable inputs and outputs for deterministic grid execution simulation."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from trading_bot.domain import DomainValidationError, OrderSide


def _require_decimal(
    name: str,
    value: object,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise DomainValidationError(f"{name} must be finite")
    if positive and value <= 0:
        raise DomainValidationError(f"{name} must be greater than zero")
    if non_negative and value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


def _require_non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int")
    if value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


class Liquidity(StrEnum):
    """Liquidity classification used to select the simulated commission rate."""

    MAKER = "MAKER"
    TAKER = "TAKER"


class IntentRole(StrEnum):
    """Whether an intent starts a grid leg or closes a previously opened leg."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Execution assumptions shared by every order in one simulation."""

    start_time_ms: int = 0
    order_latency_ms: int = 0
    queue_ahead_multiplier: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        _require_non_negative_int("start_time_ms", self.start_time_ms)
        _require_non_negative_int("order_latency_ms", self.order_latency_ms)
        _require_decimal("queue_ahead_multiplier", self.queue_ahead_multiplier, non_negative=True)


@dataclass(frozen=True, slots=True)
class MarketTrade:
    """An aggregate trade that may consume queue and resting grid liquidity."""

    event_time_ms: int
    price: Decimal
    quantity: Decimal
    aggressor_side: OrderSide
    liquidity: Liquidity = Liquidity.MAKER

    def __post_init__(self) -> None:
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        _require_decimal("price", self.price, positive=True)
        _require_decimal("quantity", self.quantity, positive=True)
        if not isinstance(self.aggressor_side, OrderSide):
            raise TypeError("aggressor_side must be OrderSide")
        if not isinstance(self.liquidity, Liquidity):
            raise TypeError("liquidity must be Liquidity")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """A deterministic logical order slice created by the grid strategy."""

    intent_id: str
    created_at_ms: int
    active_from_ms: int
    level_index: int
    side: OrderSide
    price: Decimal
    quantity: Decimal
    role: IntentRole
    opening_price: Decimal | None = None
    opening_fill_id: str | None = None

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise DomainValidationError("intent_id must not be empty")
        _require_non_negative_int("created_at_ms", self.created_at_ms)
        _require_non_negative_int("active_from_ms", self.active_from_ms)
        if self.active_from_ms < self.created_at_ms:
            raise DomainValidationError("active_from_ms must not precede created_at_ms")
        _require_non_negative_int("level_index", self.level_index)
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be OrderSide")
        _require_decimal("price", self.price, positive=True)
        _require_decimal("quantity", self.quantity, positive=True)
        if not isinstance(self.role, IntentRole):
            raise TypeError("role must be IntentRole")
        if self.role is IntentRole.EXIT:
            _require_decimal("opening_price", self.opening_price, positive=True)
            if not self.opening_fill_id:
                raise DomainValidationError("EXIT intent must have opening_fill_id")
        elif self.opening_price is not None or self.opening_fill_id is not None:
            raise DomainValidationError("ENTRY intent must not have opening fill data")


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    """A fill produced from one intent slice and one aggregate market trade."""

    fill_id: str
    intent_id: str
    event_time_ms: int
    level_index: int
    side: OrderSide
    price: Decimal
    quantity: Decimal
    liquidity: Liquidity
    role: IntentRole
    opening_price: Decimal | None
    opening_fill_id: str | None
    gross_grid_profit: Decimal

    def __post_init__(self) -> None:
        if not self.fill_id or not self.intent_id:
            raise DomainValidationError("fill_id and intent_id must not be empty")
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        _require_non_negative_int("level_index", self.level_index)
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be OrderSide")
        if not isinstance(self.liquidity, Liquidity):
            raise TypeError("liquidity must be Liquidity")
        if not isinstance(self.role, IntentRole):
            raise TypeError("role must be IntentRole")
        _require_decimal("price", self.price, positive=True)
        _require_decimal("quantity", self.quantity, positive=True)
        _require_decimal("gross_grid_profit", self.gross_grid_profit, non_negative=True)
        if self.role is IntentRole.EXIT:
            _require_decimal("opening_price", self.opening_price, positive=True)
            if not self.opening_fill_id:
                raise DomainValidationError("EXIT fill must have opening_fill_id")
        elif self.opening_price is not None or self.opening_fill_id is not None:
            raise DomainValidationError("ENTRY fill must not have opening fill data")


@dataclass(frozen=True, slots=True)
class OpenOrder:
    """Aggregate view of one active logical order at a level and side."""

    level_index: int
    side: OrderSide
    price: Decimal
    quantity: Decimal
    intent_count: int

    def __post_init__(self) -> None:
        _require_non_negative_int("level_index", self.level_index)
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be OrderSide")
        _require_decimal("price", self.price, positive=True)
        _require_decimal("quantity", self.quantity, positive=True)
        if isinstance(self.intent_count, bool) or not isinstance(self.intent_count, int):
            raise TypeError("intent_count must be int")
        if self.intent_count <= 0:
            raise DomainValidationError("intent_count must be greater than zero")


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Auditable output of the execution-only simulator."""

    intents: tuple[OrderIntent, ...]
    fills: tuple[SimulatedFill, ...]
    open_orders: tuple[OpenOrder, ...]

    @property
    def submitted_quantity(self) -> Decimal:
        return sum((intent.quantity for intent in self.intents), start=Decimal("0"))

    @property
    def filled_quantity(self) -> Decimal:
        return sum((fill.quantity for fill in self.fills), start=Decimal("0"))
