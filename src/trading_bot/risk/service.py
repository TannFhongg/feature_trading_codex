"""Risk approval boundary in front of durable order submission."""

from collections.abc import Callable
from dataclasses import replace
from time import time_ns
from typing import Protocol

from trading_bot.execution import ExchangeOrder, OrderRequest
from trading_bot.risk.breaker import RiskCircuitBreaker
from trading_bot.risk.engine import RiskEngine
from trading_bot.risk.errors import RiskRejectedError
from trading_bot.risk.models import RiskAuditEvent, RiskEventType, RuntimeRiskSnapshot


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


class DurableOrderSubmitter(Protocol):
    """Persist-before-send executor consumed after risk approval."""

    async def submit(self, request: OrderRequest) -> ExchangeOrder:
        """Persist and submit one approved order."""


class RiskAuditLedger(Protocol):
    """Audit append boundary shared by risk and recovery services."""

    async def record_risk_event(self, event: RiskAuditEvent) -> bool:
        """Insert one deterministic risk event if unseen."""


class RiskManagedOrderExecutor:
    """Require and audit deterministic risk approval before durable submission."""

    def __init__(
        self,
        risk_engine: RiskEngine,
        breaker: RiskCircuitBreaker,
        executor: DurableOrderSubmitter,
        ledger: RiskAuditLedger,
        *,
        clock_ms: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        self._risk_engine = risk_engine
        self._breaker = breaker
        self._executor = executor
        self._ledger = ledger
        self._clock_ms = clock_ms

    async def submit(
        self,
        request: OrderRequest,
        snapshot: RuntimeRiskSnapshot,
    ) -> ExchangeOrder:
        effective_snapshot = replace(
            snapshot,
            emergency_stop_active=snapshot.emergency_stop_active or self._breaker.tripped,
        )
        decision = self._risk_engine.assess(request, effective_snapshot)
        newly_tripped = self._breaker.observe(decision)
        await self._ledger.record_risk_event(
            RiskAuditEvent.from_decision(decision, request.symbol, self._clock_ms())
        )
        if newly_tripped:
            await self._ledger.record_risk_event(
                RiskAuditEvent(
                    event_type=RiskEventType.BREAKER_TRIPPED,
                    symbol=request.symbol,
                    outcome="TRIPPED",
                    event_time_ms=self._clock_ms(),
                    client_order_id=request.client_order_id,
                    reason_codes=tuple(reason.value for reason in self._breaker.reasons),
                )
            )
        if not decision.approved:
            raise RiskRejectedError(decision)
        return await self._executor.submit(request)
