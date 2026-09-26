"""Immutable domain models for grid planning."""

from dataclasses import dataclass
from decimal import Decimal

from trading_bot.domain.enums import GridDirection, GridType, OrderSide
from trading_bot.domain.errors import DomainValidationError


def _require_decimal(name: str, value: object, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise DomainValidationError(f"{name} must be finite")
    if positive and value <= 0:
        raise DomainValidationError(f"{name} must be greater than zero")
    return value


def _validate_symbol(symbol: str) -> None:
    if not symbol or symbol != symbol.strip().upper():
        raise DomainValidationError("symbol must be non-empty, trimmed, and uppercase")
    if not symbol.replace("_", "").isalnum():
        raise DomainValidationError("symbol may contain only letters, numbers, and underscores")


@dataclass(frozen=True, slots=True)
class GridConfig:
    """User-controlled settings for the P1 neutral arithmetic grid."""

    symbol: str
    lower_price: Decimal
    upper_price: Decimal
    reference_price: Decimal
    grid_count: int
    quantity_per_order: Decimal
    grid_type: GridType = GridType.ARITHMETIC
    direction: GridDirection = GridDirection.NEUTRAL

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        lower = _require_decimal("lower_price", self.lower_price, positive=True)
        upper = _require_decimal("upper_price", self.upper_price, positive=True)
        reference = _require_decimal("reference_price", self.reference_price, positive=True)
        _require_decimal("quantity_per_order", self.quantity_per_order, positive=True)

        if upper <= lower:
            raise DomainValidationError("upper_price must be greater than lower_price")
        if not lower < reference < upper:
            raise DomainValidationError("reference_price must be strictly inside the grid range")
        if isinstance(self.grid_count, bool) or not isinstance(self.grid_count, int):
            raise TypeError("grid_count must be int")
        if self.grid_count < 2:
            raise DomainValidationError("grid_count must be at least 2")
        if self.grid_type is not GridType.ARITHMETIC:
            raise DomainValidationError("P1 supports only ARITHMETIC grids")
        if self.direction is not GridDirection.NEUTRAL:
            raise DomainValidationError("P1 supports only NEUTRAL grids")


@dataclass(frozen=True, slots=True)
class SymbolRules:
    """Exchange filters required to construct valid price and quantity values."""

    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _require_decimal("tick_size", self.tick_size, positive=True)
        _require_decimal("step_size", self.step_size, positive=True)
        _require_decimal("min_qty", self.min_qty, positive=True)
        _require_decimal("min_notional", self.min_notional, positive=True)


@dataclass(frozen=True, slots=True)
class GridLevel:
    """One quantized grid price; the anchor has no active order side."""

    index: int
    price: Decimal
    side: OrderSide | None

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise TypeError("index must be int")
        if self.index < 0:
            raise DomainValidationError("index must not be negative")
        _require_decimal("price", self.price, positive=True)
        if self.side is not None and not isinstance(self.side, OrderSide):
            raise TypeError("side must be OrderSide or None")


@dataclass(frozen=True, slots=True)
class GridPlan:
    """Validated result of grid generation."""

    symbol: str
    quantity_per_order: Decimal
    anchor_index: int
    levels: tuple[GridLevel, ...]

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _require_decimal("quantity_per_order", self.quantity_per_order, positive=True)
        if not self.levels:
            raise DomainValidationError("levels must not be empty")
        if not 0 <= self.anchor_index < len(self.levels):
            raise DomainValidationError("anchor_index is outside levels")
        if self.levels[self.anchor_index].side is not None:
            raise DomainValidationError("anchor level must not have an order side")
        if sum(level.side is None for level in self.levels) != 1:
            raise DomainValidationError("grid plan must contain exactly one anchor level")
