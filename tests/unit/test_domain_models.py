from decimal import Decimal

import pytest

from trading_bot.domain import (
    DomainValidationError,
    GridConfig,
    GridDirection,
    GridLevel,
    GridPlan,
    GridType,
    OrderSide,
    StrategyState,
    SymbolRules,
)


def valid_config(**overrides: object) -> GridConfig:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "lower_price": Decimal("90000"),
        "upper_price": Decimal("110000"),
        "reference_price": Decimal("100000"),
        "grid_count": 10,
        "quantity_per_order": Decimal("0.001"),
    }
    values.update(overrides)
    return GridConfig(**values)  # type: ignore[arg-type]


def test_grid_config_accepts_p1_mode() -> None:
    config = valid_config()

    assert config.grid_type is GridType.ARITHMETIC
    assert config.direction is GridDirection.NEUTRAL


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("lower_price", Decimal("0"), "lower_price"),
        ("upper_price", Decimal("90000"), "upper_price"),
        ("reference_price", Decimal("110000"), "reference_price"),
        ("grid_count", 1, "grid_count"),
        ("quantity_per_order", Decimal("0"), "quantity_per_order"),
    ],
)
def test_grid_config_rejects_invalid_values(field: str, value: object, message: str) -> None:
    with pytest.raises(DomainValidationError, match=message):
        valid_config(**{field: value})


def test_grid_config_rejects_binary_float() -> None:
    with pytest.raises(TypeError, match="lower_price must be Decimal"):
        valid_config(lower_price=90000.0)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("grid_type", GridType.GEOMETRIC, "ARITHMETIC"),
        ("direction", GridDirection.LONG, "NEUTRAL"),
        ("direction", GridDirection.SHORT, "NEUTRAL"),
    ],
)
def test_grid_config_rejects_modes_outside_p1(field: str, value: object, message: str) -> None:
    with pytest.raises(DomainValidationError, match=message):
        valid_config(**{field: value})


def test_symbol_rules_require_positive_decimal_filters() -> None:
    with pytest.raises(DomainValidationError, match="tick_size"):
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
        )


def test_grid_plan_requires_exactly_one_anchor() -> None:
    levels = (
        GridLevel(index=0, price=Decimal("100"), side=None),
        GridLevel(index=1, price=Decimal("101"), side=None),
    )

    with pytest.raises(DomainValidationError, match="exactly one anchor"):
        GridPlan(
            symbol="BTCUSDT",
            quantity_per_order=Decimal("0.01"),
            anchor_index=0,
            levels=levels,
        )


@pytest.mark.parametrize(
    ("levels", "message"),
    [
        (
            (
                GridLevel(index=1, price=Decimal("100"), side=None),
                GridLevel(index=2, price=Decimal("101"), side=OrderSide.SELL),
            ),
            "indices",
        ),
        (
            (
                GridLevel(index=0, price=Decimal("100"), side=None),
                GridLevel(index=1, price=Decimal("100"), side=OrderSide.SELL),
            ),
            "unique",
        ),
        (
            (
                GridLevel(index=0, price=Decimal("101"), side=None),
                GridLevel(index=1, price=Decimal("100"), side=OrderSide.BUY),
            ),
            "increasing",
        ),
    ],
)
def test_grid_plan_rejects_invalid_level_sequences(
    levels: tuple[GridLevel, ...], message: str
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        GridPlan(
            symbol="BTCUSDT",
            quantity_per_order=Decimal("0.01"),
            anchor_index=0,
            levels=levels,
        )


def test_strategy_state_contract_contains_recovery_and_emergency_states() -> None:
    assert StrategyState.RECOVERING.value == "RECOVERING"
    assert StrategyState.EMERGENCY_STOP.value == "EMERGENCY_STOP"


def test_grid_level_accepts_typed_order_side() -> None:
    level = GridLevel(index=0, price=Decimal("100"), side=OrderSide.BUY)

    assert level.side is OrderSide.BUY
