"""Neutral arithmetic grid generation and exchange-filter validation."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

from trading_bot.domain import (
    DomainValidationError,
    GridConfig,
    GridLevel,
    GridPlan,
    OrderSide,
    SymbolRules,
)


def _validate_quantization_inputs(value: object, increment: object) -> tuple[Decimal, Decimal]:
    if not isinstance(value, Decimal):
        raise TypeError(f"value must be Decimal, got {type(value).__name__}")
    if not isinstance(increment, Decimal):
        raise TypeError(f"increment must be Decimal, got {type(increment).__name__}")
    if not value.is_finite():
        raise DomainValidationError("value must be finite")
    if not increment.is_finite() or increment <= 0:
        raise DomainValidationError("increment must be finite and greater than zero")
    return value, increment


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Round a Decimal down to an arbitrary positive exchange increment."""

    value, increment = _validate_quantization_inputs(value, increment)
    units = (value / increment).to_integral_value(rounding=ROUND_FLOOR)
    return (units * increment).quantize(increment)


def ceil_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Round a Decimal up to an arbitrary positive exchange increment."""

    value, increment = _validate_quantization_inputs(value, increment)
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return (units * increment).quantize(increment)


def _quantized_prices(config: GridConfig, rules: SymbolRules) -> tuple[Decimal, ...]:
    lower = ceil_to_increment(config.lower_price, rules.tick_size)
    upper = floor_to_increment(config.upper_price, rules.tick_size)
    if lower >= upper:
        raise DomainValidationError("grid range collapses after tick-size quantization")

    with localcontext() as context:
        context.prec = 50
        interval = (upper - lower) / config.grid_count
        prices = tuple(
            upper
            if index == config.grid_count
            else floor_to_increment(lower + interval * index, rules.tick_size)
            for index in range(config.grid_count + 1)
        )

    if len(set(prices)) != len(prices):
        raise DomainValidationError(
            "grid levels are not unique after tick-size quantization; reduce grid_count"
        )
    if any(price < config.lower_price or price > config.upper_price for price in prices):
        raise DomainValidationError("quantized grid price is outside the configured range")
    return prices


def _anchor_index(prices: tuple[Decimal, ...], reference_price: Decimal) -> int:
    # A lower price wins an equal-distance tie, making anchor selection deterministic.
    return min(
        range(len(prices)), key=lambda index: (abs(prices[index] - reference_price), prices[index])
    )


def generate_arithmetic_grid(config: GridConfig, rules: SymbolRules) -> GridPlan:
    """Build a validated neutral grid containing ``grid_count + 1`` price levels."""

    if config.symbol != rules.symbol:
        raise DomainValidationError("config and symbol rules must reference the same symbol")

    prices = _quantized_prices(config, rules)
    quantity = floor_to_increment(config.quantity_per_order, rules.step_size)
    if quantity < rules.min_qty:
        raise DomainValidationError("quantity is below min_qty after step-size quantization")

    anchor_index = _anchor_index(prices, config.reference_price)
    levels: list[GridLevel] = []
    for index, price in enumerate(prices):
        if index == anchor_index:
            side = None
        elif price < config.reference_price:
            side = OrderSide.BUY
        elif price > config.reference_price:
            side = OrderSide.SELL
        else:
            raise DomainValidationError("reference-price level must be selected as the anchor")

        if side is not None and price * quantity < rules.min_notional:
            raise DomainValidationError(
                f"grid level {index} is below min_notional after quantity quantization"
            )
        levels.append(GridLevel(index=index, price=price, side=side))

    return GridPlan(
        symbol=config.symbol,
        quantity_per_order=quantity,
        anchor_index=anchor_index,
        levels=tuple(levels),
    )
