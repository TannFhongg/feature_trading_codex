import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from trading_bot.application import ApplicationConfig, SecretValue, build_application
from trading_bot.binance import MarketEvent, MarkPrice
from trading_bot.domain import (
    CommandStatus,
    OrderSide,
    RuntimeCommand,
    RuntimeEventType,
    StrategyState,
)
from trading_bot.risk import CircuitBreakerState, EmergencyActionStatus


def config(**overrides: object) -> ApplicationConfig:
    values: dict[str, object] = {
        "control_token": SecretValue("x" * 32),
        "ledger_path": Path(":memory:"),
        "reconciliation_interval_seconds": 60.0,
    }
    values.update(overrides)
    return ApplicationConfig(**values)  # type: ignore[arg-type]


async def wait_until(predicate: object, *, timeout_seconds: float = 5.0) -> None:
    assert callable(predicate)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_dry_run_startup_initial_grid_fill_replacement_and_ordered_stop() -> None:
    async def scenario() -> None:
        application = await build_application(config())
        try:
            assert await application.runtime.start() is StrategyState.PAUSED
            await wait_until(lambda: application.runtime.status().ready)
            assert await application.runtime.resume() is StrategyState.RUNNING

            initial = await application.ledger.list_active_orders("BTCUSDT")
            assert len(initial) == 4
            source = next(
                order for order in initial if order.level_index == 1 and order.side is OrderSide.BUY
            )
            assert application.dry_run_gateway is not None
            await application.dry_run_gateway.simulate_fill(
                source.client_order_id,
                source.original_quantity,
            )

            async def replacement_exists() -> bool:
                orders = await application.ledger.list_active_orders("BTCUSDT")
                return any(
                    order.level_index == 2 and order.side is OrderSide.SELL for order in orders
                )

            for _ in range(100):
                if await replacement_exists():
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("confirmed fill did not create its replacement")

            assert application.runtime.state is StrategyState.RUNNING
            assert await application.runtime.pause() is StrategyState.PAUSED
            assert await application.ledger.list_active_orders("BTCUSDT") == ()
            for _ in range(100):
                positions = await application.ledger.list_positions("BTCUSDT")
                if positions[0].quantity == Decimal("0.001"):
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("confirmed position update was not persisted")
            await wait_until(lambda: application.runtime.status().ready)
            assert await application.runtime.resume() is StrategyState.RUNNING
            assert await application.runtime.stop() is StrategyState.STOPPED
            assert await application.runtime.stop() is StrategyState.STOPPED
        finally:
            await application.close()

    asyncio.run(scenario())


def test_emergency_control_command_latches_and_flattens_through_p5_boundary() -> None:
    async def scenario() -> None:
        application = await build_application(config())
        try:
            await application.runtime.start()
            await wait_until(lambda: application.runtime.status().ready)
            await application.runtime.resume()
            orders = await application.ledger.list_active_orders("BTCUSDT")
            buy = next(order for order in orders if order.side is OrderSide.BUY)
            assert application.dry_run_gateway is not None
            await application.dry_run_gateway.simulate_fill(
                buy.client_order_id,
                buy.original_quantity,
            )
            await asyncio.sleep(0.05)

            result = await application.control.execute(
                "emergency-1",
                RuntimeCommand.EMERGENCY_STOP,
                actor="operator",
            )

            assert result.status is CommandStatus.SUCCEEDED
            assert result.result_state is StrategyState.EMERGENCY_STOP
            action = await application.ledger.get_emergency_action("emergency-1")
            assert action is not None
            assert action.status is EmergencyActionStatus.SUBMITTED
            positions = await application.dry_run_gateway.fetch_positions("BTCUSDT")
            assert positions[0].quantity == 0
        finally:
            await application.close()

    asyncio.run(scenario())


def test_stale_feed_watchdog_pauses_cancels_and_latches_until_explicit_recovery() -> None:
    async def scenario() -> None:
        base = config()
        limits = replace(
            base.risk_limits,
            max_market_data_age_ms=50,
            max_user_data_age_ms=50,
        )
        application = await build_application(replace(base, risk_limits=limits))
        try:
            await application.runtime.start()
            await wait_until(lambda: application.runtime.status().ready)
            await application.runtime.resume()
            await wait_until(lambda: application.runtime.state is StrategyState.PAUSED)
            status = application.runtime.status()
            assert status.breaker_state is CircuitBreakerState.TRIPPED
            assert await application.ledger.list_active_orders("BTCUSDT") == ()
        finally:
            await application.close()

    asyncio.run(scenario())


class CrashingMarketSource:
    async def events(self) -> AsyncIterator[MarketEvent]:
        if False:
            yield MarkPrice(
                event_time_ms=1,
                symbol="BTCUSDT",
                mark_price=Decimal("60000"),
                index_price=Decimal("60000"),
                estimated_settle_price=Decimal("60000"),
                funding_rate=Decimal("0"),
                next_funding_time_ms=2,
            )
        raise RuntimeError("raw failure text must not enter audit")


class BurstingMarketSource:
    async def events(self) -> AsyncIterator[MarketEvent]:
        event_time = 1
        while True:
            yield MarkPrice(
                event_time_ms=event_time,
                symbol="BTCUSDT",
                mark_price=Decimal("60000"),
                index_price=Decimal("60000"),
                estimated_settle_price=Decimal("60000"),
                funding_rate=Decimal("0"),
                next_funding_time_ms=event_time + 1,
            )
            event_time += 1


class ReconnectHealth:
    def __init__(self) -> None:
        self.reconnect_count = 0


class ReconnectAwareMarketSource:
    def __init__(self) -> None:
        self.snapshot = ReconnectHealth()

    def health(self) -> ReconnectHealth:
        return self.snapshot

    async def events(self) -> AsyncIterator[MarketEvent]:
        event_time = 1
        while True:
            yield MarkPrice(
                event_time_ms=event_time,
                symbol="BTCUSDT",
                mark_price=Decimal("60000"),
                index_price=Decimal("60000"),
                estimated_settle_price=Decimal("60000"),
                funding_rate=Decimal("0"),
                next_funding_time_ms=event_time + 1,
            )
            event_time += 1
            await asyncio.sleep(0.01)


def test_supervised_task_crash_fails_closed_and_leaves_durable_evidence() -> None:
    async def scenario() -> None:
        application = await build_application(config())
        application.runtime._market_source = CrashingMarketSource()
        try:
            await application.runtime.start()
            await wait_until(lambda: application.runtime.state is StrategyState.ERROR)
            status = application.runtime.status()
            assert status.breaker_state is CircuitBreakerState.TRIPPED
            events = await application.ledger.list_runtime_events()
            failure = next(
                event for event in events if event.event_type is RuntimeEventType.TASK_FAILURE
            )
            assert failure.component == "market_stream"
            assert failure.reason_code == "RUNTIME_TASK_FAILURE"
        finally:
            await application.close()

    asyncio.run(scenario())


def test_bounded_queue_overflow_latches_breaker_instead_of_dropping_silently() -> None:
    async def scenario() -> None:
        application = await build_application(replace(config(), queue_capacity=1))
        application.runtime._market_source = BurstingMarketSource()
        try:
            await application.runtime.start()
            await wait_until(lambda: application.runtime.state is StrategyState.ERROR)
            events = await application.ledger.list_runtime_events()
            assert any(
                event.event_type is RuntimeEventType.BACKPRESSURE
                and event.reason_code == "EVENT_BACKPRESSURE"
                for event in events
            )
        finally:
            await application.close()

    asyncio.run(scenario())


def test_stream_reconnect_requires_reconciliation_and_another_explicit_resume() -> None:
    async def scenario() -> None:
        base = config()
        limits = replace(
            base.risk_limits,
            max_market_data_age_ms=500,
            max_user_data_age_ms=500,
        )
        application = await build_application(replace(base, risk_limits=limits))
        source = ReconnectAwareMarketSource()
        application.runtime._market_source = source
        try:
            await application.runtime.start()
            await asyncio.sleep(0.5)
            await wait_until(lambda: application.runtime.status().ready)
            await application.runtime.resume()
            source.snapshot.reconnect_count = 1
            await asyncio.sleep(0.5)
            await wait_until(lambda: application.runtime.state is StrategyState.PAUSED)
            status = application.runtime.status()
            assert not status.reconciliation_safe
            assert status.breaker_state is CircuitBreakerState.TRIPPED
            events = await application.ledger.list_runtime_events()
            assert any(event.outcome == "STREAM_RECONNECTED" for event in events)
        finally:
            await application.close()

    asyncio.run(scenario())
