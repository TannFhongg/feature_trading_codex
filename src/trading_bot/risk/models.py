"""Exchange-independent risk policy and decision records."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from trading_bot.domain import DomainValidationError, OrderSide, StrategyState


def _require_decimal(
    name: str,
    value: object,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise DomainValidationError(f"{name} must be finite")
    if positive and value <= 0:
        raise DomainValidationError(f"{name} must be greater than zero")
    if non_negative and value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


def _require_non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int")
    if value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


def _validate_symbol(symbol: str) -> None:
    if not isinstance(symbol, str):
        raise TypeError("symbol must be str")
    if not symbol or symbol != symbol.strip().upper():
        raise DomainValidationError("symbol must be non-empty, trimmed, and uppercase")
    if not symbol.replace("_", "").isalnum():
        raise DomainValidationError("symbol must contain only letters, numbers, and underscores")


class RiskReason(StrEnum):
    """Stable machine-readable reasons for a rejected execution intent."""

    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    INVALID_REDUCE_ONLY = "INVALID_REDUCE_ONLY"
    STRATEGY_NOT_RUNNING = "STRATEGY_NOT_RUNNING"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    MARKET_DATA_STALE = "MARKET_DATA_STALE"
    USER_DATA_STALE = "USER_DATA_STALE"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    MAX_POSITION_QUANTITY = "MAX_POSITION_QUANTITY"
    MAX_POSITION_NOTIONAL = "MAX_POSITION_NOTIONAL"
    MAX_OPEN_ORDERS = "MAX_OPEN_ORDERS"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    FUNDING_RATE_LIMIT = "FUNDING_RATE_LIMIT"
    LIQUIDATION_DISTANCE = "LIQUIDATION_DISTANCE"
    MAINTENANCE_MARGIN_RATIO = "MAINTENANCE_MARGIN_RATIO"
    SOFT_PRICE_BOUNDARY = "SOFT_PRICE_BOUNDARY"
    HARD_PRICE_BOUNDARY = "HARD_PRICE_BOUNDARY"


class RiskSeverity(StrEnum):
    """Operational response associated with a risk violation."""

    BLOCK = "BLOCK"
    EMERGENCY = "EMERGENCY"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Hard runtime limits for one symbol and strategy instance."""

    symbol: str
    max_abs_position_quantity: Decimal
    max_position_notional: Decimal
    max_open_orders: int
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_abs_funding_rate: Decimal
    min_liquidation_distance_ratio: Decimal
    max_maintenance_margin_ratio: Decimal
    max_market_data_age_ms: int
    max_user_data_age_ms: int
    soft_lower_price: Decimal
    soft_upper_price: Decimal
    hard_lower_price: Decimal
    hard_upper_price: Decimal

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _require_decimal("max_abs_position_quantity", self.max_abs_position_quantity, positive=True)
        _require_decimal("max_position_notional", self.max_position_notional, positive=True)
        _require_non_negative_int("max_open_orders", self.max_open_orders)
        _require_decimal("max_daily_loss", self.max_daily_loss, positive=True)
        _require_decimal("max_drawdown", self.max_drawdown, positive=True)
        funding_limit = _require_decimal(
            "max_abs_funding_rate", self.max_abs_funding_rate, positive=True
        )
        liquidation_distance = _require_decimal(
            "min_liquidation_distance_ratio",
            self.min_liquidation_distance_ratio,
            positive=True,
        )
        margin_ratio = _require_decimal(
            "max_maintenance_margin_ratio",
            self.max_maintenance_margin_ratio,
            positive=True,
        )
        _require_non_negative_int("max_market_data_age_ms", self.max_market_data_age_ms)
        _require_non_negative_int("max_user_data_age_ms", self.max_user_data_age_ms)
        soft_lower = _require_decimal("soft_lower_price", self.soft_lower_price, positive=True)
        soft_upper = _require_decimal("soft_upper_price", self.soft_upper_price, positive=True)
        hard_lower = _require_decimal("hard_lower_price", self.hard_lower_price, positive=True)
        hard_upper = _require_decimal("hard_upper_price", self.hard_upper_price, positive=True)
        if self.max_open_orders == 0:
            raise DomainValidationError("max_open_orders must be greater than zero")
        if funding_limit >= 1:
            raise DomainValidationError("max_abs_funding_rate must be less than one")
        if liquidation_distance > 1:
            raise DomainValidationError("min_liquidation_distance_ratio must not exceed one")
        if margin_ratio >= 1:
            raise DomainValidationError("max_maintenance_margin_ratio must be less than one")
        if not hard_lower < soft_lower < soft_upper < hard_upper:
            raise DomainValidationError(
                "price boundaries must satisfy hard_lower < soft_lower < soft_upper < hard_upper"
            )


@dataclass(frozen=True, slots=True)
class RuntimeRiskSnapshot:
    """Point-in-time state consumed by deterministic pre-trade risk evaluation."""

    symbol: str
    strategy_state: StrategyState
    position_quantity: Decimal
    mark_price: Decimal
    liquidation_price: Decimal
    open_order_count: int
    open_order_notional: Decimal
    realized_pnl_today: Decimal
    equity: Decimal
    peak_equity: Decimal
    funding_rate: Decimal
    maintenance_margin: Decimal
    margin_balance: Decimal
    market_data_age_ms: int
    user_data_age_ms: int
    reconciliation_safe: bool
    emergency_stop_active: bool = False

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        if not isinstance(self.strategy_state, StrategyState):
            raise TypeError("strategy_state must be StrategyState")
        _require_decimal("position_quantity", self.position_quantity)
        _require_decimal("mark_price", self.mark_price, positive=True)
        _require_decimal("liquidation_price", self.liquidation_price, non_negative=True)
        _require_non_negative_int("open_order_count", self.open_order_count)
        _require_decimal("open_order_notional", self.open_order_notional, non_negative=True)
        _require_decimal("realized_pnl_today", self.realized_pnl_today)
        equity = _require_decimal("equity", self.equity, non_negative=True)
        peak_equity = _require_decimal("peak_equity", self.peak_equity, non_negative=True)
        _require_decimal("funding_rate", self.funding_rate)
        _require_decimal("maintenance_margin", self.maintenance_margin, non_negative=True)
        _require_decimal("margin_balance", self.margin_balance, non_negative=True)
        _require_non_negative_int("market_data_age_ms", self.market_data_age_ms)
        _require_non_negative_int("user_data_age_ms", self.user_data_age_ms)
        if peak_equity < equity:
            raise DomainValidationError("peak_equity must be greater than or equal to equity")
        if not isinstance(self.reconciliation_safe, bool):
            raise TypeError("reconciliation_safe must be bool")
        if not isinstance(self.emergency_stop_active, bool):
            raise TypeError("emergency_stop_active must be bool")

    @property
    def daily_loss(self) -> Decimal:
        return max(-self.realized_pnl_today, Decimal("0"))

    @property
    def drawdown(self) -> Decimal:
        return self.peak_equity - self.equity

    @property
    def maintenance_margin_ratio(self) -> Decimal:
        if self.margin_balance == 0:
            return Decimal("Infinity") if self.maintenance_margin > 0 else Decimal("0")
        return self.maintenance_margin / self.margin_balance

    @property
    def liquidation_distance_ratio(self) -> Decimal:
        if self.position_quantity == 0:
            return Decimal("1")
        if self.position_quantity > 0:
            if self.liquidation_price == 0:
                return Decimal("1")
            return max((self.mark_price - self.liquidation_price) / self.mark_price, Decimal("0"))
        if self.liquidation_price <= self.mark_price:
            return Decimal("0")
        return (self.liquidation_price - self.mark_price) / self.mark_price


@dataclass(frozen=True, slots=True)
class RiskViolation:
    """One failed invariant with its required operational response."""

    reason: RiskReason
    severity: RiskSeverity
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason, RiskReason):
            raise TypeError("reason must be RiskReason")
        if not isinstance(self.severity, RiskSeverity):
            raise TypeError("severity must be RiskSeverity")
        if not isinstance(self.detail, str) or not self.detail:
            raise DomainValidationError("detail must be a non-empty string")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Complete auditable result of evaluating one execution intent."""

    client_order_id: str
    approved: bool
    increases_exposure: bool
    projected_position_quantity: Decimal
    projected_position_notional: Decimal
    violations: tuple[RiskViolation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.client_order_id, str) or not self.client_order_id:
            raise DomainValidationError("client_order_id must be a non-empty string")
        if not isinstance(self.approved, bool):
            raise TypeError("approved must be bool")
        if not isinstance(self.increases_exposure, bool):
            raise TypeError("increases_exposure must be bool")
        _require_decimal("projected_position_quantity", self.projected_position_quantity)
        _require_decimal(
            "projected_position_notional",
            self.projected_position_notional,
            non_negative=True,
        )
        if any(not isinstance(violation, RiskViolation) for violation in self.violations):
            raise TypeError("violations must contain RiskViolation values")
        if self.approved == bool(self.violations):
            raise DomainValidationError("approved must be true exactly when violations is empty")

    @property
    def emergency_stop_required(self) -> bool:
        return any(violation.severity is RiskSeverity.EMERGENCY for violation in self.violations)


def projected_position(quantity: Decimal, side: OrderSide, order_quantity: Decimal) -> Decimal:
    """Apply a one-way-mode order quantity to a signed position quantity."""

    return quantity + order_quantity if side is OrderSide.BUY else quantity - order_quantity
