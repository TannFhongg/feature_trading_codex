"""Restart reconciliation and explicit resume gate."""

from collections.abc import Callable
from time import time_ns
from typing import Protocol

from trading_bot.domain import StrategyState
from trading_bot.execution import ReconciliationReport
from trading_bot.risk.breaker import RiskCircuitBreaker
from trading_bot.risk.errors import CircuitBreakerResetError, RecoveryNotReadyError
from trading_bot.risk.models import (
    RecoveryResult,
    RiskAuditEvent,
    RiskEventType,
    RuntimeRiskSnapshot,
)
from trading_bot.risk.service import RiskAuditLedger


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


class RecoveryReconciler(Protocol):
    """Read-only reconciliation boundary required before restart resume."""

    async def reconcile(self, symbol: str) -> ReconciliationReport:
        """Reconcile durable local state with the exchange."""


class RestartRecoveryCoordinator:
    """Never auto-resume: reconcile first, pause, then require explicit healthy resume."""

    def __init__(
        self,
        reconciler: RecoveryReconciler,
        breaker: RiskCircuitBreaker,
        ledger: RiskAuditLedger,
        *,
        clock_ms: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        self._reconciler = reconciler
        self._breaker = breaker
        self._ledger = ledger
        self._clock_ms = clock_ms
        self._state = StrategyState.RECOVERING
        self._last_report: ReconciliationReport | None = None

    @property
    def state(self) -> StrategyState:
        return self._state

    async def recover(self, symbol: str) -> RecoveryResult:
        self._state = StrategyState.RECOVERING
        report = await self._reconciler.reconcile(symbol)
        self._last_report = report
        self._state = StrategyState.PAUSED if report.safe_to_resume else StrategyState.RECOVERING
        reason_codes = self._report_reason_codes(report)
        await self._ledger.record_risk_event(
            RiskAuditEvent(
                event_type=RiskEventType.RECOVERY,
                symbol=symbol,
                outcome="RECONCILED" if report.safe_to_resume else "MISMATCH",
                event_time_ms=self._clock_ms(),
                reason_codes=reason_codes,
            )
        )
        return RecoveryResult(state=self._state, report=report)

    async def resume(self, snapshot: RuntimeRiskSnapshot) -> StrategyState:
        report = self._last_report
        if report is None or not report.safe_to_resume or self._state is not StrategyState.PAUSED:
            raise RecoveryNotReadyError("safe reconciliation must complete before resume")
        if snapshot.symbol != report.symbol or not snapshot.reconciliation_safe:
            raise RecoveryNotReadyError(
                "resume snapshot must match the reconciled symbol and state"
            )
        if not self._breaker.reset(snapshot):
            raise CircuitBreakerResetError("runtime state is not safe enough to reset the breaker")
        await self._ledger.record_risk_event(
            RiskAuditEvent(
                event_type=RiskEventType.BREAKER_RESET,
                symbol=snapshot.symbol,
                outcome="ARMED",
                event_time_ms=self._clock_ms(),
            )
        )
        self._state = StrategyState.RUNNING
        return self._state

    @staticmethod
    def _report_reason_codes(report: ReconciliationReport) -> tuple[str, ...]:
        reasons: list[str] = []
        if report.orphan_client_order_ids:
            reasons.append("ORPHAN_ORDERS")
        if report.unresolved_local_client_order_ids:
            reasons.append("UNRESOLVED_LOCAL_ORDERS")
        if report.quantity_mismatch_client_order_ids:
            reasons.append("ORDER_QUANTITY_MISMATCH")
        if report.position_mismatch_keys:
            reasons.append("POSITION_MISMATCH")
        return tuple(reasons)
