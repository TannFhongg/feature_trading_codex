"""Latched runtime circuit breaker for critical risk and feed failures."""

from trading_bot.domain import StrategyState
from trading_bot.risk.models import (
    CircuitBreakerState,
    RiskDecision,
    RiskLimits,
    RiskReason,
    RiskSeverity,
    RuntimeRiskSnapshot,
)

_LATCHING_REASONS = {
    RiskReason.RECONCILIATION_REQUIRED,
    RiskReason.MARKET_DATA_STALE,
    RiskReason.USER_DATA_STALE,
    RiskReason.EMERGENCY_STOP_ACTIVE,
    RiskReason.POSITION_MODE_NOT_ONE_WAY,
    RiskReason.MARGIN_MODE_NOT_ISOLATED,
    RiskReason.MAX_LEVERAGE,
    RiskReason.RUNTIME_TASK_FAILURE,
    RiskReason.EVENT_BACKPRESSURE,
}


class RiskCircuitBreaker:
    """Latch safety failures until an explicit, fully healthy recovery reset."""

    def __init__(self, limits: RiskLimits) -> None:
        if not isinstance(limits, RiskLimits):
            raise TypeError("limits must be RiskLimits")
        self._limits = limits
        self._state = CircuitBreakerState.ARMED
        self._reasons: set[RiskReason] = set()

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    @property
    def tripped(self) -> bool:
        return self._state is CircuitBreakerState.TRIPPED

    @property
    def reasons(self) -> tuple[RiskReason, ...]:
        return tuple(sorted(self._reasons, key=lambda reason: reason.value))

    def observe(self, decision: RiskDecision) -> bool:
        """Latch stale/reconciliation failures and every emergency-severity violation."""

        if not isinstance(decision, RiskDecision):
            raise TypeError("decision must be RiskDecision")
        before = self.tripped
        for violation in decision.violations:
            if (
                violation.reason in _LATCHING_REASONS
                or violation.severity is RiskSeverity.EMERGENCY
            ):
                self._reasons.add(violation.reason)
        if self._reasons:
            self._state = CircuitBreakerState.TRIPPED
        return self.tripped and not before

    def trip(self, reason: RiskReason) -> bool:
        """Explicitly latch the breaker, used by an operator emergency action."""

        if not isinstance(reason, RiskReason):
            raise TypeError("reason must be RiskReason")
        before = self.tripped
        self._reasons.add(reason)
        self._state = CircuitBreakerState.TRIPPED
        return not before

    def reset(self, snapshot: RuntimeRiskSnapshot) -> bool:
        """Clear the latch only from a paused/recovering and fully healthy snapshot."""

        if not isinstance(snapshot, RuntimeRiskSnapshot):
            raise TypeError("snapshot must be RuntimeRiskSnapshot")
        if snapshot.symbol != self._limits.symbol:
            return False
        if snapshot.strategy_state not in {StrategyState.PAUSED, StrategyState.RECOVERING}:
            return False
        if not snapshot.reconciliation_safe or snapshot.emergency_stop_active:
            return False
        if not snapshot.one_way_mode or not snapshot.isolated_margin:
            return False
        if snapshot.leverage > self._limits.max_leverage:
            return False
        if snapshot.market_data_age_ms > self._limits.max_market_data_age_ms:
            return False
        if snapshot.user_data_age_ms > self._limits.max_user_data_age_ms:
            return False
        if snapshot.daily_loss >= self._limits.max_daily_loss:
            return False
        if snapshot.drawdown >= self._limits.max_drawdown:
            return False
        if abs(snapshot.funding_rate) > self._limits.max_abs_funding_rate:
            return False
        if snapshot.liquidation_distance_ratio < self._limits.min_liquidation_distance_ratio:
            return False
        if snapshot.maintenance_margin_ratio >= self._limits.max_maintenance_margin_ratio:
            return False
        if abs(snapshot.position_quantity) > self._limits.max_abs_position_quantity:
            return False
        if (
            abs(snapshot.position_quantity) * snapshot.mark_price
            > self._limits.max_position_notional
        ):
            return False
        if snapshot.open_order_count > self._limits.max_open_orders:
            return False
        if not self._limits.soft_lower_price < snapshot.mark_price < self._limits.soft_upper_price:
            return False
        self._reasons.clear()
        self._state = CircuitBreakerState.ARMED
        return True
