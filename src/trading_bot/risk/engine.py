"""Deterministic pre-trade risk evaluation."""

from decimal import Decimal

from trading_bot.domain import OrderSide, StrategyState
from trading_bot.execution import OrderRequest
from trading_bot.risk.models import (
    RiskDecision,
    RiskLimits,
    RiskReason,
    RiskSeverity,
    RiskViolation,
    RuntimeRiskSnapshot,
    projected_position,
)


class RiskEngine:
    """Fail-closed approval engine for every normal execution intent."""

    def __init__(self, limits: RiskLimits) -> None:
        if not isinstance(limits, RiskLimits):
            raise TypeError("limits must be RiskLimits")
        self._limits = limits

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    def assess(self, request: OrderRequest, snapshot: RuntimeRiskSnapshot) -> RiskDecision:
        """Evaluate all relevant invariants without mutating runtime state."""

        if not isinstance(request, OrderRequest):
            raise TypeError("request must be OrderRequest")
        if not isinstance(snapshot, RuntimeRiskSnapshot):
            raise TypeError("snapshot must be RuntimeRiskSnapshot")

        projected_quantity = projected_position(
            snapshot.position_quantity,
            request.side,
            request.quantity,
        )
        projected_notional = abs(projected_quantity) * request.price
        increases_exposure = not request.reduce_only
        violations: list[RiskViolation] = []

        if request.symbol != self._limits.symbol or snapshot.symbol != self._limits.symbol:
            self._add(
                violations,
                RiskReason.SYMBOL_MISMATCH,
                RiskSeverity.BLOCK,
                "order, snapshot, and risk policy symbols must match",
            )

        if request.reduce_only:
            if not self._is_valid_reduce_only(request, snapshot, projected_quantity):
                self._add(
                    violations,
                    RiskReason.INVALID_REDUCE_ONLY,
                    RiskSeverity.BLOCK,
                    "reduce-only intent must strictly reduce without crossing the current position",
                )
            return self._decision(
                request,
                projected_quantity,
                projected_notional,
                False,
                violations,
            )

        self._check_runtime_gates(snapshot, violations)
        self._check_account_limits(snapshot, violations)
        self._check_projected_limits(request, snapshot, projected_quantity, violations)
        return self._decision(
            request,
            projected_quantity,
            projected_notional,
            increases_exposure,
            violations,
        )

    @staticmethod
    def _is_valid_reduce_only(
        request: OrderRequest,
        snapshot: RuntimeRiskSnapshot,
        projected_quantity: Decimal,
    ) -> bool:
        current = snapshot.position_quantity
        if current == 0:
            return False
        if current > 0 and request.side is not OrderSide.SELL:
            return False
        if current < 0 and request.side is not OrderSide.BUY:
            return False
        return abs(projected_quantity) < abs(current) and (
            projected_quantity == 0 or (projected_quantity > 0) == (current > 0)
        )

    def _check_runtime_gates(
        self,
        snapshot: RuntimeRiskSnapshot,
        violations: list[RiskViolation],
    ) -> None:
        if snapshot.strategy_state is not StrategyState.RUNNING:
            self._add(
                violations,
                RiskReason.STRATEGY_NOT_RUNNING,
                RiskSeverity.BLOCK,
                "exposure may increase only while the strategy is RUNNING",
            )
        if not snapshot.reconciliation_safe:
            self._add(
                violations,
                RiskReason.RECONCILIATION_REQUIRED,
                RiskSeverity.BLOCK,
                "reconciliation must complete without mismatches before exposure increases",
            )
        if snapshot.market_data_age_ms > self._limits.max_market_data_age_ms:
            self._add(
                violations,
                RiskReason.MARKET_DATA_STALE,
                RiskSeverity.BLOCK,
                "market data is older than the configured maximum age",
            )
        if snapshot.user_data_age_ms > self._limits.max_user_data_age_ms:
            self._add(
                violations,
                RiskReason.USER_DATA_STALE,
                RiskSeverity.BLOCK,
                "user data is older than the configured maximum age",
            )
        if snapshot.emergency_stop_active:
            self._add(
                violations,
                RiskReason.EMERGENCY_STOP_ACTIVE,
                RiskSeverity.BLOCK,
                "the emergency stop is latched",
            )
        if not snapshot.one_way_mode:
            self._add(
                violations,
                RiskReason.POSITION_MODE_NOT_ONE_WAY,
                RiskSeverity.BLOCK,
                "the MVP requires Binance one-way position mode",
            )
        if not snapshot.isolated_margin:
            self._add(
                violations,
                RiskReason.MARGIN_MODE_NOT_ISOLATED,
                RiskSeverity.BLOCK,
                "the MVP requires isolated margin",
            )
        if snapshot.leverage > self._limits.max_leverage:
            self._add(
                violations,
                RiskReason.MAX_LEVERAGE,
                RiskSeverity.BLOCK,
                "configured leverage exceeds the risk-policy cap",
            )

    def _check_account_limits(
        self,
        snapshot: RuntimeRiskSnapshot,
        violations: list[RiskViolation],
    ) -> None:
        if snapshot.daily_loss >= self._limits.max_daily_loss:
            self._add(
                violations,
                RiskReason.DAILY_LOSS_LIMIT,
                RiskSeverity.EMERGENCY,
                "daily realized loss reached the hard limit",
            )
        if snapshot.drawdown >= self._limits.max_drawdown:
            self._add(
                violations,
                RiskReason.DRAWDOWN_LIMIT,
                RiskSeverity.EMERGENCY,
                "strategy drawdown reached the hard limit",
            )
        if abs(snapshot.funding_rate) > self._limits.max_abs_funding_rate:
            self._add(
                violations,
                RiskReason.FUNDING_RATE_LIMIT,
                RiskSeverity.BLOCK,
                "absolute funding rate exceeds the configured limit",
            )
        if snapshot.liquidation_distance_ratio < self._limits.min_liquidation_distance_ratio:
            self._add(
                violations,
                RiskReason.LIQUIDATION_DISTANCE,
                RiskSeverity.EMERGENCY,
                "position is too close to its liquidation price",
            )
        if snapshot.maintenance_margin_ratio >= self._limits.max_maintenance_margin_ratio:
            self._add(
                violations,
                RiskReason.MAINTENANCE_MARGIN_RATIO,
                RiskSeverity.EMERGENCY,
                "maintenance margin ratio reached the hard limit",
            )
        if not self._inside_hard_boundary(snapshot.mark_price):
            self._add(
                violations,
                RiskReason.HARD_PRICE_BOUNDARY,
                RiskSeverity.EMERGENCY,
                "mark price is outside the hard strategy boundary",
            )
        elif not self._inside_soft_boundary(snapshot.mark_price):
            self._add(
                violations,
                RiskReason.SOFT_PRICE_BOUNDARY,
                RiskSeverity.BLOCK,
                "mark price is outside the soft strategy boundary",
            )

    def _check_projected_limits(
        self,
        request: OrderRequest,
        snapshot: RuntimeRiskSnapshot,
        projected_quantity: Decimal,
        violations: list[RiskViolation],
    ) -> None:
        if abs(projected_quantity) > self._limits.max_abs_position_quantity:
            self._add(
                violations,
                RiskReason.MAX_POSITION_QUANTITY,
                RiskSeverity.BLOCK,
                "projected absolute position exceeds its limit",
            )
        projected_total_notional = snapshot.open_order_notional + request.price * request.quantity
        projected_total_notional += abs(snapshot.position_quantity) * snapshot.mark_price
        if projected_total_notional > self._limits.max_position_notional:
            self._add(
                violations,
                RiskReason.MAX_POSITION_NOTIONAL,
                RiskSeverity.BLOCK,
                "position plus open-order notional exceeds its limit",
            )
        if snapshot.open_order_count + 1 > self._limits.max_open_orders:
            self._add(
                violations,
                RiskReason.MAX_OPEN_ORDERS,
                RiskSeverity.BLOCK,
                "projected open-order count exceeds its limit",
            )
        if (
            request.price <= self._limits.hard_lower_price
            or request.price >= self._limits.hard_upper_price
        ):
            self._add(
                violations,
                RiskReason.HARD_PRICE_BOUNDARY,
                RiskSeverity.EMERGENCY,
                "order price is outside the hard strategy boundary",
            )
        elif (
            request.price <= self._limits.soft_lower_price
            or request.price >= self._limits.soft_upper_price
        ):
            self._add(
                violations,
                RiskReason.SOFT_PRICE_BOUNDARY,
                RiskSeverity.BLOCK,
                "order price is outside the soft strategy boundary",
            )

    def _inside_soft_boundary(self, price: Decimal) -> bool:
        return self._limits.soft_lower_price < price < self._limits.soft_upper_price

    def _inside_hard_boundary(self, price: Decimal) -> bool:
        return self._limits.hard_lower_price < price < self._limits.hard_upper_price

    @staticmethod
    def _add(
        violations: list[RiskViolation],
        reason: RiskReason,
        severity: RiskSeverity,
        detail: str,
    ) -> None:
        if any(existing.reason is reason for existing in violations):
            return
        violations.append(RiskViolation(reason=reason, severity=severity, detail=detail))

    @staticmethod
    def _decision(
        request: OrderRequest,
        projected_quantity: Decimal,
        projected_notional: Decimal,
        increases_exposure: bool,
        violations: list[RiskViolation],
    ) -> RiskDecision:
        return RiskDecision(
            client_order_id=request.client_order_id,
            approved=not violations,
            increases_exposure=increases_exposure,
            projected_position_quantity=projected_quantity,
            projected_position_notional=projected_notional,
            violations=tuple(violations),
        )
