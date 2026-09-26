from decimal import Decimal

import pytest

from trading_bot.domain import DomainValidationError, GridConfig, OrderSide, SymbolRules
from trading_bot.strategy import (
    ceil_to_increment,
    floor_to_increment,
    generate_arithmetic_grid,
)


def config(**overrides: object) -> GridConfig:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "lower_price": Decimal("100"),
        "upper_price": Decimal("110"),
        "reference_price": Decimal("105"),
        "grid_count": 5,
        "quantity_per_order": Decimal("0.129"),
    }
    values.update(overrides)
    return GridConfig(**values)  # type: ignore[arg-type]


def rules(**overrides: object) -> SymbolRules:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "tick_size": Decimal("0.1"),
        "step_size": Decimal("0.01"),
        "min_qty": Decimal("0.01"),
        "min_notional": Decimal("5"),
    }
    values.update(overrides)
    return SymbolRules(**values)  # type: ignore[arg-type]


def test_quantization_supports_non_power_of_ten_increment() -> None:
    assert floor_to_increment(Decimal("100.079"), Decimal("0.05")) == Decimal("100.05")
    assert ceil_to_increment(Decimal("100.021"), Decimal("0.05")) == Decimal("100.05")


def test_generate_arithmetic_grid_builds_quantized_neutral_levels() -> None:
    plan = generate_arithmetic_grid(config(), rules())

    assert plan.quantity_per_order == Decimal("0.12")
    assert plan.anchor_index == 2
    assert [level.price for level in plan.levels] == [
        Decimal("100.0"),
        Decimal("102.0"),
        Decimal("104.0"),
        Decimal("106.0"),
        Decimal("108.0"),
        Decimal("110.0"),
    ]
    assert [level.side for level in plan.levels] == [
        OrderSide.BUY,
        OrderSide.BUY,
        None,
        OrderSide.SELL,
        OrderSide.SELL,
        OrderSide.SELL,
    ]


def test_grid_uses_count_as_number_of_intervals() -> None:
    grid_config = config(grid_count=4)

    plan = generate_arithmetic_grid(grid_config, rules())

    assert len(plan.levels) == grid_config.grid_count + 1


def test_grid_stays_inside_non_aligned_range() -> None:
    grid_config = config(
        lower_price=Decimal("100.03"),
        upper_price=Decimal("100.99"),
        reference_price=Decimal("100.5"),
        grid_count=4,
        quantity_per_order=Decimal("1"),
    )

    plan = generate_arithmetic_grid(grid_config, rules())

    assert plan.levels[0].price == Decimal("100.1")
    assert plan.levels[-1].price == Decimal("100.9")
    assert all(
        grid_config.lower_price <= level.price <= grid_config.upper_price for level in plan.levels
    )


def test_grid_rejects_duplicate_prices_after_tick_quantization() -> None:
    grid_config = config(
        lower_price=Decimal("100"),
        upper_price=Decimal("100.2"),
        reference_price=Decimal("100.1"),
        grid_count=4,
        quantity_per_order=Decimal("1"),
    )

    with pytest.raises(DomainValidationError, match="not unique"):
        generate_arithmetic_grid(grid_config, rules())


def test_grid_rejects_quantity_below_minimum_after_rounding() -> None:
    with pytest.raises(DomainValidationError, match="min_qty"):
        generate_arithmetic_grid(
            config(quantity_per_order=Decimal("0.019")),
            rules(step_size=Decimal("0.01"), min_qty=Decimal("0.02")),
        )


def test_grid_rejects_active_level_below_min_notional() -> None:
    with pytest.raises(DomainValidationError, match="min_notional"):
        generate_arithmetic_grid(
            config(quantity_per_order=Decimal("0.01")),
            rules(min_notional=Decimal("5")),
        )


def test_grid_rejects_symbol_rule_mismatch() -> None:
    with pytest.raises(DomainValidationError, match="same symbol"):
        generate_arithmetic_grid(config(), rules(symbol="ETHUSDT"))


def test_grid_selects_exact_reference_level_as_anchor() -> None:
    plan = generate_arithmetic_grid(
        config(reference_price=Decimal("104")),
        rules(),
    )

    assert plan.levels[plan.anchor_index].price == Decimal("104.0")
    assert plan.levels[plan.anchor_index].side is None


def test_quantization_rejects_float_input() -> None:
    with pytest.raises(TypeError, match="value must be Decimal"):
        floor_to_increment(100.1, Decimal("0.1"))  # type: ignore[arg-type]
