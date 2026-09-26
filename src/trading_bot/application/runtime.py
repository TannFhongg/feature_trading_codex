"""P6 application orchestrator with supervised tasks and ordered shutdown."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from time import time_ns
from typing import Protocol

from trading_bot.application.config import ApplicationConfig
from trading_bot.application.errors import (
    EventBackpressureError,
    InvalidStateTransitionError,
    RuntimeDependencyError,
)
from trading_bot.application.observability import (
    AlertSink,
    LoggingAlertSink,
    RuntimeAlert,
    RuntimeMetrics,
)
from trading_bot.application.state import RuntimeStateStore, RuntimeStatusSnapshot
from trading_bot.application.strategy_runtime import NeutralGridRuntime
from trading_bot.binance import MarketEvent
from trading_bot.domain import (
    RuntimeAuditEvent,
    RuntimeEventType,
    StrategyState,
)
from trading_bot.execution import (
    ExchangeOrder,
    OrderRecord,
    OrderRequest,
    PositionSnapshot,
    ReconciliationReport,
    UserDataEvent,
)
from trading_bot.risk import (
    EmergencyExitCoordinator,
    RecoveryResult,
    RestartRecoveryCoordinator,
    RiskCircuitBreaker,
    RiskManagedOrderExecutor,
    RiskReason,
    RiskRejectedError,
    RuntimeRiskSnapshot,
)


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


class MarketEventSource(Protocol):
    def events(self) -> AsyncIterator[MarketEvent]:
        """Yield typed public market events."""


class UserEventSource(Protocol):
    def events(self) -> AsyncIterator[UserDataEvent]:
        """Yield typed authenticated account/order events."""


class RuntimePersistence(Protocol):
    async def list_active_orders(self, symbol: str) -> tuple[OrderRecord, ...]: ...

    async def list_strategy_orders(
        self,
        symbol: str,
        strategy_id: str,
    ) -> tuple[OrderRecord, ...]: ...

    async def list_positions(self, symbol: str) -> tuple[PositionSnapshot, ...]: ...

    async def record_runtime_event(self, event: RuntimeAuditEvent) -> bool: ...


class PersistentExecutionBoundary(Protocol):
    async def ingest_user_event(self, event: UserDataEvent) -> bool: ...

    async def cancel(self, symbol: str, client_order_id: str) -> ExchangeOrder | None: ...


class PeriodicReconciler(Protocol):
    async def reconcile(self, symbol: str) -> ReconciliationReport: ...


TaskFactory = Callable[[], Awaitable[None]]


class ApplicationOrchestrator:
    """Own lifecycle, queues, strategy execution, reconciliation, and fail-closed tasks."""

    def __init__(
        self,
        config: ApplicationConfig,
        state: RuntimeStateStore,
        strategy: NeutralGridRuntime,
        risk_executor: RiskManagedOrderExecutor,
        persistent_executor: PersistentExecutionBoundary,
        recovery: RestartRecoveryCoordinator,
        reconciler: PeriodicReconciler,
        emergency: EmergencyExitCoordinator,
        breaker: RiskCircuitBreaker,
        ledger: RuntimePersistence,
        market_source: MarketEventSource,
        user_source: UserEventSource,
        *,
        metrics: RuntimeMetrics | None = None,
        alerts: AlertSink | None = None,
        clock_ms: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        self._config = config
        self._state = state
        self._strategy = strategy
        self._risk_executor = risk_executor
        self._persistent_executor = persistent_executor
        self._recovery = recovery
        self._reconciler = reconciler
        self._emergency = emergency
        self._breaker = breaker
        self._ledger = ledger
        self._market_source = market_source
        self._user_source = user_source
        self._metrics = metrics or RuntimeMetrics()
        self._alerts = alerts or LoggingAlertSink()
        self._clock_ms = clock_ms
        self._logger = logging.getLogger("trading_bot.runtime")
        self._market_queue: asyncio.Queue[MarketEvent] = asyncio.Queue(config.queue_capacity)
        self._user_queue: asyncio.Queue[UserDataEvent] = asyncio.Queue(config.queue_capacity)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._execution_lock = asyncio.Lock()
        self._shutting_down = False
        self._source_reconnect_counts: dict[str, int] = {}

    @property
    def metrics(self) -> RuntimeMetrics:
        return self._metrics

    @property
    def state(self) -> StrategyState:
        return self._state.state

    def status(self) -> RuntimeStatusSnapshot:
        active_tasks = sum(not task.done() for task in self._tasks.values())
        status = self._state.status(
            self._breaker.state,
            task_count=active_tasks,
            market_queue_depth=self._market_queue.qsize(),
            user_queue_depth=self._user_queue.qsize(),
        )
        self._metrics.gauge("runtime_ready", int(status.ready))
        self._metrics.gauge("runtime_tasks", status.task_count)
        self._metrics.gauge("market_queue_depth", status.market_queue_depth)
        self._metrics.gauge("user_queue_depth", status.user_queue_depth)
        self._metrics.gauge("open_orders", status.open_order_count)
        return status

    async def start(self) -> StrategyState:
        """Validate, recover, start supervised tasks, and stop at PAUSED."""

        async with self._lifecycle_lock:
            if self.state in {
                StrategyState.PAUSED,
                StrategyState.RECOVERING,
                StrategyState.RUNNING,
            }:
                return self.state
            if self.state not in {StrategyState.DRAFT, StrategyState.STOPPED}:
                raise InvalidStateTransitionError(f"cannot start from {self.state.value}")
            self._shutting_down = False
            await self._transition(StrategyState.VALIDATING, "start")
            await self._transition(StrategyState.STARTING, "validated")
            await self._transition(StrategyState.RECOVERING, "startup_reconciliation")
            recovery = await self._recover_bounded()
            self._state.reconciliation_safe = recovery.report.safe_to_resume
            await self._restore_local_state()
            await self._transition(recovery.state, "reconciled")
            self._start_background_tasks()
            self._metrics.increment("runtime_starts")
            return self.state

    async def resume(self) -> StrategyState:
        """Reconcile again, require fresh healthy state, then place missing grid intents."""

        async with self._lifecycle_lock:
            if self.state is StrategyState.RUNNING:
                return self.state
            if self.state not in {StrategyState.PAUSED, StrategyState.RECOVERING}:
                raise InvalidStateTransitionError(f"cannot resume from {self.state.value}")
            await self._transition(StrategyState.RECOVERING, "explicit_resume")
            recovery = await self._recover_bounded()
            self._state.reconciliation_safe = recovery.report.safe_to_resume
            await self._restore_local_state()
            if not recovery.report.safe_to_resume:
                await self._transition(StrategyState.RECOVERING, "reconciliation_mismatch")
                raise RuntimeDependencyError("reconciliation did not establish a safe resume point")
            await self._transition(StrategyState.PAUSED, "reconciliation_safe")
            snapshot = self._state.risk_snapshot()
            await self._recovery.resume(snapshot)
            await self._transition(StrategyState.RUNNING, "explicit_healthy_resume")
            try:
                await self._submit_intents(self._strategy.initial_intents())
            except BaseException:
                await self._pause_after_intent_failure()
                raise
            self._metrics.increment("runtime_resumes")
            return self.state

    async def pause(self) -> StrategyState:
        """Stop new intents and cancel current grid orders while retaining the position."""

        async with self._lifecycle_lock:
            if self.state is StrategyState.PAUSED:
                return self.state
            if self.state is not StrategyState.RUNNING:
                raise InvalidStateTransitionError(f"cannot pause from {self.state.value}")
            async with self._execution_lock:
                try:
                    await self._cancel_grid_orders_locked()
                except BaseException as error:
                    self._breaker.trip(RiskReason.RUNTIME_TASK_FAILURE)
                    self._state.fail(type(error).__name__)
                    raise
                await self._transition(StrategyState.PAUSED, "operator_pause")
            self._metrics.increment("runtime_pauses")
            return self.state

    async def stop(self) -> StrategyState:
        """Apply normal stop policy, drain persisted events, and stop background tasks."""

        async with self._lifecycle_lock:
            if self.state is StrategyState.STOPPED:
                return self.state
            if self.state is StrategyState.DRAFT:
                await self._transition(StrategyState.STOPPED, "stopped_before_start")
                return self.state
            await self._transition(StrategyState.STOPPING, "operator_stop")
            async with self._execution_lock:
                await self._cancel_grid_orders_locked()
            await self._stop_background_tasks()
            await self._transition(StrategyState.STOPPED, "graceful_stop")
            self._metrics.increment("runtime_stops")
            return self.state

    async def emergency_stop(self, action_id: str) -> StrategyState:
        """Latch immediately, then invoke P5 persist-before-mutation cancel-and-flatten."""

        async with self._lifecycle_lock:
            if self.state is StrategyState.EMERGENCY_STOP:
                return self.state
            if self.state is StrategyState.DRAFT:
                raise InvalidStateTransitionError("cannot emergency-stop before startup")
            async with self._execution_lock:
                await self._transition(StrategyState.EMERGENCY_STOP, "operator_emergency")
                self._state.reconciliation_safe = False
                await self._emergency.execute(
                    action_id,
                    self._config.grid.symbol,
                    RiskReason.EMERGENCY_STOP_ACTIVE,
                )
            self._metrics.increment("emergency_stops")
            return self.state

    async def shutdown(self) -> None:
        """Idempotently stop intent creation and all owned async tasks before resource close."""

        self._shutting_down = True
        error_code: str | None = None
        try:
            if self.state is not StrategyState.STOPPED:
                await self.stop()
            else:
                await self._stop_background_tasks()
        except Exception as error:
            error_code = type(error).__name__
            self._state.fail(error_code)
            self._metrics.increment("shutdown_failures")
            await self._alerts.emit(
                RuntimeAlert("critical", error_code, "shutdown", self.state.value)
            )
        await self._ledger.record_runtime_event(
            RuntimeAuditEvent(
                event_type=RuntimeEventType.SHUTDOWN,
                symbol=self._config.grid.symbol,
                outcome="COMPLETE" if error_code is None else "INCOMPLETE",
                event_time_ms=self._clock_ms(),
                state=self.state,
                component="orchestrator",
                reason_code=error_code,
            )
        )

    async def _restore_local_state(self) -> None:
        all_orders = await self._ledger.list_strategy_orders(
            self._config.grid.symbol,
            self._config.strategy_id,
        )
        active_orders = tuple(order for order in all_orders if order.status.is_active)
        positions = await self._ledger.list_positions(self._config.grid.symbol)
        self._strategy.restore(all_orders)
        self._state.restore(active_orders, positions)

    async def _recover_bounded(self) -> RecoveryResult:
        """Allow one read-only verification pass after reconciliation repairs local observations."""

        result = await self._recovery.recover(self._config.grid.symbol)
        if not result.report.safe_to_resume:
            result = await self._recovery.recover(self._config.grid.symbol)
        return result

    def _start_background_tasks(self) -> None:
        if any(not task.done() for task in self._tasks.values()):
            return
        self._tasks.clear()
        self._capture_reconnect_baseline()
        factories: dict[str, TaskFactory] = {
            "market_stream": self._market_producer,
            "market_worker": self._market_worker,
            "user_stream": self._user_producer,
            "user_worker": self._user_worker,
            "reconciler": self._periodic_reconciliation,
            "stale_watchdog": self._stale_watchdog,
        }
        for name, factory in factories.items():
            self._tasks[name] = asyncio.create_task(
                self._supervise(name, factory),
                name=f"trading-bot:{name}",
            )

    async def _supervise(self, name: str, factory: TaskFactory) -> None:
        try:
            await factory()
            if not self._shutting_down:
                raise RuntimeDependencyError(f"{name} ended unexpectedly")
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            reason = (
                RiskReason.EVENT_BACKPRESSURE
                if isinstance(error, EventBackpressureError)
                else RiskReason.RUNTIME_TASK_FAILURE
            )
            await self._handle_task_failure(name, reason, type(error).__name__)

    async def _market_producer(self) -> None:
        async for event in self._market_source.events():
            try:
                self._market_queue.put_nowait(event)
            except asyncio.QueueFull as error:
                raise EventBackpressureError("market event queue is full") from error

    async def _market_worker(self) -> None:
        while True:
            event = await self._market_queue.get()
            try:
                self._state.apply_market_event(event)
                self._metrics.increment("market_events")
            finally:
                self._market_queue.task_done()

    async def _user_producer(self) -> None:
        async for event in self._user_source.events():
            try:
                self._user_queue.put_nowait(event)
            except asyncio.QueueFull as error:
                raise EventBackpressureError("user event queue is full") from error

    async def _user_worker(self) -> None:
        while True:
            event = await self._user_queue.get()
            try:
                self._state.touch_user()
                is_new = await self._persistent_executor.ingest_user_event(event)
                if not is_new:
                    self._metrics.increment("duplicate_user_events")
                    continue
                self._state.apply_user_event(event)
                self._metrics.increment("user_events")
                intents = self._strategy.on_user_event(event)
                if self.state is StrategyState.RUNNING:
                    try:
                        await self._submit_intents(intents)
                    except RiskRejectedError:
                        await self._pause_after_intent_failure()
            finally:
                self._user_queue.task_done()

    async def _submit_intents(self, intents: tuple[OrderRequest, ...]) -> None:
        for request in intents:
            async with self._execution_lock:
                if self.state is not StrategyState.RUNNING:
                    self._strategy.release_intent(request)
                    return
                snapshot: RuntimeRiskSnapshot = self._state.risk_snapshot()
                try:
                    order = await self._risk_executor.submit(request, snapshot)
                except BaseException:
                    self._strategy.release_intent(request)
                    self._metrics.increment("order_submit_failures")
                    raise
                self._strategy.observe_submission(request, order)
                self._state.observe_order(order)
                self._metrics.increment("orders_submitted")

    async def _pause_after_intent_failure(self) -> None:
        async with self._execution_lock:
            if self._breaker.tripped:
                await self._transition(StrategyState.PAUSED, "risk_breaker_latched")
            elif self.state is StrategyState.RUNNING:
                await self._transition(StrategyState.PAUSED, "intent_submission_failed")
            await self._cancel_grid_orders_locked()

    async def _cancel_grid_orders_locked(self) -> None:
        orders = await self._ledger.list_active_orders(self._config.grid.symbol)
        for order in orders:
            if order.strategy_id != self._config.strategy_id:
                continue
            canceled = await self._persistent_executor.cancel(
                order.symbol,
                order.client_order_id,
            )
            if canceled is None:
                self._state.reconciliation_safe = False
                raise RuntimeDependencyError("grid order cancellation could not be resolved")
            self._state.observe_order(canceled)
            self._metrics.increment("orders_canceled")

    async def _periodic_reconciliation(self) -> None:
        while True:
            await asyncio.sleep(self._config.reconciliation_interval_seconds)
            report = await self._reconciler.reconcile(self._config.grid.symbol)
            self._state.reconciliation_safe = report.safe_to_resume
            self._metrics.increment("reconciliation_runs")
            await self._ledger.record_runtime_event(
                RuntimeAuditEvent(
                    event_type=RuntimeEventType.RECONCILIATION,
                    symbol=self._config.grid.symbol,
                    outcome="SAFE" if report.safe_to_resume else "MISMATCH",
                    event_time_ms=self._clock_ms(),
                    state=self.state,
                    component="reconciler",
                )
            )
            if not report.safe_to_resume:
                await self._fail_closed_pause(
                    RiskReason.RECONCILIATION_REQUIRED,
                    "reconciliation_mismatch",
                    "reconciler",
                )

    async def _stale_watchdog(self) -> None:
        shortest_age_ms = min(
            self._config.risk_limits.max_market_data_age_ms,
            self._config.risk_limits.max_user_data_age_ms,
        )
        interval = max(min(shortest_age_ms / 2_000, 1.0), 0.05)
        while True:
            await asyncio.sleep(interval)
            reconnected_component = self._reconnected_component()
            if reconnected_component is not None:
                self._state.reconciliation_safe = False
                await self._ledger.record_runtime_event(
                    RuntimeAuditEvent(
                        event_type=RuntimeEventType.RECONCILIATION,
                        symbol=self._config.grid.symbol,
                        outcome="STREAM_RECONNECTED",
                        event_time_ms=self._clock_ms(),
                        state=self.state,
                        component=reconnected_component,
                        reason_code=RiskReason.RECONCILIATION_REQUIRED.value,
                    )
                )
                await self._fail_closed_pause(
                    RiskReason.RECONCILIATION_REQUIRED,
                    "stream_reconnected",
                    reconnected_component,
                )
                continue
            if self.state is not StrategyState.RUNNING:
                continue
            snapshot = self._state.risk_snapshot()
            if snapshot.market_data_age_ms > self._config.risk_limits.max_market_data_age_ms:
                await self._fail_closed_pause(
                    RiskReason.MARKET_DATA_STALE,
                    "market_data_stale",
                    "stale_watchdog",
                )
            elif snapshot.user_data_age_ms > self._config.risk_limits.max_user_data_age_ms:
                await self._fail_closed_pause(
                    RiskReason.USER_DATA_STALE,
                    "user_data_stale",
                    "stale_watchdog",
                )

    async def _fail_closed_pause(
        self,
        reason: RiskReason,
        code: str,
        component: str,
    ) -> None:
        self._breaker.trip(reason)
        async with self._execution_lock:
            if self.state is StrategyState.RUNNING:
                await self._cancel_grid_orders_locked()
                await self._transition(StrategyState.PAUSED, code)
        self._metrics.increment("breaker_trips")
        await self._alerts.emit(RuntimeAlert("critical", code, component, self.state.value))

    def _capture_reconnect_baseline(self) -> None:
        self._source_reconnect_counts.clear()
        for name, source in (
            ("market_stream", self._market_source),
            ("user_stream", self._user_source),
        ):
            count = self._source_reconnect_count(source)
            if count is not None:
                self._source_reconnect_counts[name] = count

    def _reconnected_component(self) -> str | None:
        for name, source in (
            ("market_stream", self._market_source),
            ("user_stream", self._user_source),
        ):
            count = self._source_reconnect_count(source)
            if count is None:
                continue
            previous = self._source_reconnect_counts.get(name, count)
            self._source_reconnect_counts[name] = count
            if count > previous:
                return name
        return None

    @staticmethod
    def _source_reconnect_count(source: object) -> int | None:
        health_method = getattr(source, "health", None)
        if not callable(health_method):
            return None
        health = health_method()
        reconnect_count = getattr(health, "reconnect_count", None)
        if isinstance(reconnect_count, bool) or not isinstance(reconnect_count, int):
            return None
        return reconnect_count

    async def _handle_task_failure(
        self,
        component: str,
        reason: RiskReason,
        error_code: str,
    ) -> None:
        self._breaker.trip(reason)
        self._state.fail(error_code)
        self._metrics.increment("task_failures")
        event_type = (
            RuntimeEventType.BACKPRESSURE
            if reason is RiskReason.EVENT_BACKPRESSURE
            else RuntimeEventType.TASK_FAILURE
        )
        with suppress(BaseException):
            await self._ledger.record_runtime_event(
                RuntimeAuditEvent(
                    event_type=event_type,
                    symbol=self._config.grid.symbol,
                    outcome="FAILED_CLOSED",
                    event_time_ms=self._clock_ms(),
                    state=self.state,
                    component=component,
                    reason_code=reason.value,
                )
            )
        async with self._execution_lock:
            with suppress(BaseException):
                await self._cancel_grid_orders_locked()
        current = asyncio.current_task()
        for task in self._tasks.values():
            if task is not current and not task.done():
                task.cancel()
        await self._alerts.emit(RuntimeAlert("critical", error_code, component, self.state.value))
        self._logger.error(
            "supervised task failed closed",
            extra={
                "event": "task_failure",
                "context": {"component": component, "error_code": error_code},
            },
        )

    async def _stop_background_tasks(self) -> None:
        producer_names = {"market_stream", "user_stream", "reconciler", "stale_watchdog"}
        for name in producer_names:
            task = self._tasks.get(name)
            if task is not None and not task.done():
                task.cancel()
        await self._await_tasks(producer_names)
        with suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(self._market_queue.join(), self._user_queue.join()),
                timeout=5.0,
            )
        worker_names = {"market_worker", "user_worker"}
        for name in worker_names:
            task = self._tasks.get(name)
            if task is not None and not task.done():
                task.cancel()
        await self._await_tasks(worker_names)
        self._tasks.clear()

    async def _await_tasks(self, names: set[str]) -> None:
        tasks = [self._tasks[name] for name in names if name in self._tasks]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _transition(self, state: StrategyState, outcome: str) -> None:
        self._state.transition(state)
        await self._ledger.record_runtime_event(
            RuntimeAuditEvent(
                event_type=RuntimeEventType.STATE_TRANSITION,
                symbol=self._config.grid.symbol,
                outcome=outcome,
                event_time_ms=self._clock_ms(),
                state=state,
                component="orchestrator",
            )
        )
