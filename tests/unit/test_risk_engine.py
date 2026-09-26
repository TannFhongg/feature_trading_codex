from dataclasses import replace
from decimal import Decimal

import pytest

from trading_bot.domain import DomainValidationError, OrderSide, StrategyState
from trading_bot.execution import OrderRequest
from trading_bot.risk import (
    RiskEngine,
    RiskLimits,
    RiskReason,
    RuntimeRiskSnapshot,
)


def limits() -> RiskLimits:
    return RiskLimits(
        symbol="BTCUSDT",
        max_abs_position_quantity=Decimal("1"),
        max_position_notional=Decimal("50000"),
        max_open_orders=4,
        max_daily_loss=Decimal("100"),
        max_drawdown=Decimal("200"),
        max_abs_funding_rate=Decimal("0.001"),
        min_liquidation_distance_ratio=Decimal("0.10"),
        max_maintenance_margin_ratio=Decimal("0.50"),
        max_market_data_age_ms=2_000,
        max_user_data_age_ms=5_000,
        soft_lower_price=Decimal("25000"),
        soft_upper_price=Decimal("35000"),
        hard_lower_price=Decimal("24000"),
        hard_upper_price=Decimal("36000"),
    )


def snapshot(**overrides: object) -> RuntimeRiskSnapshot:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "strategy_state": StrategyState.RUNNING,
        "position_quantity": Decimal("0.2"),
        "mark_price": Decimal("30000"),
        "liquidation_price": Decimal("20000"),
        "open_order_count": 1,
        "open_order_notional": Decimal("3000"),
        "realized_pnl_today": Decimal("10"),
        "equity": Decimal("1000"),
        "peak_equity": Decimal("1050"),
        "funding_rate": Decimal("0.0001"),
        "maintenance_margin": Decimal("100"),
        "margin_balance": Decimal("1000"),
        "market_data_age_ms": 100,
        "user_data_age_ms": 100,
        "reconciliation_safe": True,
    }
    values.update(overrides)
    return RuntimeRiskSnapshot(**values)  # type: ignore[arg-type]


def request(
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("30000"),
    reduce_only: bool = False,
) -> OrderRequest:
    return OrderRequest(
        strategy_id="alpha",
        level_index=1,
        cycle=1,
        symbol="BTCUSDT",
        side=side,
        price=price,
        quantity=quantity,
        reduce_only=reduce_only,
    )


def reasons(decision: object) -> set[RiskReason]:
    assert hasattr(decision, "violations")
    return {violation.reason for violation in decision.violations}  # type: ignore[union-attr]


def test_healthy_exposure_increase_is_approved() -> None:
    decision = RiskEngine(limits()).assess(request(), snapshot())

    assert decision.approved
    assert decision.increases_exposure
    assert decision.projected_position_quantity == Decimal("0.3")
    assert decision.violations == ()


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"strategy_state": StrategyState.PAUSED}, RiskReason.STRATEGY_NOT_RUNNING),
        ({"reconciliation_safe": False}, RiskReason.RECONCILIATION_REQUIRED),
        ({"market_data_age_ms": 2_001}, RiskReason.MARKET_DATA_STALE),
        ({"user_data_age_ms": 5_001}, RiskReason.USER_DATA_STALE),
        ({"emergency_stop_active": True}, RiskReason.EMERGENCY_STOP_ACTIVE),
    ],
)
def test_runtime_gates_fail_closed(changes: dict[str, object], expected: RiskReason) -> None:
    decision = RiskEngine(limits()).assess(request(), snapshot(**changes))

    assert not decision.approved
    assert expected in reasons(decision)


def test_position_notional_and_open_order_caps_are_all_reported() -> None:
    policy = replace(
        limits(),
        max_abs_position_quantity=Decimal("0.25"),
        max_position_notional=Decimal("5000"),
        max_open_orders=1,
    )
    decision = RiskEngine(policy).assess(request(), snapshot())

    assert reasons(decision) >= {
        RiskReason.MAX_POSITION_QUANTITY,
        RiskReason.MAX_POSITION_NOTIONAL,
        RiskReason.MAX_OPEN_ORDERS,
    }


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"realized_pnl_today": Decimal("-100")}, RiskReason.DAILY_LOSS_LIMIT),
        ({"equity": Decimal("800")}, RiskReason.DRAWDOWN_LIMIT),
        ({"funding_rate": Decimal("0.0011")}, RiskReason.FUNDING_RATE_LIMIT),
        ({"liquidation_price": Decimal("28000")}, RiskReason.LIQUIDATION_DISTANCE),
        ({"maintenance_margin": Decimal("500")}, RiskReason.MAINTENANCE_MARGIN_RATIO),
    ],
)
def test_financial_limits_block_new_exposure(
    changes: dict[str, object], expected: RiskReason
) -> None:
    decision = RiskEngine(limits()).assess(request(), snapshot(**changes))

    assert not decision.approved
    assert expected in reasons(decision)
    if expected in {
        RiskReason.DAILY_LOSS_LIMIT,
        RiskReason.DRAWDOWN_LIMIT,
        RiskReason.LIQUIDATION_DISTANCE,
        RiskReason.MAINTENANCE_MARGIN_RATIO,
    }:
        assert decision.emergency_stop_required


def test_soft_and_hard_boundaries_have_distinct_response_severity() -> None:
    engine = RiskEngine(limits())

    soft = engine.assess(request(price=Decimal("24500")), snapshot())
    hard = engine.assess(
        request(price=Decimal("23000")),
        snapshot(mark_price=Decimal("23000")),
    )

    assert RiskReason.SOFT_PRICE_BOUNDARY in reasons(soft)
    assert not soft.emergency_stop_required
    assert RiskReason.HARD_PRICE_BOUNDARY in reasons(hard)
    assert hard.emergency_stop_required


def test_valid_reduce_only_exit_bypasses_stale_and_latched_entry_gates() -> None:
    unhealthy = snapshot(
        strategy_state=StrategyState.EMERGENCY_STOP,
        market_data_age_ms=100_000,
        user_data_age_ms=100_000,
        reconciliation_safe=False,
        emergency_stop_active=True,
    )
    decision = RiskEngine(limits()).assess(
        request(side=OrderSide.SELL, quantity=Decimal("0.2"), reduce_only=True),
        unhealthy,
    )

    assert decision.approved
    assert not decision.increases_exposure
    assert decision.projected_position_quantity == 0


@pytest.mark.parametrize(
    "order",
    [
        request(side=OrderSide.BUY, reduce_only=True),
        request(side=OrderSide.SELL, quantity=Decimal("0.3"), reduce_only=True),
    ],
)
def test_invalid_reduce_only_intent_is_rejected(order: OrderRequest) -> None:
    decision = RiskEngine(limits()).assess(order, snapshot())

    assert not decision.approved
    assert reasons(decision) == {RiskReason.INVALID_REDUCE_ONLY}


def test_short_liquidation_distance_is_direction_aware() -> None:
    safe = snapshot(position_quantity=Decimal("-0.2"), liquidation_price=Decimal("34000"))
    unsafe = replace(safe, liquidation_price=Decimal("32000"))

    assert safe.liquidation_distance_ratio > Decimal("0.10")
    assert RiskReason.LIQUIDATION_DISTANCE in reasons(
        RiskEngine(limits()).assess(request(side=OrderSide.SELL), unsafe)
    )


def test_risk_inputs_reject_binary_float_and_invalid_boundary_order() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        replace(limits(), max_drawdown=1.0)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="boundaries"):
        replace(limits(), hard_lower_price=Decimal("26000"))
