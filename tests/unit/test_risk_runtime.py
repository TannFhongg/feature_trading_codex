import asyncio
from decimal import Decimal

import pytest

from trading_bot.domain import OrderSide, StrategyState
from trading_bot.execution import (
    EmergencyCloseRequest,
    ExchangeOrder,
    OrderRequest,
    OrderStatus,
    PositionSnapshot,
    ReconciliationReport,
)
from trading_bot.persistence import SqliteExecutionLedger
from trading_bot.risk import (
    CircuitBreakerResetError,
    CircuitBreakerState,
    EmergencyActionAlreadyRecordedError,
    EmergencyActionStatus,
    EmergencyExitCoordinator,
    RecoveryNotReadyError,
    RestartRecoveryCoordinator,
    RiskCircuitBreaker,
    RiskEngine,
    RiskLimits,
    RiskManagedOrderExecutor,
    RiskReason,
    RiskRejectedError,
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


def order_request() -> OrderRequest:
    return OrderRequest(
        strategy_id="alpha",
        level_index=1,
        cycle=1,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("30000"),
        quantity=Decimal("0.1"),
    )


def exchange_order(
    client_order_id: str,
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("0.1"),
    reduce_only: bool = False,
) -> ExchangeOrder:
    return ExchangeOrder(
        symbol="BTCUSDT",
        client_order_id=client_order_id,
        exchange_order_id=42,
        side=side,
        status=OrderStatus.FILLED if reduce_only else OrderStatus.NEW,
        price=Decimal("0") if reduce_only else Decimal("30000"),
        original_quantity=quantity,
        executed_quantity=quantity if reduce_only else Decimal("0"),
        average_price=Decimal("30000") if reduce_only else Decimal("0"),
        reduce_only=reduce_only,
        update_time_ms=1_000,
    )


class RecordingSubmitter:
    def __init__(self) -> None:
        self.requests: list[OrderRequest] = []

    async def submit(self, request: OrderRequest) -> ExchangeOrder:
        self.requests.append(request)
        return exchange_order(request.client_order_id)


def test_risk_managed_executor_audits_and_blocks_before_submission() -> None:
    async def scenario() -> None:
        ledger = await SqliteExecutionLedger.open(":memory:")
        submitter = RecordingSubmitter()
        breaker = RiskCircuitBreaker(limits())
        executor = RiskManagedOrderExecutor(
            RiskEngine(limits()),
            breaker,
            submitter,
            ledger,
            clock_ms=lambda: 10,
        )

        with pytest.raises(RiskRejectedError):
            await executor.submit(order_request(), snapshot(market_data_age_ms=2_001))

        assert submitter.requests == []
        assert breaker.state is CircuitBreakerState.TRIPPED
        events = await ledger.list_risk_events()
        assert {event.outcome for event in events} == {"REJECTED", "TRIPPED"}
        assert all(event.reason_codes == (RiskReason.MARKET_DATA_STALE.value,) for event in events)
        await ledger.close()

    asyncio.run(scenario())


def test_risk_managed_executor_submits_only_after_durable_approval_audit() -> None:
    async def scenario() -> None:
        ledger = await SqliteExecutionLedger.open(":memory:")
        submitter = RecordingSubmitter()
        executor = RiskManagedOrderExecutor(
            RiskEngine(limits()),
            RiskCircuitBreaker(limits()),
            submitter,
            ledger,
            clock_ms=lambda: 10,
        )

        result = await executor.submit(order_request(), snapshot())

        assert result.status is OrderStatus.NEW
        assert submitter.requests == [order_request()]
        assert (await ledger.list_risk_events())[0].outcome == "APPROVED"
        await ledger.close()

    asyncio.run(scenario())


def test_breaker_reset_requires_paused_reconciled_and_healthy_state() -> None:
    breaker = RiskCircuitBreaker(limits())
    breaker.trip(RiskReason.MARKET_DATA_STALE)

    assert not breaker.reset(
        snapshot(strategy_state=StrategyState.PAUSED, market_data_age_ms=9_000)
    )
    assert breaker.tripped
    assert breaker.reset(snapshot(strategy_state=StrategyState.PAUSED))
    assert breaker.state is CircuitBreakerState.ARMED
    assert breaker.reasons == ()


def report(*, safe: bool) -> ReconciliationReport:
    return ReconciliationReport(
        symbol="BTCUSDT",
        started_at_ms=1,
        completed_at_ms=2,
        orphan_client_order_ids=() if safe else ("orphan",),
        unresolved_local_client_order_ids=(),
        quantity_mismatch_client_order_ids=(),
        position_mismatch_keys=(),
        inserted_fills=0,
        inserted_income_records=0,
    )


class FakeReconciler:
    def __init__(self, result: ReconciliationReport) -> None:
        self.result = result
        self.calls: list[str] = []

    async def reconcile(self, symbol: str) -> ReconciliationReport:
        self.calls.append(symbol)
        return self.result


def test_restart_reconciles_to_pause_and_requires_explicit_healthy_resume() -> None:
    async def scenario() -> None:
        ledger = await SqliteExecutionLedger.open(":memory:")
        breaker = RiskCircuitBreaker(limits())
        breaker.trip(RiskReason.RECONCILIATION_REQUIRED)
        reconciler = FakeReconciler(report(safe=True))
        coordinator = RestartRecoveryCoordinator(
            reconciler,
            breaker,
            ledger,
            clock_ms=lambda: 10,
        )

        result = await coordinator.recover("BTCUSDT")

        assert result.safe_to_resume
        assert result.state is StrategyState.PAUSED
        assert coordinator.state is StrategyState.PAUSED
        resumed = await coordinator.resume(snapshot(strategy_state=StrategyState.PAUSED))
        assert resumed is StrategyState.RUNNING
        assert not breaker.tripped
        assert {event.outcome for event in await ledger.list_risk_events()} == {
            "ARMED",
            "RECONCILED",
        }
        await ledger.close()

    asyncio.run(scenario())


def test_restart_mismatch_or_unhealthy_snapshot_cannot_resume() -> None:
    async def scenario() -> None:
        ledger = await SqliteExecutionLedger.open(":memory:")
        breaker = RiskCircuitBreaker(limits())
        unsafe = RestartRecoveryCoordinator(FakeReconciler(report(safe=False)), breaker, ledger)
        await unsafe.recover("BTCUSDT")
        with pytest.raises(RecoveryNotReadyError):
            await unsafe.resume(snapshot(strategy_state=StrategyState.PAUSED))

        healthy = RestartRecoveryCoordinator(FakeReconciler(report(safe=True)), breaker, ledger)
        await healthy.recover("BTCUSDT")
        with pytest.raises(CircuitBreakerResetError):
            await healthy.resume(
                snapshot(strategy_state=StrategyState.PAUSED, market_data_age_ms=10_000)
            )
        await ledger.close()

    asyncio.run(scenario())


def position(quantity: Decimal, *, position_side: str = "BOTH") -> PositionSnapshot:
    return PositionSnapshot(
        symbol="BTCUSDT",
        position_side=position_side,
        quantity=quantity,
        entry_price=Decimal("30000"),
        break_even_price=Decimal("30010"),
        unrealized_pnl=Decimal("0"),
        margin_type="isolated",
        isolated_wallet=Decimal("100"),
        update_time_ms=1_000,
    )


class EmergencyGateway:
    def __init__(
        self,
        positions: tuple[PositionSnapshot, ...],
        *,
        submit_error: BaseException | None = None,
    ) -> None:
        self.positions = positions
        self.submit_error = submit_error
        self.calls: list[str] = []
        self.request: EmergencyCloseRequest | None = None

    async def cancel_all_open_orders(self, symbol: str) -> None:
        self.calls.append(f"cancel:{symbol}")

    async def fetch_positions(self, symbol: str) -> tuple[PositionSnapshot, ...]:
        self.calls.append(f"positions:{symbol}")
        return self.positions

    async def submit_reduce_only_market(
        self,
        request: EmergencyCloseRequest,
    ) -> ExchangeOrder:
        self.calls.append("flatten")
        self.request = request
        if self.submit_error is not None:
            raise self.submit_error
        return exchange_order(
            request.client_order_id,
            side=request.side,
            quantity=request.quantity,
            reduce_only=True,
        )


def test_emergency_exit_persists_before_cancel_and_flattens_long_reduce_only() -> None:
    async def scenario() -> None:
        ledger = await SqliteExecutionLedger.open(":memory:")
        gateway = EmergencyGateway((position(Decimal("0.2")),))
        breaker = RiskCircuitBreaker(limits())
        coordinator = EmergencyExitCoordinator(gateway, ledger, breaker, clock_ms=lambda: 10)

        result = await coordinator.execute(
            "incident-1",
            "BTCUSDT",
            RiskReason.HARD_PRICE_BOUNDARY,
        )

        assert gateway.calls == ["cancel:BTCUSDT", "positions:BTCUSDT", "flatten"]
        assert gateway.request is not None
        assert gateway.request.side is OrderSide.SELL
        assert gateway.request.quantity == Decimal("0.2")
        assert result.status is EmergencyActionStatus.SUBMITTED
        assert result.close_order is not None and result.close_order.reduce_only
        action = await ledger.get_emergency_action("incident-1")
        assert action is not None and action.status is EmergencyActionStatus.SUBMITTED
        assert breaker.tripped

        with pytest.raises(EmergencyActionAlreadyRecordedError):
            await coordinator.execute(
                "incident-1",
                "BTCUSDT",
                RiskReason.HARD_PRICE_BOUNDARY,
            )
        assert gateway.calls == ["cancel:BTCUSDT", "positions:BTCUSDT", "flatten"]
        await ledger.close()

    asyncio.run(scenario())


def test_emergency_exit_records_no_position_and_unknown_submission() -> None:
    async def scenario() -> None:
        ledger = await SqliteExecutionLedger.open(":memory:")
        no_position = EmergencyGateway(())
        coordinator = EmergencyExitCoordinator(
            no_position,
            ledger,
            RiskCircuitBreaker(limits()),
            clock_ms=lambda: 10,
        )
        result = await coordinator.execute("incident-empty", "BTCUSDT", RiskReason.DAILY_LOSS_LIMIT)
        assert result.status is EmergencyActionStatus.NO_POSITION
        assert no_position.calls == ["cancel:BTCUSDT", "positions:BTCUSDT"]

        failed = EmergencyGateway((position(Decimal("-0.1")),), submit_error=TimeoutError())
        coordinator = EmergencyExitCoordinator(
            failed,
            ledger,
            RiskCircuitBreaker(limits()),
            clock_ms=lambda: 20,
        )
        with pytest.raises(TimeoutError):
            await coordinator.execute(
                "incident-unknown", "BTCUSDT", RiskReason.LIQUIDATION_DISTANCE
            )
        assert failed.request is not None and failed.request.side is OrderSide.BUY
        action = await ledger.get_emergency_action("incident-unknown")
        assert action is not None and action.status is EmergencyActionStatus.UNKNOWN
        await ledger.complete_emergency_action(
            "incident-unknown",
            EmergencyActionStatus.NO_POSITION,
            client_order_id=action.client_order_id,
            exchange_order_id=None,
            updated_at_ms=21,
        )
        resolved = await ledger.get_emergency_action("incident-unknown")
        assert resolved is not None and resolved.status is EmergencyActionStatus.NO_POSITION
        await ledger.close()

    asyncio.run(scenario())
