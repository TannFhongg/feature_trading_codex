"""Errors raised by risk and recovery orchestration."""

from trading_bot.risk.models import RiskDecision


class RiskControlError(RuntimeError):
    """Base error for P5 safety-control failures."""


class RiskRejectedError(RiskControlError):
    """Raised before persistence/network submission when risk denies an intent."""

    def __init__(self, decision: RiskDecision) -> None:
        reasons = ",".join(violation.reason.value for violation in decision.violations)
        super().__init__(f"risk rejected {decision.client_order_id}: {reasons}")
        self.decision = decision


class CircuitBreakerResetError(RiskControlError):
    """A latched breaker cannot be reset from the supplied runtime state."""


class RecoveryNotReadyError(RiskControlError):
    """Restart recovery has not established a safe resume point."""


class EmergencyActionAlreadyRecordedError(RiskControlError):
    """An emergency action ID already exists and must be reconciled, not repeated."""


class EmergencyExitError(RiskControlError):
    """The emergency close plan violates one-way/reduce-only invariants."""
