from decimal import Decimal

import pytest

from trading_bot.domain import DomainValidationError, OrderSide
from trading_bot.execution import (
    CommissionRates,
    ExchangeOrder,
    OrderRequest,
    OrderStatus,
    deterministic_client_order_id,
)


def request(**overrides: object) -> OrderRequest:
    values: dict[str, object] = {
        "strategy_id": "neutral-btc",
        "level_index": 12,
        "cycle": 3,
        "symbol": "BTCUSDT",
        "side": OrderSide.BUY,
        "price": Decimal("65000.10"),
        "quantity": Decimal("0.002"),
    }
    values.update(overrides)
    return OrderRequest(**values)  # type: ignore[arg-type]


def test_client_order_id_is_deterministic_and_encodes_logical_key() -> None:
    first = request().client_order_id
    second = deterministic_client_order_id("neutral-btc", 12, OrderSide.BUY, 3)

    assert first == second == "grid-neutral-btc-12-buy-3"


def test_long_client_order_id_uses_stable_bounded_digest() -> None:
    first = deterministic_client_order_id("strategy-" + "x" * 100, 12, OrderSide.SELL, 8)
    second = deterministic_client_order_id("strategy-" + "x" * 100, 12, OrderSide.SELL, 8)
    different_cycle = deterministic_client_order_id("strategy-" + "x" * 100, 12, OrderSide.SELL, 9)

    assert first == second
    assert first != different_cycle
    assert len(first) <= 36
    assert first.startswith("grid-s-")


def test_sanitized_strategy_ids_cannot_collide() -> None:
    spaced = deterministic_client_order_id("alpha beta", 1, OrderSide.BUY, 1)
    dashed = deterministic_client_order_id("alpha-beta", 1, OrderSide.BUY, 1)

    assert spaced != dashed


def test_order_request_rejects_float_and_invalid_symbol() -> None:
    with pytest.raises(TypeError, match="price must be Decimal"):
        request(price=65000.1)
    with pytest.raises(DomainValidationError, match="uppercase"):
        request(symbol="btcusdt")


def test_exchange_order_rejects_local_only_status_and_overfill() -> None:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "client_order_id": request().client_order_id,
        "exchange_order_id": 42,
        "side": OrderSide.BUY,
        "status": OrderStatus.NEW,
        "price": Decimal("65000.1"),
        "original_quantity": Decimal("0.002"),
        "executed_quantity": Decimal("0"),
        "average_price": Decimal("0"),
        "reduce_only": False,
        "update_time_ms": 100,
    }

    with pytest.raises(TypeError, match="exchange OrderStatus"):
        ExchangeOrder(**{**values, "status": OrderStatus.UNKNOWN})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exchange OrderStatus"):
        ExchangeOrder(  # type: ignore[arg-type]
            **{**values, "status": OrderStatus.SUBMISSION_REJECTED}
        )
    rejected = ExchangeOrder(  # type: ignore[arg-type]
        **{**values, "status": OrderStatus.REJECTED}
    )
    assert rejected.status is OrderStatus.REJECTED
    with pytest.raises(DomainValidationError, match="must not exceed"):
        ExchangeOrder(  # type: ignore[arg-type]
            **{**values, "executed_quantity": Decimal("0.003")}
        )


def test_order_status_active_set_includes_unresolved_local_states() -> None:
    assert OrderStatus.PENDING_SUBMIT.is_active
    assert OrderStatus.UNKNOWN.is_active
    assert OrderStatus.PARTIALLY_FILLED.is_active
    assert not OrderStatus.FILLED.is_active


def test_commission_rates_are_decimal_and_below_one() -> None:
    rates = CommissionRates("BTCUSDT", Decimal("0.0002"), Decimal("0.0004"))

    assert rates.maker_rate == Decimal("0.0002")
    with pytest.raises(TypeError, match="Decimal"):
        CommissionRates("BTCUSDT", 0.0002, Decimal("0.0004"))  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="less than one"):
        CommissionRates("BTCUSDT", Decimal("1"), Decimal("0.0004"))
